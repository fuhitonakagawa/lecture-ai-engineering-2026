"""Pydantic AI版と同じ指示・関数を利用するGoogle ADKエージェント。"""

import json
from collections.abc import AsyncGenerator
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import ConfigDict, Field

from src import tools
from src.agent import INSTRUCTIONS
from src.codex_backend import CodexBackend, CodexSettings


class CodexAdkModel(BaseLlm):
    """Codexの応答をADKの関数呼び出しと回答に変換するモデル。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    settings: CodexSettings
    backend: CodexBackend | None = Field(default=None, exclude=True)

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        """ADKの会話履歴と関数定義から、一括のモデル応答を返す。"""
        config = llm_request.config or types.GenerateContentConfig()
        declarations: list[dict[str, Any]] = []
        for tool in config.tools or []:
            if isinstance(tool, types.Tool):
                for function in tool.function_declarations or []:
                    declarations.append(
                        function.model_dump(mode="json", exclude_none=True)
                    )
        instruction = config.system_instruction
        if isinstance(instruction, types.Content):
            instructions = "\n".join(
                part.text or "" for part in instruction.parts or []
            )
        else:
            instructions = str(instruction or "")
        backend = self.backend or CodexBackend(self.settings)
        decision, usage = await backend.complete(
            instructions,
            [
                content.model_dump(mode="json", exclude_none=True)
                for content in llm_request.contents
            ],
            declarations,
        )
        parts = [
            types.Part(
                function_call=types.FunctionCall(
                    name=call.name, args=json.loads(call.arguments)
                )
            )
            for call in decision.tool_calls
        ]
        if decision.answer:
            parts.append(types.Part(text=decision.answer))
        yield LlmResponse(
            content=types.Content(role="model", parts=parts),
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=usage.get("input_tokens", 0),
                candidates_token_count=usage.get("output_tokens", 0),
                cached_content_token_count=usage.get("cached_input_tokens", 0),
            ),
            partial=False,
            turn_complete=not decision.tool_calls,
        )


def build_adk_agent(
    settings: CodexSettings, backend: CodexBackend | None = None
) -> LlmAgent:
    """共通の3ツールと呼び出し回数制限を持つ学習支援エージェントを返す。"""

    def limit_calls(callback_context: CallbackContext, llm_request: LlmRequest) -> None:
        """1回の質問に対するモデル呼び出し回数を制限する。"""
        if not llm_request.contents:
            raise ValueError("質問または会話履歴が必要です。")
        key = "temp:codex_requests"
        count = callback_context.state.get(key, 0) + 1
        if count > settings.max_requests:
            raise RuntimeError("LLM呼び出し回数の上限に達しました。")
        callback_context.state[key] = count

    return LlmAgent(
        name="study_agent",
        model=CodexAdkModel(model=settings.model, settings=settings, backend=backend),
        instruction=INSTRUCTIONS,
        tools=[tools.search_glossary, tools.get_lecture_summary, tools.make_quiz],
        before_model_callback=limit_calls,
    )
