"""比較実験の保存先・実行ロック・成功結果・失敗履歴を管理する。"""

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from src.config.settings import CodexSettings
from src.utils.artifacts import write_json


def prepare_run(
    output_root: Path,
    run_name: str,
    settings: CodexSettings,
    questions: list[str],
    version: str,
) -> Path:
    """新規の保存先か、同じ比較条件の再開対象を返す。"""
    if run_name == "new":
        directory = output_root / datetime.now(ZoneInfo("Asia/Tokyo")).strftime(
            "%Y%m%d-%H%M%S"
        )
        directory.mkdir(parents=True)
        write_json(
            directory / "settings.json",
            {**asdict(settings), "codex_version": version, "questions": questions},
        )
        return directory
    if not re.fullmatch(r"\d{8}-\d{6}", run_name):
        raise ValueError(
            "AIE_RUN_DIRECTORYはnewまたは実行フォルダ名を指定してください。"
        )
    directory = output_root / run_name
    if directory.is_symlink():
        raise ValueError("再開対象には通常のディレクトリが必要です。")
    saved = json.loads((directory / "settings.json").read_text(encoding="utf-8"))
    expected = {
        "model": settings.model,
        "reasoning_effort": settings.reasoning_effort,
        "max_requests": settings.max_requests,
        "codex_version": version,
        "questions": questions,
    }
    if any(saved.get(key) != value for key, value in expected.items()):
        raise ValueError(
            "再開対象とモデル・推論設定・要求上限・CLIの版・質問が一致しません。"
        )
    return directory


@contextmanager
def run_lock(directory: Path) -> Iterator[None]:
    """同じ実験フォルダへの同時実行を拒否する。"""
    path = directory / ".run.lock"
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write("running\n")
    except FileExistsError:
        raise RuntimeError(
            "実行フォルダにロックがあります。同じ実験の重複起動を確認してください。"
        ) from None
    try:
        yield
    finally:
        path.unlink()


def saved_success(directory: Path, mode: str, question: str) -> dict[str, Any] | None:
    """条件と必要な成果物がそろう成功結果だけを返す。"""
    if directory.is_symlink():
        raise ValueError("ケースの保存先には通常のディレクトリが必要です。")
    path = directory / "result.json"
    if not path.is_file():
        return None
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("mode") != mode or record.get("question") != question:
        raise ValueError("保存済みのケースと質問・方式が一致しません。")
    if record.get("status") != "success":
        return None
    required = ("answer.md", "trace.json", "messages.json", "model_requests.json")
    if any(not (directory / name).is_file() for name in required):
        raise ValueError(f"成功結果のファイルが不足しています: {directory}")
    if not (directory / "answer.md").read_text(encoding="utf-8").strip():
        raise ValueError("成功結果の回答が空です。")
    requests = json.loads(
        (directory / "model_requests.json").read_text(encoding="utf-8")
    )
    if len(requests) != record.get("requests") or any(
        row.get("status") != "success" for row in requests
    ):
        raise ValueError("成功結果とモデル要求の記録が一致しません。")
    return record


def archive_case(directory: Path) -> None:
    """失敗または中断したケースを連番の履歴として保持する。"""
    if not directory.exists():
        return
    if directory.is_symlink():
        raise ValueError("ケースの保存先には通常のディレクトリが必要です。")
    history = directory.parent / "history" / directory.name
    history.mkdir(parents=True, exist_ok=True)
    number = 1
    while (history / f"{number:03}").exists():
        number += 1
    directory.rename(history / f"{number:03}")
