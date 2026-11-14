"""環境設定に従って、ツール一覧・単独質問・基礎課題の比較を実行する。"""

import asyncio
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from src.config.logging import configure_logging
from src.config.settings import (
    PROJECT_ROOT,
    load_codex_settings,
    positive_setting,
    required_setting,
)
from src.pipeline import ExperimentCase, run_case, run_comparison, save_inspection
from src.quiz_validation import verify_quiz


async def run() -> None:
    """モデルAPIが必要な操作だけでCodexの必須設定を読み込む。"""
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    configure_logging()
    action = required_setting("MCP_ACTION")
    timeout = positive_setting("MCP_TIMEOUT_SECONDS")
    if action == "verify_quiz":
        directory = await verify_quiz(timeout)
    elif action == "compare":
        directory = await run_comparison(
            load_codex_settings(), timeout, required_setting("AIE_RUN_DIRECTORY")
        )
    else:
        mode = required_setting("MCP_CLIENT_MODE")
        style = required_setting("MCP_DESCRIPTION_STYLE")
        config_name = required_setting("MCP_CONFIG_FILE")
        config_path = PROJECT_ROOT / config_name
        if action == "inspect":
            directory = await save_inspection(mode, config_path, style, timeout)
        elif action == "ask":
            case = ExperimentCase(
                "single", mode, config_name, style, required_setting("MCP_QUESTION")
            )
            directory = (
                PROJECT_ROOT
                / "output"
                / datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y%m%d-%H%M%S")
            )
            directory.mkdir(parents=True)
            record = await run_case(
                case, load_codex_settings(), timeout, directory / "single"
            )
            if record["status"] != "success":
                raise RuntimeError(
                    f"{record['error_code']}: {record['error_message']} 記録: {directory}"
                )
            print((directory / "single/answer.md").read_text(encoding="utf-8"))
            print(
                f"requests={record['requests']} cli_attempts={record['cli_attempts']}"
            )
        else:
            raise ValueError(
                "MCP_ACTIONはinspect・ask・compare・verify_quizを指定してください。"
            )
    print("出力先:", directory)


def main() -> None:
    """引数を使わず、専用.envの設定でエントリポイントを起動する。"""
    if len(sys.argv) > 1:
        raise SystemExit("動作は.envのMCP_ACTIONなどで指定してください。")
    asyncio.run(run())


if __name__ == "__main__":
    main()
