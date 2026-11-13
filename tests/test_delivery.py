"""Delivery that survives a dead turn, and presence that does not lie.

Scar (12 Nov 15:59, 13 Nov 11:15): the prompt hook claimed a message and
printed it, but the turn was cut by a rate limit before the model saw it. The
message was already in _read, so it was lost. Now an injected message is
"delivered, unconfirmed" (stream/<vessel>/_unconfirmed/) and becomes read only
when a later event from the same vessel proves the turn ran.

Second scar: a cut turn never runs Stop, so presence stayed "busy" forever.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from keiraku import core, hooks

from tests.test_identify import _age, _run_cli


def _hook(source, event, sid="s1"):
    payload = {"session_id": sid, "cwd": "/p", "hook_event_name": event}
    return hooks.run(["--host", "claude-code", "--source", str(source), "--alias", "me"],
                     stdin_text=json.dumps(payload), env={})


def _setup(source):
    _hook(source, "SessionStart")
    me = core.read_presence(source)[0]["vessel_id"]
    peer = core.attach(source, host="codex", session_id="peer", cwd="/q", alias="peer")
    return me, peer


def _dirs(source, vid):
    base = Path(source) / "stream" / vid
    names = lambda d: sorted(p.name for p in d.glob("*.json")) if d.is_dir() else []
    return names(base), names(base / "_unconfirmed"), names(base / "_read")


def test_inject_then_stop_confirms(source):
    me, peer = _setup(source)
    core.deliver(source, peer.vessel_id, me, "hello", "hi")
    _, out = _hook(source, "UserPromptSubmit")
    assert "hello" in out and "may have been shown before" not in out
    pending, unconf, read = _dirs(source, me)
    assert pending == [] and len(unconf) == 1 and read == []
    _hook(source, "Stop")
    pending, unconf, read = _dirs(source, me)
    assert unconf == [] and len(read) == 1


def test_second_prompt_without_stop_reinjects_with_note(source):
    me, peer = _setup(source)
    core.deliver(source, peer.vessel_id, me, "hello")
    _hook(source, "UserPromptSubmit")
    _, out = _hook(source, "UserPromptSubmit")
    assert "hello" in out and "may have been shown before" in out
    assert len(_dirs(source, me)[1]) == 1


def test_session_start_without_stop_reinjects(source):
    me, peer = _setup(source)
    core.deliver(source, peer.vessel_id, me, "hello")
    _hook(source, "UserPromptSubmit")
    _, out = _hook(source, "SessionStart")
    # SessionStart does not inject today; it must not confirm either.
    assert len(_dirs(source, me)[1]) == 1
    _, out = _hook(source, "UserPromptSubmit")
    assert "hello" in out and "may have been shown before" in out


def test_confirmed_messages_are_not_reinjected(source):
    me, peer = _setup(source)
    core.deliver(source, peer.vessel_id, me, "hello")
    _hook(source, "UserPromptSubmit")
    _hook(source, "Stop")
    _, out = _hook(source, "UserPromptSubmit")
    assert out == ""


def test_mid_turn_event_confirms(source):
    me, peer = _setup(source)
    core.deliver(source, peer.vessel_id, me, "hello")
    _hook(source, "UserPromptSubmit")
    _, out = _hook(source, "PostToolUse")
    assert out == "" and _dirs(source, me)[1] == []
    assert len(_dirs(source, me)[2]) == 1


def test_old_unconfirmed_and_new_message_together(source):
    me, peer = _setup(source)
    core.deliver(source, peer.vessel_id, me, "older")
    _hook(source, "UserPromptSubmit")
    core.deliver(source, peer.vessel_id, me, "newer")
    _, out = _hook(source, "UserPromptSubmit")
    assert out.index("older") < out.index("newer")
    older_line = [l for l in out.splitlines() if "older" in l]
    assert older_line and "newer" not in older_line[0]
    assert out.count("may have been shown before") == 1
    # the note belongs to the older message, not the new one
    assert out.index("may have been shown before") < out.index("newer")
    _hook(source, "Stop")
    pending, unconf, read = _dirs(source, me)
    assert unconf == [] and len(read) == 2
    _, out = _hook(source, "UserPromptSubmit")
    assert out == ""


def test_files_already_in_read_stay_read(source):
    me, peer = _setup(source)
    rd = Path(source) / "stream" / me / "_read"
    rd.mkdir(parents=True)
    (rd / "0000000000001-aaa.json").write_text(json.dumps({"from": "x", "body": "old"}), encoding="utf-8")
    _, out = _hook(source, "UserPromptSubmit")
    assert out == ""


def test_read_stream_default_is_confirmed(source):
    me, peer = _setup(source)
    v = core.attach(source, host="claude-code", session_id="s1", cwd="/p")
    core.deliver(source, peer.vessel_id, v.vessel_id, "x")
    assert len(v.read_stream()) == 1
    assert _dirs(source, v.vessel_id)[2] != [] and _dirs(source, v.vessel_id)[1] == []


# -- defect 2 ------------------------------------------------------------------

def _two(source):
    a = core.attach(source, host="claude-code", session_id="s-a", cwd="D:/w", alias="meian-build")
    b = core.attach(source, host="codex", session_id="s-b", cwd="D:/w", alias="mei")
    return a, b


def test_old_busy_reads_as_unknown_without_rewriting(source):
    a, b = _two(source)
    b.heartbeat(status="busy")
    _age(source, b.vessel_id, 3600)
    p = Path(source) / "presence" / f"{b.vessel_id}.json"
    before = p.read_bytes()
    rec = [r for r in core.read_presence(source, include_stale=True) if r["vessel_id"] == b.vessel_id][0]
    assert rec["status"] == "unknown"
    assert p.read_bytes() == before


def test_fresh_busy_and_old_idle_are_unchanged(source):
    a, b = _two(source)
    b.heartbeat(status="busy")
    a.heartbeat(status="idle")
    _age(source, a.vessel_id, 3600)
    recs = {r["vessel_id"]: r["status"] for r in core.read_presence(source, include_stale=True)}
    assert recs[b.vessel_id] == "busy" and recs[a.vessel_id] == "idle"


def test_who_prints_unknown_for_old_busy(source):
    a, b = _two(source)
    b.heartbeat(status="busy")
    _age(source, b.vessel_id, 3600)
    r = _run_cli("--source", str(source), "who", "--all")
    assert r.returncode == 0
    assert b"unknown" in r.stdout and b"busy" not in r.stdout


def test_send_still_delivers_to_unknown_vessel(source):
    a, b = _two(source)
    b.heartbeat(status="busy")
    _age(source, b.vessel_id, 3600)
    r = _run_cli("--source", str(source), "send", "--from", "meian-build", "--to", "mei", "x")
    assert r.returncode == 0, r.stderr.decode()
    assert len(list((Path(source) / "stream" / b.vessel_id).glob("*.json"))) == 1
