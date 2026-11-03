"""学習支援エージェントの仕様を検証する。"""

from pydantic_ai.messages import ToolCallPart
from pydantic_ai.models.test import TestModel
from src.agent import agent


def test_quiz_is_registered_for_test_model() -> None:
    """APIなしでも確認問題ツールがモデルに公開される。"""
    model = TestModel(call_tools=["make_quiz"])
    result = agent.run_sync("第2回の確認問題", model=model)
    calls = [
        part
        for message in result.all_messages()
        for part in message.parts
        if isinstance(part, ToolCallPart)
    ]
    assert [call.tool_name for call in calls] == ["make_quiz"]
