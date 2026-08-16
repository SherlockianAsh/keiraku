"""Presence: who is alive, where, doing what. (SPEC §3)"""

import json

import keiraku


def _presence_file(source, vessel_id):
    return source / "presence" / f"{vessel_id}.json"


def test_attach_writes_a_presence_record(source):
    v = keiraku.attach(source, host="python", session_id="s1", cwd="/p")
    rec = json.loads(_presence_file(source, v.vessel_id).read_text())

    assert rec["vessel_id"] == v.vessel_id
    assert rec["host"] == "python"
    assert rec["status"] == "idle"
    assert rec["heartbeat_at"]


def test_heartbeat_advances_the_timestamp(source):
    v = keiraku.attach(source, host="python", session_id="s1", cwd="/p")
    first = json.loads(_presence_file(source, v.vessel_id).read_text())["heartbeat_at"]

    v.heartbeat(status="busy", note="running the migration")
    rec = json.loads(_presence_file(source, v.vessel_id).read_text())

    assert rec["heartbeat_at"] >= first
    assert rec["status"] == "busy"
    assert rec["note"] == "running the migration"


def test_read_presence_excludes_self_by_default(source):
    a = keiraku.attach(source, host="python", session_id="a", cwd="/p")
    keiraku.attach(source, host="codex", session_id="b", cwd="/p")

    seen = {p["vessel_id"] for p in a.read_presence()}
    assert a.vessel_id not in seen
    assert len(seen) == 1


def test_read_presence_can_include_self(source):
    a = keiraku.attach(source, host="python", session_id="a", cwd="/p")
    seen = {p["vessel_id"] for p in a.read_presence(include_self=True)}
    assert a.vessel_id in seen


def test_stale_vessels_are_excluded(source):
    a = keiraku.attach(source, host="python", session_id="a", cwd="/p")
    b = keiraku.attach(source, host="codex", session_id="b", cwd="/p")

    # Age b beyond the staleness window by rewriting its heartbeat.
    rec = json.loads(_presence_file(source, b.vessel_id).read_text())
    rec["heartbeat_at"] = "2020-01-01T00:00:00Z"
    _presence_file(source, b.vessel_id).write_text(json.dumps(rec))

    assert a.read_presence() == []
    assert len(a.read_presence(include_stale=True)) == 1


def test_detach_marks_stopped_but_never_deletes(source):
    """SPEC §3.1 — a link that deletes records it does not own eventually
    deletes a live one. Ageing out is the only reaping mechanism."""
    v = keiraku.attach(source, host="python", session_id="s1", cwd="/p")
    v.detach()

    f = _presence_file(source, v.vessel_id)
    assert f.exists()
    assert json.loads(f.read_text())["status"] == "stopped"


def test_a_corrupt_presence_file_does_not_break_the_read(source):
    """One bad record must not blind a vessel to every other vessel."""
    a = keiraku.attach(source, host="python", session_id="a", cwd="/p")
    keiraku.attach(source, host="codex", session_id="b", cwd="/p")
    (source / "presence" / "garbage.json").write_text("{not json")

    assert len(a.read_presence()) == 1
