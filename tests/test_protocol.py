"""Tier-1 unit tests for the wire protocol (no GIMP).

Covers framing over real sockets (socketpair), chunked/partial reads, pipelined
frames, clean-EOF handling, the MAX_FRAME guards, envelope round-trips, response
shapes, JSON fallback, tokens, and the endpoint-file/discovery helpers.
"""
from __future__ import annotations

import json
import os
import socket
import struct
import threading

import pytest

from gimp_mcp.bridge import protocol as P


# ---------------------------------------------------------------------------
# Framing
# ---------------------------------------------------------------------------
def test_pack_layout():
    raw = P.pack({"op": "ping"})
    (length,) = struct.unpack(">I", raw[:4])
    assert length == len(raw) - 4
    assert json.loads(raw[4:].decode("utf-8")) == {"op": "ping"}


def test_framing_roundtrip_socketpair():
    a, b = socket.socketpair()
    try:
        obj = {"op": "x", "n": 42, "list": [1, 2, 3]}
        P.send_message(a, obj)
        assert P.recv_message(b) == obj
    finally:
        a.close()
        b.close()


def test_framing_large_payload_chunked():
    """A multi-MB payload forces _recv_exactly to loop over many chunks."""
    a, b = socket.socketpair()
    try:
        big = {"op": "x", "blob": "Z" * (3 * 1024 * 1024)}
        # send in a thread so a small socket buffer can't deadlock the test
        t = threading.Thread(target=P.send_message, args=(a, big), daemon=True)
        t.start()
        got = P.recv_message(b)
        t.join(timeout=5.0)
        assert got == big
    finally:
        a.close()
        b.close()


def test_recv_handles_partial_reads():
    """Header and body delivered in tiny dribbles must still reassemble."""
    a, b = socket.socketpair()
    try:
        payload = P.pack({"hello": "world", "k": list(range(50))})

        def drip():
            for byte in payload:
                a.sendall(bytes([byte]))

        t = threading.Thread(target=drip, daemon=True)
        t.start()
        got = P.recv_message(b)
        t.join(timeout=5.0)
        assert got == {"hello": "world", "k": list(range(50))}
    finally:
        a.close()
        b.close()


def test_two_frames_pipelined_back_to_back():
    """Two frames written in one go must read back as two distinct messages."""
    a, b = socket.socketpair()
    try:
        first = {"op": "ping", "id": 1}
        second = {"op": "info", "id": 2}
        a.sendall(P.pack(first) + P.pack(second))
        assert P.recv_message(b) == first
        assert P.recv_message(b) == second
    finally:
        a.close()
        b.close()


def test_recv_exactly_none_on_clean_eof():
    a, b = socket.socketpair()
    try:
        a.close()  # peer hangs up before sending anything
        assert P._recv_exactly(b, 4) is None
        # recv_message should likewise return None (clean EOF -> None).
    finally:
        b.close()


def test_recv_message_none_on_clean_eof():
    a, b = socket.socketpair()
    try:
        a.close()
        assert P.recv_message(b) is None
    finally:
        b.close()


def test_recv_message_none_on_eof_mid_body():
    """Header says N bytes but peer closes after sending fewer -> None."""
    a, b = socket.socketpair()
    try:
        # Claim 100 bytes of body, then send only 4 and close.
        a.sendall(struct.pack(">I", 100) + b"abcd")
        a.close()
        assert P.recv_message(b) is None
    finally:
        b.close()


# ---------------------------------------------------------------------------
# MAX_FRAME guards
# ---------------------------------------------------------------------------
def test_pack_rejects_oversize_send(monkeypatch):
    # Shrink the cap so we don't allocate 256 MB.
    monkeypatch.setattr(P, "MAX_FRAME", 16)
    with pytest.raises(ValueError, match="too large to send"):
        P.pack({"blob": "x" * 100})


def test_recv_rejects_oversize_header(monkeypatch):
    monkeypatch.setattr(P, "MAX_FRAME", 16)
    a, b = socket.socketpair()
    try:
        # Hand-craft a header claiming a frame larger than the (patched) cap.
        a.sendall(struct.pack(">I", 1024))
        with pytest.raises(ValueError, match="too large to receive"):
            P.recv_message(b)
    finally:
        a.close()
        b.close()


# ---------------------------------------------------------------------------
# Envelopes — round-trips
# ---------------------------------------------------------------------------
def test_bridge_error_roundtrip():
    err = P.BridgeError("ZeroDivisionError", "division by zero",
                        failing_line="x = 1 / 0", traceback="Traceback...")
    back = P.BridgeError.from_dict(err.to_dict())
    assert back == err


def test_bridge_error_from_none():
    assert P.BridgeError.from_dict(None) is None
    assert P.BridgeError.from_dict({}) is None


def test_bridge_error_from_partial_dict_defaults():
    back = P.BridgeError.from_dict({"message": "boom"})
    assert back.type == "Error"
    assert back.message == "boom"
    assert back.failing_line is None
    assert back.traceback is None


def test_bridge_response_roundtrip_ok():
    resp = P.BridgeResponse(ok=True, result={"sum": 45}, stdout="hi\n",
                            warnings=["w1", "w2"])
    back = P.BridgeResponse.from_dict(resp.to_dict())
    assert back.ok is True
    assert back.result == {"sum": 45}
    assert back.stdout == "hi\n"
    assert back.warnings == ["w1", "w2"]
    assert back.error is None


def test_bridge_response_roundtrip_error_keeps_stdout():
    """The never-drop-stdout-on-exception path."""
    resp = P.BridgeResponse(ok=False, stdout="before\n",
                            error=P.BridgeError("E", "m", "line", "tb"))
    back = P.BridgeResponse.from_dict(resp.to_dict())
    assert back.ok is False
    assert back.stdout == "before\n"
    assert back.error.type == "E"
    assert back.error.failing_line == "line"


def test_bridge_request_roundtrip_full():
    req = P.BridgeRequest(op="exec", payload={"code": "1+1"}, token="abc",
                          checkpoint=False, undo_group=False, timeout=2.5)
    back = P.BridgeRequest.from_dict(req.to_dict())
    assert back == req


def test_bridge_request_to_dict_omits_none_token_and_timeout():
    d = P.BridgeRequest(op="ping").to_dict()
    assert "token" not in d
    assert "timeout" not in d
    assert d["op"] == "ping"
    assert d["checkpoint"] is True
    assert d["undo_group"] is True


def test_bridge_request_from_empty_dict_defaults():
    back = P.BridgeRequest.from_dict({})
    assert back.op == ""
    assert back.payload == {}
    assert back.token is None
    assert back.checkpoint is True
    assert back.undo_group is True
    assert back.timeout is None


# ---------------------------------------------------------------------------
# Response factory shapes
# ---------------------------------------------------------------------------
def test_ok_response_shape():
    d = P.ok_response({"x": 1}, stdout="out", warnings=["w"])
    assert d == {
        "ok": True,
        "result": {"x": 1},
        "stdout": "out",
        "warnings": ["w"],
        "error": None,
    }


def test_ok_response_defaults():
    d = P.ok_response()
    assert d["ok"] is True
    assert d["result"] is None
    assert d["stdout"] == ""
    assert d["warnings"] == []
    assert d["error"] is None


def test_error_response_shape():
    d = P.error_response("ValueError", "bad", failing_line="f()", tb="tb",
                         stdout="partial")
    assert d["ok"] is False
    assert d["result"] is None
    assert d["stdout"] == "partial"
    assert d["error"] == {
        "type": "ValueError",
        "message": "bad",
        "failing_line": "f()",
        "traceback": "tb",
    }


# ---------------------------------------------------------------------------
# JSON fallback
# ---------------------------------------------------------------------------
def test_json_default_on_non_json_object():
    class Weird:
        def __repr__(self):
            return "<weird-obj>"

    out = json.dumps({"k": Weird()}, default=P._json_default)
    assert json.loads(out) == {"k": "<weird-obj>"}


def test_pack_serializes_non_json_via_default():
    class Weird:
        def __repr__(self):
            return "REPR"

    raw = P.pack({"obj": Weird()})
    decoded = json.loads(raw[4:].decode("utf-8"))
    assert decoded == {"obj": "REPR"}


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------
def test_new_token_length_and_hex():
    t = P.new_token()
    assert len(t) == 64  # token_hex(32) -> 64 hex chars
    int(t, 16)  # must be valid hex


def test_new_token_unique():
    tokens = {P.new_token() for _ in range(100)}
    assert len(tokens) == 100


def test_tokens_equal_true():
    t = P.new_token()
    assert P.tokens_equal(t, t) is True


def test_tokens_equal_false():
    assert P.tokens_equal(P.new_token(), P.new_token()) is False
    assert P.tokens_equal("abc", "abd") is False


@pytest.mark.parametrize("a,b", [(None, "x"), ("x", None), (None, None),
                                 ("", "x"), ("x", "")])
def test_tokens_equal_none_or_empty(a, b):
    assert P.tokens_equal(a, b) is False


# ---------------------------------------------------------------------------
# Endpoint file (discovery)
# ---------------------------------------------------------------------------
def test_write_read_endpoint_file_roundtrip(tmp_path):
    path = str(tmp_path / "sub" / P.ENDPOINT_FILENAME)
    info = {"host": "127.0.0.1", "port": 9876, "token": P.new_token(), "pid": 123}
    P.write_endpoint_file(path, info)
    assert os.path.exists(path)
    assert P.read_endpoint_file(path) == info


def test_read_endpoint_file_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        P.read_endpoint_file(str(tmp_path / "nope.json"))


def test_default_endpoint_file_env_override(monkeypatch):
    monkeypatch.setenv(P.ENV_ENDPOINT, "/explicit/path.json")
    assert P.default_endpoint_file() == "/explicit/path.json"


def test_default_endpoint_file_falls_back_to_user_dir(monkeypatch):
    monkeypatch.delenv(P.ENV_ENDPOINT, raising=False)
    expected = os.path.join(P.gimp_user_dir(), P.ENDPOINT_FILENAME)
    assert P.default_endpoint_file() == expected


# ---------------------------------------------------------------------------
# _gimp_config_base — per-OS parent dir (…/GIMP, version-agnostic)
# ---------------------------------------------------------------------------
def _norm(p):
    return p.replace("\\", "/")


def test_gimp_config_base_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(P.platform, "system", lambda: "Windows")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    b = P._gimp_config_base()
    assert os.path.basename(b) == "GIMP"
    assert "Roaming" in b


def test_gimp_config_base_darwin(monkeypatch):
    monkeypatch.setattr(P.platform, "system", lambda: "Darwin")
    b = P._gimp_config_base()
    assert _norm(b).endswith("Library/Application Support/GIMP")


def test_gimp_config_base_linux_xdg(monkeypatch):
    monkeypatch.setattr(P.platform, "system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/home/x/.config")
    b = P._gimp_config_base()
    assert _norm(b).endswith(".config/GIMP")


# ---------------------------------------------------------------------------
# gimp_user_dir — version-aware discovery + 3.0 fallback
# ---------------------------------------------------------------------------
def test_gimp_user_dir_fallback_when_no_config_dirs(monkeypatch, tmp_path):
    # Nothing under …/GIMP yet -> conventional 3.0 fallback so a clean machine
    # still resolves.
    monkeypatch.setattr(P, "_gimp_config_base", lambda: str(tmp_path / "GIMP"))
    d = P.gimp_user_dir()
    assert os.path.basename(d) == "3.0"
    assert os.path.basename(os.path.dirname(d)) == "GIMP"


def test_gimp_user_dir_prefers_freshest_endpoint(monkeypatch, tmp_path):
    # Two release dirs both with an endpoint file: the freshest wins (the live
    # GIMP that most recently published), regardless of version ordering.
    base = tmp_path / "GIMP"
    (base / "3.0").mkdir(parents=True)
    (base / "3.2").mkdir(parents=True)
    ep30 = base / "3.0" / P.ENDPOINT_FILENAME
    ep32 = base / "3.2" / P.ENDPOINT_FILENAME
    ep30.write_text("{}", encoding="utf-8")
    ep32.write_text("{}", encoding="utf-8")
    os.utime(str(ep30), (1_000_000, 1_000_000))
    os.utime(str(ep32), (2_000_000, 2_000_000))
    monkeypatch.setattr(P, "_gimp_config_base", lambda: str(base))
    monkeypatch.delenv(P.ENV_ENDPOINT, raising=False)
    assert os.path.basename(P.gimp_user_dir()) == "3.2"


def test_gimp_user_dir_highest_version_when_no_endpoint(monkeypatch, tmp_path):
    base = tmp_path / "GIMP"
    (base / "3.0").mkdir(parents=True)
    (base / "3.2").mkdir(parents=True)
    monkeypatch.setattr(P, "_gimp_config_base", lambda: str(base))
    assert os.path.basename(P.gimp_user_dir()) == "3.2"


# ---------------------------------------------------------------------------
# unsupported_response — capability gaps must not report ok:true
# ---------------------------------------------------------------------------
def _ok_env(result):
    return P.ok_response(result=result, stdout="out", warnings=["w"])


def test_unsupported_response_demotes_supported_false():
    env = P.unsupported_response(_ok_env({"supported": False, "note": "n"}), "use X")
    assert env["ok"] is False
    assert env["error"]["type"] == "UnsupportedOperation"
    assert env["error"]["message"] == "use X"
    # The result is PRESERVED so callers can still introspect why it failed,
    # and stdout/warnings survive the demotion (envelope contract).
    assert env["result"] == {"supported": False, "note": "n"}
    assert env["stdout"] == "out"
    assert env["warnings"] == ["w"]


def test_unsupported_response_passes_through_supported_true():
    env = P.unsupported_response(_ok_env({"supported": True, "op": "undo"}), "use X")
    assert env["ok"] is True
    assert env["error"] is None


@pytest.mark.parametrize("result", [None, "text", 42, [], {}, {"supported": None}])
def test_unsupported_response_ignores_non_capability_results(result):
    """Only an explicit supported=False is a capability gap. Notably a missing
    or None `supported` key must NOT be demoted."""
    assert P.unsupported_response(_ok_env(result), "use X")["ok"] is True


def test_unsupported_response_leaves_real_errors_untouched():
    err = P.error_response("ValueError", "boom")
    assert P.unsupported_response(err, "use X") == err


def test_unsupported_response_does_not_mutate_input():
    original = _ok_env({"supported": False})
    P.unsupported_response(original, "use X")
    assert original["ok"] is True and original["error"] is None
