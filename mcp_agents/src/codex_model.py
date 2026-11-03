"""Pydantic AIのモデル要求をCodex CLIに接続する。"""

from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelResponse,
    ModelResponsePart,
    TextPart,
    ToolCallPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

from src.codex_backend import CodexBackend


def build_codex_model(backend: CodexBackend) -> FunctionModel:
    """Codexを使う非ストリーミングのPydantic AIモデルを返す。"""

    async def request(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        """履歴・指示・関数定義を渡し、フレームワークの応答へ変換する。"""
        decision, usage = await backend.complete(
            info.instructions or "",
            ModelMessagesTypeAdapter.dump_python(messages, mode="json"),
            [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters_json_schema,
                }
                for tool in info.function_tools
            ],
        )
        parts: list[ModelResponsePart] = [
            ToolCallPart(call.name, call.arguments) for call in decision.tool_calls
        ]
        if decision.answer:
            parts.append(TextPart(decision.answer))
        return ModelResponse(
            parts=parts,
            usage=RequestUsage(
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                cache_read_tokens=usage.get("cached_input_tokens", 0),
            ),
            model_name=backend.settings.model,
        )

    return FunctionModel(function=request, model_name=backend.settings.model)
