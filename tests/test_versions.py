from __future__ import annotations

import subprocess
import sys

import anyio
import pytest
from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

import gimp_mcp
from gimp_mcp import cli, install
from gimp_mcp.bridge import protocol as P
from gimp_mcp.bridge.protocol import BridgeResponse
from gimp_mcp.server import GimpContext, build_server
from gimp_mcp.tools import session


def _initialize():
    mcp, _ctx = build_server(GimpContext(prefer_headless=True))
    lowlevel = mcp._mcp_server

    async def main():
        async with create_client_server_memory_streams() as (client_streams, server_streams):
            async with anyio.create_task_group() as tg:
                tg.start_soon(lambda: lowlevel.run(
                    server_streams[0], server_streams[1],
                    lowlevel.create_initialization_options(), raise_exceptions=True))
                async with ClientSession(client_streams[0], client_streams[1]) as client:
                    result = await client.initialize()
                tg.cancel_scope.cancel()
        return result

    return anyio.run(main)


def test_mcp_clients_see_the_package_version():
    result = _initialize()
    assert result.serverInfo.name == "gimp-mcp"
    assert result.serverInfo.version == gimp_mcp.__version__


def test_cli_version_flag(capsys):
    assert cli.main(["--version"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "gimp-mcp %s" % gimp_mcp.__version__
    assert out[1].split() == ["bridge", install.source_bridge_version()]


def test_cli_version_flag_as_a_command():
    done = subprocess.run([sys.executable, "-m", "gimp_mcp.cli", "--version"],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines()[0] == "gimp-mcp %s" % gimp_mcp.__version__


def test_older_bridge_warning():
    shipped = install.source_bridge_version()
    major, minor, patch = (int(p) for p in shipped.split("."))
    older = "%d.%d.%d" % (major, minor - 1, patch) if minor else "%d.9.9" % (major - 1)
    warning = install.older_bridge_warning(older)
    assert warning is not None
    assert older in warning and shipped in warning
    assert "gimp-mcp install-plugin --force" in warning


@pytest.mark.parametrize("running", [None, "", "test", "99.0.0"])
def test_no_bridge_warning_for_current_newer_or_unknown(running):
    assert install.older_bridge_warning(running) is None
    assert install.older_bridge_warning(install.source_bridge_version()) is None


class StatusClient:
    def __init__(self, version: str) -> None:
        self.session = "s"
        self.bridge_version = version

    def ensure_connected(self) -> None:
        pass

    def info(self):
        return BridgeResponse(ok=True, result={"gimp_version": "3.2.4",
                                               "bridge_version": self.bridge_version})


def _status(version: str) -> dict:
    ctx = GimpContext(prefer_headless=True)
    ctx._client = StatusClient(version)
    return session._status(ctx)


def test_status_warns_about_an_older_bridge(monkeypatch):
    for name in ("GIMP_MCP_OPERATION_TIMEOUT", "GIMP_MCP_START_TIMEOUT",
                 "GIMP_MCP_CONNECT_TIMEOUT"):
        monkeypatch.delenv(name, raising=False)
    envelope = _status("0.4.0")
    assert envelope["ok"] is True
    assert len(envelope["warnings"]) == 1
    assert "gimp-mcp install-plugin --force" in envelope["warnings"][0]


def test_status_is_quiet_for_the_shipped_bridge(monkeypatch):
    for name in ("GIMP_MCP_OPERATION_TIMEOUT", "GIMP_MCP_START_TIMEOUT",
                 "GIMP_MCP_CONNECT_TIMEOUT"):
        monkeypatch.delenv(name, raising=False)
    assert _status(install.source_bridge_version())["warnings"] == []


def test_status_reports_an_unusable_timeout(monkeypatch):
    monkeypatch.setenv("GIMP_MCP_OPERATION_TIMEOUT", "soon")
    warnings = _status(install.source_bridge_version())["warnings"]
    assert any("GIMP_MCP_OPERATION_TIMEOUT" in w for w in warnings)


def test_doctor_warns_about_an_older_live_bridge(tmp_path, monkeypatch, bridge_factory):
    bridge = bridge_factory(bridge_version="0.4.0")
    endpoint = str(tmp_path / P.ENDPOINT_FILENAME)
    bridge.write_endpoint(endpoint)
    monkeypatch.setenv(P.ENV_ENDPOINT, endpoint)
    for name in ("GIMP_MCP_OPERATION_TIMEOUT", "GIMP_MCP_START_TIMEOUT",
                 "GIMP_MCP_CONNECT_TIMEOUT"):
        monkeypatch.delenv(name, raising=False)
    _ok, lines = install.doctor(gimp_dir=str(tmp_path))
    report = "\n".join(lines)
    assert "live bridge  : REACHABLE" in report
    assert "running bridge 0.4.0" in report
    assert "gimp-mcp install-plugin --force" in report
    assert "timeouts     : operation 120 s, start 60 s, connect 10 s" in report


def test_doctor_is_quiet_for_the_shipped_bridge(tmp_path, monkeypatch, bridge_factory):
    bridge = bridge_factory(bridge_version=install.source_bridge_version())
    endpoint = str(tmp_path / P.ENDPOINT_FILENAME)
    bridge.write_endpoint(endpoint)
    monkeypatch.setenv(P.ENV_ENDPOINT, endpoint)
    _ok, lines = install.doctor(gimp_dir=str(tmp_path))
    assert not any("running bridge" in line for line in lines)
