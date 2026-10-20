from __future__ import annotations

import os
import shutil
import sys
import time

import pytest

from gimp_mcp import settings
from gimp_mcp.bridge import launcher
from gimp_mcp.bridge.client import BridgeClient, BridgeConnectionError
from gimp_mcp.server import GimpContext
from gimp_mcp.tools import session


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", " on "])
def test_truthy_values_enable_a_flag(monkeypatch, value):
    monkeypatch.setenv("GIMP_MCP_HEADLESS", value)
    assert settings.flag(settings.HEADLESS) is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "anything"])
def test_other_values_leave_a_flag_off(monkeypatch, value):
    monkeypatch.setenv("GIMP_MCP_HEADLESS", value)
    assert settings.flag(settings.HEADLESS) is False


def test_headless_zero_does_not_force_headless(monkeypatch):
    monkeypatch.setenv("GIMP_MCP_HEADLESS", "0")
    assert GimpContext().prefer_headless is False


def test_headless_one_forces_headless(monkeypatch):
    monkeypatch.setenv("GIMP_MCP_HEADLESS", "1")
    assert GimpContext().prefer_headless is True


def test_headless_unset_attaches_live_when_possible(monkeypatch):
    monkeypatch.delenv("GIMP_MCP_HEADLESS", raising=False)
    assert GimpContext().prefer_headless is False


def test_no_exec_uses_the_same_parsing(monkeypatch):
    monkeypatch.setenv("GIMP_MCP_NO_EXEC", "0")
    assert session.exec_disabled() is False
    monkeypatch.setenv("GIMP_MCP_NO_EXEC", "yes")
    assert session.exec_disabled() is True


def test_timeout_defaults(monkeypatch):
    for name in settings.TIMEOUT_DEFAULTS:
        monkeypatch.delenv(name, raising=False)
    assert settings.operation_timeout() == 120.0
    assert settings.start_timeout() == 60.0
    assert settings.connect_timeout() == 10.0
    assert settings.problems() == []


def test_timeout_names_follow_the_project_prefix():
    assert set(settings.TIMEOUT_DEFAULTS) == {
        "GIMP_MCP_OPERATION_TIMEOUT", "GIMP_MCP_START_TIMEOUT", "GIMP_MCP_CONNECT_TIMEOUT"}


def test_timeouts_follow_the_environment(monkeypatch):
    monkeypatch.setenv("GIMP_MCP_OPERATION_TIMEOUT", "600")
    monkeypatch.setenv("GIMP_MCP_START_TIMEOUT", "180.5")
    monkeypatch.setenv("GIMP_MCP_CONNECT_TIMEOUT", "2")
    assert settings.operation_timeout() == 600.0
    assert settings.start_timeout() == 180.5
    assert settings.connect_timeout() == 2.0
    assert settings.problems() == []


@pytest.mark.parametrize("raw", ["abc", "0", "-5", "inf", "nan", "1e12"])
def test_unusable_timeouts_fall_back_and_are_reported(monkeypatch, raw):
    monkeypatch.setenv("GIMP_MCP_CONNECT_TIMEOUT", raw)
    assert settings.connect_timeout() == 10.0
    problems = settings.problems()
    assert len(problems) == 1
    assert "GIMP_MCP_CONNECT_TIMEOUT" in problems[0]


def test_connect_timeout_reaches_the_client(monkeypatch):
    monkeypatch.setenv("GIMP_MCP_CONNECT_TIMEOUT", "3.5")
    assert BridgeClient(endpoint_file="unused").connect_timeout == 3.5


def test_missing_gimp_console_names_the_environment_variable(monkeypatch, tmp_path):
    monkeypatch.delenv("GIMP_MCP_EXE", raising=False)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(launcher.glob, "glob", lambda pattern: [])
    with pytest.raises(FileNotFoundError) as info:
        launcher.find_gimp_console()
    message = str(info.value)
    assert "GIMP_MCP_EXE" in message
    assert "--gimp-exe" not in message


def _exits_at_once() -> str:
    if sys.platform == "win32":
        return os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "hostname.exe")
    return shutil.which("true")


def test_headless_exit_without_bridge_names_install_and_doctor(monkeypatch, tmp_path):
    monkeypatch.setenv("GIMP_MCP_EXE", _exits_at_once())
    started = time.monotonic()
    with pytest.raises(BridgeConnectionError) as info:
        launcher.launch_headless(endpoint_file=str(tmp_path / "endpoint.json"), wait=20.0)
    assert time.monotonic() - started < 10.0
    message = str(info.value)
    assert "exited" in message
    assert "gimp-mcp install-plugin" in message
    assert "gimp-mcp doctor" in message


class _SilentProcess:
    returncode = None

    def __init__(self, *args, **kwargs):
        self.terminated = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True


def test_headless_start_timeout_follows_the_setting(monkeypatch, tmp_path):
    monkeypatch.setenv("GIMP_MCP_EXE", sys.executable)
    monkeypatch.setenv("GIMP_MCP_START_TIMEOUT", "0.5")
    monkeypatch.setattr(launcher.subprocess, "Popen", _SilentProcess)
    started = time.monotonic()
    with pytest.raises(BridgeConnectionError) as info:
        launcher.launch_headless(endpoint_file=str(tmp_path / "endpoint.json"))
    elapsed = time.monotonic() - started
    assert 0.4 <= elapsed < 5.0
    message = str(info.value)
    assert "GIMP_MCP_START_TIMEOUT" in message
    assert "gimp-mcp install-plugin" in message
    assert "gimp-mcp doctor" in message
