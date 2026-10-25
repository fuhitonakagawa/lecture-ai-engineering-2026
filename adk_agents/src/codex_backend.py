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
            raise RuntimeError("LLM呼び出し回数の上限に達しました。")
        payload = {"instructions": instructions, "messages": messages, "tools": tools}
        prompt = PROTOCOL + "\n" + json.dumps(payload, ensure_ascii=False)
        started = perf_counter()
        record: dict[str, Any] = {"request": len(self.records) + 1, "status": "error"}
        self.records.append(record)
        response = await asyncio.to_thread(self._execute, prompt)
        if response.tool_types:
            raise RuntimeError(
                "Codexがアプリ外のツールを使用したため評価を停止します。"
            )
        decision = ModelDecision.model_validate_json(response.answer)
        allowed = {tool["name"] for tool in tools}
        for call in decision.tool_calls:
            if call.name not in allowed:
                raise ValueError(f"未登録のツールです: {call.name}")
            if not isinstance(json.loads(call.arguments), dict):
                raise ValueError("ツール引数はJSONオブジェクトが必要です。")
        record.update(
            status="success",
            elapsed_seconds=round(perf_counter() - started, 3),
            usage=response.usage,
            decision=decision.model_dump(),
        )
        structlog.wrap_logger(logger).info(
            "model_request", request=record["request"], status="success"
        )
        return decision, response.usage

    def _execute(self, prompt: str) -> ParsedResponse:
        """一時ディレクトリでCodexを実行し、JSONLの応答を検証する。"""
        executable = shutil.which("codex")
        if executable is None:
            raise RuntimeError("Codex CLIをインストールし、ログインしてください。")
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
            except subprocess.TimeoutExpired:
                raise RuntimeError("Codexの応答が制限時間を超えました。") from None
        if result.returncode:
            if (
                "Operation not permitted" in result.stderr
                or "Permission denied" in result.stderr
            ):
                raise RuntimeError(
                    "この実行環境ではCodexを起動する権限がありません。ローカルの通常のターミナルで実行してください。"
                )
            raise RuntimeError(
                "Codex CLIが失敗しました。ログインとモデル設定を確認してください。"
            )
        return parse_response(result.stdout)
