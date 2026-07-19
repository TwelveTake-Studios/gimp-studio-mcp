"""Bridge wire protocol — the structured envelope + framing shared by both sides.

This is the contract between the external MCP server and the GIMP-side bridge.

IMPORTANT: **stdlib-only.** This module is imported by the external MCP server
(its own venv) AND copied next to the GIMP-side bridge, where it runs under
GIMP's bundled Python via PyGObject. It must therefore NEVER import `gi` or any
third-party package — only the Python standard library. (A CI lint enforces this.)

Wire format:
  - Every message is a 4-byte big-endian unsigned length prefix followed by that
    many bytes of UTF-8 JSON. Works in both directions; cleanly handles partial
    reads, pipelining, and multi-MB base64 bitmaps. Frames are capped at MAX_FRAME.
  - The first client message on a connection MUST be an auth frame carrying the
    per-instance token (one-time handshake). The bridge drops unauthenticated
    connections.
  - Discovery: the bridge publishes {host, port, token, pid, ...} to an endpoint
    file in GIMP's per-user config dir; the client reads it to find + authenticate.
"""
from __future__ import annotations

import hmac
import json
import os
import platform
import secrets
import struct
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PROTOCOL_VERSION = 1
MAX_FRAME = 256 * 1024 * 1024  # 256 MB hard cap on a single frame

ENDPOINT_FILENAME = "mcp-bridge.json"

# Environment variables (read by the bridge inside GIMP and/or the launcher)
ENV_DISABLED = "GIMP_MCP_DISABLED"        # set -> bridge acks and does not serve
ENV_ENDPOINT = "GIMP_MCP_ENDPOINT_FILE"   # explicit endpoint-file path (headless)
ENV_PORT = "GIMP_MCP_PORT"                # pin a port instead of ephemeral :0
ENV_MODE = "GIMP_MCP_MODE"               # "headless" | "live" (informational)

# Ops
OP_AUTH = "auth"
OP_PING = "ping"
OP_INFO = "info"
OP_EXEC = "exec"
OP_RESET = "reset_namespace"
OP_LIST_IMAGES = "list_images"
OP_SET_ACTIVE = "set_active_image"
OP_SHUTDOWN = "shutdown"


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------
@dataclass
class BridgeError:
    """Structured error — never just a string."""
    type: str
    message: str
    failing_line: str | None = None
    traceback: str | None = None

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "message": self.message,
            "failing_line": self.failing_line,
            "traceback": self.traceback,
        }

    @staticmethod
    def from_dict(d: dict | None) -> "BridgeError | None":
        if not d:
            return None
        return BridgeError(
            type=d.get("type", "Error"),
            message=d.get("message", ""),
            failing_line=d.get("failing_line"),
            traceback=d.get("traceback"),
        )


@dataclass
class BridgeResponse:
    """Every bridge call returns this. stdout is captured even when error is set."""
    ok: bool
    result: Any = None
    stdout: str = ""
    warnings: list = field(default_factory=list)
    error: BridgeError | None = None

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "result": self.result,
            "stdout": self.stdout,
            "warnings": list(self.warnings),
            "error": self.error.to_dict() if self.error else None,
        }

    @staticmethod
    def from_dict(d: dict) -> "BridgeResponse":
        return BridgeResponse(
            ok=bool(d.get("ok")),
            result=d.get("result"),
            stdout=d.get("stdout", ""),
            warnings=list(d.get("warnings") or []),
            error=BridgeError.from_dict(d.get("error")),
        )


@dataclass
class BridgeRequest:
    """A unit of work sent to the GIMP-side bridge."""
    op: str
    payload: dict = field(default_factory=dict)
    token: str | None = None
    checkpoint: bool = True       # auto-snapshot before destructive ops (future)
    undo_group: bool = True       # wrap as a single undo step
    timeout: float | None = None  # per-call main-thread timeout (seconds)

    def to_dict(self) -> dict:
        d = {"op": self.op, "payload": self.payload,
             "checkpoint": self.checkpoint, "undo_group": self.undo_group}
        if self.token is not None:
            d["token"] = self.token
        if self.timeout is not None:
            d["timeout"] = self.timeout
        return d

    @staticmethod
    def from_dict(d: dict) -> "BridgeRequest":
        return BridgeRequest(
            op=d.get("op", ""),
            payload=d.get("payload") or {},
            token=d.get("token"),
            checkpoint=bool(d.get("checkpoint", True)),
            undo_group=bool(d.get("undo_group", True)),
            timeout=d.get("timeout"),
        )


def ok_response(result: Any = None, stdout: str = "", warnings: list | None = None) -> dict:
    return BridgeResponse(ok=True, result=result, stdout=stdout,
                          warnings=warnings or []).to_dict()


def error_response(etype: str, message: str, failing_line: str | None = None,
                   tb: str | None = None, stdout: str = "") -> dict:
    return BridgeResponse(ok=False, stdout=stdout,
                          error=BridgeError(etype, message, failing_line, tb)).to_dict()


def unsupported_response(env: dict, message: str) -> dict:
    """Demote an envelope whose result reports ``supported: False`` to ``ok: false``.

    A tool that cannot perform its ADVERTISED operation on this GIMP must not
    report ``ok: true`` — a caller that only checks ``ok`` reads that as success
    and moves on believing the edit landed. The ``supported``/``note`` result is
    preserved so callers can still introspect why, and ``error.message`` names
    the working alternative.

    Only for capability gaps (the op can never run here). Tools that legitimately
    return ``supported: False`` as an INPUT-conditional outcome — the op ran and
    correctly reported a condition — keep ``ok: true`` and must not use this.
    """
    if not env.get("ok"):
        return env
    result = env.get("result")
    if not isinstance(result, dict) or result.get("supported") is not False:
        return env
    demoted = dict(env)
    demoted["ok"] = False
    demoted["error"] = BridgeError("UnsupportedOperation", message).to_dict()
    return demoted


# ---------------------------------------------------------------------------
# Framing  (4-byte big-endian length prefix + UTF-8 JSON)
# ---------------------------------------------------------------------------
def _json_default(o: Any) -> str:
    return repr(o)


def pack(obj: Any) -> bytes:
    data = json.dumps(obj, default=_json_default).encode("utf-8")
    if len(data) > MAX_FRAME:
        raise ValueError("frame too large to send: %d bytes" % len(data))
    return struct.pack(">I", len(data)) + data


def _recv_exactly(sock, n: int) -> bytes | None:
    chunks = []
    got = 0
    while got < n:
        b = sock.recv(min(n - got, 1 << 20))
        if not b:
            return None
        chunks.append(b)
        got += len(b)
    return b"".join(chunks)


def send_message(sock, obj: Any) -> None:
    sock.sendall(pack(obj))


def recv_message(sock) -> Any:
    """Read one framed message. Returns the decoded object, or None on clean EOF."""
    hdr = _recv_exactly(sock, 4)
    if hdr is None:
        return None
    (length,) = struct.unpack(">I", hdr)
    if length > MAX_FRAME:
        raise ValueError("frame too large to receive: %d bytes" % length)
    body = _recv_exactly(sock, length)
    if body is None:
        return None
    return json.loads(body.decode("utf-8"))


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------
def new_token() -> str:
    return secrets.token_hex(32)


def tokens_equal(a: str | None, b: str | None) -> bool:
    """Constant-time token comparison."""
    if not a or not b:
        return False
    return hmac.compare_digest(str(a), str(b))


# ---------------------------------------------------------------------------
# Endpoint file (discovery)
# ---------------------------------------------------------------------------
def _gimp_config_base() -> str:
    """Per-OS parent dir that holds the per-release GIMP config dirs (…/GIMP)."""
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "GIMP")
    if system == "Darwin":
        return os.path.expanduser("~/Library/Application Support/GIMP")
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(xdg, "GIMP")


def _gimp_3x_config_dirs(base: str) -> list[str]:
    """Existing …/GIMP/3.x config dirs, sorted oldest→newest by version tuple."""
    found = []
    try:
        entries = os.listdir(base)
    except OSError:
        entries = []
    for name in entries:
        full = os.path.join(base, name)
        if not os.path.isdir(full) or not name.startswith("3"):
            continue
        try:
            ver = tuple(int(p) for p in name.split(".") if p.isdigit())
        except ValueError:
            ver = ()
        found.append((ver, full))
    found.sort(key=lambda t: t[0])
    return [full for _ver, full in found]


def gimp_user_dir() -> str:
    """Best-effort per-user GIMP 3.x config dir, computed WITHOUT importing gi.

    GIMP's GObject-introspection typelib stays ``Gimp-3.0`` across the whole 3.x
    series, but the per-user CONFIG dir is named by the minor release (``3.0``,
    ``3.2``, …). The bridge inside GIMP uses the authoritative ``Gimp.directory()``;
    this is the external client's fallback and must point at whichever release the
    *running* GIMP used. We can't import gi here (stdlib-only), so we discover it:
    prefer the 3.x dir whose endpoint file is freshest (the live GIMP that most
    recently published one), then the highest-version 3.x dir, then a ``3.0``
    fallback so a clean machine still resolves. (``GIMP3_DIRECTORY`` and an explicit
    ``GIMP_MCP_ENDPOINT_FILE`` still override this upstream.)
    """
    base = _gimp_config_base()
    dirs = _gimp_3x_config_dirs(base)
    with_ep = [d for d in dirs if os.path.exists(os.path.join(d, ENDPOINT_FILENAME))]
    if with_ep:
        return max(with_ep, key=lambda d: os.path.getmtime(os.path.join(d, ENDPOINT_FILENAME)))
    if dirs:
        return dirs[-1]
    return os.path.join(base, "3.0")


def default_endpoint_file() -> str:
    return os.environ.get(ENV_ENDPOINT) or os.path.join(gimp_user_dir(), ENDPOINT_FILENAME)


def write_endpoint_file(path: str, info: dict) -> None:
    """Write the endpoint descriptor with owner-only perms where the OS supports it."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    raw = json.dumps(info, indent=2).encode("utf-8")
    if os.name == "posix":
        # 0600: only the owning user can read the token.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, raw)
        finally:
            os.close(fd)
    else:
        # Windows: rely on the per-user AppData ACL + the token itself.
        with open(path, "wb") as f:
            f.write(raw)


def read_endpoint_file(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
