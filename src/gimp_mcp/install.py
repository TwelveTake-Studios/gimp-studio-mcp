"""Plug-in installation + diagnostics for the two-interpreter setup.

The MCP server is pip-installed into an external venv; the GIMP-side bridge must
live in GIMP's plug-ins dir and run under GIMP's bundled Python. So the bridge
files are COPIED (not imported) from this package into GIMP. GIMP 3.0 requires a
plug-in at ``plug-ins/<name>/<name>.py`` (folder name == file name), executable
on Linux/macOS.
"""
from __future__ import annotations

import os
import re
import shutil

from .bridge import protocol as P  # stdlib-only; gives us gimp_user_dir() + the bridge dir

PLUGIN_FOLDER = "gimp-mcp-bridge"  # folder AND file stem (must match — GIMP rule)
_BRIDGE_DIR = os.path.dirname(os.path.abspath(P.__file__))
_BRIDGE_FILES = ("gimp_side.py", "protocol.py", "gimp_compat.py")  # copied into plug-in dir


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------
def resolve_gimp_dir(explicit: str | None = None) -> str:
    """The GIMP 3.0 per-user config dir (the one that contains plug-ins/)."""
    if explicit:
        return explicit
    # GIMP3_DIRECTORY relocates the whole personal GIMP dir (legacy but honored).
    env = os.environ.get("GIMP3_DIRECTORY")
    if env:
        return env
    return P.gimp_user_dir()


def plugins_dir(explicit_gimp_dir: str | None = None) -> str:
    return os.path.join(resolve_gimp_dir(explicit_gimp_dir), "plug-ins")


def _target_paths(explicit_gimp_dir: str | None = None):
    dst = os.path.join(plugins_dir(explicit_gimp_dir), PLUGIN_FOLDER)
    main = os.path.join(dst, PLUGIN_FOLDER + ".py")
    return dst, main


def _bridge_version(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            m = re.search(r"__bridge_version__\s*=\s*['\"]([^'\"]+)['\"]", f.read())
            return m.group(1) if m else None
    except OSError:
        return None


def source_bridge_version() -> str | None:
    return _bridge_version(os.path.join(_BRIDGE_DIR, "gimp_side.py"))


# ---------------------------------------------------------------------------
# Install / uninstall
# ---------------------------------------------------------------------------
def install_plugin(gimp_dir: str | None = None, force: bool = False) -> dict:
    dst, main = _target_paths(gimp_dir)
    if os.path.exists(main) and not force:
        raise FileExistsError(
            "bridge already installed at %s (use --force to overwrite)" % main)
    os.makedirs(dst, exist_ok=True)
    # main file must be named <folder>.py
    shutil.copyfile(os.path.join(_BRIDGE_DIR, "gimp_side.py"), main)
    # sibling modules the bridge imports
    for name in _BRIDGE_FILES:
        if name == "gimp_side.py":
            continue
        shutil.copyfile(os.path.join(_BRIDGE_DIR, name), os.path.join(dst, name))
    if os.name == "posix":
        os.chmod(main, 0o755)  # GIMP ignores non-executable plug-ins on Unix
    return {
        "installed_to": main,
        "bridge_version": source_bridge_version(),
        "executable_bit_set": os.name == "posix",
    }


def uninstall_plugin(gimp_dir: str | None = None) -> dict:
    dst, _main = _target_paths(gimp_dir)
    existed = os.path.isdir(dst)
    if existed:
        shutil.rmtree(dst, ignore_errors=True)
    return {"removed": existed, "path": dst}


def plugin_status(gimp_dir: str | None = None) -> dict:
    dst, main = _target_paths(gimp_dir)
    installed = os.path.isfile(main)
    return {
        "plugins_dir": plugins_dir(gimp_dir),
        "target": main,
        "installed": installed,
        "executable": (os.access(main, os.X_OK) if installed and os.name == "posix" else None),
        "installed_version": _bridge_version(main) if installed else None,
        "source_version": source_bridge_version(),
    }


# ---------------------------------------------------------------------------
# Doctor
# ---------------------------------------------------------------------------
def doctor(gimp_dir: str | None = None, headless: bool = False) -> tuple[bool, list[str]]:
    """Return (ok, report_lines). Checks install, GIMP exe, and bridge reachability."""
    lines: list[str] = []
    ok = True

    st = plugin_status(gimp_dir)
    lines.append("plug-ins dir : %s" % st["plugins_dir"])
    if st["installed"]:
        lines.append("bridge       : INSTALLED (%s) v%s" % (st["target"], st["installed_version"]))
        if os.name == "posix" and st["executable"] is False:
            ok = False
            lines.append("  ! NOT executable — GIMP will ignore it. Re-run install-plugin.")
        if st["installed_version"] != st["source_version"]:
            lines.append("  ! version mismatch: installed v%s vs package v%s — run "
                         "`gimp-mcp install-plugin --force`"
                         % (st["installed_version"], st["source_version"]))
    else:
        ok = False
        lines.append("bridge       : NOT INSTALLED — run `gimp-mcp install-plugin`")

    # GIMP executable
    from .bridge import launcher
    try:
        exe = launcher.find_gimp_console()
        lines.append("gimp-console : %s" % exe)
    except FileNotFoundError as e:
        lines.append("gimp-console : NOT FOUND (%s)" % e)

    # Live reachability
    endpoint = P.default_endpoint_file()
    if os.path.exists(endpoint):
        try:
            client = launcher.attach_live(timeout=5.0)
            info = client.info()
            client.close()
            if info.ok:
                lines.append("live bridge  : REACHABLE — GIMP %s, bridge v%s (pid %s)"
                             % (info.result.get("gimp_version"),
                                info.result.get("bridge_version"),
                                info.result.get("pid")))
            else:
                ok = False
                lines.append("live bridge  : connected but info failed: %s" % info.error)
        except Exception as e:  # noqa: BLE001
            ok = False
            lines.append("live bridge  : endpoint present but UNREACHABLE (%s)" % e)
    else:
        lines.append("live bridge  : none (no endpoint at %s - start GIMP to test live)" % endpoint)

    # Optional headless round-trip
    if headless:
        try:
            hg = launcher.launch_headless()
            info = hg.client.info()
            hg.shutdown()
            if info.ok:
                lines.append("headless     : OK - spawned GIMP %s, round-trip succeeded"
                             % info.result.get("gimp_version"))
            else:
                ok = False
                lines.append("headless     : spawned but info failed: %s" % info.error)
        except Exception as e:  # noqa: BLE001
            ok = False
            lines.append("headless     : FAILED to spawn/connect (%s)" % e)

    return ok, lines
