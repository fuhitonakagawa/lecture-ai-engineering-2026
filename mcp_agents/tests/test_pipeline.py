"""Codex変換、MCP通信、成果物保存を接続して検証する。"""

import asyncio
import json
from pathlib import Path

import pytest

from src.codex_backend import CodexBackend
from src.config.settings import CodexSettings
from src.pipeline import ExperimentCase, run_case
from src.utils.codex_cli import ParsedResponse
from tests.helpers import response


def test_codex_model_receives_mcp_result_and_saves_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codexのツール選択をMCPで実行し、回答と実際の履歴を保存する。"""
    prompts: list[str] = []

    def execute(self: CodexBackend, prompt: str) -> ParsedResponse:
        prompts.append(prompt)
        if len(prompts) == 1:
            assert "glossary_get_lecture_summary" in prompt
            return response(
                "",
                [
                    {
                        "name": "glossary_get_lecture_summary",
                        "arguments": '{"lecture":2}',
                    }
                ],
            )
        assert "ファインチューニングとLoRA" in prompt
        return response("第2回はLoRAを学びます。", [])

    monkeypatch.setattr(CodexBackend, "_execute", execute)
    case = ExperimentCase(
        "stdio", "mcp", "config/stdio.json", "brief", "第2回では何を学びますか？"
    )
    directory = tmp_path / "case"
    result = asyncio.run(
        run_case(case, CodexSettings("test", "low", 10, 3, 2, 0), 15, directory)
    )
    assert result["status"] == "success"
    assert result["requests"] == result["cli_attempts"] == 2
    assert result["tool_calls"] == [
        {"kind": "call", "name": "glossary_get_lecture_summary", "args": {"lecture": 2}}
    ]
    assert json.loads((directory / "trace.json").read_text())[1]["kind"] == "return"
    assert "LoRA" in (directory / "answer.md").read_text()
    assert len(json.loads((directory / "tools.json").read_text())) == 3


def test_invalid_mode_is_saved_as_failure(tmp_path: Path) -> None:
    """接続設定の失敗をモデルの成功回答として扱わない。"""
    case = ExperimentCase("invalid", "invalid", "config/stdio.json", "brief", "質問")
    directory = tmp_path / "case"
    result = asyncio.run(
        run_case(case, CodexSettings("test", "low", 10, 3, 2, 0), 15, directory)
    )
    assert result["status"] == "error"
    assert result["requests"] == 0
    assert not (directory / "answer.md").exists()
    assert (
        json.loads((directory / "result.json").read_text())["error_type"]
        == "ValueError"
    )
