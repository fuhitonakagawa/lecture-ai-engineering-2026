"""アプリとライブラリのログを相関ID付きのJSON Linesにそろえる。"""

import logging
import sys
from datetime import datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import structlog

from src.config.settings import required_setting


def normalize_event(
    logger: object, method: str, event: dict[str, Any]
) -> dict[str, Any]:
    """許可された診断項目だけを出力し、本文や例外の値を伏せる。"""
    name = event.get("logger", getattr(logger, "name", ""))
    event_name = event.get("event", "")
    allowed_events = {
        "model_request",
        "model_request_started",
        "model_request_failed",
        "experiment_finished",
        "experiment_failed",
    }
    sanitized = {
        key: event[key]
        for key in (
            "logger",
            "level",
            "correlation_id",
            "request",
            "status",
            "attempt",
            "error_code",
            "last_event",
            "last_item_type",
            "output_bytes",
            "exit_code",
        )
        if key in event
    }
    sanitized["event"] = (
        event_name
        if name.startswith("src.") and event_name in allowed_events
        else "library_message"
    )
    sanitized["logger"] = name
    sanitized["level"] = event.get("level", method)
    sanitized["timestamp"] = datetime.now(ZoneInfo("Asia/Tokyo")).isoformat()
    if "exception" in event:
        sanitized["exception"] = [
            {"exc_type": item.get("exc_type"), "exc_value": "[REDACTED]"}
            for item in event["exception"]
        ]
    return sanitized


def configure_logging() -> None:
    """LOG_LEVELに従い、標準エラーへJSON形式のログを出力する。"""
    level = required_setting("LOG_LEVEL").upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError("LOG_LEVELが不正です。")
    chain = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.dict_tracebacks,
    ]
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=chain,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            normalize_event,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
    )
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    structlog.configure(
        processors=[*chain, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
    structlog.contextvars.bind_contextvars(correlation_id=str(uuid4()))
