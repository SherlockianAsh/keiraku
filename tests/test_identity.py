"""Identity is derived, never hand-set. (SPEC §2)

The prototype that let vessels share one hand-set address delivered messages to
whichever vessel woke first. These tests exist so that cannot come back.
"""

import keiraku


def test_vessel_id_is_derived_and_stable():
    a = keiraku.derive_vessel_id("claude-code", "sess-1", "/home/u/proj")
    b = keiraku.derive_vessel_id("claude-code", "sess-1", "/home/u/proj")
    assert a == b


def test_vessel_id_carries_the_host_prefix():
    vid = keiraku.derive_vessel_id("codex", "sess-1", "/home/u/proj")
    assert vid.startswith("codex-")


def test_different_session_gives_different_id():
    a = keiraku.derive_vessel_id("claude-code", "sess-1", "/p")
    b = keiraku.derive_vessel_id("claude-code", "sess-2", "/p")
    assert a != b


def test_different_cwd_gives_different_id():
    """Two sessions of the same host in different projects are different vessels."""
    a = keiraku.derive_vessel_id("claude-code", "sess-1", "/proj-a")
    b = keiraku.derive_vessel_id("claude-code", "sess-1", "/proj-b")
    assert a != b


def test_separator_cannot_be_forged_by_concatenation():
    """('ab','c') and ('a','bc') must not collide — the hash input is delimited."""
    a = keiraku.derive_vessel_id("h", "ab", "c")
    b = keiraku.derive_vessel_id("h", "a", "bc")
    assert a != b


def test_alias_does_not_change_identity(source):
    v1 = keiraku.attach(source, host="python", session_id="s", cwd="/p", alias="backend")
    v2 = keiraku.attach(source, host="python", session_id="s", cwd="/p", alias="frontend")
    assert v1.vessel_id == v2.vessel_id
