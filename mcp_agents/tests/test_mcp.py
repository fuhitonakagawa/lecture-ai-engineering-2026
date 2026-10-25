"""stdio・HTTPと説明文の違いを、実際のMCP接続で検証する。"""

import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart

from src.client import ServerSpec, build_agent, inspect_tools, make_client
from src.config.settings import PROJECT_ROOT
from src.utils.artifacts import write_json


def test_direct_and_stdio_expose_same_three_tools() -> None:
    """直接登録とMCPではサーバー名の接頭辞が異なる。"""

    async def inspect() -> None:
        direct = await inspect_tools(
            build_agent("direct", PROJECT_ROOT / "config/stdio.json", "brief", 15)
        )
        remote = await inspect_tools(
            build_agent("mcp", PROJECT_ROOT / "config/stdio.json", "brief", 15)
        )
        names = {"search_glossary", "get_lecture_summary", "make_quiz"}
        assert {tool["name"] for tool in direct} == names
        assert {tool["name"] for tool in remote} == {
            f"glossary_{name}" for name in names
        }

    asyncio.run(inspect())


def test_description_changes_without_changing_parameter_types() -> None:
    """説明文の比較は公開名・引数名・型を固定する。"""

    async def inspect() -> None:
        brief = await inspect_tools(
            build_agent("mcp", PROJECT_ROOT / "config/stdio.json", "brief", 15)
        )
        detailed = await inspect_tools(
            build_agent(
                "mcp", PROJECT_ROOT / "config/stdio_detailed.json", "detailed", 15
            )
        )
        before = next(
            tool for tool in brief if tool["name"] == "glossary_get_lecture_summary"
        )
        after = next(
            tool for tool in detailed if tool["name"] == "glossary_get_lecture_summary"
        )
        assert before["description"] != after["description"]
        assert "第2回では何を学びますか" in after["description"]
        assert before["parameters"] == after["parameters"]

    asyncio.run(inspect())


def test_http_and_stdio_return_identical_summary(http_url: str) -> None:
    """両トランスポートが同じ講義要約とツール定義を返す。"""

    async def inspect() -> None:
        stdio_spec = ServerSpec(
            command="uv",
            args=["run", "python", "-m", "src.server"],
            env={"MCP_TRANSPORT": "stdio", "MCP_DESCRIPTION_STYLE": "brief"},
        )
        async with (
            make_client(stdio_spec, 15) as stdio,
            make_client(ServerSpec(url=http_url), 15) as http,
        ):
            stdio_tools = await stdio.list_tools()
            http_tools = await http.list_tools()
            assert [tool.model_dump() for tool in stdio_tools] == [
                tool.model_dump() for tool in http_tools
            ]
            stdio_result = await stdio.call_tool("get_lecture_summary", {"lecture": 2})
            http_result = await http.call_tool("get_lecture_summary", {"lecture": 2})
            assert stdio_result.content == http_result.content
            assert "LoRA" in str(http_result.content)

    asyncio.run(inspect())


@pytest.mark.parametrize("transport", ["stdio", "http"])
def test_model_receives_mcp_tool_return(
    transport: str, http_url: str, tmp_path: Path
) -> None:
    """モデルが選んだ関数がMCPで実行され、次の入力へ結果が戻る。"""
    config_path = PROJECT_ROOT / "config/stdio.json"
    if transport == "http":
        config_path = tmp_path / "http.json"
        write_json(config_path, {"mcpServers": {"glossary": {"url": http_url}}})
    calls = 0

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        assert "glossary_get_lecture_summary" in {
            tool.name for tool in info.function_tools
        }
        if calls == 1:
            return ModelResponse(
                parts=[ToolCallPart("glossary_get_lecture_summary", {"lecture": 2})]
            )
        returns = [
            part
            for message in messages
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        assert "LoRA" in str(returns[-1].content)
        return ModelResponse(parts=[TextPart("第2回はLoRAを学びます。")])

    target = build_agent("mcp", config_path, "brief", 15)
    result = target.run_sync("第2回では何を学びますか？", model=FunctionModel(model))
    assert "LoRA" in result.output and calls == 2


def test_fetch_is_added_only_by_configuration() -> None:
    """fetch用の設定を選ぶと、既製サーバーのツールがモデルへ届く。"""
    definitions = asyncio.run(
        inspect_tools(
            build_agent("mcp", PROJECT_ROOT / "config/fetch.json", "brief", 15)
        )
    )
    assert {tool["name"] for tool in definitions} == {
        "glossary_search_glossary",
        "glossary_get_lecture_summary",
        "glossary_make_quiz",
        "fetch_fetch",
    }


@pytest.mark.parametrize(
    "spec",
    [
        {},
        {"command": "uv", "url": "http://localhost/mcp"},
        {"url": "file:///tmp/server"},
    ],
)
def test_invalid_connection_settings_are_rejected(spec: dict[str, str]) -> None:
    """曖昧な接続方法やHTTP以外のURLを起動前に拒否する。"""
    with pytest.raises(ValidationError):
        ServerSpec.model_validate(spec)
