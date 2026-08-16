"""Shared state and leases. (SPEC §7)

State is last-writer-wins by default, with compare-and-set for writers that
care. Locks are CAS over a state key, so there is no second mechanism and no
second expiry clock.
"""

import threading

import pytest

import keiraku


# -- basic state ------------------------------------------------------------

def test_set_then_get(source):
    v = keiraku.attach(source, host="python", session_id="a", cwd="/p")
    v.set_state("build", {"status": "green", "tests": 68})

    got = v.get_state("build")
    assert got["value"] == {"status": "green", "tests": 68}
    assert got["updated_by"] == v.vessel_id
    assert got["version"] == 1


def test_get_missing_key_returns_none(source):
    v = keiraku.attach(source, host="python", session_id="a", cwd="/p")
    assert v.get_state("nothing") is None


def test_set_is_visible_to_other_vessels(source):
    a = keiraku.attach(source, host="python", session_id="a", cwd="/p")
    b = keiraku.attach(source, host="codex", session_id="b", cwd="/p")

    a.set_state("current-page", 78)
    assert b.get_state("current-page")["value"] == 78


def test_set_bumps_version_and_is_last_writer_wins(source):
    a = keiraku.attach(source, host="python", session_id="a", cwd="/p")
    b = keiraku.attach(source, host="codex", session_id="b", cwd="/p")

    a.set_state("k", 1)
    b.set_state("k", 2)

    got = a.get_state("k")
    assert got["value"] == 2
    assert got["version"] == 2
    assert got["updated_by"] == b.vessel_id


def test_list_state(source):
    v = keiraku.attach(source, host="python", session_id="a", cwd="/p")
    v.set_state("alpha", 1)
    v.set_state("beta", 2)

    assert sorted(s["key"] for s in v.list_state()) == ["alpha", "beta"]


def test_keys_with_awkward_characters_round_trip(source):
    """Keys are used in filenames. The design commitment is that a source stays
    inspectable with ls and cat, so keys are slugified rather than hashed away."""
    v = keiraku.attach(source, host="python", session_id="a", cwd="/p")
    for key in ["a/b", "with space", "kolon:key", "../escape", "unicode-é"]:
        v.set_state(key, key)
        assert v.get_state(key)["value"] == key

    assert len(v.list_state()) == 5


def test_similar_keys_do_not_collide(source):
    """'a/b' and 'a_b' slugify to the same string; they must not share a file."""
    v = keiraku.attach(source, host="python", session_id="a", cwd="/p")
    v.set_state("a/b", "slash")
    v.set_state("a_b", "underscore")

    assert v.get_state("a/b")["value"] == "slash"
    assert v.get_state("a_b")["value"] == "underscore"


def test_state_files_stay_inside_the_source(source):
    """A key must never be able to write outside state/."""
    v = keiraku.attach(source, host="python", session_id="a", cwd="/p")
    v.set_state("../../escaped", "nope")

    assert not (source.parent / "escaped.json").exists()
    assert len(list((source / "state").glob("*.json"))) == 1


# -- compare-and-set --------------------------------------------------------

def test_cas_succeeds_on_matching_version(source):
    v = keiraku.attach(source, host="python", session_id="a", cwd="/p")
    v.set_state("k", 1)

    assert v.cas_state("k", 2, expected_version=1) is True
    assert v.get_state("k")["value"] == 2


def test_cas_fails_on_stale_version(source):
    a = keiraku.attach(source, host="python", session_id="a", cwd="/p")
    b = keiraku.attach(source, host="codex", session_id="b", cwd="/p")

    a.set_state("k", 1)
    b.set_state("k", 2)          # a's view is now stale

    assert a.cas_state("k", 99, expected_version=1) is False
    assert a.get_state("k")["value"] == 2


def test_cas_creates_a_key_from_version_zero(source):
    v = keiraku.attach(source, host="python", session_id="a", cwd="/p")
    assert v.cas_state("fresh", "x", expected_version=0) is True
    assert v.get_state("fresh")["version"] == 1


def test_cas_on_existing_key_from_zero_fails(source):
    v = keiraku.attach(source, host="python", session_id="a", cwd="/p")
    v.set_state("k", 1)
    assert v.cas_state("k", "x", expected_version=0) is False


def test_only_one_writer_wins_a_cas_race(source):
    """Barrier-synchronised: many vessels attempt the same version bump."""
    a = keiraku.attach(source, host="python", session_id="a", cwd="/p")
    a.set_state("counter", 0)

    workers = 12
    barrier = threading.Barrier(workers)
    wins, errors = [], []
    lock = threading.Lock()

    def contend(n):
        try:
            v = keiraku.attach(source, host="python", session_id=f"w{n}", cwd="/p")
            barrier.wait(timeout=20)
            if v.cas_state("counter", n, expected_version=1):
                with lock:
                    wins.append(n)
        except Exception as exc:
            with lock:
                errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=contend, args=(i,)) for i in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, errors
    assert len(wins) == 1, f"expected exactly 1 winner, got {len(wins)}"
    assert a.get_state("counter")["version"] == 2


def test_cas_retry_loop_converges(source):
    """The intended usage pattern: read, modify, CAS, retry on failure.
    All increments must survive - none may be lost."""
    keiraku.attach(source, host="python", session_id="seed", cwd="/p").set_state("n", 0)

    workers = 8
    barrier = threading.Barrier(workers)
    errors = []
    lock = threading.Lock()

    def increment(i):
        try:
            v = keiraku.attach(source, host="python", session_id=f"w{i}", cwd="/p")
            barrier.wait(timeout=20)
            for _ in range(200):
                cur = v.get_state("n")
                if v.cas_state("n", cur["value"] + 1, expected_version=cur["version"]):
                    return
            raise AssertionError("did not converge in 200 attempts")
        except Exception as exc:
            with lock:
                errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=increment, args=(i,)) for i in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not errors, errors
    assert keiraku.read_state(source, "n")["value"] == workers


# -- locks ------------------------------------------------------------------

def test_acquire_and_release(source):
    v = keiraku.attach(source, host="python", session_id="a", cwd="/p")
    token = v.acquire_lock("ledger")
    assert token
    assert v.release_lock("ledger", token) is True


def test_a_held_lock_blocks_others(source):
    a = keiraku.attach(source, host="python", session_id="a", cwd="/p")
    b = keiraku.attach(source, host="codex", session_id="b", cwd="/p")

    assert a.acquire_lock("ledger")
    assert b.acquire_lock("ledger") is None


def test_a_released_lock_is_available(source):
    a = keiraku.attach(source, host="python", session_id="a", cwd="/p")
    b = keiraku.attach(source, host="codex", session_id="b", cwd="/p")

    token = a.acquire_lock("ledger")
    a.release_lock("ledger", token)
    assert b.acquire_lock("ledger")


def test_release_with_a_wrong_token_is_refused(source):
    """A vessel must not be able to release a lock it does not hold - that is
    how one worker's cleanup silently unlocks another worker's critical section."""
    a = keiraku.attach(source, host="python", session_id="a", cwd="/p")
    b = keiraku.attach(source, host="codex", session_id="b", cwd="/p")

    a.acquire_lock("ledger")
    assert b.release_lock("ledger", "not-my-token") is False
    assert b.acquire_lock("ledger") is None


def test_an_expired_lock_can_be_taken_over(source):
    """A vessel can die holding a lock. Without expiry that is a permanent
    deadlock, and nothing may delete another vessel's record to fix it."""
    a = keiraku.attach(source, host="python", session_id="a", cwd="/p")
    b = keiraku.attach(source, host="codex", session_id="b", cwd="/p")

    a.acquire_lock("ledger", ttl=0.0)   # already expired
    assert b.acquire_lock("ledger")


def test_renew_extends_a_held_lock(source):
    a = keiraku.attach(source, host="python", session_id="a", cwd="/p")
    b = keiraku.attach(source, host="codex", session_id="b", cwd="/p")

    token = a.acquire_lock("ledger", ttl=0.0)
    assert a.renew_lock("ledger", token, ttl=300) is True
    assert b.acquire_lock("ledger") is None


def test_renew_with_a_wrong_token_is_refused(source):
    a = keiraku.attach(source, host="python", session_id="a", cwd="/p")
    a.acquire_lock("ledger")
    assert a.renew_lock("ledger", "wrong", ttl=300) is False


def test_only_one_vessel_acquires_a_contended_lock(source):
    keiraku.attach(source, host="python", session_id="seed", cwd="/p")

    workers = 12
    barrier = threading.Barrier(workers)
    holders, errors = [], []
    lock = threading.Lock()

    def grab(n):
        try:
            v = keiraku.attach(source, host="python", session_id=f"w{n}", cwd="/p")
            barrier.wait(timeout=20)
            if v.acquire_lock("ledger", ttl=60):
                with lock:
                    holders.append(n)
        except Exception as exc:
            with lock:
                errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=grab, args=(i,)) for i in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, errors
    assert len(holders) == 1, f"expected exactly 1 holder, got {len(holders)}"


def test_only_one_vessel_steals_an_expired_lock(source):
    """The dangerous case: many vessels see the same dead holder at once."""
    dead = keiraku.attach(source, host="python", session_id="dead", cwd="/p")
    dead.acquire_lock("ledger", ttl=0.0)

    workers = 12
    barrier = threading.Barrier(workers)
    holders, errors = [], []
    lock = threading.Lock()

    def steal(n):
        try:
            v = keiraku.attach(source, host="python", session_id=f"w{n}", cwd="/p")
            barrier.wait(timeout=20)
            if v.acquire_lock("ledger", ttl=60):
                with lock:
                    holders.append(n)
        except Exception as exc:
            with lock:
                errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=steal, args=(i,)) for i in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, errors
    assert len(holders) == 1, f"expected exactly 1 thief, got {len(holders)}"


def test_lock_context_manager_releases_on_exception(source):
    v = keiraku.attach(source, host="python", session_id="a", cwd="/p")
    b = keiraku.attach(source, host="codex", session_id="b", cwd="/p")

    with pytest.raises(ValueError):
        with v.lock("ledger"):
            raise ValueError("boom")

    assert b.acquire_lock("ledger"), "lock was not released on exception"


def test_lock_context_manager_raises_when_unavailable(source):
    a = keiraku.attach(source, host="python", session_id="a", cwd="/p")
    b = keiraku.attach(source, host="codex", session_id="b", cwd="/p")
    a.acquire_lock("ledger")

    with pytest.raises(keiraku.LockUnavailable):
        with b.lock("ledger"):
            pass


# -- module-level read (no attach) -----------------------------------------

def test_read_state_without_attaching(source):
    v = keiraku.attach(source, host="python", session_id="a", cwd="/p")
    v.set_state("k", 42)

    assert keiraku.read_state(source, "k")["value"] == 42
    assert len(list((source / "presence").glob("*.json"))) == 1
