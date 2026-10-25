"""親リポジトリのコード・データがなくても動作することを検証する。"""

import os
from pathlib import Path
import shutil
import subprocess
import sys

from src.config.settings import PROJECT_ROOT


def test_project_can_load_without_parent_repository(tmp_path: Path) -> None:
    """独立したフォルダからツールと両フレームワークを読み込める。"""
    for name in ("src", "data"):
        shutil.copytree(
            PROJECT_ROOT / name,
            tmp_path / name,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    script = """
import importlib.util
import json
from src.agent import agent
from src.adk_agent import build_adk_agent
from src.config.settings import CodexSettings
from src.tools import make_quiz

assert importlib.util.find_spec("app") is None
assert json.loads(make_quiz(2))["lecture"] == 2
assert len(build_adk_agent(CodexSettings("test", "low", 10, 2)).tools) == 3
assert agent is not None
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
