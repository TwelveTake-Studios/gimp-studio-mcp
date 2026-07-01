"""`gimp-mcp` console-script dispatcher.

Subcommands:
  serve              run the MCP server over stdio (what the MCP client invokes)
  install-plugin     copy the GIMP-side bridge into GIMP's plug-ins dir
  uninstall-plugin   remove it
  doctor             diagnose install + GIMP exe + bridge reachability
  status             print install status as JSON
  version            print versions
"""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__, install


def _cmd_install(args) -> int:
    try:
        res = install.install_plugin(gimp_dir=args.gimp_dir, force=args.force)
    except FileExistsError as e:
        print(str(e), file=sys.stderr)
        return 1
    print("Installed bridge -> %s (v%s)" % (res["installed_to"], res["bridge_version"]))
    print("Restart GIMP (or it loads on next launch). Then: gimp-mcp doctor")
    return 0


def _cmd_uninstall(args) -> int:
    res = install.uninstall_plugin(gimp_dir=args.gimp_dir)
    print("Removed %s" % res["path"] if res["removed"] else "Nothing to remove at %s" % res["path"])
    return 0


def _cmd_doctor(args) -> int:
    ok, lines = install.doctor(gimp_dir=args.gimp_dir, headless=args.headless)
    print("\n".join(lines))
    print("\n%s" % ("OK" if ok else "PROBLEMS FOUND"))
    return 0 if ok else 1


def _cmd_status(args) -> int:
    print(json.dumps(install.plugin_status(gimp_dir=args.gimp_dir), indent=2))
    return 0


def _cmd_version(_args) -> int:
    print("gimp-mcp %s" % __version__)
    print("bridge   %s" % (install.source_bridge_version() or "?"))
    return 0


def _cmd_serve(_args) -> int:
    from .server import main as serve_main
    serve_main()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gimp-mcp", description="GIMP 3.0 MCP server + bridge tooling")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("serve", help="run the MCP server over stdio").set_defaults(func=_cmd_serve)

    ip = sub.add_parser("install-plugin", help="install the GIMP-side bridge")
    ip.add_argument("--gimp-dir", help="GIMP 3.0 user dir (contains plug-ins/)")
    ip.add_argument("--force", action="store_true", help="overwrite an existing install")
    ip.set_defaults(func=_cmd_install)

    up = sub.add_parser("uninstall-plugin", help="remove the GIMP-side bridge")
    up.add_argument("--gimp-dir")
    up.set_defaults(func=_cmd_uninstall)

    dp = sub.add_parser("doctor", help="diagnose install + bridge reachability")
    dp.add_argument("--gimp-dir")
    dp.add_argument("--headless", action="store_true", help="also spawn a headless GIMP and round-trip")
    dp.set_defaults(func=_cmd_doctor)

    sp = sub.add_parser("status", help="print install status as JSON")
    sp.add_argument("--gimp-dir")
    sp.set_defaults(func=_cmd_status)

    sub.add_parser("version", help="print versions").set_defaults(func=_cmd_version)
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
