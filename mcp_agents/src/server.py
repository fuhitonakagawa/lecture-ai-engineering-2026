"""3つの学習支援ツールをstdioまたはStreamable HTTPで公開する。"""

import logging
import os
import sys

import structlog
import uvicorn
from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer

from src.config.logging import configure_logging
from src.config.settings import PROJECT_ROOT, positive_setting, required_setting
from src.profiles import tool_functions

logger = logging.getLogger(__name__)


def build_server(style: str) -> MCPServer:
    """関数名・型・docstringを公開するMCPサーバーを返す。"""
    server = MCPServer(
        "aie2026-glossary",
        instructions="AIE2026の用語集・講義要約・確認問題を提供する。",
    )
    for name, function in tool_functions(style).items():
        server.tool(name=name)(function)
    return server


def main() -> None:
    """必須環境設定に従い、標準出力をプロトコル専用にして起動する。"""
    if len(sys.argv) > 1:
        raise SystemExit(
            "通信方式は.envまたはMCP_TRANSPORT環境変数で指定してください。"
        )
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    configure_logging()
    style = required_setting("MCP_DESCRIPTION_STYLE")
    transport = required_setting("MCP_TRANSPORT")
    server = build_server(style)
    structlog.wrap_logger(logger).info(
        "server_started",
        transport=transport,
        process_id=os.getpid(),
        description_style=style,
    )
    if transport == "stdio":
        server.run(transport="stdio")
    elif transport == "streamable-http":
        host = required_setting("MCP_HTTP_HOST")
        port = positive_setting("MCP_HTTP_PORT")
        if host not in {"127.0.0.1", "localhost", "::1"} or port > 65535:
            raise ValueError(
                "HTTPサーバーはループバックと有効なポート番号で起動してください。"
            )
        uvicorn.run(
            server.streamable_http_app(host=host),
            host=host,
            port=port,
            log_config=None,
            access_log=False,
        )
    else:
        raise ValueError(
            "MCP_TRANSPORTはstdioまたはstreamable-httpを指定してください。"
        )


if __name__ == "__main__":
    main()
