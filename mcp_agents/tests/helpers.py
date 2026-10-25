"""外部APIを使わないモデル応答のテストデータ。"""

import json

from src.utils.codex_cli import ParsedResponse


def response(answer: str, calls: list[dict[str, str]]) -> ParsedResponse:
    """ツール選択を制御したCLI応答を返す。"""
    return ParsedResponse(
        json.dumps({"answer": answer, "tool_calls": calls}),
        {"input_tokens": 30, "output_tokens": 10},
        (),
    )
