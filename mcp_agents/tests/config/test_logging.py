"""学習支援エージェントの仕様を検証する。"""

import json

from src.config.logging import normalize_event


def test_logs_omit_unapproved_content() -> None:
    """アプリ・ライブラリの自由文や例外値をログに残さない。"""
    event = normalize_event(
        None,
        "error",
        {
            "logger": "vendor",
            "event": "private-message",
            "api_key": "secret",
            "correlation_id": "test",
            "exception": [{"exc_type": "ValueError", "exc_value": "secret"}],
        },
    )
    assert "secret" not in json.dumps(event)
    assert "private-message" not in json.dumps(event)
    assert event["logger"] == "vendor"
    assert event["correlation_id"] == "test"
