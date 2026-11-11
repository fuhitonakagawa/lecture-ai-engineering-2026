"""Codex CLIの出力形式と使用量を検証する。"""

import json
import subprocess
from dataclasses import dataclass

EXEC_OPTIONS = (
    "exec",
    "--ignore-user-config",
    "--ephemeral",
    "--skip-git-repo-check",
    "--sandbox",
    "read-only",
    "--color",
    "never",
    "--json",
    "-c",
    'web_search="disabled"',
    "-c",
    "project_doc_max_bytes=0",
)


@dataclass(frozen=True)
class ParsedResponse:
    """回答本文、使用量、比較条件に関する観測結果。"""

    answer: str
    usage: dict[str, int]
    tool_types: tuple[str, ...]


def codex_version(executable: str, timeout_seconds: int) -> str:
    """比較に使う Codex CLI のバージョンを取得する。

    Args:
        executable: Codex CLI の実行ファイル。
        timeout_seconds: 待機時間の上限。

    Returns:
        CLI が表示するバージョン文字列。

    Raises:
        RuntimeError: CLI の起動またはバージョン取得に失敗した場合。
    """
    try:
        result = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout_seconds,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError("Codex CLI のバージョンを取得できませんでした。") from None
    return result.stdout.strip()


def extract_usage(raw_usage: object) -> dict[str, int]:
    """使用量から既知のトークン数だけを返す。

    Args:
        raw_usage: Codex の試行終了イベントの使用量。

    Returns:
        非負の整数として得られたトークン数。
    """
    if not isinstance(raw_usage, dict):
        return {}
    return {
        key: raw_usage[key]
        for key in (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_output_tokens",
        )
        if type(raw_usage.get(key)) is int and raw_usage[key] >= 0
    }


def parse_response(output: str) -> ParsedResponse:
    """Codex の JSONL から回答と使用量を取得する。

    Args:
        output: codex exec --json の標準出力。

    Returns:
        最後の回答本文、使用量、外部ツールの種類。

    Raises:
        ValueError: 出力が不正、試行が失敗、または回答がない場合。
    """
    answer = ""
    usage: dict[str, int] = {}
    tool_types: set[str] = set()
    completed = False
    for line in output.splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if not isinstance(event, dict):
            raise ValueError("Codex の出力形式が不正です。")
        event_type = event.get("type")
        if event_type in {"error", "turn.failed"}:
            raise ValueError("Codex が試行の失敗を報告しました。")
        if event_type == "turn.completed":
            completed = True
            usage = extract_usage(event.get("usage"))
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if isinstance(item_type, str) and item_type not in {
            "agent_message",
            "reasoning",
            "plan",
        }:
            tool_types.add(item_type)
        if event_type == "item.completed" and item_type == "agent_message":
            text = item.get("text")
            if isinstance(text, str):
                answer = text
    if not completed or not answer.strip():
        raise ValueError("Codex の回答または試行終了イベントがありません。")
    return ParsedResponse(answer, usage, tuple(sorted(tool_types)))
