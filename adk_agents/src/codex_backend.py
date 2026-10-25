"""Codex CLIで、回答またはアプリケーションのツール呼び出しを生成する。"""

import asyncio
import json
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict, model_validator

from src.config.settings import CodexSettings
from src.codex_errors import (
    RETRYABLE_CODES,
    CodexFailure,
    classify_process_error,
    output_diagnostics,
)
from src.utils.codex_cli import EXEC_OPTIONS, ParsedResponse, parse_response

logger = logging.getLogger(__name__)
PROTOCOL = """あなたは学習支援アプリケーションのLLMです。
入力JSONのinstructionsに従い、messagesの続きとして次の応答を決めてください。
必要な情報はtoolsで提示された関数に問い合わせてください。
関数を使う場合はtool_callsに名前と引数のJSON文字列を返し、answerは空にします。
ツールの結果が十分ならtool_callsを空にして、answerに日本語の最終回答を返します。
過去のツール結果を参照し、同じ問い合わせを理由なく繰り返さないでください。
登録された関数の実行はアプリケーションが担当します。結果を捏造しないでください。
Codex自体のシェル、ファイル操作、Web検索、MCPなどの組み込みツールは使わず、
この入力だけから指定されたJSONを生成してください。
"""


class FunctionCall(BaseModel):
    """モデルが依頼する関数名とJSON形式の引数。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    name: str
    arguments: str


class ModelDecision(BaseModel):
    """関数呼び出しか最終回答のいずれかを表す応答。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    answer: str
    tool_calls: list[FunctionCall]

    @model_validator(mode="after")
    def validate_choice(self) -> "ModelDecision":
        """回答とツール呼び出しが排他的に存在することを確認する。"""
        if bool(self.answer.strip()) == bool(self.tool_calls):
            raise ValueError("回答かツール呼び出しのいずれかを指定してください。")
        return self


@dataclass
class CodexBackend:
    """1つの質問に対するCodexの呼び出しと使用量の記録。"""

    settings: CodexSettings
    records: list[dict[str, Any]] = field(default_factory=list)

    async def complete(
        self,
        instructions: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> tuple[ModelDecision, dict[str, int]]:
        """会話履歴と関数定義から次の応答を生成する。

        Args:
            instructions: 学習支援エージェントの指示。
            messages: 質問、回答、ツール結果の履歴。
            tools: 利用可能な関数名・説明・引数スキーマ。

        Returns:
            検証済みの応答とCodexが報告したトークン使用量。

        Raises:
            RuntimeError: 呼び出し上限、CLIの失敗、または制約外のツール使用。
            ValueError: 未登録関数や不正な引数を含む応答。
        """
        if len(self.records) >= self.settings.max_requests:
            raise CodexFailure("request_limit", {})
        payload = {"instructions": instructions, "messages": messages, "tools": tools}
        prompt = PROTOCOL + "\n" + json.dumps(payload, ensure_ascii=False)
        started = perf_counter()
        record: dict[str, Any] = {
            "request": len(self.records) + 1,
            "status": "error",
            "attempts": [],
        }
        self.records.append(record)
        try:
            response = await self._execute_with_retries(prompt, record)
            decision = validate_decision(response, tools)
        except CodexFailure as error:
            record.update(error_code=error.code, diagnostics=error.diagnostics)
            raise
        except ValueError:
            record["error_code"] = "invalid_response"
            raise
        finally:
            record["elapsed_seconds"] = round(perf_counter() - started, 3)
        record.update(
            status="success",
            usage=response.usage,
            decision=decision.model_dump(),
        )
        structlog.wrap_logger(logger).info(
            "model_request", request=record["request"], status="success"
        )
        return decision, response.usage

    async def _execute_with_retries(
        self, prompt: str, record: dict[str, Any]
    ) -> ParsedResponse:
        """一時的な通信失敗だけを上限回数まで再試行する。"""
        for number in range(1, self.settings.max_attempts + 1):
            attempt: dict[str, Any] = {"attempt": number, "status": "error"}
            record["attempts"].append(attempt)
            started = perf_counter()
            structlog.wrap_logger(logger).info(
                "model_request_started", request=record["request"], attempt=number
            )
            try:
                response = await asyncio.to_thread(self._execute, prompt)
                attempt.update(status="success", usage=response.usage)
                return response
            except CodexFailure as error:
                attempt.update(error_code=error.code, diagnostics=error.diagnostics)
                structlog.wrap_logger(logger).warning(
                    "model_request_failed",
                    request=record["request"],
                    attempt=number,
                    error_code=error.code,
                    **error.diagnostics,
                )
                if (
                    error.code not in RETRYABLE_CODES
                    or number == self.settings.max_attempts
                ):
                    raise
            finally:
                attempt["elapsed_seconds"] = round(perf_counter() - started, 3)
            await asyncio.sleep(self.settings.retry_delay_seconds)
        raise ValueError("max_attemptsには正の整数が必要です。")

    def _execute(self, prompt: str) -> ParsedResponse:
        """一時ディレクトリでCodexを実行し、JSONLの応答を検証する。"""
        executable = shutil.which("codex")
        if executable is None:
            raise CodexFailure("cli_missing", {})
        with TemporaryDirectory(prefix="aie-codex-") as directory:
            schema_path = Path(directory) / "response.schema.json"
            schema_path.write_text(
                json.dumps(ModelDecision.model_json_schema()), encoding="utf-8"
            )
            command = [
                executable,
                *EXEC_OPTIONS,
                "--model",
                self.settings.model,
                "-c",
                f"model_reasoning_effort={json.dumps(self.settings.reasoning_effort)}",
                "--output-schema",
                str(schema_path),
                "-",
            ]
            try:
                result = subprocess.run(
                    command,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    cwd=directory,
                    timeout=self.settings.timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as error:
                raise CodexFailure(
                    "timeout", output_diagnostics(error.output)
                ) from None
            except PermissionError:
                raise CodexFailure("permission_denied", {}) from None
        if result.returncode:
            raise CodexFailure(
                classify_process_error(result.stderr),
                {"exit_code": result.returncode, **output_diagnostics(result.stdout)},
            )
        try:
            return parse_response(result.stdout)
        except ValueError:
            raise CodexFailure(
                "invalid_response", output_diagnostics(result.stdout)
            ) from None


def validate_decision(
    response: ParsedResponse, tools: list[dict[str, Any]]
) -> ModelDecision:
    """外部ツール利用と、関数名・引数・最終回答の形式を検証する。"""
    if response.tool_types:
        raise CodexFailure(
            "external_tools_used", {"tool_types": list(response.tool_types)}
        )
    decision = ModelDecision.model_validate_json(response.answer)
    allowed = {tool["name"] for tool in tools}
    for call in decision.tool_calls:
        if call.name not in allowed:
            raise ValueError(f"未登録のツールです: {call.name}")
        if not isinstance(json.loads(call.arguments), dict):
            raise ValueError("ツール引数はJSONオブジェクトが必要です。")
    return decision
