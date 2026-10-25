"""Codexの失敗理由と、秘密情報を含めない診断情報。"""

import json
from typing import Any

MESSAGES = {
    "timeout": "Codexの応答が制限時間を超えました。",
    "network_error": "Codexとの通信が切断されました。",
    "rate_limit": "Codexの利用制限に達しました。",
    "permission_denied": "この実行環境ではCodexを起動する権限がありません。",
    "authentication_error": "Codexのログイン状態を確認してください。",
    "cli_missing": "Codex CLIが見つかりません。",
    "process_failed": "Codex CLIが異常終了しました。",
    "invalid_response": "Codexの応答形式を検証できませんでした。",
    "external_tools_used": "Codexがアプリ外のツールを使用したため評価を停止します。",
    "request_limit": "LLM呼び出し回数の上限に達しました。",
}
RETRYABLE_CODES = {"timeout", "network_error", "rate_limit"}
FATAL_CODES = {"permission_denied", "authentication_error", "cli_missing"}


class CodexFailure(RuntimeError):
    """固定のエラー分類と安全な診断情報を持つ例外。"""

    def __init__(self, code: str, diagnostics: dict[str, Any]) -> None:
        """分類コードと診断情報から例外を作る。"""
        self.code = code
        self.diagnostics = diagnostics
        super().__init__(MESSAGES[code])


def decode_output(output: str | bytes | None) -> str:
    """CLIの途中出力をUTF-8の文字列として返す。"""
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output or ""


def output_diagnostics(output: str | bytes | None) -> dict[str, Any]:
    """本文を含めず、最後に観測したイベントの種類を返す。"""
    text = decode_output(output)
    diagnostics: dict[str, Any] = {"output_bytes": len(text.encode("utf-8"))}
    events = {
        "thread.started",
        "turn.started",
        "turn.completed",
        "turn.failed",
        "error",
        "item.started",
        "item.updated",
        "item.completed",
    }
    items = {
        "agent_message",
        "reasoning",
        "plan",
        "command_execution",
        "file_change",
        "mcp_tool_call",
        "web_search",
    }
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        if isinstance(event_type, str) and event_type in events:
            diagnostics["last_event"] = event_type
        item = event.get("item")
        if (
            isinstance(item, dict)
            and isinstance(item.get("type"), str)
            and item["type"] in items
        ):
            diagnostics["last_item_type"] = item["type"]
    return diagnostics


def classify_process_error(stderr: str) -> str:
    """標準エラーの内容を、本文を露出しない固定分類に変換する。"""
    lowered = stderr.lower()
    for code, markers in (
        ("permission_denied", ("operation not permitted", "permission denied")),
        (
            "authentication_error",
            ("unauthorized", "not authenticated", "not logged in", "401"),
        ),
        ("rate_limit", ("rate limit", "429")),
        (
            "network_error",
            (
                "stream disconnected",
                "connection reset",
                "connection closed",
                "error sending request",
                "connection timed out",
            ),
        ),
    ):
        if any(marker in lowered for marker in markers):
            return code
    return "process_failed"
