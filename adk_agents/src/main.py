"""環境設定に従って比較実験または単独の質問を実行する。"""

import asyncio
import inspect
import json
import sys
from typing import Any

from pydantic_ai.models.test import TestModel

from src.agent import agent
from src.codex_backend import CodexBackend
from src.config.logging import configure_logging
from src.config.settings import load_codex_settings, required_setting
from src.pipeline import questions_from_env, run_adk, run_experiment, run_pydantic


async def print_tools() -> None:
    """APIを呼ばず、3ツールの名前・説明・引数を表示する。"""
    model = TestModel(call_tools=[])
    await agent.run("ツール一覧", model=model)
    parameters = model.last_model_request_parameters
    if parameters is None:
        raise RuntimeError("ツール定義を取得できませんでした。")
    for tool in parameters.function_tools:
        print(f"\n[{tool.name}]")
        print(inspect.cleandoc(tool.description or ""))
        print(json.dumps(tool.parameters_json_schema, ensure_ascii=False))


def print_trace(trace: list[dict[str, Any]], backend: CodexBackend) -> None:
    """関数の呼び出し・返り値とモデル要求回数を表示する。"""
    for event in trace:
        if event["kind"] == "call":
            print(f"[tool call] {event['name']}({event['args']})")
        else:
            print(f"[tool return] {event['name']} -> {event['content']}")
    print(f"[usage] requests={len(backend.records)}")


async def run() -> None:
    """選択された動作モードを必須設定から実行する。"""
    settings = load_codex_settings()
    configure_logging()
    mode = required_setting("AIE_AGENT_MODE")
    if mode == "compare":
        directory = await run_experiment(settings, questions_from_env())
        print("出力先:", directory)
        return
    if mode == "list_tools":
        await print_tools()
        return
    if mode not in {"local", "adk", "plain"}:
        raise ValueError(
            "AIE_AGENT_MODEはcompare・local・adk・plain・list_toolsのいずれかです。"
        )
    question = required_setting("AIE_QUESTION")
    trace_enabled = required_setting("AIE_TRACE").lower()
    if trace_enabled not in {"true", "false"}:
        raise ValueError("AIE_TRACEはtrueまたはfalseを指定してください。")
    backend = CodexBackend(settings)
    if mode == "adk":
        answer, trace, _ = await run_adk(question, backend)
    else:
        answer, trace, _ = await run_pydantic(question, backend, plain=mode == "plain")
    print(answer)
    if trace_enabled == "true":
        print_trace(trace, backend)


def main() -> None:
    """環境変数で指定された学習支援の処理を起動する。"""
    if len(sys.argv) > 1:
        raise SystemExit(
            "質問と動作モードは.envのAIE_から始まる項目で指定してください。"
        )
    asyncio.run(run())


if __name__ == "__main__":
    main()
