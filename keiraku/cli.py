"""Minimal CLI. `keiraku who` prints the live vessel table."""

from __future__ import annotations

import argparse
import os
import sys

from . import core


def _default_source() -> str:
    return os.environ.get("KEIRAKU_SOURCE", os.path.join(os.path.expanduser("~"), ".keiraku"))


def cmd_init(args) -> int:
    root = core.init_source(args.source)
    print(f"source ready: {root}")
    return 0


def cmd_who(args) -> int:
    try:
        vessel = core.attach(
            args.source,
            host="cli",
            session_id=f"cli-{os.getpid()}",
            cwd=os.getcwd(),
        )
    except core.NotASource as exc:
        print(exc, file=sys.stderr)
        return 1

    peers = vessel.read_presence(include_self=False, include_stale=args.all)
    if not peers:
        print("no live vessels")
        return 0

    width = max(len(p["vessel_id"]) for p in peers)
    for p in peers:
        alias = f" ({p['alias']})" if p.get("alias") else ""
        note = f"  — {p['note']}" if p.get("note") else ""
        print(f"{p['vessel_id']:<{width}}  {p.get('status','?'):<7}  {p.get('cwd','')}{alias}{note}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="keiraku", description=__doc__)
    parser.add_argument("--source", default=_default_source(), help="source directory")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create a source").set_defaults(func=cmd_init)

    who = sub.add_parser("who", help="list live vessels")
    who.add_argument("--all", action="store_true", help="include stale vessels")
    who.set_defaults(func=cmd_who)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
