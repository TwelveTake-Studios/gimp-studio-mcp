from __future__ import annotations

import socket
import threading
import time

import pytest

from gimp_mcp.bridge import protocol as P
from gimp_mcp.bridge.client import BridgeClient, BridgeConnectionError


@pytest.mark.parametrize("abortive", [False, True], ids=["closed", "reset"])
def test_first_call_after_bridge_restart_succeeds(tmp_path, bridge_factory, abortive):
    endpoint = str(tmp_path / P.ENDPOINT_FILENAME)
    first = bridge_factory()
    first.write_endpoint(endpoint)
    client = BridgeClient(endpoint_file=endpoint)
    try:
        assert client.ping().ok
        first.stop(abortive=abortive)
        time.sleep(0.2)
        second = bridge_factory()
        second.write_endpoint(endpoint)

        response = client.ping()

        assert response.ok is True
        assert second.auth_attempts == [second.token]
        assert client.session == second.token
    finally:
        client.close()


def test_live_connection_is_reused(fake_bridge):
    host, port, token = fake_bridge.endpoint
    client = BridgeClient(host=host, port=port, token=token)
    try:
        for _ in range(3):
            assert client.ping().ok
        assert fake_bridge.auth_attempts == [token]
    finally:
        client.close()


def test_closed_bridge_with_no_replacement_raises_connection_error(tmp_path, bridge_factory):
    endpoint = str(tmp_path / P.ENDPOINT_FILENAME)
    bridge = bridge_factory()
    bridge.write_endpoint(endpoint)
    client = BridgeClient(endpoint_file=endpoint, connect_timeout=1.0)
    try:
        assert client.ping().ok
        bridge.stop()
        time.sleep(0.2)
        with pytest.raises(BridgeConnectionError):
            client.ping()
        assert client.connected is False
    finally:
        client.close()


def test_handshake_times_out_instead_of_hanging():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    held = []

    def accept_and_stay_silent():
        conn, _ = srv.accept()
        held.append(conn)

    threading.Thread(target=accept_and_stay_silent, daemon=True).start()
    outcome = {}

    def connect():
        client = BridgeClient(host="127.0.0.1", port=port, token="x", connect_timeout=0.5)
        started = time.monotonic()
        try:
            client.connect()
        except BridgeConnectionError as e:
            outcome["error"] = e
        outcome["elapsed"] = time.monotonic() - started

    worker = threading.Thread(target=connect, daemon=True)
    worker.start()
    worker.join(timeout=10.0)
    try:
        assert not worker.is_alive(), "the auth handshake blocked without a timeout"
        assert isinstance(outcome.get("error"), BridgeConnectionError)
        assert outcome["elapsed"] < 5.0
    finally:
        for conn in held:
            conn.close()
        srv.close()


def test_auth_reply_bridge_version_is_recorded(bridge_factory):
    bridge = bridge_factory(bridge_version="0.5.0")
    host, port, token = bridge.endpoint
    client = BridgeClient(host=host, port=port, token=token)
    try:
        client.connect()
        assert client.bridge_version == "0.5.0"
    finally:
        client.close()


def test_operation_timeout_travels_with_every_request(fake_bridge):
    host, port, token = fake_bridge.endpoint
    client = BridgeClient(host=host, port=port, token=token, operation_timeout=7.5)
    try:
        client.ping()
        client.exec("_result = 1")
        client.exec("_result = 2", timeout=30.0)
    finally:
        client.close()
    timeouts = [request.get("timeout") for request in fake_bridge.requests]
    assert timeouts == [7.5, 7.5, 30.0]


def test_operation_timeout_default_is_120_seconds(fake_bridge, monkeypatch):
    monkeypatch.delenv("GIMP_MCP_OPERATION_TIMEOUT", raising=False)
    host, port, token = fake_bridge.endpoint
    client = BridgeClient(host=host, port=port, token=token)
    try:
        client.ping()
    finally:
        client.close()
    assert fake_bridge.requests[-1]["timeout"] == 120.0


def test_operation_timeout_follows_the_environment(fake_bridge, monkeypatch):
    monkeypatch.setenv("GIMP_MCP_OPERATION_TIMEOUT", "900")
    host, port, token = fake_bridge.endpoint
    client = BridgeClient(host=host, port=port, token=token)
    try:
        client.ping()
    finally:
        client.close()
    assert fake_bridge.requests[-1]["timeout"] == 900.0
