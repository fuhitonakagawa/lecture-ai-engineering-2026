"""成功結果の再利用、失敗履歴の保持、再開時の比較条件を検証する。"""

import asyncio
from dataclasses import asdict, replace
import json
from pathlib import Path
from typing import Any

import pytest

from src import pipeline
from src.codex_backend import CodexBackend
from src.codex_errors import CodexFailure
from src.config.settings import CodexSettings
from src.experiment_storage import archive_case, prepare_run, run_lock, saved_success
from src.utils.artifacts import write_json


def write_success(directory: Path, mode: str, question: str) -> dict[str, Any]:
    """再開の検証用に、モデルAPIを使わず成果物を作る。"""
    directory.mkdir()
    record = {
        "mode": mode,
        "question": question,
        "status": "success",
        "requests": 1,
        "tool_calls": [],
    }
    write_json(directory / "result.json", record)
    write_json(directory / "model_requests.json", [{"request": 1, "status": "success"}])
    write_json(directory / "trace.json", [])
    write_json(directory / "messages.json", [])
    (directory / "answer.md").write_text("保存された回答\n", encoding="utf-8")
    return record


def test_resume_preserves_success_and_archives_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """成功済みのケースを呼び直さず、失敗したケースだけを履歴に移す。"""
    settings = CodexSettings("test", "low", 1, 3, 2, 0)
    success_dir = tmp_path / "01_plain"
    write_success(success_dir, "plain", "質問")
    original = (success_dir / "answer.md").read_bytes()
    failed_dir = tmp_path / "01_pydantic"
    failed_dir.mkdir()
    failed_record = {"mode": "pydantic", "question": "質問", "status": "error"}
    write_json(failed_dir / "result.json", failed_record)
    calls: list[str] = []

    async def run_case(
        mode: str, question: str, settings: CodexSettings, directory: Path
    ) -> dict[str, Any]:
        calls.append(mode)
        return write_success(directory, mode, question)

    monkeypatch.setattr(pipeline, "run_case", run_case)
    asyncio.run(pipeline.run_cases(tmp_path, settings, ["質問"]))
    assert calls == ["pydantic", "adk"]
    assert (success_dir / "answer.md").read_bytes() == original
    assert (
        json.loads((tmp_path / "history/01_pydantic/001/result.json").read_text())
        == failed_record
    )
    assert len(json.loads((tmp_path / "summary.json").read_text())) == 3
    calls.clear()
    asyncio.run(pipeline.run_cases(tmp_path, settings, ["質問"]))
    assert calls == []


def test_timeout_does_not_prevent_other_cases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一時的な失敗が残っても他方式を実行し、全体は失敗として終了する。"""
    settings = CodexSettings("test", "low", 1, 3, 2, 0)
    calls: list[str] = []

    async def run_case(
        mode: str, question: str, settings: CodexSettings, directory: Path
    ) -> dict[str, Any]:
        calls.append(mode)
        if mode == "plain":
            return {
                "mode": mode,
                "question": question,
                "status": "error",
                "error_code": "timeout",
                "error_message": "制限時間",
            }
        return write_success(directory, mode, question)

    monkeypatch.setattr(pipeline, "run_case", run_case)
    with pytest.raises(RuntimeError, match="01_plain"):
        asyncio.run(pipeline.run_cases(tmp_path, settings, ["質問"]))
    assert calls == ["plain", "pydantic", "adk"]
    assert [
        r["status"] for r in json.loads((tmp_path / "summary.json").read_text())
    ] == ["error", "success", "success"]


def test_permission_error_stops_experiment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """起動権限の失敗では別方式から同じCodexを呼び直さない。"""
    settings = CodexSettings("test", "low", 1, 3, 2, 0)
    calls: list[str] = []

    async def run_case(
        mode: str, question: str, settings: CodexSettings, directory: Path
    ) -> dict[str, Any]:
        calls.append(mode)
        return {
            "mode": mode,
            "question": question,
            "status": "error",
            "error_code": "permission_denied",
            "error_message": "起動権限",
        }

    monkeypatch.setattr(pipeline, "run_case", run_case)
    with pytest.raises(RuntimeError, match="起動条件"):
        asyncio.run(pipeline.run_cases(tmp_path, settings, ["質問"]))
    assert calls == ["plain"]


def test_failed_case_saves_attempts_and_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """再試行が尽きたケースに失敗理由・試行数・途中イベントを保存する。"""

    def execute(self: CodexBackend, prompt: str) -> None:
        raise CodexFailure("timeout", {"last_event": "turn.started"})

    monkeypatch.setattr(CodexBackend, "_execute", execute)
    directory = tmp_path / "case"
    record = asyncio.run(
        pipeline.run_case(
            "plain", "質問", CodexSettings("test", "low", 1, 3, 2, 0), directory
        )
    )
    assert record["status"] == "error"
    assert record["error_code"] == "timeout"
    assert record["requests"] == 1 and record["cli_attempts"] == 2
    requests = json.loads((directory / "model_requests.json").read_text())
    assert len(requests[0]["attempts"]) == 2
    assert requests[0]["diagnostics"]["last_event"] == "turn.started"
    assert not (directory / "answer.md").exists()


def test_resume_requires_same_model_and_questions(tmp_path: Path) -> None:
    """モデルや質問の異なる結果を同じ実験として混ぜない。"""
    settings = CodexSettings("test", "low", 1, 3, 2, 0)
    directory = tmp_path / "20260101-000000"
    directory.mkdir()
    write_json(
        directory / "settings.json",
        {**asdict(settings), "questions": ["質問"], "codex_version": "test-cli"},
    )
    assert (
        prepare_run(tmp_path, directory.name, settings, ["質問"], "test-cli")
        == directory
    )
    for changed, questions in [
        (replace(settings, model="different"), ["質問"]),
        (settings, ["別の質問"]),
    ]:
        with pytest.raises(ValueError, match="一致しません"):
            prepare_run(tmp_path, directory.name, changed, questions, "test-cli")


def test_lock_is_exclusive_and_released_on_error(tmp_path: Path) -> None:
    """同時実行を拒否し、異常終了時にも所有するロックを解放する。"""
    with pytest.raises(ValueError):
        with run_lock(tmp_path):
            with pytest.raises(RuntimeError, match="ロック"):
                with run_lock(tmp_path):
                    pytest.fail("同じ実験のロックを取得できてはいけません。")
            assert (tmp_path / ".run.lock").is_file()
            raise ValueError("停止")
    assert not (tmp_path / ".run.lock").exists()


def test_missing_success_artifact_is_not_silently_rerun(tmp_path: Path) -> None:
    """成功と記録された回答の欠損を検知し、自動上書きを避ける。"""
    directory = tmp_path / "case"
    write_success(directory, "plain", "質問")
    (directory / "answer.md").unlink()
    with pytest.raises(ValueError, match="不足"):
        saved_success(directory, "plain", "質問")


def test_incomplete_case_is_archived(tmp_path: Path) -> None:
    """途中で停止してresult.jsonがないケースも履歴に残す。"""
    directory = tmp_path / "01_plain"
    directory.mkdir()
    (directory / "partial.json").write_text("{}")
    archive_case(directory)
    assert not directory.exists()
    assert (tmp_path / "history/01_plain/001/partial.json").read_text() == "{}"
