"""Codex CLIを利用するADK Webのルートエージェント。"""

from src.adk_agent import build_adk_agent
from src.config.logging import configure_logging
from src.config.settings import load_codex_settings

settings = load_codex_settings()
configure_logging()
root_agent = build_adk_agent(settings)
