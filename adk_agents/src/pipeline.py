"""素の応答・Pydantic AI・Google ADKの結果とツール実行履歴を保存する。"""

import json
import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from google.adk.agents.run_config import RunConfig
from google.adk.runners import InMemoryRunner
from google.genai import types
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessagesTypeAdapter, ToolCallPart, ToolReturnPart
from pydantic_ai.usage import UsageLimits

from src.adk_agent import build_adk_agent
from src.agent import agent
from src.codex_backend import (
    CodexBackend,
    CodexSettings,
)
from src.config.settings import PROJECT_ROOT, required_setting
from src.utils.artifacts import write_json
from src.utils.codex_cli import codex_version
from src.codex_model import build_codex_model

logger = logging.getLogger(__name__)


def questions_from_env() -> list[str]:
    """必須環境変数のJSON配列から比較対象の質問を取得する。"""
    questions = json.loads(required_setting("AIE_EXPERIMENT_QUESTIONS"))
    if (
        not isinstance(questions, list)
        or not questions
        or any(
            not isinstance(question, str) or not question.strip()
            for question in questions
        )
    ):
        raise ValueError(
            "AIE_EXPERIMENT_QUESTIONSには空でない質問のJSON配列を指定してください。"
        )
    return questions


async def run_pydantic(
    question: str, backend: CodexBackend, *, plain: bool
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """Pydantic AIを実行し、回答・トレース・会話履歴を返す。"""
    target = (
        Agent(
            instructions="日本語で質問に回答してください。分からないことは推測で断定しないでください。"
        )
        if plain
        else agent
    )
    result = await target.run(
        question,
        model=build_codex_model(backend),
        usage_limits=UsageLimits(request_limit=backend.settings.max_requests),
    )
    trace = []
    for message in result.all_messages():
        for part in message.parts:
            if isinstance(part, ToolCallPart):
                trace.append(
                    {
                        "kind": "call",
                        "name": part.tool_name,
                        "args": part.args_as_dict(),
                    }
                )
            elif isinstance(part, ToolReturnPart):
                trace.append(
                    {"kind": "return", "name": part.tool_name, "content": part.content}
                )
    messages = ModelMessagesTypeAdapter.dump_python(result.all_messages(), mode="json")
    return result.output, trace, messages


async def run_adk(
    question: str, backend: CodexBackend
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """ADKのセッションを作り、回答・ツール履歴・イベントを返す。"""
    runner = InMemoryRunner(
        agent=build_adk_agent(backend.settings, backend), app_name="study_agent"
    )
    session = await runner.session_service.create_session(
        app_name="study_agent", user_id="learner"
    )
    answer = ""
    trace = []
    events = []
    try:
        async for event in runner.run_async(
            user_id="learner",
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text=question)]),
            run_config=RunConfig(max_llm_calls=backend.settings.max_requests),
        ):
            events.append(event.model_dump(mode="json", exclude_none=True))
            for call in event.get_function_calls():
                trace.append({"kind": "call", "name": call.name, "args": call.args})
            for response in event.get_function_responses():
                trace.append(
                    {
                        "kind": "return",
                        "name": response.name,
                        "content": response.response,
                    }
                )
            if event.is_final_response() and event.content:
                answer = "\n".join(
                    part.text or "" for part in event.content.parts or []
                )
    finally:
        await runner.close()
    if not answer.strip():
        raise RuntimeError("ADKから最終回答を取得できませんでした。")
    return answer, trace, events


async def run_case(
    mode: str, question: str, settings: CodexSettings, directory: Path
) -> dict[str, Any]:
    """1つの質問について実測結果と成否を保存する。"""
    directory.mkdir()
    backend = CodexBackend(settings)
    started = perf_counter()
    record: dict[str, Any] = {"mode": mode, "question": question, "status": "error"}
    try:
        if mode == "adk":
            answer, trace, events = await run_adk(question, backend)
        else:
            answer, trace, events = await run_pydantic(
                question, backend, plain=mode == "plain"
            )
        (directory / "answer.md").write_text(answer + "\n", encoding="utf-8")
        write_json(directory / "trace.json", trace)
        write_json(directory / "messages.json", events)
        record.update(
            status="success",
            tool_calls=[event for event in trace if event["kind"] == "call"],
        )
    except (RuntimeError, ValueError, OSError) as error:
        record["error_type"] = type(error).__name__
        structlog.wrap_logger(logger).error("experiment_failed", status="error")
    record.update(
        requests=len(backend.records),
        elapsed_seconds=round(perf_counter() - started, 3),
    )
    write_json(directory / "model_requests.json", backend.records)
    write_json(directory / "result.json", record)
    return record


async def run_experiment(settings: CodexSettings, questions: list[str]) -> Path:
    """全質問を独立した会話で実行し、同条件の比較結果を保存する。"""
    directory = (
        PROJECT_ROOT
        / "output"
        / datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y%m%d-%H%M%S")
    )
    directory.mkdir(parents=True)
    write_json(
        directory / "settings.json",
        {
            **asdict(settings),
            "codex_version": codex_version("codex", settings.timeout_seconds),
            "questions": questions,
        },
    )
    records = []
    for index, question in enumerate(questions, start=1):
        for mode in ("plain", "pydantic", "adk"):
            print(f"[{mode}] {index}/{len(questions)}: {question}", flush=True)
            case_dir = directory / f"{index:02}_{mode}"
            record = await run_case(mode, question, settings, case_dir)
            record["directory"] = case_dir.name
            records.append(record)
            write_json(directory / "summary.json", records)
            if record["status"] != "success":
                raise RuntimeError(f"実行に失敗しました。記録: {case_dir}")
    structlog.wrap_logger(logger).info("experiment_finished", status="success")
    return directory
