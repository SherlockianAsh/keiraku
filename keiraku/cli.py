"""Minimal CLI. `keiraku who` prints the live vessel table; `keiraku send`
writes one message to one vessel."""

from __future__ import annotations

import argparse
import os
import sys

from . import core


def _default_source() -> str:
    return os.environ.get("KEIRAKU_SOURCE", os.path.join(os.path.expanduser("~"), ".keiraku"))


def _write(text: str, err: bool = False) -> None:
    # Write UTF-8 bytes ourselves. print() uses the console codepage (cp1252 on
    # Windows) and raises on a name like "Fuin" spelt with its macron.
    stream = sys.stderr if err else sys.stdout
    stream.flush()
    stream.buffer.write((text + "\n").encode("utf-8"))
    stream.buffer.flush()


def cmd_init(args) -> int:
    root = core.init_source(args.source)
    _write(f"source ready: {root}")
    return 0


def cmd_who(args) -> int:
    # Read without attaching. Looking at the list must not add you to it.
    try:
        peers = core.read_presence(args.source, include_stale=args.all)
    except core.NotASource as exc:
        _write(str(exc), err=True)
        return 1

    if not peers:
        _write("no live vessels")
        return 0

    width = max(len(p["vessel_id"]) for p in peers)
    for p in peers:
        alias = f" ({p['alias']})" if p.get("alias") else ""
        note = f"  - {p['note']}" if p.get("note") else ""
        _write(f"{p['vessel_id']:<{width}}  {p.get('status','?'):<7}  {p.get('cwd','')}{alias}{note}")
    return 0


def _match(records, ref: str) -> list:
    """Vessels named by `ref`: an exact vessel id first, else every alias match."""
    exact = [r for r in records if r["vessel_id"] == ref]
    return exact or [r for r in records if r.get("alias") == ref]


def _one(records, ref: str, role: str):
    """Resolve `ref` to exactly one vessel that has not ended, or explain why not.

    Quiet (stale) vessels count: an inbox is a mailbox, and an idle session
    that has not heartbeat for a while is still alive and will read it on its
    next turn. Stopped vessels do not: nobody will ever read their inbox.
    """
    found = _match(records, ref)
    if not found:
        return None, f"{role}: no vessel matches {ref!r}"
    open_ = [r for r in found if r.get("status") != "stopped"]
    if not open_:
        return None, f"{role}: {ref!r} has ended (status stopped)"
    if len(open_) > 1:
        lines = [f"{role}: {ref!r} is ambiguous; use one of these vessel ids:"]
        for r in open_:
            lines.append(f"  {r['vessel_id']}  {r.get('status','?')}  last seen {r.get('heartbeat_at','?')}")
        return None, "\n".join(lines)
    return open_[0], None


def cmd_send(args) -> int:
    # Never attach: sending must not rewrite the sender's presence record.
    try:
        records = core.read_presence(args.source, include_stale=True)
    except core.NotASource as exc:
        _write(str(exc), err=True)
        return 1

    sender, err = _one(records, args.sender, "from")
    if err:
        _write(err, err=True)
        return 1
    target, err = _one(records, args.to, "to")
    if err:
        _write(err, err=True)
        return 1
    if sender["vessel_id"] == target["vessel_id"]:
        _write("cannot address yourself", err=True)
        return 1

    body = sys.stdin.read() if args.body == "-" else args.body
    msg_id = core.deliver(args.source, sender["vessel_id"], target["vessel_id"], body, args.summary)
    _write(f"sent {msg_id} to {target['vessel_id']}"
           + (f" ({target['alias']})" if target.get("alias") else ""))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="keiraku", description=__doc__)
    parser.add_argument("--source", default=_default_source(), help="source directory")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create a source").set_defaults(func=cmd_init)

    who = sub.add_parser("who", help="list live vessels")
    who.add_argument("--all", action="store_true", help="include stale vessels")
    who.set_defaults(func=cmd_who)

    send = sub.add_parser("send", help="write one message to one vessel")
    send.add_argument("--from", dest="sender", required=True, help="your vessel id or alias")
    send.add_argument("--to", required=True, help="recipient vessel id or alias")
    send.add_argument("--summary", help="one line shown before the body")
    send.add_argument("body", help="message text, or - to read it from stdin")
    send.set_defaults(func=cmd_send)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
