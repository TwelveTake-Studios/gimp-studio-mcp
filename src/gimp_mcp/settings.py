from __future__ import annotations

import math
import os
import threading

TRUTHY = frozenset({"1", "true", "yes", "on"})

HEADLESS = "GIMP_MCP_HEADLESS"
NO_EXEC = "GIMP_MCP_NO_EXEC"
OPERATION_TIMEOUT = "GIMP_MCP_OPERATION_TIMEOUT"
START_TIMEOUT = "GIMP_MCP_START_TIMEOUT"
CONNECT_TIMEOUT = "GIMP_MCP_CONNECT_TIMEOUT"

TIMEOUT_DEFAULTS = {
    OPERATION_TIMEOUT: 120.0,
    START_TIMEOUT: 60.0,
    CONNECT_TIMEOUT: 10.0,
}


def flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in TRUTHY


def _read_seconds(name: str) -> tuple[float, str | None]:
    default = TIMEOUT_DEFAULTS[name]
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default, None
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or value <= 0 or value > threading.TIMEOUT_MAX:
        return default, ("%s=%r is not a usable number of seconds; using the default of %g"
                         % (name, raw, default))
    return value, None


def seconds(name: str) -> float:
    return _read_seconds(name)[0]


def operation_timeout() -> float:
    return seconds(OPERATION_TIMEOUT)


def start_timeout() -> float:
    return seconds(START_TIMEOUT)


def connect_timeout() -> float:
    return seconds(CONNECT_TIMEOUT)


def problems() -> list[str]:
    return [p for p in (_read_seconds(name)[1] for name in TIMEOUT_DEFAULTS) if p]


def describe_timeouts() -> str:
    return "operation %g s, start %g s, connect %g s" % (
        operation_timeout(), start_timeout(), connect_timeout())
