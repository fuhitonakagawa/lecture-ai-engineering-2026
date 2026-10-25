"""学習支援エージェントの仕様を検証する。"""

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any
import pytest
from pydantic import ValidationError
from src.codex_backend import CodexBackend, CodexSettings, ModelDecision
from src.codex_errors import CodexFailure, output_diagnostics
from src.utils.codex_cli import ParsedResponse
from tests.helpers import response


@pytest.mark.parametrize(
    "decision",
    [
        {"answer": "", "tool_calls": []},
        {"answer": "回答", "tool_calls": [{"name": "make_quiz", "arguments": "{}"}]},
    ],
)
def test_decision_requires_exactly_one_action(decision: dict[str, Any]) -> None:
    """曖昧または空のモデル出力は実行しない。"""
    with pytest.raises(ValidationError):
        ModelDecision.model_validate(decision)


@pytest.mark.parametrize(
    "call",
    [
        {"name": "delete_files", "arguments": "{}"},
        {"name": "make_quiz", "arguments": "[]"},
    ],
)
def test_invalid_tool_call_is_rejected(
    monkeypatch: pytest.MonkeyPatch, call: dict[str, str]
) -> None:
    """未登録関数とオブジェクトでない引数を拒否する。"""
    monkeypatch.setattr(
        CodexBackend, "_execute", lambda self, prompt: response("", [call])
    )
    backend = CodexBackend(CodexSettings("test-model", "low", 10, 2, 2, 0))
    with pytest.raises(ValueError):
        asyncio.run(backend.complete("", [], [{"name": "make_quiz"}]))


def test_backend_limits_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    """予算を超えるモデル呼び出しは開始しない。"""
    monkeypatch.setattr(
        CodexBackend, "_execute", lambda self, prompt: response("回答", [])
    )
    backend = CodexBackend(CodexSettings("test-model", "low", 10, 1, 2, 0))
    asyncio.run(backend.complete("", [], []))
    with pytest.raises(RuntimeError, match="上限"):
        asyncio.run(backend.complete("", [], []))


def test_builtin_tool_usage_is_excluded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Codexの組み込みツールを使用した応答は比較に混ぜない。"""
    monkeypatch.setattr(
        CodexBackend,
        "_execute",
        lambda self, prompt: ParsedResponse("回答", {}, ("command_execution",)),
    )
    backend = CodexBackend(CodexSettings("test-model", "low", 10, 1, 2, 0))
    with pytest.raises(RuntimeError, match="アプリ外"):
        asyncio.run(backend.complete("", [], []))


def test_exec_uses_schema_stdin_and_read_only_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLIに渡す入力・スキーマ・実行制限とJSONLの解析を検証する。"""
    monkeypatch.setattr("src.codex_backend.shutil.which", lambda name: "/usr/bin/codex")

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert command[command.index("--sandbox") + 1] == "read-only"
        assert command[-1] == "-"
        assert kwargs["input"] == "input text"
        assert kwargs["timeout"] == 12
        schema = json.loads(
            Path(command[command.index("--output-schema") + 1]).read_text()
        )
        assert set(schema["required"]) == {"answer", "tool_calls"}
        assert schema["additionalProperties"] is False
        stdout = "\n".join(
            json.dumps(event)
            for event in [
                {
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": '{"answer":"回答","tool_calls":[]}',
                    },
                },
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 8, "output_tokens": 4},
                },
            ]
        )
        return subprocess.CompletedProcess(command, 0, stdout, "")

    monkeypatch.setattr("src.codex_backend.subprocess.run", run)
    backend = CodexBackend(CodexSettings("test-model", "low", 12, 2, 2, 0))
    result = backend._execute("input text")
    assert result.usage == {"input_tokens": 8, "output_tokens": 4}
    assert json.loads(result.answer)["answer"] == "回答"


def test_exec_timeout_has_safe_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """タイムアウト時にCLIの出力や入力本文を例外へ含めない。"""
    monkeypatch.setattr("src.codex_backend.shutil.which", lambda name: "/usr/bin/codex")

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, kwargs["timeout"], output="private")

    monkeypatch.setattr("src.codex_backend.subprocess.run", run)
    with pytest.raises(RuntimeError, match="制限時間") as error:
        CodexBackend(CodexSettings("test-model", "low", 1, 2, 2, 0))._execute("private")
    assert "private" not in str(error.value)


def test_exec_permission_error_is_not_reported_as_login_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OSの権限による起動失敗を認証エラーと区別する。"""
    monkeypatch.setattr("src.codex_backend.shutil.which", lambda name: "/usr/bin/codex")
    monkeypatch.setattr(
        "src.codex_backend.subprocess.run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 1, "", "Operation not permitted"
        ),
    )
    with pytest.raises(RuntimeError, match="起動する権限"):
        CodexBackend(CodexSettings("test-model", "low", 1, 2, 2, 0))._execute("test")


def test_timeout_is_retried_with_identical_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """タイムアウト後の試行は同じ入力を使い、失敗と成功の両方を保存する。"""
    prompts: list[str] = []

    def execute(self: CodexBackend, prompt: str) -> ParsedResponse:
        prompts.append(prompt)
        if len(prompts) == 1:
            raise CodexFailure("timeout", {"last_event": "turn.started"})
        return response("回答", [])

    monkeypatch.setattr(CodexBackend, "_execute", execute)
    backend = CodexBackend(CodexSettings("test-model", "low", 1, 3, 2, 0))
    decision, _ = asyncio.run(backend.complete("指示", [], []))
    assert decision.answer == "回答"
    assert len(prompts) == 2 and prompts[0] == prompts[1]
    assert len(backend.records) == 1
    assert [attempt["status"] for attempt in backend.records[0]["attempts"]] == [
        "error",
        "success",
    ]
    assert backend.records[0]["attempts"][0]["error_code"] == "timeout"


@pytest.mark.parametrize(
    "code", ["permission_denied", "authentication_error", "invalid_response"]
)
def test_permanent_failures_are_not_retried(
    monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    """権限・認証・応答形式の失敗は自動再試行しない。"""

    def execute(self: CodexBackend, prompt: str) -> ParsedResponse:
        raise CodexFailure(code, {})

    monkeypatch.setattr(CodexBackend, "_execute", execute)
    backend = CodexBackend(CodexSettings("test-model", "low", 1, 3, 2, 0))
    with pytest.raises(CodexFailure):
        asyncio.run(backend.complete("", [], []))
    assert len(backend.records[0]["attempts"]) == 1
    assert backend.records[0]["error_code"] == code
    assert "elapsed_seconds" in backend.records[0]


def test_partial_output_diagnostics_do_not_include_content() -> None:
    """途中出力からはイベントの種類だけを残す。"""
    data = "\n".join(
        [
            '{"type":"turn.started","private":"secret"}',
            '{"type":"item.updated","item":{"type":"reasoning","text":"private reasoning"}}',
            "incomplete {private",
        ]
    )
    diagnostics = output_diagnostics(data.encode())
    assert diagnostics["last_event"] == "item.updated"
    assert diagnostics["last_item_type"] == "reasoning"
    assert "secret" not in json.dumps(diagnostics)
    assert "private" not in json.dumps(diagnostics)
