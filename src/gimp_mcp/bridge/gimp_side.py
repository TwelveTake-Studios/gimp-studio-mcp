#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GIMP-side bridge — runs INSIDE GIMP's Python (PyGObject), not via pip.

Loads as a no-argument ``Gimp.PDBProcType.PERSISTENT`` procedure → an "automatic"
extension that GIMP auto-starts on every launch (GUI *and* headless
``gimp-console``), with no menu click and no open image. Validated by the spike
on GIMP 3.0.4; re-verified on 3.2.4.

Responsibilities:
  - bind an ephemeral localhost socket, publish {host, port, token, pid} to an
    endpoint file, and require a token handshake;
  - run a GLib.MainLoop for the GIMP session;
  - service each request by MARSHALING the GIMP/PDB/GEGL work onto the main-loop
    thread via GLib.idle_add (socket worker threads do network I/O only — GIMP's
    libgimp wire is single-threaded; calling it off-thread races/deadlocks it);
  - capture stdout AND exceptions into the structured envelope so output is never
    dropped on error (the #1 pain of the reference tool);
  - keep ONE managed persistent namespace (deterministic seed, integer-ID image
    handles, two-tier reset).

DEPENDENCIES: stdlib + gi.repository ONLY (no venv access). Imports the sibling
``protocol`` module that install-plugin copies alongside this file.

This is the real bridge (NOT the throwaway spike under spike/).
"""
import os
import sys
import time
import json
import socket
import threading
import traceback
import contextlib
import io
import tempfile

import gi
gi.require_version("Gimp", "3.0")
from gi.repository import Gimp, GLib  # noqa: E402  (must follow gi.require_version)

# Import the sibling protocol module (copied next to this file by install-plugin).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import protocol as P  # noqa: E402  (must follow the sys.path.insert above)

__bridge_version__ = "0.4.1"  # +do_set_i18n (silence GIMP 3.x i18n load warnings); 0.4.0: +Gegl.init seed, +scratch reset

_LOGFILE = os.path.join(tempfile.gettempdir(), "gimp-mcp-bridge.log")


def log(msg: str) -> None:
    try:
        with open(_LOGFILE, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%H:%M:%S"), msg))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Image handle resolution (integer IDs, validity-checked)
# ---------------------------------------------------------------------------
def find_image(spec=None):
    """Resolve an image by id (int), basename (str), or None=active/first.

    Raises ValueError (-> StaleHandle structured error) instead of crashing on a
    dead handle: Gimp.Image.get_by_id returns None for an invalid id.
    """
    images = Gimp.get_images()
    if spec is None:
        if not images:
            raise ValueError("no images are open")
        return images[0]
    if isinstance(spec, int) or (isinstance(spec, str) and spec.isdigit()):
        img = Gimp.Image.get_by_id(int(spec))
        if img is None or not img.is_valid():
            raise ValueError("StaleHandle: no valid image with id %r" % spec)
        return img
    for im in images:
        f = im.get_file()
        if f is not None and f.get_basename() == spec:
            return im
    raise ValueError("no open image matching %r" % spec)


def _iter_layers(img):
    """Yield every layer in an image, recursing into layer groups (DFS)."""
    stack = list(img.get_layers())
    while stack:
        layer = stack.pop(0)
        yield layer
        try:
            if layer.is_group():
                stack[:0] = list(layer.get_children())
        except Exception:
            pass


def find_drawable(image=None, layer=None):
    """Resolve the drawable to operate on (companion to find_image).

    ``image`` selects the image (id/basename/None=active). ``layer`` selects a
    drawable within it: an integer id, a layer name (searched recursively through
    groups), or None. None -> the image's currently selected drawable (GIMP 3.0
    multi-select aware), else its first/top layer. Bad/dead handles raise
    ValueError -> a clean StaleHandle envelope rather than a crash.
    """
    img = find_image(image)
    if layer is not None:
        if isinstance(layer, int) or (isinstance(layer, str) and layer.isdigit()):
            it = Gimp.Item.get_by_id(int(layer))
            if it is None or not it.is_valid():
                raise ValueError("StaleHandle: no valid drawable with id %r" % layer)
            return it
        for lyr in _iter_layers(img):
            if lyr.get_name() == layer:
                return lyr
        raise ValueError("no layer named %r in image %s" % (layer, img.get_id()))
    sel = img.get_selected_drawables()
    if sel:
        return sel[0]
    layers = img.get_layers()
    if not layers:
        raise ValueError("image %s has no layers" % img.get_id())
    return layers[0]


# ---------------------------------------------------------------------------
# Managed persistent namespace
# ---------------------------------------------------------------------------
class Namespace:
    """One global namespace per bridge/session; deterministically seeded.

    Soft reset mutates the SAME dict in place (del user keys + re-seed) — never
    rebinds it, because exec() makes this dict the __globals__ of every function
    the agent defines and rebinding would orphan those closures.
    """

    def __init__(self):
        self.ns: dict = {}
        self._seed_keys: set = set()
        self.seed()

    def seed(self) -> None:
        ns = self.ns
        ns["gi"] = gi
        ns["Gimp"] = Gimp
        ns["gimp"] = Gimp
        ns["GLib"] = GLib
        ns["pdb"] = Gimp.get_pdb()
        ns["find_image"] = find_image
        ns["find_drawable"] = find_drawable
        # Optional modules — seed if importable so sessions don't re-import them.
        for modname, ver in (("Gegl", "0.4"), ("Gio", None),
                             ("GObject", None), ("Babl", "0.1")):
            try:
                if ver:
                    try:
                        gi.require_version(modname, ver)
                    except Exception:
                        pass
                mod = getattr(__import__("gi.repository", fromlist=[modname]), modname)
                ns[modname] = mod
            except Exception as e:
                log("seed: could not import %s: %s" % (modname, e))
        # Initialize GEGL's operation registry so introspection works headless
        # (Gegl.list_operations / Operation.get_key / list_properties return
        # empty/raise until this runs). Idempotent — safe to call every seed.
        if "Gegl" in ns:
            try:
                ns["Gegl"].init(None)
            except Exception as e:
                log("seed: Gegl.init failed: %s" % e)
        # GIMP-side quirk helpers (sibling module copied next to the bridge).
        try:
            import gimp_compat
            ns["compat"] = gimp_compat
        except Exception as e:
            log("seed: could not import gimp_compat: %s" % e)
        self._seed_keys = set(ns.keys())

    def reset_soft(self) -> dict:
        removed = [k for k in list(self.ns.keys()) if k not in self._seed_keys]
        for k in removed:
            del self.ns[k]
        self.seed()  # refresh seeded values IN PLACE (no rebind)
        return {"scope": "soft", "cleared_user_names": removed}

    def reset_scratch(self) -> dict:
        """SOFT reset + delete bridge-created SCRATCH images only.

        Guarded HARD reset: sweeps ONLY images whose ids the
        bridge itself tagged as scratch via the ``_gimpmcp_scratch`` provenance set
        (currently checkpoint snapshots from the safety tools). User-opened / agent
        working images are NEVER touched. Runs before the soft reset clears that set.
        """
        scratch = self.ns.get("_gimpmcp_scratch") or set()
        deleted, skipped = [], []
        for img_id in list(scratch):
            try:
                img = Gimp.Image.get_by_id(int(img_id))
            except Exception:
                img = None
            if img is None or not img.is_valid():
                continue  # already gone
            try:
                img.delete()
                deleted.append(int(img_id))
            except Exception:
                skipped.append(int(img_id))
        result = self.reset_soft()  # clears _gimpmcp_* user vars + re-seeds in place
        result["scope"] = "scratch"
        result["deleted_scratch_images"] = deleted
        result["skipped_scratch_images"] = skipped
        return result


# ---------------------------------------------------------------------------
# Code execution with stdout + exception capture (the core differentiator)
# ---------------------------------------------------------------------------
_EXEC_FILENAME = "<gimp-mcp-exec>"


def _failing_line(code: str) -> str | None:
    """Best-effort: the source line in the exec'd code where the exception arose."""
    tb = sys.exc_info()[2]
    lineno = None
    for frame, ln in traceback.walk_tb(tb):
        if frame.f_code.co_filename == _EXEC_FILENAME:
            lineno = ln
    if lineno is None:
        return None
    lines = code.splitlines()
    if 1 <= lineno <= len(lines):
        return lines[lineno - 1].strip()
    return None


def _jsonable(v):
    if v is None:
        return None
    try:
        json.dumps(v)
        return v
    except (TypeError, ValueError):
        return repr(v)


def run_code(code: str, ns: dict, args=None) -> dict:
    """Exec ``code`` in ``ns``; capture stdout AND any exception. Never drop stdout.

    ``args`` (a JSON-serializable dict) is injected as the ``args`` name before
    execution so tool code references parameters as DATA (injection-safe) rather
    than interpolating them into the code string.

    Convention: if the code sets a top-level ``_result`` variable, it is returned
    (JSON-coerced) as the envelope ``result``.
    """
    try:
        compiled = compile(code, _EXEC_FILENAME, "exec")
    except SyntaxError as e:
        return P.error_response(
            "SyntaxError", str(e),
            failing_line=(e.text or "").rstrip() if e.text else None,
            tb=traceback.format_exc(),
        )

    ns["args"] = args
    ns.pop("_result", None)  # don't let a previous call's result leak through
    buf = io.StringIO()
    err = None
    result = None
    with contextlib.redirect_stdout(buf):
        try:
            exec(compiled, ns)
            result = ns.get("_result")
        except BaseException as e:  # noqa: BLE001 — capture everything, never leak
            err = P.BridgeError(
                type=type(e).__name__,
                message=str(e),
                failing_line=_failing_line(code),
                traceback=traceback.format_exc(),
            )
    return P.BridgeResponse(
        ok=(err is None),
        result=_jsonable(result),
        stdout=buf.getvalue(),
        error=err,
    ).to_dict()


# ---------------------------------------------------------------------------
# The persistent extension
# ---------------------------------------------------------------------------
class BridgePlugIn(Gimp.PlugIn):

    PROC = "gimp-mcp-bridge"

    def __init__(self):
        super().__init__()
        self.token = None
        self.port = None
        self._srv = None
        self._loop = None
        self.namespace = None
        self.mode = os.environ.get(P.ENV_MODE, "unknown")

    # ---- registration -----------------------------------------------------
    def do_set_i18n(self, procname):
        # The bridge ships no translation catalog; returning False tells GIMP 3.x
        # not to look for one (silences the per-procedure "catalog directory does
        # not exist" warning logged on every plug-in load). Upstream PR #20 parity.
        return False

    def do_query_procedures(self):
        log("do_query_procedures")
        return [self.PROC]

    def do_create_procedure(self, name):
        log("do_create_procedure: " + name)
        proc = Gimp.Procedure.new(self, name, Gimp.PDBProcType.PERSISTENT,
                                  self.run, None)
        proc.set_documentation(
            "gimp-mcp bridge",
            "Persistent localhost bridge for the gimp-mcp MCP server",
            name,
        )
        # NO add_menu_path, NO arguments -> "automatic" auto-start extension.
        return proc

    # ---- run (auto-started entry point) -----------------------------------
    def run(self, *args):
        procedure = args[0]
        try:
            if os.environ.get(P.ENV_DISABLED):
                log("disabled via %s — acking, not serving" % P.ENV_DISABLED)
                procedure.persistent_ready()
                return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS,
                                                   GLib.Error())
            self.namespace = Namespace()
            self.token = P.new_token()
            self._start_server()
            procedure.persistent_ready()        # or the GIMP core locks up
            if hasattr(self, "persistent_enable"):
                self.persistent_enable()        # pump core msgs while in MainLoop
            self._loop = GLib.MainLoop()
            log("entering GLib.MainLoop (port=%s)" % self.port)
            self._loop.run()
            log("MainLoop exited")
        except Exception:
            log("run() EXC:\n" + traceback.format_exc())
        finally:
            self._cleanup()
        return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())

    def _stop(self):
        log("stop requested -> quitting MainLoop")
        try:
            self._loop.quit()
        except Exception:
            pass
        return False

    # ---- socket server (worker threads: network I/O ONLY, never GIMP) ------
    def _start_server(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", int(os.environ.get(P.ENV_PORT, "0"))))
        srv.listen(8)
        self._srv = srv
        self.port = srv.getsockname()[1]
        self._write_endpoint()
        threading.Thread(target=self._accept_loop, name="mcp-accept",
                         daemon=True).start()
        log("listening on 127.0.0.1:%d" % self.port)

    def _endpoint_path(self) -> str:
        p = os.environ.get(P.ENV_ENDPOINT)
        if p:
            return p
        try:
            return os.path.join(Gimp.directory(), P.ENDPOINT_FILENAME)
        except Exception:
            return P.default_endpoint_file()

    def _write_endpoint(self):
        info = {
            "host": "127.0.0.1",
            "port": self.port,
            "token": self.token,
            "pid": os.getpid(),
            "protocol": P.PROTOCOL_VERSION,
            "bridge_version": __bridge_version__,
            "mode": self.mode,
            "started_at": time.time(),
        }
        self._endpoint_file = self._endpoint_path()
        P.write_endpoint_file(self._endpoint_file, info)
        log("endpoint file -> " + self._endpoint_file)

    def _cleanup(self):
        try:
            if getattr(self, "_endpoint_file", None) and os.path.exists(self._endpoint_file):
                os.remove(self._endpoint_file)
        except Exception:
            pass

    def _accept_loop(self):
        while True:
            try:
                conn, _addr = self._srv.accept()
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn,),
                             name="mcp-client", daemon=True).start()

    def _handle(self, conn):
        authed = False
        try:
            conn.settimeout(None)
            while True:
                msg = P.recv_message(conn)
                if msg is None:
                    break
                if not authed:
                    if msg.get("op") == P.OP_AUTH and P.tokens_equal(msg.get("token"), self.token):
                        authed = True
                        P.send_message(conn, P.ok_response(
                            {"authenticated": True,
                             "protocol": P.PROTOCOL_VERSION,
                             "bridge_version": __bridge_version__}))
                        continue
                    P.send_message(conn, P.error_response(
                        "auth_required", "valid token required as first frame"))
                    break
                P.send_message(conn, self._handle_request(msg))
        except Exception:
            log("client handler EXC:\n" + traceback.format_exc())
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # ---- request handling: marshal onto the main-loop thread --------------
    def _handle_request(self, msg: dict) -> dict:
        timeout = msg.get("timeout") or 120.0
        box: dict = {}
        done = threading.Event()

        def job():
            try:
                box["resp"] = self._dispatch(msg)
            except BaseException as e:  # noqa: BLE001
                box["resp"] = P.error_response(
                    type(e).__name__, str(e), tb=traceback.format_exc())
            finally:
                done.set()
            return False  # idle_add: run once

        GLib.idle_add(job)
        if not done.wait(timeout):
            # The op is still running on the main thread; we cannot preempt a
            # blocked synchronous PDB call, so we just report a timeout here.
            return P.error_response(
                "Timeout", "operation exceeded %ss on the GIMP main thread" % timeout)
        return box["resp"]

    def _dispatch(self, msg: dict) -> dict:
        """Runs on the GIMP main-loop thread — safe to call the PDB/GEGL here."""
        op = msg.get("op")
        payload = msg.get("payload") or {}
        if op == P.OP_PING:
            return P.ok_response({"pong": True,
                                  "thread": threading.current_thread().name})
        if op == P.OP_INFO:
            return P.ok_response(self._info())
        if op == P.OP_EXEC:
            return self._exec_op(payload, msg)
        if op == P.OP_LIST_IMAGES:
            return P.ok_response(self._list_images())
        if op == P.OP_SET_ACTIVE:
            return self._set_active(payload)
        if op == P.OP_RESET:
            return self._reset(payload)
        if op == P.OP_SHUTDOWN:
            GLib.idle_add(self._stop)
            return P.ok_response({"shutting_down": True})
        return P.error_response("unknown_op", "unknown op %r" % op)

    # ---- ops --------------------------------------------------------------
    def _exec_op(self, payload: dict, msg: dict) -> dict:
        code = payload.get("code", "")
        img = None
        if msg.get("undo_group", True):
            try:
                img = find_image(payload.get("image"))
            except Exception:
                img = None  # no image to group; exec still runs
        if img is not None:
            img.undo_group_start()
        try:
            return run_code(code, self.namespace.ns, payload.get("args"))
        finally:
            # Only close the group if the image still exists: exec'd code may have
            # deleted it (e.g. cleaning up scratch/duplicate images). Calling
            # undo_group_end on a deleted image makes GIMP surface a PDB error
            # ("invalid ID for argument 'image'") to its console even though we
            # catch the Python exception — is_valid() avoids triggering it at all.
            if img is not None:
                try:
                    if img.is_valid():
                        img.undo_group_end()
                except Exception:
                    pass

    def _info(self) -> dict:
        return {
            "gimp_version": Gimp.version(),
            "bridge_version": __bridge_version__,
            "protocol": P.PROTOCOL_VERSION,
            "pid": os.getpid(),
            "mode": self.mode,
            "num_images": len(Gimp.get_images()),
            "seeded_names": sorted(self.namespace._seed_keys),
        }

    def _list_images(self) -> dict:
        out = []
        for im in Gimp.get_images():
            f = im.get_file()
            out.append({
                "id": im.get_id(),
                "name": f.get_basename() if f is not None else None,
                "width": im.get_width(),
                "height": im.get_height(),
                "dirty": bool(im.is_dirty()),
            })
        return {"images": out}

    def _set_active(self, payload: dict) -> dict:
        try:
            img = find_image(payload.get("image"))
        except Exception as e:
            return P.error_response("StaleHandle", str(e))
        # Stash the chosen id so exec code can rely on a stable "active" notion.
        self.namespace.ns["_active_image_id"] = img.get_id()
        return P.ok_response({"active_image_id": img.get_id()})

    def _reset(self, payload: dict) -> dict:
        scope = payload.get("scope", "soft")
        if scope == "soft":
            return P.ok_response(self.namespace.reset_soft())
        if scope in ("scratch", "hard"):
            # Guarded HARD reset: soft + delete only provenance-tagged scratch images.
            return P.ok_response(self.namespace.reset_scratch())
        # "images" (sweep ALL bridge images) stays deferred — too blunt for live mode.
        return P.error_response(
            "not_implemented",
            "reset scope %r not implemented (use 'soft' or 'scratch')" % scope)


log("=== gimp_side module loaded (bridge v%s), calling Gimp.main ===" % __bridge_version__)
Gimp.main(BridgePlugIn.__gtype__, sys.argv)
