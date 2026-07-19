"""Group M — Safety / workflow.

Snapshot/rollback primitives plus interactive-undo passthroughs. Snapshots are
real GIMP image duplicates kept in the persistent namespace so they survive
across calls; their ids are also registered as bridge "scratch" so a later HARD
reset can reap them.

Tools:
  - checkpoint           snapshot an image before risky ops
  - restore              roll an image back to a checkpoint
  - list_checkpoints     enumerate live checkpoints
  - undo_group_begin / undo_group_end
  - undo / redo

Persistent-state convention (a later bridge HARD-reset depends on these EXACT
names): checkpoints live in globals()["_gimpmcp_checkpoints"] (cid -> snapshot
image id) and every bridge-created scratch image id is in
globals()["_gimpmcp_scratch"].
"""
from __future__ import annotations

from gimp_mcp.bridge.protocol import unsupported_response

# checkpoint: duplicate the target image; stash the dup's id under a fresh int
# cid in the persistent checkpoint map AND in the scratch set. A duplicate is a
# real, independent snapshot (it also opens a hidden image in live mode — fine
# for v1). undo_group=False: we are creating a new image, not editing one.
_CHECKPOINT_CODE = """
img = find_image(args.get("image"))
cps = globals().setdefault("_gimpmcp_checkpoints", {})
scratch = globals().setdefault("_gimpmcp_scratch", set())
_next = globals().get("_gimpmcp_cp_counter", 0) + 1
globals()["_gimpmcp_cp_counter"] = _next
dup = img.duplicate()
_dup_id = dup.get_id()
# Record BOTH the snapshot AND the origin image id so restore() can target the
# image the checkpoint was taken from — never the (possibly unrelated) active image.
cps[_next] = {"snapshot": _dup_id, "origin": img.get_id()}
scratch.add(_dup_id)
_result = {
    "checkpoint_id": _next,
    "image": img.get_id(),
    "label": args.get("label"),
    "snapshot_image_id": _dup_id,
}
"""

# restore: locate the snapshot image stored for cid, then rebuild the target
# image's layer stack from copies of the snapshot's layers. Robust-but-simple:
# delete the target's current layers, then insert a copy of each snapshot layer
# (top-to-bottom). The snapshot itself is left intact for re-restoring. restore
# DEFAULTS the target to the ORIGIN image recorded at checkpoint time — never the
# active image (clearing the wrong image's layers is silent data loss). An explicit
# image= that differs from the origin is allowed but flagged with a warning.
#
# GIMP-3.0.4 quirk (verified headless): Layer.copy() produces a layer bound to
# the SOURCE image's context — inserting it into a DIFFERENT image silently
# no-ops (insert_layer returns without error but adds nothing). The correct
# cross-image copy is Gimp.Layer.new_from_drawable(src_layer, target_image),
# which yields a layer owned by `target` that inserts cleanly.
_RESTORE_CODE = """
cid = args["checkpoint_id"]
cps = globals().setdefault("_gimpmcp_checkpoints", {})
if cid not in cps:
    raise ValueError("no checkpoint with id %r" % (cid,))
entry = cps[cid]
# Back-compat: pre-fix checkpoints stored just the snapshot id (a bare int).
if isinstance(entry, dict):
    snap_id = entry["snapshot"]
    origin_id = entry.get("origin")
else:
    snap_id = entry
    origin_id = None
snap = Gimp.Image.get_by_id(snap_id)
if snap is None or not snap.is_valid():
    raise ValueError("checkpoint %r snapshot image %r no longer exists" % (cid, snap_id))
# Default target = the ORIGIN image, NOT the active image. Restore wipes the whole
# layer stack, so defaulting to whatever happens to be active is a data-loss trap.
req = args.get("image")
warning = None
if req is None:
    if origin_id is None:
        target = find_image(None)  # legacy checkpoint w/o origin
        warning = "legacy checkpoint had no origin; restored into active image %d" % target.get_id()
    else:
        target = Gimp.Image.get_by_id(origin_id)
        if target is None or not target.is_valid():
            raise ValueError(
                "checkpoint %r origin image %r no longer exists; pass image= to restore elsewhere"
                % (cid, origin_id))
else:
    target = find_image(req)
    if origin_id is not None and target.get_id() != origin_id:
        warning = ("restoring into image %d, but checkpoint %r was taken from image %d"
                   % (target.get_id(), cid, origin_id))
# Clear the target's existing layers, then re-insert copies of the snapshot's
# (index 0 = top). new_from_drawable yields a layer owned by `target` (a bare
# Layer.copy() binds to the source image and silently no-ops on insert — GIMP 3.0 quirk).
for lyr in list(target.get_layers()):
    target.remove_layer(lyr)
restored = 0
for i, src in enumerate(snap.get_layers()):
    cp = Gimp.Layer.new_from_drawable(src, target)
    cp.set_name(src.get_name())
    target.insert_layer(cp, None, i)
    restored += 1
_result = {
    "restored": True,
    "checkpoint_id": cid,
    "image": target.get_id(),
    "layers_restored": restored,
    "warning": warning,
}
"""

# list_checkpoints: report each stored checkpoint with whether its snapshot image
# is still a valid (un-deleted) image.
_LIST_CHECKPOINTS_CODE = """
cps = globals().setdefault("_gimpmcp_checkpoints", {})
out = []
for cid, entry in cps.items():
    if isinstance(entry, dict):
        snap_id = entry["snapshot"]
        origin_id = entry.get("origin")
    else:
        snap_id = entry
        origin_id = None
    im = Gimp.Image.get_by_id(snap_id)
    out.append({
        "id": cid,
        "image_id": snap_id,
        "origin_image_id": origin_id,
        "valid": bool(im is not None and im.is_valid()),
    })
out.sort(key=lambda c: c["id"])
_result = {"checkpoints": out}
"""

_UNDO_GROUP_BEGIN_CODE = """
img = find_image(args.get("image"))
img.undo_group_start()
_result = {"image": img.get_id(), "undo_group": "begin"}
"""

_UNDO_GROUP_END_CODE = """
img = find_image(args.get("image"))
img.undo_group_end()
_result = {"image": img.get_id(), "undo_group": "end"}
"""

# undo/redo: GIMP 3.0 undo is interactive (driven by the GUI), not reliably
# scriptable. Try the PDB edit-undo/redo procs if present; otherwise report
# unsupported and steer the agent to checkpoints. We never raise on absence.
_UNDO_CODE = """
img = find_image(args.get("image"))
_op = args["op"]  # "undo" or "redo"
_proc = "gimp-image-undo" if _op == "undo" else "gimp-image-redo"
proc = pdb.lookup_procedure(_proc)
if proc is None:
    _result = {
        "supported": False,
        "op": _op,
        "image": img.get_id(),
        "note": "use checkpoints; GIMP undo is interactive",
    }
else:
    try:
        cfg = proc.create_config()
        cfg.set_property("image", img)
        res = proc.run(cfg)
        status = res.index(0)
        _result = {
            "supported": True,
            "op": _op,
            "image": img.get_id(),
            "status": str(status),
        }
    except Exception as e:
        _result = {
            "supported": False,
            "op": _op,
            "image": img.get_id(),
            "error": str(e),
            "note": "use checkpoints; GIMP undo is interactive",
        }
"""


def _checkpoint(ctx, image=None, label=None):
    return ctx.run(_CHECKPOINT_CODE, args={"image": image, "label": label},
                   undo_group=False).to_dict()


def _restore(ctx, checkpoint_id, image=None):
    # Destructive: we rebuild the target image's layer stack. Pass image both as
    # the run target and inside args, and wrap in one undo group.
    return ctx.run(_RESTORE_CODE,
                   args={"checkpoint_id": checkpoint_id, "image": image},
                   image=image, undo_group=True).to_dict()


def _list_checkpoints(ctx):
    return ctx.run(_LIST_CHECKPOINTS_CODE, args={}, undo_group=False).to_dict()


def _undo_group_begin(ctx, image=None):
    return ctx.run(_UNDO_GROUP_BEGIN_CODE, args={"image": image},
                   undo_group=False).to_dict()


def _undo_group_end(ctx, image=None):
    return ctx.run(_UNDO_GROUP_END_CODE, args={"image": image},
                   undo_group=False).to_dict()


# Two paths reach supported:False here — the PDB proc is absent (every GIMP 3.x),
# or it exists but raised. The message must be true of BOTH, so it states the
# OUTCOME rather than diagnosing a cause; `result.note`/`result.error` carry the
# specifics.
_UNDO_UNSUPPORTED_MSG = (
    "No scriptable {op} is available on this GIMP — undo is driven by the "
    "interactive GUI stack — so NOTHING was rolled back. Use checkpoint() "
    "before risky edits and restore() to roll back."
)


def _undo(ctx, image=None):
    env = ctx.run(_UNDO_CODE, args={"image": image, "op": "undo"},
                  undo_group=False).to_dict()
    return unsupported_response(env, _UNDO_UNSUPPORTED_MSG.format(op="undo"))


def _redo(ctx, image=None):
    env = ctx.run(_UNDO_CODE, args={"image": image, "op": "redo"},
                  undo_group=False).to_dict()
    return unsupported_response(env, _UNDO_UNSUPPORTED_MSG.format(op="redo"))


def register(mcp, ctx) -> None:

    @mcp.tool(name="checkpoint")
    def checkpoint(image: int | str | None = None,
                   label: str | None = None) -> dict:
        """Snapshot an image (a real duplicate) so you can restore() it later.
        Returns a numeric checkpoint_id. `image` = id/basename, or omit for active."""
        return _checkpoint(ctx, image, label)

    @mcp.tool(name="restore")
    def restore(checkpoint_id: int, image: int | str | None = None) -> dict:
        """Roll an image back to a checkpoint by rebuilding its layer stack from the
        snapshot. The checkpoint stays usable. `image` = which image to restore into."""
        return _restore(ctx, checkpoint_id, image)

    @mcp.tool(name="list_checkpoints")
    def list_checkpoints() -> dict:
        """List live checkpoints: id, snapshot image_id, and whether it's still valid."""
        return _list_checkpoints(ctx)

    @mcp.tool(name="undo_group_begin")
    def undo_group_begin(image: int | str | None = None) -> dict:
        """Begin an undo group on an image: subsequent edits collapse into one undo
        step. Pair with undo_group_end. `image` = id/basename, or omit for active."""
        return _undo_group_begin(ctx, image)

    @mcp.tool(name="undo_group_end")
    def undo_group_end(image: int | str | None = None) -> dict:
        """End the undo group started by undo_group_begin on this image."""
        return _undo_group_end(ctx, image)

    @mcp.tool(name="undo")
    def undo(image: int | str | None = None) -> dict:
        """Undo the last operation — only if GIMP exposes scriptable undo, which
        GIMP 3.x does NOT (undo is interactive), so this returns ok=false with
        {supported: false} and rolls nothing back. Use checkpoint()/restore()."""
        return _undo(ctx, image)

    @mcp.tool(name="redo")
    def redo(image: int | str | None = None) -> dict:
        """Redo the last undone operation — only if GIMP exposes scriptable redo,
        which GIMP 3.x does NOT, so this returns ok=false with {supported: false}
        and redoes nothing. Use checkpoint()/restore() for reliable rollback."""
        return _redo(ctx, image)
