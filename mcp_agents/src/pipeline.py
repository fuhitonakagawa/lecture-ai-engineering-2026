"""MCPの接続方式と説明文を比較し、定義・回答・トレースを保存する。"""

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from zoneinfo import ZoneInfo

from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.usage import UsageLimits

from src.client import build_agent, inspect_tools
from src.codex_backend import CodexBackend
from src.codex_errors import FATAL_CODES, CodexFailure
from src.codex_model import build_codex_model
from src.config.settings import PROJECT_ROOT, CodexSettings
from src.experiment_storage import archive_case, prepare_run, run_lock, saved_success
from src.utils.artifacts import write_json
from src.utils.codex_cli import codex_version


@dataclass(frozen=True)
class ExperimentCase:
    """接続方式・説明文と、比較対象の質問。"""

    name: str
    mode: str
    config_file: str
    style: str
    question: str


def comparison_cases() -> list[ExperimentCase]:
    """共通演習と基礎課題の6ケースを返す。"""
    course_question = "AIE2026の講座の宿題にある「3レベル」とは何ですか？"
    summary_question = "第2回では何を学びますか？"
    return [
        ExperimentCase(
            "01_direct", "direct", "config/stdio.json", "brief", course_question
        ),
        ExperimentCase(
            "02_stdio", "mcp", "config/stdio.json", "brief", course_question
        ),
        ExperimentCase("03_http", "mcp", "config/http.json", "brief", course_question),
        ExperimentCase(
            "04_brief", "mcp", "config/stdio.json", "brief", summary_question
        ),
        ExperimentCase(
            "05_detailed",
            "mcp",
            "config/stdio_detailed.json",
            "detailed",
            summary_question,
        ),
        ExperimentCase(
            "06_fetch",
            "mcp",
            "config/fetch.json",
            "brief",
            "https://modelcontextprotocol.io/docs/getting-started/intro.md の冒頭を日本語で3行に要約して",
        ),
    ]


def extract_trace(messages: list[ModelMessage]) -> list[dict[str, Any]]:
    """モデルが依頼した関数名・引数と、実際の返り値を抽出する。"""
    trace = []
    for message in messages:
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
            elif isinstance(part, RetryPromptPart):
                trace.append(
                    {"kind": "retry", "name": part.tool_name, "content": part.content}
                )
    return trace


async def run_case(
    case: ExperimentCase, settings: CodexSettings, timeout: int, directory: Path
) -> dict[str, Any]:
    """接続・ツール一覧・回答を記録し、失敗を成功と混在させない。"""
    directory.mkdir()
    config_path = PROJECT_ROOT / case.config_file
    connection = (
        json.loads(config_path.read_text(encoding="utf-8"))
        if case.mode == "mcp"
        else {"connection": "direct"}
    )
    write_json(
        directory / "execution_settings.json",
        {
            **asdict(settings),
            "case": asdict(case),
            "mcp_timeout_seconds": timeout,
            "connection": connection,
        },
    )
    backend = CodexBackend(settings)
    record: dict[str, Any] = {
        "mode": case.name,
        "question": case.question,
        "status": "error",
    }
    started = perf_counter()
    try:
        agent = build_agent(case.mode, config_path, case.style, timeout)
        async with agent:
            definitions = await inspect_tools(agent)
            write_json(directory / "tools.json", definitions)
            result = await agent.run(
                case.question,
                model=build_codex_model(backend),
                usage_limits=UsageLimits(request_limit=settings.max_requests),
            )
        messages = result.all_messages()
        trace = extract_trace(messages)
        (directory / "answer.md").write_text(result.output + "\n", encoding="utf-8")
        write_json(directory / "trace.json", trace)
        write_json(
            directory / "messages.json",
            ModelMessagesTypeAdapter.dump_python(messages, mode="json"),
        )
        record.update(
            status="success",
            tool_calls=[item for item in trace if item["kind"] == "call"],
        )
    except Exception as error:
        record["error_type"] = type(error).__name__
        if isinstance(error, CodexFailure):
            record.update(
                error_code=error.code,
                error_message=str(error),
                diagnostics=error.diagnostics,
            )
        else:
            record.update(
                error_code="mcp_or_execution_error",
                error_message="接続・ツール実行・回答処理のいずれかに失敗しました。設定とサーバーの起動状態を確認してください。",
            )
    record.update(
        requests=len(backend.records),
        cli_attempts=sum(len(row["attempts"]) for row in backend.records),
        elapsed_seconds=round(perf_counter() - started, 3),
    )
    write_json(directory / "model_requests.json", backend.records)
    write_json(directory / "result.json", record)
    return record


async def run_comparison(settings: CodexSettings, timeout: int, run_name: str) -> Path:
    """モデル・接続設定・質問を固定した比較実験を新規実行または再開する。"""
    cases = comparison_cases()
    plan = [
        {
            **asdict(case),
            "connection": json.loads(
                (PROJECT_ROOT / case.config_file).read_text(encoding="utf-8")
            ),
        }
        for case in cases
    ]
    directory = prepare_run(
        PROJECT_ROOT / "output",
        run_name,
        settings,
        [case.question for case in cases],
        codex_version("codex", settings.timeout_seconds),
    )
    print("実行フォルダ:", directory, flush=True)
    with run_lock(directory):
        plan_path = directory / "plan.json"
        if (
            plan_path.exists()
            and json.loads(plan_path.read_text(encoding="utf-8")) != plan
        ):
            raise ValueError(
                "比較の接続設定が保存時と異なります。新しい実験を開始してください。"
            )
        write_json(plan_path, plan)
        records = []
        for case in cases:
            case_directory = directory / case.name
            record = saved_success(case_directory, case.name, case.question)
            if record is None:
                archive_case(case_directory)
                print(f"[{case.name}] {case.question}", flush=True)
                record = await run_case(case, settings, timeout, case_directory)
            else:
                print(f"[{case.name}] 保存済みの成功結果を使用", flush=True)
            record["directory"] = case.name
            records.append(record)
            write_json(directory / "summary.json", records)
            if record.get("error_code") in FATAL_CODES:
                raise RuntimeError(
                    f"Codexの起動条件を確認してください。記録: {case_directory}"
                )
        failures = [
            record["directory"] for record in records if record["status"] != "success"
        ]
        if failures:
            raise RuntimeError(
                f"失敗したケース: {', '.join(failures)}。記録: {directory}"
            )
    return directory


async def save_inspection(
    mode: str, config_path: Path, style: str, timeout: int
) -> Path:
    """モデルAPIを使わずツール定義を取得し、ファイルに保存する。"""
    agent = build_agent(mode, config_path, style, timeout)
    definitions = await inspect_tools(agent)
    directory = (
        PROJECT_ROOT
        / "output"
        / datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y%m%d-%H%M%S")
    )
    directory.mkdir(parents=True)
    write_json(directory / "tools.json", definitions)
    write_json(
        directory / "inspection.json",
        {
            "mode": mode,
            "config_file": str(config_path.relative_to(PROJECT_ROOT)),
            "style": style,
            "tool_count": len(definitions),
        },
    )
    print(json.dumps(definitions, ensure_ascii=False, indent=2))
    return directory
