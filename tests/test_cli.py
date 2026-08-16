"""The CLI, exercised the way a reader would type it.

These run the real module as a subprocess rather than calling main() directly.
That distinction matters: a documented command can be wrong in a way no
in-process test can see. `python -m keiraku` was documented in a binding README
and could not run at all, because the package had no __main__.py.
"""

import json
import subprocess
import sys

import keiraku


def _run(*args):
    return subprocess.run(
        [sys.executable, "-m", "keiraku", *args],
        capture_output=True, text=True,
    )


def test_python_dash_m_keiraku_is_executable(source):
    """Regression guard: `python -m keiraku` must not fail with
    'is a package and cannot be directly executed'."""
    r = _run("--source", str(source), "who")
    assert r.returncode == 0, r.stderr
    assert "cannot be directly executed" not in r.stderr


def test_init_creates_a_source(tmp_path):
    target = tmp_path / "fresh"
    r = _run("--source", str(target), "init")
    assert r.returncode == 0, r.stderr
    assert (target / "keiraku.json").is_file()


def test_who_reports_nothing_when_empty(source):
    r = _run("--source", str(source), "who")
    assert "no live vessels" in r.stdout


def test_who_lists_attached_vessels(source):
    keiraku.attach(source, host="python", session_id="a", cwd="/proj", alias="worker")
    r = _run("--source", str(source), "who")
    assert "worker" in r.stdout
    assert "/proj" in r.stdout


def test_who_does_not_register_itself(source):
    """Looking at the list must not add you to it."""
    keiraku.attach(source, host="python", session_id="a", cwd="/proj")
    for _ in range(3):
        _run("--source", str(source), "who")
    assert len(list((source / "presence").glob("*.json"))) == 1


def test_who_on_a_non_source_fails_loudly(tmp_path):
    """The CLI is a human tool, so here failing is correct -- unlike the hook,
    which must never break its host."""
    r = _run("--source", str(tmp_path / "nope"), "who")
    assert r.returncode == 1
    assert "not a Keiraku source" in (r.stdout + r.stderr)


def test_cli_output_is_ascii(source):
    keiraku.attach(source, host="python", session_id="a", cwd="/proj",
                   alias="worker").heartbeat(status="busy", note="indexing")
    r = _run("--source", str(source), "who")
    r.stdout.encode("ascii")  # Windows consoles are cp1252


def test_hook_module_is_executable(source):
    """The command every binding README tells the reader to configure."""
    r = subprocess.run(
        [sys.executable, "-m", "keiraku.hook", "--host", "claude-code", "--source", str(source)],
        input=json.dumps({"session_id": "s1", "cwd": "/p", "hook_event_name": "SessionStart"}),
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    vid = keiraku.derive_vessel_id("claude-code", "s1", "/p")
    assert (source / "presence" / f"{vid}.json").is_file()
