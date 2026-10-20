from __future__ import annotations

from gimp_mcp.tools.print_dtf import _gang_report


def _envelope(result, ok=True, error=None):
    return {"ok": ok, "result": result, "stdout": "", "warnings": [], "error": error}


TOO_WIDE = {"path": "C:/art/banner.png",
            "error": "too large for the sheet: 9000 px wide, wider than the sheet's "
                     "printable width of 6525 px; it fits rotated (rotate=True)"}
UNREADABLE = {"path": "C:/art/missing.png", "error": "GLib.Error: could not open"}


def test_a_full_sheet_has_no_warnings():
    env = _gang_report(_envelope({"placed": 3, "skipped": [], "image": 7}))
    assert env["ok"] is True
    assert env["warnings"] == []


def test_each_skipped_design_becomes_a_warning():
    env = _gang_report(_envelope({"placed": 2, "skipped": [TOO_WIDE, UNREADABLE], "image": 7}))
    assert env["ok"] is True
    assert len(env["warnings"]) == 2
    assert "banner.png" in env["warnings"][0] and "too large" in env["warnings"][0]
    assert "missing.png" in env["warnings"][1]


def test_an_empty_sheet_is_refused_with_every_reason():
    env = _gang_report(_envelope({"placed": 0, "skipped": [TOO_WIDE, UNREADABLE],
                                  "refused": "none of the designs could be placed"}))
    assert env["ok"] is False
    assert env["error"]["type"] == "NothingPlaced"
    message = env["error"]["message"]
    assert "none of the designs could be placed" in message
    assert "banner.png" in message and "too large" in message
    assert "missing.png" in message
    assert env["result"]["skipped"] == [TOO_WIDE, UNREADABLE]
    assert len(env["warnings"]) == 2


def test_a_failed_save_keeps_the_reasons():
    env = _gang_report(_envelope({
        "placed": 1, "skipped": [UNREADABLE],
        "failed": {"type": "FileNotFoundError",
                   "message": "cannot save x.png: the folder C:/nope does not exist"}}))
    assert env["ok"] is False
    assert env["error"]["type"] == "FileNotFoundError"
    assert "C:/nope" in env["error"]["message"]
    assert "missing.png" in env["error"]["message"]
    assert env["result"]["skipped"] == [UNREADABLE]


def test_a_bridge_error_passes_through():
    error = {"type": "KeyError", "message": "out_path", "failing_line": None, "traceback": None}
    env = _envelope(None, ok=False, error=error)
    assert _gang_report(env) == env
