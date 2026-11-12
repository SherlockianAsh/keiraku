"""Telling sessions apart, and sending to one of them.

The scar (12 Nov 2026): two Claude Code vessels sat in the same folder with no
alias, told apart only by a hash. A Codex peer replied to the wrong one, and a
session that knew nothing of the work claimed the report. Separately, an idle
Codex vessel fell out of `who` after five minutes, so nothing could be sent to
it although it was alive. These tests pin the fixes:

- a Claude Code vessel takes its session's name from Claude's own registry;
- `keiraku send` addresses one vessel, refuses ambiguity, keeps the sender's
  presence untouched, and still reaches a quiet (stale) vessel, never a
  stopped one.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import keiraku
from keiraku import core, hooks


def _registry(tmp_path, entries):
    d = tmp_path / "claude-sessions"
    d.mkdir()
    for i, (sid, name) in enumerate(entries):
        (d / f"{1000 + i}.json").write_text(
            json.dumps({"pid": 1000 + i, "sessionId": sid, "name": name}),
            encoding="utf-8",
        )
    return d


def _run_cli(*args, env_extra=None):
    env = dict(os.environ)
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, "-m", "keiraku", *args],
        capture_output=True, env=env,
    )


def _age(source, vessel_id, seconds):
    p = Path(source) / "presence" / f"{vessel_id}.json"
    rec = json.loads(p.read_text(encoding="utf-8"))
    old = time.gmtime(time.time() - seconds)
    rec["heartbeat_at"] = time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", old)
    p.write_text(json.dumps(rec), encoding="utf-8")


# -- the Claude session name ---------------------------------------------------

def test_claude_session_name_found_in_registry(tmp_path):
    reg = _registry(tmp_path, [("aaa", "Fuin Mei"), ("bbb", "meian-build")])
    assert hooks.claude_session_name("bbb", reg) == "meian-build"


def test_claude_session_name_absent_or_unreadable(tmp_path):
    reg = _registry(tmp_path, [("aaa", "Fuin Mei")])
    (reg / "broken.json").write_text("{not json", encoding="utf-8")
    assert hooks.claude_session_name("zzz", reg) is None
    assert hooks.claude_session_name("aaa", tmp_path / "missing") is None


def test_claude_hook_takes_alias_from_session_name(source, tmp_path):
    reg = _registry(tmp_path, [("sess-1", "meian-build")])
    payload = json.dumps({"hook_event_name": "SessionStart", "session_id": "sess-1", "cwd": "D:\\w"})
    code, _ = hooks.run(["--host", "claude-code", "--source", str(source)],
                        stdin_text=payload, env={"KEIRAKU_CLAUDE_SESSIONS": str(reg)})
    assert code == 0
    peers = core.read_presence(source)
    assert [p["alias"] for p in peers] == ["meian-build"]


def test_explicit_alias_wins_over_session_name(source, tmp_path):
    reg = _registry(tmp_path, [("sess-1", "meian-build")])
    payload = json.dumps({"hook_event_name": "SessionStart", "session_id": "sess-1", "cwd": "D:\\w"})
    hooks.run(["--host", "claude-code", "--source", str(source), "--alias", "chosen"],
              stdin_text=payload, env={"KEIRAKU_CLAUDE_SESSIONS": str(reg)})
    assert [p["alias"] for p in core.read_presence(source)] == ["chosen"]


def test_codex_hook_does_not_read_the_claude_registry(source, tmp_path):
    reg = _registry(tmp_path, [("sess-1", "meian-build")])
    payload = json.dumps({"hook_event_name": "SessionStart", "session_id": "sess-1", "cwd": "D:\\w"})
    hooks.run(["--host", "codex", "--source", str(source)],
              stdin_text=payload, env={"KEIRAKU_CLAUDE_SESSIONS": str(reg)})
    assert [p["alias"] for p in core.read_presence(source)] == [None]


# -- keiraku send -------------------------------------------------------------

def _two(source):
    a = core.attach(source, host="claude-code", session_id="s-a", cwd="D:\\w", alias="meian-build")
    b = core.attach(source, host="codex", session_id="s-b", cwd="D:\\w", alias="mei")
    return a, b


def _inbox(source, vid):
    return sorted((Path(source) / "stream" / vid).glob("*.json"))


def test_send_by_alias_delivers_one_copy_with_sender_id(source):
    a, b = _two(source)
    r = _run_cli("--source", str(source), "send", "--from", "meian-build", "--to", "mei",
                 "--summary", "hello", "the body")
    assert r.returncode == 0, r.stderr.decode()
    files = _inbox(source, b.vessel_id)
    assert len(files) == 1
    msg = json.loads(files[0].read_text(encoding="utf-8"))
    assert msg["from"] == a.vessel_id and msg["body"] == "the body" and msg["summary"] == "hello"


def test_send_does_not_touch_the_senders_presence(source):
    a, b = _two(source)
    p = Path(source) / "presence" / f"{a.vessel_id}.json"
    before = p.read_bytes()
    _run_cli("--source", str(source), "send", "--from", a.vessel_id, "--to", b.vessel_id, "x")
    assert p.read_bytes() == before


def test_send_reaches_a_quiet_vessel(source):
    a, b = _two(source)
    _age(source, b.vessel_id, 3600)
    assert b.vessel_id not in [p["vessel_id"] for p in core.read_presence(source)]
    r = _run_cli("--source", str(source), "send", "--from", "meian-build", "--to", "mei", "x")
    assert r.returncode == 0, r.stderr.decode()
    assert len(_inbox(source, b.vessel_id)) == 1


def test_send_refuses_a_stopped_vessel(source):
    a, b = _two(source)
    b.detach()
    r = _run_cli("--source", str(source), "send", "--from", "meian-build", "--to", "mei", "x")
    assert r.returncode == 1
    assert _inbox(source, b.vessel_id) == []


def test_send_refuses_an_ambiguous_alias_and_lists_the_ids(source):
    a, b = _two(source)
    c = core.attach(source, host="codex", session_id="s-c", cwd="D:\\w", alias="mei")
    r = _run_cli("--source", str(source), "send", "--from", "meian-build", "--to", "mei", "x")
    assert r.returncode == 1
    err = r.stderr.decode("utf-8")
    assert b.vessel_id in err and c.vessel_id in err
    assert _inbox(source, b.vessel_id) == [] and _inbox(source, c.vessel_id) == []


def test_one_session_seen_from_two_folders_is_not_ambiguous(source):
    """The vessel id hashes the folder, so a session that changed folder has two
    records (12 Nov 2026: the Meian session after a `cd`). Same host + same
    session id is one session: send goes to the freshest record."""
    a, b = _two(source)
    old = core.attach(source, host="claude-code", session_id="s-x", cwd="D:\\w", alias="moved")
    _age(source, old.vessel_id, 600)
    new = core.attach(source, host="claude-code", session_id="s-x", cwd="D:\\w\\sub", alias="moved")
    assert old.vessel_id != new.vessel_id
    r = _run_cli("--source", str(source), "send", "--from", "mei", "--to", "moved", "x")
    assert r.returncode == 0, r.stderr.decode()
    assert len(_inbox(source, new.vessel_id)) == 1 and _inbox(source, old.vessel_id) == []
    r = _run_cli("--source", str(source), "send", "--from", "moved", "--to", "mei", "y")
    assert r.returncode == 0, r.stderr.decode()
    msg = json.loads(_inbox(source, b.vessel_id)[0].read_text(encoding="utf-8"))
    assert msg["from"] == new.vessel_id


def test_send_refuses_an_unknown_sender_and_self(source):
    a, b = _two(source)
    r = _run_cli("--source", str(source), "send", "--from", "nobody", "--to", "mei", "x")
    assert r.returncode == 1
    r = _run_cli("--source", str(source), "send", "--from", "mei", "--to", "mei", "x")
    assert r.returncode == 1
    assert _inbox(source, b.vessel_id) == []


def test_who_prints_a_non_ascii_alias_on_a_cp1252_console(source):
    core.attach(source, host="claude-code", session_id="s-f", cwd="D:\\w", alias="F\u016bin Mei")
    r = _run_cli("--source", str(source), "who", env_extra={"PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"})
    assert r.returncode == 0, r.stderr
    assert "F\u016bin Mei".encode("utf-8") in r.stdout
