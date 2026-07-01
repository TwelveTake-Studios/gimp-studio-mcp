"""External (MCP-server side) client to the GIMP-side bridge.

Tools call ``BridgeClient.send(...)`` (or the convenience wrappers) and get a
``BridgeResponse``. The client hides the transport (localhost socket), discovers
the bridge via its endpoint file, and performs the one-time token handshake.

Runs in the MCP server's own venv — may use third-party deps elsewhere, but this
module stays stdlib + protocol so it can be exercised without GIMP.
"""
from __future__ import annotations

import socket

from . import protocol as P
from .protocol import BridgeRequest, BridgeResponse


class BridgeError(Exception):
    """Base class for client-side transport/auth failures (NOT the envelope error)."""


class BridgeConnectionError(BridgeError):
    pass


class BridgeAuthError(BridgeError):
    pass


class BridgeClient:
    """Connects to a live or headless GIMP bridge and exchanges envelopes."""

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        token: str | None = None,
        endpoint_file: str | None = None,
        connect_timeout: float = 10.0,
        request_timeout: float | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.token = token
        self.endpoint_file = endpoint_file
        self.connect_timeout = connect_timeout
        self.request_timeout = request_timeout
        self._sock: socket.socket | None = None

    # ---- discovery --------------------------------------------------------
    def _resolve_endpoint(self) -> tuple[str, int, str]:
        """Return (host, port, token), preferring explicit values over the file."""
        if self.host and self.port and self.token:
            return self.host, int(self.port), self.token
        path = self.endpoint_file or P.default_endpoint_file()
        try:
            info = P.read_endpoint_file(path)
        except FileNotFoundError as e:
            raise BridgeConnectionError(
                "bridge endpoint file not found: %s — is GIMP running with the "
                "bridge installed? (try `gimp-mcp doctor`)" % path
            ) from e
        except (OSError, ValueError) as e:
            raise BridgeConnectionError("could not read endpoint file %s: %s" % (path, e)) from e
        host = self.host or info.get("host", "127.0.0.1")
        port = int(self.port or info["port"])
        token = self.token or info.get("token")
        return host, port, token

    # ---- lifecycle --------------------------------------------------------
    def connect(self) -> None:
        host, port, token = self._resolve_endpoint()
        try:
            sock = socket.create_connection((host, port), timeout=self.connect_timeout)
        except OSError as e:
            raise BridgeConnectionError("cannot connect to bridge at %s:%d: %s"
                                        % (host, port, e)) from e
        sock.settimeout(self.request_timeout)
        self._sock = sock
        # One-time auth handshake (first frame must carry the token).
        try:
            P.send_message(sock, {"op": P.OP_AUTH, "token": token})
            resp = P.recv_message(sock)
        except OSError as e:
            self.close()
            raise BridgeConnectionError("handshake I/O failed: %s" % e) from e
        if not resp or not resp.get("ok"):
            self.close()
            raise BridgeAuthError("bridge rejected the auth token")

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    @property
    def connected(self) -> bool:
        return self._sock is not None

    # ---- request/response -------------------------------------------------
    def send(self, request: BridgeRequest) -> BridgeResponse:
        """Send one request, return the structured response."""
        if self._sock is None:
            self.connect()
        assert self._sock is not None
        try:
            P.send_message(self._sock, request.to_dict())
            raw = P.recv_message(self._sock)
        except OSError as e:
            self.close()
            raise BridgeConnectionError("request I/O failed: %s" % e) from e
        if raw is None:
            self.close()
            raise BridgeConnectionError("bridge closed the connection")
        return BridgeResponse.from_dict(raw)

    # ---- convenience wrappers --------------------------------------------
    def ping(self) -> BridgeResponse:
        return self.send(BridgeRequest(op=P.OP_PING))

    def info(self) -> BridgeResponse:
        return self.send(BridgeRequest(op=P.OP_INFO))

    def exec(self, code: str, *, args=None, image=None, undo_group: bool = True,
             timeout: float | None = None) -> BridgeResponse:
        payload = {"code": code}
        if args is not None:
            payload["args"] = args
        if image is not None:
            payload["image"] = image
        return self.send(BridgeRequest(op=P.OP_EXEC, payload=payload,
                                       undo_group=undo_group, timeout=timeout))

    def list_images(self) -> BridgeResponse:
        return self.send(BridgeRequest(op=P.OP_LIST_IMAGES))

    def set_active_image(self, image) -> BridgeResponse:
        return self.send(BridgeRequest(op=P.OP_SET_ACTIVE, payload={"image": image}))

    def reset_namespace(self, scope: str = "soft") -> BridgeResponse:
        return self.send(BridgeRequest(op=P.OP_RESET, payload={"scope": scope}))

    def shutdown(self) -> BridgeResponse:
        return self.send(BridgeRequest(op=P.OP_SHUTDOWN))

    # ---- context manager --------------------------------------------------
    def __enter__(self) -> "BridgeClient":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()
