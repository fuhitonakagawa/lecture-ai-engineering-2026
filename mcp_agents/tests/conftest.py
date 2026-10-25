"""ローカルHTTPサーバーをテストの間だけ起動する。"""

from collections.abc import Iterator
import os
import socket
import subprocess
import sys
import time

import pytest

from src.config.settings import PROJECT_ROOT


@pytest.fixture(scope="session")
def http_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """空いているローカルポートで実際のMCPサーバーを提供する。"""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    environment = {
        **os.environ,
        "MCP_TRANSPORT": "streamable-http",
        "MCP_HTTP_HOST": "127.0.0.1",
        "MCP_HTTP_PORT": str(port),
        "MCP_DESCRIPTION_STYLE": "brief",
        "LOG_LEVEL": "ERROR",
    }
    log_path = tmp_path_factory.mktemp("mcp-http") / "server.log"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "src.server"],
            cwd=PROJECT_ROOT,
            env=environment,
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 15
            while True:
                if process.poll() is not None:
                    raise RuntimeError(log_path.read_text())
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError(
                            "HTTPサーバーの起動が制限時間を超えました。"
                        ) from None
                    time.sleep(0.05)
            yield f"http://127.0.0.1:{port}/mcp"
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
