"""非同期のCLIからAPIを使わずツール一覧を取得できることを検証する。"""

import asyncio

import pytest

from src.main import print_tools


def test_tools_can_be_listed_inside_event_loop(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLIと同じイベントループ内で3ツールの説明を表示する。"""
    asyncio.run(print_tools())
    output = capsys.readouterr().out
    for name in ("search_glossary", "get_lecture_summary", "make_quiz"):
        assert f"[{name}]" in output
