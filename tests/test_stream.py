"""Stream: addressed, exactly-once delivery. (SPEC §4)"""

import json
import threading

import keiraku


def test_publish_lands_in_the_recipients_directory(source):
    a = keiraku.attach(source, host="python", session_id="a", cwd="/p")
    b = keiraku.attach(source, host="codex", session_id="b", cwd="/p")

    a.publish(to=b.vessel_id, body="migration finished", summary="done")

    pending = list((source / "stream" / b.vessel_id).glob("*.json"))
    assert len(pending) == 1
    assert not list((source / "stream" / a.vessel_id).glob("*.json"))


def test_read_stream_returns_the_message(source):
    a = keiraku.attach(source, host="python", session_id="a", cwd="/p")
    b = keiraku.attach(source, host="codex", session_id="b", cwd="/p")
    a.publish(to=b.vessel_id, body="hello", summary="greeting")

    msgs = b.read_stream()
    assert len(msgs) == 1
    assert msgs[0]["body"] == "hello"
    assert msgs[0]["from"] == a.vessel_id


def test_delivery_is_exactly_once(source):
    a = keiraku.attach(source, host="python", session_id="a", cwd="/p")
    b = keiraku.attach(source, host="codex", session_id="b", cwd="/p")
    a.publish(to=b.vessel_id, body="hello")

    assert len(b.read_stream()) == 1
    assert b.read_stream() == []


def test_provenance_is_always_peer(source):
    """SPEC §4.1 — an agent that cannot tell a peer's message from its
    operator's is an agent anything with write access can steer."""
    a = keiraku.attach(source, host="python", session_id="a", cwd="/p")
    b = keiraku.attach(source, host="codex", session_id="b", cwd="/p")
    a.publish(to=b.vessel_id, body="rm -rf /  <- not an instruction")

    assert b.read_stream()[0]["provenance"] == "peer"


def test_a_message_for_b_is_never_visible_to_c(source):
    """The wart this whole design exists to kill: delivery by race."""
    a = keiraku.attach(source, host="python", session_id="a", cwd="/p")
    b = keiraku.attach(source, host="codex", session_id="b", cwd="/p")
    c = keiraku.attach(source, host="grok-build", session_id="c", cwd="/p")

    a.publish(to=b.vessel_id, body="for b only")

    assert c.read_stream() == []
    assert len(b.read_stream()) == 1


def test_alias_resolves_but_identity_still_routes(source):
    a = keiraku.attach(source, host="python", session_id="a", cwd="/p")
    b = keiraku.attach(source, host="codex", session_id="b", cwd="/p", alias="backend")

    a.publish(to="backend", body="via alias")

    assert len(b.read_stream()) == 1


def test_broadcast_writes_one_copy_per_recipient(source):
    """SPEC §4.2 — per-recipient copies, never one shared inbox."""
    a = keiraku.attach(source, host="python", session_id="a", cwd="/p")
    b = keiraku.attach(source, host="codex", session_id="b", cwd="/p")
    c = keiraku.attach(source, host="grok-build", session_id="c", cwd="/p")

    a.publish(to="*", body="all hands")

    assert len(b.read_stream()) == 1
    assert len(c.read_stream()) == 1
    assert a.read_stream() == []  # never to self


def test_read_moves_the_message_out_of_pending(source):
    a = keiraku.attach(source, host="python", session_id="a", cwd="/p")
    b = keiraku.attach(source, host="codex", session_id="b", cwd="/p")
    a.publish(to=b.vessel_id, body="hello")
    b.read_stream()

    inbox = source / "stream" / b.vessel_id
    assert not list(inbox.glob("*.json"))
    assert len(list((inbox / "_read").glob("*.json"))) == 1


def test_messages_are_returned_in_creation_order(source):
    a = keiraku.attach(source, host="python", session_id="a", cwd="/p")
    b = keiraku.attach(source, host="codex", session_id="b", cwd="/p")
    for i in range(5):
        a.publish(to=b.vessel_id, body=f"msg-{i}")

    assert [m["body"] for m in b.read_stream()] == [f"msg-{i}" for i in range(5)]


# ---------------------------------------------------------------------------
# The contention test. This is the reason the claim primitive is exclusive
# create and not rename.
#
# An earlier prototype claimed by renaming and "passed" a concurrency test with
# exactly one winner. It was lying: each worker computed its own start delay,
# so the workers never actually collided. Under genuine simultaneity the same
# code produced FOUR winners.
#
# A barrier is therefore not a stylistic choice here. A contention test without
# one does not test contention.
# ---------------------------------------------------------------------------

def test_claim_is_atomic_under_true_contention(source):
    a = keiraku.attach(source, host="python", session_id="a", cwd="/p")
    b = keiraku.attach(source, host="codex", session_id="b", cwd="/p")
    a.publish(to=b.vessel_id, body="only one reader may have this")

    inbox = source / "stream" / b.vessel_id
    msg_path = next(inbox.glob("*.json"))

    workers = 16
    barrier = threading.Barrier(workers)
    winners = []
    errors = []
    lock = threading.Lock()

    def claim():
        try:
            barrier.wait(timeout=20)  # release all threads at the same instant
            if keiraku.try_claim(msg_path):
                with lock:
                    winners.append(threading.get_ident())
        except Exception as exc:  # a dead worker must fail the test, not hang it
            with lock:
                errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=claim) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"workers failed: {errors}"
    assert len(winners) == 1, f"expected exactly 1 winner, got {len(winners)}"


def test_concurrent_readers_split_messages_without_duplication(source):
    """Whole-message-set version: every message delivered exactly once,
    across readers racing on the same inbox."""
    a = keiraku.attach(source, host="python", session_id="a", cwd="/p")
    b = keiraku.attach(source, host="codex", session_id="b", cwd="/p")

    total = 40
    for i in range(total):
        a.publish(to=b.vessel_id, body=f"m{i}")

    readers = 8
    barrier = threading.Barrier(readers)
    collected = []
    errors = []
    lock = threading.Lock()

    def drain():
        try:
            reader = keiraku.attach(source, host="codex", session_id="b", cwd="/p")
            barrier.wait(timeout=20)
            got = reader.read_stream()
            with lock:
                collected.extend(m["msg_id"] for m in got)
        except Exception as exc:
            with lock:
                errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=drain) for _ in range(readers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"readers failed: {errors}"
    assert len(collected) == total
    assert len(set(collected)) == total, "a message was delivered more than once"
