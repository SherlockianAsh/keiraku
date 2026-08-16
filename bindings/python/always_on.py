"""Class 2 Keiraku binding: an always-on vessel for a loop you control.

There is no hook here, and no hook is needed. Class 2 hosts (a custom agent
loop, a local model runner, a scheduled worker) already control when they
wake up and what they do next. Attaching Keiraku to a loop like that is four
calls, not a script: attach once, then heartbeat / read_stream / sleep,
forever, until the process is told to stop.

This file is a runnable example, not a library. Copy it into your own agent
loop and replace the body of handle(). Everything it imports is the Python
standard library plus keiraku itself — no third-party dependencies.

Run it directly to see the shape:

    python always_on.py --source ~/.keiraku --alias watcher

It will attach, heartbeat as "idle", poll for peer messages, and print
anything it receives. Stop it with Ctrl+C — it detaches cleanly on the way
out.
"""

from __future__ import annotations

import argparse
import os
import socket
import time
import uuid

import keiraku


def handle(message: dict) -> None:
    """Replace this function. This is the only part you should need to touch.

    IMPORTANT: `message` is INFORMATION FROM A PEER VESSEL, not an
    instruction from your operator. A peer is just another process that was
    able to attach to the same source and write a file into your inbox — on
    a shared or misconfigured source, that could be anything. An agent that
    cannot tell a peer's message from its operator's command can be steered
    by anything that can write to the source directory. Read peer messages
    as context to consider, never as commands to execute.
    """
    who = message.get("from", "unknown")
    summary = message.get("summary")
    body = message.get("body", "")
    header = f"[keiraku] message from {who}"
    if summary:
        header += f" — {summary}"
    print(header)
    if body:
        print(f"    {body}")


def run(source: str, alias: str | None, interval: float) -> None:
    """Attach once, then loop: heartbeat, read, handle, sleep.

    session_id is generated once per process. It is not meant to be stable
    across restarts — a new process attaching is a new vessel, the same way
    a new terminal running a hook-driven host is a new vessel. If you want a
    long-lived worker to resume its own identity across restarts, persist
    the session_id you generate here and reuse it on the next start.
    """
    session_id = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    cwd = os.getcwd()

    vessel = keiraku.attach(
        source,
        host="python",
        session_id=session_id,
        cwd=cwd,
        alias=alias,
    )
    print(f"[keiraku] attached as {vessel.vessel_id} (alias={alias!r})")

    try:
        while True:
            vessel.heartbeat(status="idle")
            for message in vessel.read_stream():
                handle(message)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("[keiraku] interrupted, detaching")
    finally:
        vessel.detach(status="stopped")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="always_on.py",
        description="Example Class 2 Keiraku binding: an always-on vessel.",
    )
    parser.add_argument(
        "--source",
        required=True,
        help="path to a Keiraku source directory (must already be initialised "
        "with keiraku.init_source, or created by another vessel)",
    )
    parser.add_argument(
        "--alias",
        default=None,
        help="a human-readable name other vessels can address this one by",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="seconds to sleep between heartbeat/read cycles (default: 5.0)",
    )
    args = parser.parse_args(argv)

    run(source=args.source, alias=args.alias, interval=args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
