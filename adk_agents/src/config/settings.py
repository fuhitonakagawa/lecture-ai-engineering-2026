"""このプロジェクトの.envと環境変数から必須設定を取得する。"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class CodexSettings:
    """全フレームワークで共有するモデルと呼び出し制限。"""

    model: str
    reasoning_effort: str
    timeout_seconds: int
    max_requests: int
    max_attempts: int
    retry_delay_seconds: int


def required_setting(name: str) -> str:
    """指定した環境変数の空でない文字列を返す。

    Args:
        name: 必須環境変数の名前。

    Returns:
        前後の空白を除いた設定値。

    Raises:
        ValueError: 値が未設定または空の場合。
    """
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise ValueError(f"必須環境変数 {name} を設定してください。")
    return value.strip()


def positive_setting(name: str) -> int:
    """必須環境変数から正の整数を取得する。"""
    try:
        value = int(required_setting(name))
    except ValueError:
        raise ValueError(f"{name} は正の整数で指定してください。") from None
    if value < 1:
        raise ValueError(f"{name} は正の整数で指定してください。")
    return value


def load_codex_settings() -> CodexSettings:
    """プロジェクト直下の.envと環境変数からCodex設定を取得する。"""
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    return CodexSettings(
        model=required_setting("AIE_CODEX_MODEL"),
        reasoning_effort=required_setting("AIE_CODEX_REASONING_EFFORT"),
        timeout_seconds=positive_setting("AIE_CODEX_TIMEOUT_SECONDS"),
        max_requests=positive_setting("AIE_CODEX_MAX_REQUESTS"),
        max_attempts=positive_setting("AIE_CODEX_MAX_ATTEMPTS"),
        retry_delay_seconds=positive_setting("AIE_CODEX_RETRY_DELAY_SECONDS"),
    )
