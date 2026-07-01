"""Tier-1 pytest fixtures.

Tier 1 = NO GIMP. Pure stdlib + the stdlib-only project modules
(``protocol.py``, ``client.py``, ``compat.py``). Runs everywhere in <5s.

This module:
  * Puts ``src/`` on ``sys.path`` so ``import gimp_mcp...`` works without an install.
  * Provides a FAKE in-process bridge server: a daemon thread that binds
    ``127.0.0.1:0``, speaks the length-prefixed wire protocol via
    ``protocol.recv_message`` / ``protocol.send_message``, enforces the one-time
    auth handshake, and answers ping/info/exec with canned envelopes — enough for
    ``BridgeClient`` to be exercised end to end without GIMP.
"""
from __future__ import annotations

import os
import socket
import sys
import threading

import pytest

# --- make src/ importable (no install needed) ------------------------------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from gimp_mcp.bridge import protocol as P  # noqa: E402


class FakeBridgeServer:
    """A minimal, in-process stand-in for the GIMP-side bridge.

    Binds an ephemeral loopback port and serves connections on a daemon thread.
    Each connection must send the auth frame first; an accepted token gets an
    ``ok_response`` and the connection stays open for subsequent ops. A wrong
    token gets an ``error_response`` and the connection is closed (mirroring the
    real bridge's drop-unauthenticated behaviour).
    """

    def __init__(self, token: str | None = None, host: str = "127.0.0.1") -> None:
        self.token = token if token is not None else P.new_token()
        self.host = host
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind((host, 0))
        self._srv.listen(8)
        self.port = self._srv.getsockname()[1]
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        # Records of what the server saw, for assertions.
        self.auth_attempts: list[str | None] = []
        self.requests: list[dict] = []
        self._lock = threading.Lock()
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> "FakeBridgeServer":
        self._accept_thread.start()
        self._ready.wait(timeout=2.0)
        return self

    def stop(self) -> None:
        self._stop.set()
        try:
            self._srv.close()
        except OSError:
            pass
        for t in list(self._threads):
            t.join(timeout=1.0)
        self._accept_thread.join(timeout=1.0)

    @property
    def endpoint(self) -> tuple[str, int, str]:
        return self.host, self.port, self.token

    # -- accept loop --------------------------------------------------------
    def _accept_loop(self) -> None:
        self._srv.settimeout(0.2)
        self._ready.set()
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            t = threading.Thread(target=self._serve_conn, args=(conn,), daemon=True)
            with self._lock:
                self._threads.append(t)
            t.start()

    # -- per-connection handler --------------------------------------------
    def _serve_conn(self, conn: socket.socket) -> None:
        try:
            # 1) Auth frame MUST come first.
            auth = P.recv_message(conn)
            if auth is None:
                return
            tok = auth.get("token") if isinstance(auth, dict) else None
            with self._lock:
                self.auth_attempts.append(tok)
            if not P.tokens_equal(tok, self.token):
                P.send_message(conn, P.error_response("AuthError", "bad token"))
                return
            P.send_message(conn, P.ok_response({"authed": True}))

            # 2) Serve ops until the peer goes away.
            while not self._stop.is_set():
                req = P.recv_message(conn)
                if req is None:
                    return
                with self._lock:
                    self.requests.append(req)
                P.send_message(conn, self._dispatch(req))
        except OSError:
            return
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _dispatch(self, req: dict) -> dict:
        op = req.get("op")
        if op == P.OP_PING:
            return P.ok_response({"pong": True, "thread": "MainThread"})
        if op == P.OP_INFO:
            return P.ok_response({"gimp_version": "3.0.4", "bridge_version": "test"})
        if op == P.OP_EXEC:
            payload = req.get("payload") or {}
            code = payload.get("code", "")
            args = payload.get("args")
            # Canned, deterministic exec behaviour the client tests rely on.
            if code == "1/0":
                return P.error_response(
                    "ZeroDivisionError", "division by zero", failing_line="1/0",
                    stdout="before\n",
                )
            return P.ok_response(
                {"echo_code": code, "args": args},
                stdout="hi\n",
            )
        return P.error_response("UnknownOp", "unknown op: %r" % op)


@pytest.fixture
def fake_bridge():
    """Yield a started FakeBridgeServer; torn down after the test."""
    srv = FakeBridgeServer().start()
    try:
        yield srv
    finally:
        srv.stop()
