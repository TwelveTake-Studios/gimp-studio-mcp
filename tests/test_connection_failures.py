from __future__ import annotations

import os
import shutil
import socket
import sys

import pytest

from gimp_mcp.bridge import protocol as P
from gimp_mcp.bridge.client import BridgeClient
from gimp_mcp.server import GimpContext
from gimp_mcp.tools import session


class CapturingMCP:
    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self, *args, name=None, **kwargs):
        def deco(fn):
            self.tools[name or fn.__name__] = fn
            return fn
        return deco


def _dead_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def dead_endpoint(tmp_path, monkeypatch):
    path = str(tmp_path / P.ENDPOINT_FILENAME)
    P.write_endpoint_file(path, {"host": "127.0.0.1", "port": _dead_port(), "token": "x"})
    monkeypatch.setenv(P.ENV_ENDPOINT, path)
    monkeypatch.delenv("GIMP_MCP_HEADLESS", raising=False)
    monkeypatch.setenv("GIMP_MCP_CONNECT_TIMEOUT", "0.5")
    return path


def _assert_connection_failure(envelope: dict) -> None:
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "BridgeConnectionError"
    assert "gimp-mcp doctor" in envelope["error"]["message"]


def test_run_returns_an_error_envelope(dead_endpoint):
    ctx = GimpContext()
    _assert_connection_failure(ctx.run("_result = 1", undo_group=False).to_dict())


def test_call_returns_an_error_envelope(dead_endpoint):
    ctx = GimpContext()
    _assert_connection_failure(ctx.call("info").to_dict())


@pytest.mark.parametrize("tool, kwargs", [
    ("status", {}),
    ("list_images", {}),
    ("set_active_image", {"image": 1}),
    ("reset_namespace", {}),
    ("gimp_exec", {"code": "_result = 1"}),
])
def test_session_tools_return_an_error_envelope(dead_endpoint, monkeypatch, tool, kwargs):
    monkeypatch.delenv("GIMP_MCP_NO_EXEC", raising=False)
    mcp = CapturingMCP()
    session.register(mcp, GimpContext())
    _assert_connection_failure(mcp.tools[tool](**kwargs))


def test_a_missing_endpoint_file_names_install_and_doctor(tmp_path, monkeypatch):
    monkeypatch.setenv(P.ENV_ENDPOINT, str(tmp_path / "missing.json"))
    ctx = GimpContext(prefer_headless=False)
    ctx._client = BridgeClient()
    envelope = ctx.run("_result = 1", undo_group=False).to_dict()
    assert envelope["ok"] is False
    assert "gimp-mcp install-plugin" in envelope["error"]["message"]
    assert "gimp-mcp doctor" in envelope["error"]["message"]


def test_a_gimp_without_the_bridge_names_install_and_doctor(tmp_path, monkeypatch):
    if sys.platform == "win32":
        exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "hostname.exe")
    else:
        exe = shutil.which("true")
    monkeypatch.setenv("GIMP_MCP_EXE", exe)
    monkeypatch.setenv(P.ENV_ENDPOINT, str(tmp_path / "private.json"))
    ctx = GimpContext(prefer_headless=True)
    envelope = ctx.run("_result = 1", undo_group=False).to_dict()
    assert envelope["ok"] is False
    assert "gimp-mcp install-plugin" in envelope["error"]["message"]
    assert "gimp-mcp doctor" in envelope["error"]["message"]
