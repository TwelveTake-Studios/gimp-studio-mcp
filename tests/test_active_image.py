from __future__ import annotations

import pytest

from gimp_mcp import server
from gimp_mcp.bridge.protocol import BridgeError, BridgeResponse
from gimp_mcp.server import GimpContext
from gimp_mcp.tools import safety
from gimp_mcp.tools._snippets import CHECKPOINT_TAG


class FakeImage:
    def __init__(self, image_id: int, tagged: bool = False) -> None:
        self.image_id = image_id
        self.tagged = tagged

    def get_id(self) -> int:
        return self.image_id

    def get_parasite(self, name):
        return object() if self.tagged and name == CHECKPOINT_TAG else None


class FakeGimp:
    def __init__(self, images) -> None:
        self.images = images

    def get_images(self):
        return list(self.images)


def pick(images, want=None, horizon=None, **namespace):
    ns = {"Gimp": FakeGimp(images),
          "args": {"want": want, "horizon": horizon, "tag": CHECKPOINT_TAG}}
    ns.update(namespace)
    exec(server._PICK_IMAGE_CODE, ns)
    assert "_gimpmcp_pick_image" not in ns
    return ns["_result"]


def newest_first(*ids, tagged=()):
    return [FakeImage(i, i in tagged) for i in sorted(ids, reverse=True)]


def test_without_a_choice_the_newest_working_image_is_used():
    assert pick(newest_first(1, 2, 3))["image"] == 3


def test_a_checkpoint_copy_is_never_the_default():
    assert pick(newest_first(1, 2, 3, tagged={3}))["image"] == 2


def test_checkpoint_copies_from_the_namespace_are_skipped():
    assert pick(newest_first(1, 2, 3), _gimpmcp_scratch={3})["image"] == 2
    checkpoints = {1: {"snapshot": 3, "origin": 1}}
    assert pick(newest_first(1, 2, 3), _gimpmcp_checkpoints=checkpoints)["image"] == 2


def test_the_chosen_image_wins_while_nothing_newer_opens():
    assert pick(newest_first(1, 2, 3), want=1, horizon=3)["image"] == 1


def test_an_image_opened_after_the_choice_becomes_active():
    assert pick(newest_first(1, 2, 3, 4), want=1, horizon=3)["image"] == 4


def test_a_checkpoint_taken_after_the_choice_does_not_take_over():
    assert pick(newest_first(1, 2, 3, 4, tagged={4}), want=1, horizon=3)["image"] == 1


def test_a_closed_choice_falls_back_to_gimps_first_image():
    assert pick(newest_first(2, 3), want=1, horizon=3)["image"] == 3


def test_only_checkpoint_copies_open_means_no_working_image():
    result = pick(newest_first(2, tagged={2}))
    assert result["image"] is None
    assert result["open"] == 1


def test_no_images_open():
    result = pick([])
    assert result == {"image": None, "open": 0, "newest": 0, "checkpoint": False}


def test_pick_reports_checkpoint_copies_and_the_newest_id():
    result = pick(newest_first(1, 5, tagged={5}), want=5)
    assert result["checkpoint"] is True
    assert result["newest"] == 5


class FakeClient:
    def __init__(self, session: str = "s1") -> None:
        self.session = session
        self.bridge_version = "0.5.0"
        self.calls: list[dict] = []
        self.pick_result = {"image": 4, "open": 2, "newest": 4, "checkpoint": False}
        self.pick_ok = True
        self.active_reply = BridgeResponse(ok=True, result={"active_image_id": 5})

    def ensure_connected(self) -> None:
        pass

    def exec(self, code, *, args=None, image=None, undo_group=True, timeout=None):
        self.calls.append({"code": code, "args": args, "image": image,
                           "undo_group": undo_group})
        if code == server._PICK_IMAGE_CODE:
            if not self.pick_ok:
                return BridgeResponse(ok=False, error=BridgeError("RuntimeError", "boom"))
            return BridgeResponse(ok=True, result=dict(self.pick_result))
        return BridgeResponse(ok=True, result={"ran": True})

    def set_active_image(self, image):
        self.calls.append({"op": "set_active_image", "image": image})
        return self.active_reply

    def tool_calls(self):
        return [c for c in self.calls if c.get("code") not in (server._PICK_IMAGE_CODE, None)]

    def picks(self):
        return [c for c in self.calls if c.get("code") == server._PICK_IMAGE_CODE]


@pytest.fixture
def ctx_and_client():
    ctx = GimpContext(prefer_headless=True)
    client = FakeClient()
    ctx._client = client
    return ctx, client


def test_omitted_image_resolves_to_the_active_image(ctx_and_client):
    ctx, client = ctx_and_client
    args = {"image": None, "layer": None}
    response = ctx.run("TOOL", args=args, image=None, undo_group=True)
    assert response.ok
    assert len(client.picks()) == 1
    [call] = client.tool_calls()
    assert call["args"] == {"image": 4, "layer": None}
    assert call["image"] == 4
    assert args == {"image": None, "layer": None}


def test_an_explicit_image_is_left_alone(ctx_and_client):
    ctx, client = ctx_and_client
    ctx.run("TOOL", args={"image": 9}, image=9)
    assert client.picks() == []
    assert client.tool_calls()[0]["image"] == 9


def test_calls_without_an_image_argument_skip_resolution(ctx_and_client):
    ctx, client = ctx_and_client
    ctx.run("TOOL", args={"path": "x.png"}, undo_group=False)
    ctx.run("TOOL", undo_group=False)
    assert client.picks() == []


def test_restore_keeps_its_origin_default(ctx_and_client):
    ctx, client = ctx_and_client
    safety._restore(ctx, 3)
    assert client.picks() == []
    assert client.tool_calls()[0]["args"]["image"] is None


def test_checkpoint_tags_its_copy(ctx_and_client):
    ctx, client = ctx_and_client
    safety._checkpoint(ctx)
    call = client.tool_calls()[0]
    assert call["args"]["tag"] == CHECKPOINT_TAG
    assert "attach_parasite" in call["code"]


def test_only_checkpoint_copies_open_refuses_the_call(ctx_and_client):
    ctx, client = ctx_and_client
    client.pick_result = {"image": None, "open": 2, "newest": 2, "checkpoint": False}
    response = ctx.run("TOOL", args={"image": None})
    assert response.ok is False
    assert response.error.type == "NoWorkingImage"
    assert client.tool_calls() == []


def test_no_open_images_lets_the_tool_report_it(ctx_and_client):
    ctx, client = ctx_and_client
    client.pick_result = {"image": None, "open": 0, "newest": 0, "checkpoint": False}
    ctx.run("TOOL", args={"image": None})
    assert client.tool_calls()[0]["args"]["image"] is None


def test_a_failed_resolution_is_returned_not_ignored(ctx_and_client):
    ctx, client = ctx_and_client
    client.pick_ok = False
    response = ctx.run("TOOL", args={"image": None})
    assert response.ok is False
    assert client.tool_calls() == []


def test_set_active_image_is_remembered_with_its_horizon(ctx_and_client):
    ctx, client = ctx_and_client
    client.pick_result = {"image": 5, "open": 9, "newest": 9, "checkpoint": False}
    response = ctx.set_active_image(5)
    assert response.ok
    assert response.result == {"active_image_id": 5}
    assert ctx.active_image == 5
    client.calls.clear()
    ctx.run("TOOL", args={"image": None})
    assert client.picks()[0]["args"]["want"] == 5
    assert client.picks()[0]["args"]["horizon"] == 9


def test_set_active_image_refuses_a_checkpoint_copy(ctx_and_client):
    ctx, client = ctx_and_client
    client.pick_result = {"image": 2, "open": 3, "newest": 5, "checkpoint": True}
    response = ctx.set_active_image(5)
    assert response.ok is False
    assert response.error.type == "CheckpointCopy"
    assert ctx.active_image is None


def test_set_active_image_passes_a_stale_handle_error_through(ctx_and_client):
    ctx, client = ctx_and_client
    client.active_reply = BridgeResponse(ok=False, error=BridgeError("StaleHandle", "gone"))
    response = ctx.set_active_image(99)
    assert response.ok is False
    assert response.error.type == "StaleHandle"
    assert client.picks() == []


def test_a_new_gimp_session_forgets_the_choice(ctx_and_client):
    ctx, client = ctx_and_client
    client.pick_result = {"image": 5, "open": 9, "newest": 9, "checkpoint": False}
    ctx.set_active_image(5)
    client.session = "s2"
    client.calls.clear()
    ctx.run("TOOL", args={"image": None})
    assert client.picks()[0]["args"]["want"] is None
    assert client.picks()[0]["args"]["horizon"] is None
    assert ctx.active_image is None
