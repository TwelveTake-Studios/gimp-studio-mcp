"""Bridge lifecycle — attach to a live GIMP or spawn a headless one.

The only place that knows the difference between the two modes. Both end up with
the same `BridgeClient` over the same socket protocol; they differ only in who
launches GIMP and where the endpoint file lands.
"""
from __future__ import annotations

import glob
import os
import platform
import subprocess
import tempfile
import time

from . import protocol as P
from .client import BridgeClient


# ---------------------------------------------------------------------------
# Live attach
# ---------------------------------------------------------------------------
def attach_live(endpoint_file: str | None = None, timeout: float = 10.0) -> BridgeClient:
    """Connect to an already-running GIMP whose bridge auto-started."""
    client = BridgeClient(endpoint_file=endpoint_file, connect_timeout=timeout)
    client.connect()
    return client


# ---------------------------------------------------------------------------
# GIMP executable resolution
# ---------------------------------------------------------------------------
def find_gimp_console(explicit: str | None = None) -> str:
    """Locate the headless GIMP binary.

    Order: explicit arg > GIMP_MCP_EXE env > PATH (`gimp-console-3.0`/`gimp-console`)
    > per-OS default install glob. Prefer console binaries (no GUI subsystem).
    """
    import shutil

    cand = explicit or os.environ.get("GIMP_MCP_EXE")
    if cand and os.path.exists(cand):
        return cand

    for name in ("gimp-console-3.0", "gimp-console"):
        found = shutil.which(name)
        if found:
            return found

    system = platform.system()
    patterns: list[str] = []
    if system == "Windows":
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        patterns = [
            os.path.join(pf, "GIMP*", "bin", "gimp-console-*.exe"),
        ]
    elif system == "Darwin":
        patterns = [
            "/Applications/GIMP.app/Contents/MacOS/gimp-console",
            "/Applications/GIMP.app/Contents/MacOS/gimp",
        ]
    else:  # Linux/other
        patterns = ["/usr/bin/gimp-console*", "/usr/local/bin/gimp-console*"]

    for pat in patterns:
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]  # latest version if several

    raise FileNotFoundError(
        "could not locate gimp-console; pass --gimp-exe or set GIMP_MCP_EXE")


# ---------------------------------------------------------------------------
# Headless spawn
# ---------------------------------------------------------------------------
class HeadlessGimp:
    """A server-spawned headless GIMP plus its connected client."""

    def __init__(self, proc: subprocess.Popen, client: BridgeClient, endpoint_file: str):
        self.proc = proc
        self.client = client
        self.endpoint_file = endpoint_file

    def shutdown(self, kill_timeout: float = 5.0) -> None:
        # Ask the bridge to quit its MainLoop, then tear down the core process.
        try:
            self.client.shutdown()
        except Exception:
            pass
        try:
            self.client.close()
        except Exception:
            pass
        try:
            self.proc.terminate()
            self.proc.wait(timeout=kill_timeout)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass


def launch_headless(
    gimp_exe: str | None = None,
    endpoint_file: str | None = None,
    wait: float = 60.0,
) -> HeadlessGimp:
    """Spawn a long-running headless GIMP with the bridge loaded, then connect.

    The bridge auto-starts (no batch glue needed); we pass it a private endpoint
    file path via env so it never collides with a live GIMP's default endpoint.
    """
    exe = find_gimp_console(gimp_exe)

    if endpoint_file is None:
        fd, endpoint_file = tempfile.mkstemp(suffix="-gimp-mcp-endpoint.json")
        os.close(fd)
    if os.path.exists(endpoint_file):
        os.remove(endpoint_file)

    env = dict(os.environ)
    env[P.ENV_ENDPOINT] = endpoint_file
    env[P.ENV_MODE] = "headless"
    # The bridge runs under GIMP's OWN bundled Python (gi only) — it must NEVER see
    # the external MCP-server venv. Strip interpreter-path vars so our src/ or a
    # venv on PYTHONPATH can't leak in and break GIMP's plug-in loading (§7 #5).
    for _var in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONEXECUTABLE"):
        env.pop(_var, None)

    proc = subprocess.Popen(
        [exe],
        env=env,
        stdin=subprocess.DEVNULL,   # never inherit the MCP stdio pipe — console GIMP
                                    # blocks reading a batch script from a pipe stdin
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    deadline = time.monotonic() + wait
    while not os.path.exists(endpoint_file):
        if proc.poll() is not None:
            raise RuntimeError(
                "gimp-console exited (code %s) before publishing its endpoint" % proc.returncode)
        if time.monotonic() > deadline:
            try:
                proc.terminate()
            except Exception:
                pass
            raise TimeoutError("headless GIMP did not publish an endpoint within %ss" % wait)
        time.sleep(0.25)

    client = BridgeClient(endpoint_file=endpoint_file)
    client.connect()
    return HeadlessGimp(proc, client, endpoint_file)


def shutdown(handle) -> None:
    """Cleanly stop a server-launched headless GIMP, or just close a live client."""
    if isinstance(handle, HeadlessGimp):
        handle.shutdown()
    elif isinstance(handle, BridgeClient):
        handle.close()
