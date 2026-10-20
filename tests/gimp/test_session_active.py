import pytest

pytestmark = pytest.mark.gimp

_DELETE_CODE = """
img = Gimp.Image.get_by_id(args["i"])
if img is not None and img.is_valid():
    img.delete()
"""


@pytest.fixture
def groups(load_group, gimp):
    yield load_group("document"), load_group("safety"), load_group("session")
    gimp.active_image = None
    gimp._horizon = None


def _new(document, gimp, name):
    r = document._new_image(gimp, 16, 12, name=name)
    assert r["ok"], r["error"]
    return r["result"]["image"]


def _target(document, gimp):
    r = document._get_metadata(gimp)
    assert r["ok"], r["error"]
    return r["result"]["image"]


def _delete(gimp, image_id):
    gimp.run(_DELETE_CODE, args={"i": image_id}, undo_group=False)


def test_set_active_image_steers_tools_that_omit_image(gimp, groups):
    document, _safety, _session = groups
    a = _new(document, gimp, "a")
    b = _new(document, gimp, "b")
    try:
        assert _target(document, gimp) == b
        chosen = gimp.set_active_image(a).to_dict()
        assert chosen["ok"], chosen["error"]
        assert chosen["result"] == {"active_image_id": a}
        assert _target(document, gimp) == a
    finally:
        _delete(gimp, a)
        _delete(gimp, b)


def test_a_checkpoint_copy_never_becomes_the_target(gimp, groups):
    document, safety, _session = groups
    a = _new(document, gimp, "a")
    try:
        cp = safety._checkpoint(gimp, a)
        assert cp["ok"], cp["error"]
        copy = cp["result"]["snapshot_image_id"]
        assert copy > a
        assert _target(document, gimp) == a
        refused = gimp.set_active_image(copy).to_dict()
        assert refused["ok"] is False
        assert refused["error"]["type"] == "CheckpointCopy"
        assert _target(document, gimp) == a
    finally:
        _delete(gimp, a)


def test_a_checkpoint_copy_stays_excluded_after_a_namespace_reset(gimp, groups):
    document, safety, _session = groups
    a = _new(document, gimp, "a")
    try:
        cp = safety._checkpoint(gimp, a)
        assert cp["ok"], cp["error"]
        copy = cp["result"]["snapshot_image_id"]
        reset = gimp.call("reset_namespace", "soft").to_dict()
        assert reset["ok"], reset["error"]
        assert _target(document, gimp) == a
        _delete(gimp, copy)
    finally:
        _delete(gimp, a)


def test_an_image_opened_after_the_choice_takes_over(gimp, groups):
    document, _safety, _session = groups
    a = _new(document, gimp, "a")
    later = None
    try:
        assert gimp.set_active_image(a).ok
        later = _new(document, gimp, "later")
        assert _target(document, gimp) == later
        _delete(gimp, later)
        later = None
        assert _target(document, gimp) == a
    finally:
        _delete(gimp, a)
        if later is not None:
            _delete(gimp, later)


def test_a_closed_choice_falls_back_to_the_first_working_image(gimp, groups):
    document, safety, _session = groups
    a = _new(document, gimp, "a")
    f = _new(document, gimp, "f")
    copy = None
    try:
        cp = safety._checkpoint(gimp, f)
        assert cp["ok"], cp["error"]
        copy = cp["result"]["snapshot_image_id"]
        assert gimp.set_active_image(a).ok
        _delete(gimp, a)
        assert _target(document, gimp) == f
    finally:
        _delete(gimp, f)
        if copy is not None:
            _delete(gimp, copy)


def test_set_active_image_rejects_an_unknown_image(gimp, groups):
    r = gimp.set_active_image(987654).to_dict()
    assert r["ok"] is False
    assert r["error"]["type"] == "StaleHandle"
