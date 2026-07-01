"""Tier-1 tests for BridgeClient against the in-process FakeBridgeServer (no GIMP).

Covers the auth handshake, ping/info/exec round-trips, auth failure, connection
failures (server gone / mid-stream close / missing endpoint file), and the
endpoint-file discovery path.
"""
from __future__ import annotations

import socket
import threading

import pytest

from gimp_mcp.bridge import protocol as P
from gimp_mcp.bridge.client import (
    BridgeAuthError,
    BridgeClient,
    BridgeConnectionError,
)


# ---------------------------------------------------------------------------
# Handshake + happy-path round-trips
# ---------------------------------------------------------------------------
def test_connect_handshake_succeeds(fake_bridge):
    host, port, token = fake_bridge.endpoint
    c = BridgeClient(host=host, port=port, token=token)
    try:
        c.connect()
        assert c.connected is True
        assert fake_bridge.auth_attempts == [token]
    finally:
        c.close()
    assert c.connected is False


def test_ping_roundtrip(fake_bridge):
    host, port, token = fake_bridge.endpoint
    with BridgeClient(host=host, port=port, token=token) as c:
        r = c.ping()
        assert r.ok is True
        assert r.result == {"pong": True, "thread": "MainThread"}


def test_info_roundtrip(fake_bridge):
    host, port, token = fake_bridge.endpoint
    with BridgeClient(host=host, port=port, token=token) as c:
        r = c.info()
        assert r.ok is True
        assert r.result["gimp_version"] == "3.0.4"


def test_exec_roundtrip_with_args_and_stdout(fake_bridge):
    host, port, token = fake_bridge.endpoint
    with BridgeClient(host=host, port=port, token=token) as c:
        r = c.exec("_result = args['a']", args={"a": 1})
        assert r.ok is True
        assert r.stdout == "hi\n"
        assert r.result["echo_code"] == "_result = args['a']"
        assert r.result["args"] == {"a": 1}


def test_exec_error_envelope_preserves_stdout(fake_bridge):
    """Structured error must surface and stdout must NOT be dropped."""
    host, port, token = fake_bridge.endpoint
    with BridgeClient(host=host, port=port, token=token) as c:
        r = c.exec("1/0")
        assert r.ok is False
        assert r.stdout == "before\n"
        assert r.error is not None
        assert r.error.type == "ZeroDivisionError"
        assert r.error.failing_line == "1/0"


def test_send_autoconnects(fake_bridge):
    """Calling a wrapper without an explicit connect() should auto-connect."""
    host, port, token = fake_bridge.endpoint
    c = BridgeClient(host=host, port=port, token=token)
    try:
        assert c.connected is False
        r = c.ping()  # triggers connect()
        assert c.connected is True
        assert r.ok is True
    finally:
        c.close()


def test_request_payload_reaches_server(fake_bridge):
    host, port, token = fake_bridge.endpoint
    with BridgeClient(host=host, port=port, token=token) as c:
        c.exec("CODE", args={"k": "v"})
    # First recorded request after auth is the exec.
    assert fake_bridge.requests, "server saw no requests"
    req = fake_bridge.requests[-1]
    assert req["op"] == P.OP_EXEC
    assert req["payload"]["code"] == "CODE"
    assert req["payload"]["args"] == {"k": "v"}


# ---------------------------------------------------------------------------
# Auth failure
# ---------------------------------------------------------------------------
def test_wrong_token_raises_auth_error(fake_bridge):
    host, port, _token = fake_bridge.endpoint
    c = BridgeClient(host=host, port=port, token="deadbeef")
    with pytest.raises(BridgeAuthError):
        c.connect()
    assert c.connected is False  # client closed itself on rejection


# ---------------------------------------------------------------------------
# Connection failures
# ---------------------------------------------------------------------------
def test_connect_refused_raises_connection_error():
    """Bind+immediately-close a port to get a guaranteed-closed address."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing is listening here now
    c = BridgeClient(host="127.0.0.1", port=port, token="x", connect_timeout=1.0)
    with pytest.raises(BridgeConnectionError):
        c.connect()


def test_server_closes_mid_stream_raises_connection_error():
    """Server accepts + auths, then drops the socket before answering a request."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    ready = threading.Event()

    def handler():
        ready.set()
        conn, _ = srv.accept()
        # consume auth, ack it, then read one request and hang up rudely.
        P.recv_message(conn)
        P.send_message(conn, P.ok_response({"authed": True}))
        P.recv_message(conn)
        conn.close()

    t = threading.Thread(target=handler, daemon=True)
    t.start()
    ready.wait(timeout=2.0)

    c = BridgeClient(host="127.0.0.1", port=port, token="x", connect_timeout=2.0)
    try:
        c.connect()  # handshake succeeds
        with pytest.raises(BridgeConnectionError):
            c.ping()  # server closes -> recv_message returns None -> error
    finally:
        c.close()
        srv.close()
        t.join(timeout=2.0)


def test_endpoint_file_missing_raises_connection_error(tmp_path):
    missing = str(tmp_path / "does-not-exist.json")
    c = BridgeClient(endpoint_file=missing)
    with pytest.raises(BridgeConnectionError, match="endpoint file not found"):
        c.connect()


def test_malformed_endpoint_file_raises_connection_error(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    c = BridgeClient(endpoint_file=str(bad))
    with pytest.raises(BridgeConnectionError, match="could not read endpoint file"):
        c.connect()


# ---------------------------------------------------------------------------
# Endpoint-file discovery path
# ---------------------------------------------------------------------------
def test_endpoint_file_discovery(fake_bridge, tmp_path):
    host, port, token = fake_bridge.endpoint
    ep = tmp_path / P.ENDPOINT_FILENAME
    P.write_endpoint_file(str(ep), {"host": host, "port": port, "token": token})

    c = BridgeClient(endpoint_file=str(ep))  # no explicit host/port/token
    try:
        c.connect()
        assert c.connected is True
        r = c.ping()
        assert r.ok is True
    finally:
        c.close()
    # The token read from the file was the one offered to the server.
    assert fake_bridge.auth_attempts == [token]


def test_resolve_prefers_explicit_over_file(fake_bridge, tmp_path):
    """Explicit host/port/token wins; a bogus endpoint file is never read."""
    host, port, token = fake_bridge.endpoint
    ep = tmp_path / P.ENDPOINT_FILENAME  # intentionally never created
    c = BridgeClient(host=host, port=port, token=token, endpoint_file=str(ep))
    try:
        c.connect()
        assert c.connected is True
    finally:
        c.close()
