"""実装課題の正常入力・範囲外・型エラーを実MCPとテスト用モデルで観測する。"""

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from src.client import build_agent, make_client, read_configuration
from src.config.settings import PROJECT_ROOT
from src.pipeline import extract_trace
from src.utils.artifacts import write_json


async def protocol_cases(timeout: int) -> list[dict[str, Any]]:
    """同じMCP接続で正常・未収録・型不正の返却を取得する。"""
    configuration = read_configuration(PROJECT_ROOT / "config/stdio.json")
    cases = [
        ("valid", 2),
        ("missing", 99),
        ("zero", 0),
        ("negative", -1),
        ("invalid_type", "invalid"),
        ("boolean", True),
    ]
    records = []
    async with make_client(configuration.mcp_servers["glossary"], timeout) as client:
        for label, lecture in cases:
            result = await client.call_tool(
                "make_quiz", {"lecture": lecture}, raise_on_error=False
            )
            records.append(
                {
                    "case": label,
                    "arguments": {"lecture": lecture},
                    "is_error": result.is_error,
                    "content": [
                        part.model_dump(mode="json") for part in result.content
                    ],
                }
            )
    return records


async def model_cases(timeout: int) -> list[dict[str, Any]]:
    """テスト用モデルに届く通常の返り値と再試行メッセージを取得する。"""
    records = []
    for lecture in (2, 99):
        agent = build_agent("mcp", PROJECT_ROOT / "config/stdio.json", "brief", timeout)
        result = await agent.run(
            f"第{lecture}回の確認問題",
            model=TestModel(call_tools=["glossary_make_quiz"], seed=lecture),
        )
        records.append(
            {
                "case": f"lecture_{lecture}",
                "model": "TestModel",
                "real_llm_used": False,
                "trace": extract_trace(result.all_messages()),
            }
        )

    def invalid_input_model(
        messages: list[ModelMessage], info: AgentInfo
    ) -> ModelResponse:
        """型エラーの返り値を確認するため、最初の引数だけを制御する。"""
        if "glossary_make_quiz" not in {tool.name for tool in info.function_tools}:
            raise ValueError("確認問題ツールが公開されていません。")
        errors = [
            part
            for message in messages
            for part in message.parts
            if isinstance(part, RetryPromptPart)
        ]
        if not errors:
            return ModelResponse(
                parts=[ToolCallPart("glossary_make_quiz", {"lecture": "invalid"})]
            )
        return ModelResponse(
            parts=[TextPart("型エラーのモデル向けメッセージを受信しました。")]
        )

    agent = build_agent("mcp", PROJECT_ROOT / "config/stdio.json", "brief", timeout)
    result = await agent.run("不正な型の確認", model=FunctionModel(invalid_input_model))
    records.append(
        {
            "case": "invalid_type",
            "model": "FunctionModel",
            "real_llm_used": False,
            "trace": extract_trace(result.all_messages()),
        }
    )
    return records


async def verify_quiz(timeout: int) -> Path:
    """モデルAPIを使わず実際のMCP観測結果を保存する。"""
    protocol = await protocol_cases(timeout)
    model = await model_cases(timeout)
    directory = (
        PROJECT_ROOT
        / "output"
        / datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y%m%d-%H%M%S")
    )
    directory.mkdir(parents=True)
    write_json(
        directory / "quiz_validation.json",
        {
            "kind": "mcp_quiz_validation",
            "real_llm_used": False,
            "transport": "stdio",
            "protocol_cases": protocol,
            "model_cases": model,
        },
    )
    return directory
