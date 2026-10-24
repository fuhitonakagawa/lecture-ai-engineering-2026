"""同じ質問へのモデル別の回答を outputs/ に保存する。

実行方法:
    uv run python -m app.codex_comparison
"""

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ANSWER_INSTRUCTIONS = (
    "外部検索、ファイル参照、コマンド実行などのツールを使わず、"
    "学習済みの知識だけで日本語で回答してください。"
)
EXEC_OPTIONS = (
    "exec",
    "--ignore-user-config",
    "--ephemeral",
    "--skip-git-repo-check",
    "--sandbox",
    "read-only",
    "--color",
    "never",
    "--json",
    "-c",
    'web_search="disabled"',
    "-c",
    "project_doc_max_bytes=0",
)


@dataclass(frozen=True)
class ComparisonSettings:
    """比較するモデル、共通の質問、試行条件。"""

    question: str
    models: tuple[str, ...]
    repeats: int
    reasoning_effort: str
    timeout_seconds: int


@dataclass(frozen=True)
class ParsedResponse:
    """回答本文、使用量、比較条件に関する観測結果。"""

    answer: str
    usage: dict[str, int]
    tool_types: tuple[str, ...]


def required_setting(name: str) -> str:
    """空でない必須環境変数を返す。

    Args:
        name: 環境変数名。

    Returns:
        設定値の前後の空白を除いた文字列。

    Raises:
        ValueError: 環境変数が未設定または空の場合。
    """
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise ValueError(f"必須環境変数 {name} を設定してください。")
    return value.strip()


def positive_setting(name: str) -> int:
    """必須環境変数から正の整数を返す。

    Args:
        name: 環境変数名。

    Returns:
        設定された正の整数。

    Raises:
        ValueError: 値が正の整数でない場合。
    """
    raw_value = required_setting(name)
    try:
        value = int(raw_value)
    except ValueError:
        raise ValueError(f"{name} は正の整数で指定してください。") from None
    if value < 1:
        raise ValueError(f"{name} は正の整数で指定してください。")
    return value


def load_settings(env_path: Path) -> ComparisonSettings:
    """.env と環境変数から比較条件を取得する。

    Args:
        env_path: 設定ファイル。既存の環境変数が優先される。

    Returns:
        全試行に適用する比較条件。

    Raises:
        ValueError: 必須設定の不足、重複モデル、不正な数値がある場合。
    """
    load_dotenv(env_path, override=False)
    models = tuple(
        model.strip()
        for model in required_setting("CODEX_COMPARE_MODELS").split(",")
    )
    if len(models) < 2 or not all(models) or len(set(models)) != len(models):
        raise ValueError(
            "CODEX_COMPARE_MODELS は異なるモデルを2つ以上指定してください。"
        )
    return ComparisonSettings(
        question=required_setting("CODEX_COMPARE_QUESTION"),
        models=models,
        repeats=positive_setting("CODEX_COMPARE_REPEATS"),
        reasoning_effort=required_setting("CODEX_COMPARE_REASONING_EFFORT"),
        timeout_seconds=positive_setting("CODEX_COMPARE_TIMEOUT_SECONDS"),
    )


def write_json(path: Path, data: object) -> None:
    """JSON ファイルを UTF-8 で保存する。

    Args:
        path: 保存先。
        data: JSON に変換可能な値。
    """
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def codex_version(executable: str, timeout_seconds: int) -> str:
    """比較に使う Codex CLI のバージョンを取得する。

    Args:
        executable: Codex CLI の実行ファイル。
        timeout_seconds: 待機時間の上限。

    Returns:
        CLI が表示するバージョン文字列。

    Raises:
        RuntimeError: CLI の起動またはバージョン取得に失敗した場合。
    """
    try:
        result = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout_seconds,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError(
            "Codex CLI のバージョンを取得できませんでした。"
        ) from None
    return result.stdout.strip()


def extract_usage(raw_usage: object) -> dict[str, int]:
    """使用量から既知のトークン数だけを返す。

    Args:
        raw_usage: Codex の試行終了イベントの使用量。

    Returns:
        非負の整数として得られたトークン数。
    """
    if not isinstance(raw_usage, dict):
        return {}
    return {
        key: raw_usage[key]
        for key in (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_output_tokens",
        )
        if type(raw_usage.get(key)) is int and raw_usage[key] >= 0
    }


def parse_response(output: str) -> ParsedResponse:
    """Codex の JSONL から回答と使用量を取得する。

    Args:
        output: codex exec --json の標準出力。

    Returns:
        最後の回答本文、使用量、外部ツールの種類。

    Raises:
        ValueError: 出力が不正、試行が失敗、または回答がない場合。
    """
    answer = ""
    usage: dict[str, int] = {}
    tool_types: set[str] = set()
    completed = False
    for line in output.splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if not isinstance(event, dict):
            raise ValueError("Codex の出力形式が不正です。")
        event_type = event.get("type")
        if event_type in {"error", "turn.failed"}:
            raise ValueError("Codex が試行の失敗を報告しました。")
        if event_type == "turn.completed":
            completed = True
            usage = extract_usage(event.get("usage"))
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if isinstance(item_type, str) and item_type not in {
            "agent_message",
            "reasoning",
            "plan",
        }:
            tool_types.add(item_type)
        if event_type == "item.completed" and item_type == "agent_message":
            text = item.get("text")
            if isinstance(text, str):
                answer = text
    if not completed or not answer.strip():
        raise ValueError("Codex の回答または試行終了イベントがありません。")
    return ParsedResponse(answer, usage, tuple(sorted(tool_types)))


def run_trial(
    command: list[str], prompt: str, timeout_seconds: int, trial_dir: Path
) -> dict[str, object]:
    """独立した試行の回答と実行結果を保存する。

    Args:
        command: Codex CLI の引数列。
        prompt: モデルへの入力全文。
        timeout_seconds: 待機時間の上限。
        trial_dir: 試行の保存先。

    Returns:
        成否、使用量、外部ツール使用の有無を含む記録。
    """
    record: dict[str, object] = {"status": "error"}
    started = perf_counter()
    try:
        with TemporaryDirectory(prefix="codex-comparison-") as work_dir:
            result = subprocess.run(
                command,
                input=prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                cwd=work_dir,
                timeout=timeout_seconds,
                check=False,
            )
        record["exit_code"] = result.returncode
        if result.returncode:
            record["error"] = "codex_exit_error"
        else:
            response = parse_response(result.stdout)
            (trial_dir / "answer.md").write_text(
                response.answer, encoding="utf-8"
            )
            record["usage"] = response.usage
            record["tool_types"] = list(response.tool_types)
            record["status"] = "excluded" if response.tool_types else "success"
            if response.tool_types:
                record["error"] = "external_tools_used"
    except subprocess.TimeoutExpired:
        record["error"] = "timeout"
    except OSError:
        record["error"] = "execution_or_storage_error"
    except (ValueError, TypeError):
        record["error"] = "invalid_response"
    record["elapsed_seconds"] = round(perf_counter() - started, 3)
    return record


def prepare_output(
    settings: ComparisonSettings, output_root: Path, version: str
) -> tuple[Path, str]:
    """実験用フォルダに質問と共通設定を保存する。

    Args:
        settings: 共通の質問と試行条件。
        output_root: 実行ごとのフォルダを配置するディレクトリ。
        version: Codex CLI のバージョン。

    Returns:
        出力先と、全試行に渡す入力全文。

    Raises:
        FileExistsError: 同じ日時の出力先が存在する場合。
    """
    output_root.mkdir(parents=True, exist_ok=True)
    directory_name = datetime.now(ZoneInfo("Asia/Tokyo")).strftime(
        "%Y%m%d-%H%M%S"
    )
    output_dir = output_root / directory_name
    output_dir.mkdir()
    prompt = f"{ANSWER_INSTRUCTIONS}\n質問：{settings.question}\n"
    (output_dir / "question.txt").write_text(
        settings.question + "\n", encoding="utf-8"
    )
    (output_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    write_json(
        output_dir / "settings.json",
        {
            "models": settings.models,
            "repeats": settings.repeats,
            "reasoning_effort": settings.reasoning_effort,
            "timeout_seconds": settings.timeout_seconds,
            "codex_version": version,
            "exec_options": EXEC_OPTIONS,
        },
    )
    return output_dir, prompt


def run_comparison(settings: ComparisonSettings, output_root: Path) -> Path:
    """全モデルの試行を実行し、共通条件と各回答を保存する。

    Args:
        settings: 共通の質問とモデル別の試行条件。
        output_root: 実行ごとのフォルダを配置するディレクトリ。

    Returns:
        質問、設定、各試行の結果を含むフォルダ。

    Raises:
        RuntimeError: Codex CLI が利用できない場合。
        OSError: 出力ファイルを保存できない場合。
    """
    executable = shutil.which("codex")
    if executable is None:
        raise RuntimeError(
            "Codex CLI が見つかりません。codex を PATH に登録してください。"
        )
    version = codex_version(executable, settings.timeout_seconds)
    output_dir, prompt = prepare_output(settings, output_root, version)
    records: list[dict[str, object]] = []
    for trial in range(1, settings.repeats + 1):
        for index, model in enumerate(settings.models, start=1):
            trial_dir = output_dir / f"model_{index:02}" / f"trial_{trial:02}"
            trial_dir.mkdir(parents=True)
            command = [
                executable,
                *EXEC_OPTIONS,
                "--model",
                model,
                "-c",
                f"model_reasoning_effort={json.dumps(settings.reasoning_effort)}",
                "-",
            ]
            write_json(
                trial_dir / "settings.json",
                {
                    "model": model,
                    "trial": trial,
                    "command": ["codex", *command[1:]],
                },
            )
            record: dict[str, object] = {"model": model, "trial": trial}
            record.update(
                run_trial(command, prompt, settings.timeout_seconds, trial_dir)
            )
            record["directory"] = str(trial_dir.relative_to(output_dir))
            write_json(trial_dir / "result.json", record)
            records.append(record)
            write_json(output_dir / "summary.json", records)
    return output_dir


def main() -> None:
    """環境設定に従って比較し、出力先と終了コードを返す。"""
    try:
        settings = load_settings(PROJECT_ROOT / ".env")
        output_dir = run_comparison(settings, PROJECT_ROOT / "outputs")
        records = json.loads(
            (output_dir / "summary.json").read_text(encoding="utf-8")
        )
    except (ValueError, RuntimeError, OSError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
    print(f"出力先: {output_dir}")
    successes = sum(record["status"] == "success" for record in records)
    print(f"比較に使える回答: {successes}/{len(records)} 件")
    if successes != len(records):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
