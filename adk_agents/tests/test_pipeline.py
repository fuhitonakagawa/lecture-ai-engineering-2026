"""学習支援エージェントの仕様を検証する。"""

import asyncio
import pytest
from src.codex_backend import CodexBackend, CodexSettings
from src.utils.codex_cli import ParsedResponse
from src.pipeline import run_adk, run_pydantic
from tests.helpers import response


@pytest.mark.parametrize("framework", ["pydantic", "adk"])
def test_framework_executes_quiz_and_returns_result_to_codex(
    monkeypatch: pytest.MonkeyPatch, framework: str
) -> None:
    """モデルの依頼が実関数を通り、次のモデル入力に正解が含まれる。"""
    prompts: list[str] = []

    def execute(self: CodexBackend, prompt: str) -> ParsedResponse:
        prompts.append(prompt)
        if len(prompts) == 1:
            assert "make_quiz" in prompt
            return response("", [{"name": "make_quiz", "arguments": '{"lecture": 2}'}])
        assert "事前学習" in prompt
        assert "大量の汎用テキスト" in prompt
        return response("事前学習とは何ですか？（第2回）", [])

    monkeypatch.setattr(CodexBackend, "_execute", execute)
    backend = CodexBackend(CodexSettings("test-model", "low", 10, 3, 2, 0))
    if framework == "pydantic":
        answer, trace, _ = asyncio.run(
            run_pydantic("第2回の確認問題", backend, plain=False)
        )
    else:
        answer, trace, _ = asyncio.run(run_adk("第2回の確認問題", backend))
    assert "事前学習" in answer
    assert trace[0] == {"kind": "call", "name": "make_quiz", "args": {"lecture": 2}}
    assert trace[1]["kind"] == "return"
    assert len(backend.records) == 2


def test_plain_model_has_no_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """素の応答にはツール定義を渡さない。"""

    def execute(self: CodexBackend, prompt: str) -> ParsedResponse:
        assert '"tools": []' in prompt
        return response("講座固有の情報は分かりません。", [])

    monkeypatch.setattr(CodexBackend, "_execute", execute)
    backend = CodexBackend(CodexSettings("test-model", "low", 10, 2, 2, 0))
    _, trace, _ = asyncio.run(run_pydantic("講座のタグとは？", backend, plain=True))
    assert trace == []
    assert len(backend.records) == 1
