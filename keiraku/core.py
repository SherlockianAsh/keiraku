"""Keiraku core — the L0 protocol client.

A source is a plain directory. A vessel is one attached agent session.
Everything here is standard library, on purpose: a vessel must be attachable
inside any agent harness without dragging a dependency tree into it.

See SPEC.md for the on-disk contract this implements.
"""

from __future__ import annotations

import hashlib
import json
import re
import os
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SPEC_VERSION = "0.1.0"

DEFAULT_STALE_AFTER = 300.0  # seconds
PROVENANCE_PEER = "peer"


class KeirakuError(Exception):
    """Base for every Keiraku failure."""


class NotASource(KeirakuError):
    """The target directory is not an initialised Keiraku source.

    Raised rather than creating one implicitly: silently initialising a source
    in the wrong directory is worse than failing loudly.
    """


class UnknownRecipient(KeirakuError):
    """No live vessel matches the given id or alias."""


class LockUnavailable(KeirakuError):
    """The lock is held by another vessel and has not expired."""


# --------------------------------------------------------------------------
# time & ids
# --------------------------------------------------------------------------

def _now() -> str:
    """RFC 3339, UTC, lexicographically sortable."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse(ts: str) -> float:
    """Parse a timestamp we wrote. Returns epoch seconds; 0.0 if unreadable."""
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=timezone.utc
        ).timestamp()
    except (ValueError, TypeError):
        try:
            return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            ).timestamp()
        except (ValueError, TypeError):
            return 0.0


def new_msg_id() -> str:
    """Sortable by creation time, unique under concurrency.

    Fixed-width milliseconds keeps lexicographic order equal to chronological
    order, which is what SPEC §4.1 requires.
    """
    return f"{int(time.time() * 1000):013d}-{uuid.uuid4().hex[:12]}"


def derive_vessel_id(host: str, session_id: str, cwd: str) -> str:
    """Identity is derived, never hand-set (SPEC §2).

    The NUL delimiter matters: without it ('ab', 'c') and ('a', 'bc') would
    hash identically, and two unrelated vessels would share an address.
    """
    digest = hashlib.sha256(f"{session_id}\0{cwd}".encode("utf-8")).hexdigest()
    return f"{host}-{digest[:8]}"


# --------------------------------------------------------------------------
# atomic primitives
# --------------------------------------------------------------------------

def try_claim(msg_path: Path) -> bool:
    """Attempt to claim a message. True if this caller won.

    Exclusive create is the only primitive that survives real contention.
    Rename is NOT atomic under concurrent writers — a prototype that claimed
    by renaming passed a flawed test with one winner and produced four under
    genuine simultaneity.
    """
    claim = Path(msg_path).with_suffix(".claim")
    try:
        fd = os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    except OSError:
        return False
    os.close(fd)
    return True


def _replace(src: Path, dst: Path, attempts: int = 60, delay: float = 0.002) -> None:
    """os.replace, retried.

    POSIX rename is atomic and always succeeds. Windows MoveFileEx is not
    contention-safe: replacing a destination that another thread or process is
    simultaneously replacing fails with ERROR_ACCESS_DENIED (WinError 5). The
    operation is still atomic when it succeeds — it just has to be retried.

    Found by a barrier-synchronised test, not by review: two of eight threads
    died here, and an earlier hand-written repro of the same hypothesis had
    reported no errors because it never generated enough contention to collide.
    """
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(min(delay * (attempt + 1), 0.02))


def _write_json(path: Path, payload: dict) -> None:
    """Write via a temp file in the same directory, then replace.

    A reader must never observe a half-written record.
    """
    tmp = path.with_suffix(f".{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        _replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def _read_json(path: Path, attempts: int = 60, delay: float = 0.002):
    """Return the parsed record, or None if it is genuinely absent or corrupt.

    The retry is not defensive padding. On Windows, opening a file while
    another process is replacing it raises PermissionError -- so a reader that
    treated every OSError as "absent" would transiently report an EXISTING key
    as unset. A caller would then conclude the key had never been written and
    recreate it, losing the other vessel's value.

    So the three outcomes are kept distinct:
      missing   -> None immediately (legitimately absent)
      locked    -> retry; it is mid-replace, not gone
      corrupt   -> None (one bad record must not blind the whole read)
    """
    for attempt in range(attempts):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except PermissionError:
            if attempt == attempts - 1:
                return None
            time.sleep(min(delay * (attempt + 1), 0.02))
        except (OSError, ValueError):
            return None


# --------------------------------------------------------------------------
# source
# --------------------------------------------------------------------------

def init_source(path) -> Path:
    """Create a source. Idempotent — an existing descriptor is left alone."""
    root = Path(path)
    (root / "presence").mkdir(parents=True, exist_ok=True)
    (root / "stream").mkdir(parents=True, exist_ok=True)

    desc = root / "keiraku.json"
    if not desc.exists():
        _write_json(desc, {"keiraku": SPEC_VERSION, "created_at": _now()})
    return root


def read_presence(
    source,
    exclude=None,
    include_stale: bool = False,
    stale_after: float = DEFAULT_STALE_AFTER,
) -> list:
    """Read presence WITHOUT attaching.

    Observing who is alive must not make the observer alive. A monitor, a
    status bar, or a `who` command should be able to look without joining --
    otherwise every glance leaves a vessel behind in the list.
    """
    root = _require_source(source)
    out = []
    cutoff = time.time() - stale_after
    pdir = root / "presence"
    if not pdir.is_dir():
        return out

    for f in sorted(pdir.glob("*.json")):
        rec = _read_json(f)
        if not rec or "vessel_id" not in rec:
            continue
        if exclude and rec["vessel_id"] == exclude:
            continue
        if not include_stale and _parse(rec.get("heartbeat_at", "")) < cutoff:
            continue
        out.append(rec)
    return out


# --------------------------------------------------------------------------
# state (L4)
# --------------------------------------------------------------------------

_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def state_filename(key: str) -> str:
    """Filesystem-safe, human-readable, and collision-free.

    A slug alone would let 'a/b' and 'a_b' share a file; a hash alone would
    make `ls state/` unreadable, which breaks the "inspectable with ls and cat"
    commitment. So: slug for humans, hash for correctness.
    """
    slug = _SAFE.sub("_", key)[:48].strip(".") or "key"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]
    return f"{slug}.{digest}.json"


def read_state(source, key: str):
    """Read one state key without attaching. None if unset."""
    root = _require_source(source)
    return _read_json(root / "state" / state_filename(key))


def list_state(source) -> list:
    """Every state key, without attaching."""
    root = _require_source(source)
    sdir = root / "state"
    if not sdir.is_dir():
        return []
    out = []
    for f in sorted(sdir.glob("*.json")):
        rec = _read_json(f)
        if rec and "key" in rec:
            out.append(rec)
    return out


def _write_state(root: Path, key: str, value, version: int, by: str) -> dict:
    record = {
        "key": key,
        "value": value,
        "version": version,
        "updated_by": by,
        "updated_at": _now(),
    }
    sdir = root / "state"
    sdir.mkdir(parents=True, exist_ok=True)
    _write_json(sdir / state_filename(key), record)
    return record


def _cas(root: Path, key: str, value, expected_version: int, by: str) -> bool:
    """Compare-and-set, using the one primitive this project trusts.

    To move a key from version N to N+1 a writer must exclusively create the
    token `_cas/<key>.<N+1>`. Only one writer can, so only one writer performs
    the bump. No locks, no deletion, and no expiry are involved.
    """
    current = read_state(root, key)
    have = current["version"] if current else 0
    if have != expected_version:
        return False

    cdir = root / "state" / "_cas"
    cdir.mkdir(parents=True, exist_ok=True)
    token = cdir / f"{state_filename(key)[:-5]}.{expected_version + 1}"
    try:
        fd = os.open(token, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    except OSError:
        return False
    os.close(fd)

    _write_state(root, key, value, expected_version + 1, by)
    return True


def _require_source(path) -> Path:
    root = Path(path)
    if not (root / "keiraku.json").is_file():
        raise NotASource(
            f"{root} is not a Keiraku source (no keiraku.json). "
            f"Run init_source() explicitly if this is the directory you meant."
        )
    return root


# --------------------------------------------------------------------------
# vessel
# --------------------------------------------------------------------------

class Vessel:
    """One attached agent session."""

    def __init__(
        self,
        source: Path,
        host: str,
        session_id: str,
        cwd: str,
        alias=None,
        model=None,
        host_version=None,
        stale_after: float = DEFAULT_STALE_AFTER,
    ):
        self.source = source
        self.host = host
        self.session_id = session_id
        self.cwd = cwd
        self.alias = alias
        self.model = model
        self.host_version = host_version
        self.stale_after = stale_after
        self.vessel_id = derive_vessel_id(host, session_id, cwd)
        self.started_at = _now()

    # -- paths ------------------------------------------------------------
    @property
    def _presence_path(self) -> Path:
        return self.source / "presence" / f"{self.vessel_id}.json"

    def _inbox(self, vessel_id: str) -> Path:
        return self.source / "stream" / vessel_id

    # -- presence ---------------------------------------------------------
    def heartbeat(self, status: str = "idle", note=None) -> dict:
        record = {
            "vessel_id": self.vessel_id,
            "alias": self.alias,
            "host": self.host,
            "host_version": self.host_version,
            "session_id": self.session_id,
            "cwd": self.cwd,
            "model": self.model,
            "pid": os.getpid(),
            "started_at": self.started_at,
            "heartbeat_at": _now(),
            "status": status,
            "note": note,
        }
        self._presence_path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(self._presence_path, record)
        return record

    def read_presence(self, include_self: bool = False, include_stale: bool = False):
        """Live vessels. Self and stale records excluded by default."""
        return read_presence(
            self.source,
            exclude=None if include_self else self.vessel_id,
            include_stale=include_stale,
            stale_after=self.stale_after,
        )

    def detach(self, status: str = "stopped") -> None:
        """Mark this vessel stopped. The record is never deleted — a link that
        removes records it does not own eventually removes a live one."""
        self.heartbeat(status=status)

    # -- stream -----------------------------------------------------------
    def _resolve(self, to: str):
        """Resolve a recipient to concrete vessel ids.

        Alias lookup is a convenience only; routing is always by vessel_id.
        """
        peers = self.read_presence()
        if to == "*":
            return [p["vessel_id"] for p in peers]
        if any(p["vessel_id"] == to for p in peers):
            return [to]
        by_alias = [p["vessel_id"] for p in peers if p.get("alias") == to]
        if by_alias:
            return by_alias
        raise UnknownRecipient(f"no live vessel matches {to!r}")

    def publish(self, to: str, body: str, summary=None) -> list:
        """Write one copy per recipient. There is no shared broadcast inbox —
        a shared inbox is exactly how delivery-by-race happens."""
        return [deliver(self.source, self.vessel_id, vid, body, summary)
                for vid in self._resolve(to)]

    def read_stream(self) -> list:
        """Claim and return unread messages, oldest first."""
        inbox = self._inbox(self.vessel_id)
        if not inbox.is_dir():
            return []
        read_dir = inbox / "_read"

        out = []
        for msg_path in sorted(inbox.glob("*.json")):
            if not try_claim(msg_path):
                continue
            rec = _read_json(msg_path)
            read_dir.mkdir(parents=True, exist_ok=True)
            try:
                _replace(msg_path, read_dir / msg_path.name)
                claim = msg_path.with_suffix(".claim")
                if claim.exists():
                    _replace(claim, read_dir / claim.name)
            except OSError:
                # The claim already decided ownership; failing to file the
                # message away does not un-deliver it.
                pass
            if rec:
                out.append(rec)
        return out


    # -- state (L4) -------------------------------------------------------
    def set_state(self, key: str, value) -> dict:
        """Last writer wins. Use cas_state if that is not acceptable."""
        current = read_state(self.source, key)
        version = (current["version"] if current else 0) + 1
        return _write_state(self.source, key, value, version, self.vessel_id)

    def get_state(self, key: str):
        return read_state(self.source, key)

    def list_state(self) -> list:
        return list_state(self.source)

    def cas_state(self, key: str, value, expected_version: int) -> bool:
        """Set only if the key is still at expected_version. False if it moved.

        The intended pattern is read-modify-CAS-retry:

            while True:
                cur = v.get_state("n")
                if v.cas_state("n", cur["value"] + 1, cur["version"]):
                    break
        """
        return _cas(self.source, key, value, expected_version, self.vessel_id)

    # -- locks (L4) -------------------------------------------------------
    def acquire_lock(self, name: str, ttl: float = 60.0):
        """Take a lease. Returns a token, or None if held by someone else.

        A lock is a state key holding {owner, token, expires_at}, taken by CAS.
        Because CAS is exclusive-create underneath, exactly one vessel wins --
        including the dangerous case where several vessels notice the same dead
        holder simultaneously and all try to take over at once.

        Leases expire because a vessel can die holding one, and nothing in
        Keiraku may delete another vessel's record to rescue it.
        """
        key = f"_lock:{name}"
        current = read_state(self.source, key)
        version = current["version"] if current else 0

        if current:
            held = current["value"] or {}
            if held.get("expires_at", 0) > time.time():
                return None  # genuinely held

        token = uuid.uuid4().hex
        lease = {
            "owner": self.vessel_id,
            "token": token,
            "acquired_at": _now(),
            "expires_at": time.time() + ttl,
        }
        return token if self.cas_state(key, lease, version) else None

    def renew_lock(self, name: str, token: str, ttl: float = 60.0) -> bool:
        """Extend a lease you hold. False if you do not hold it."""
        key = f"_lock:{name}"
        current = read_state(self.source, key)
        if not current or (current["value"] or {}).get("token") != token:
            return False
        lease = dict(current["value"])
        lease["expires_at"] = time.time() + ttl
        return self.cas_state(key, lease, current["version"])

    def release_lock(self, name: str, token: str) -> bool:
        """Release a lease you hold. False if the token does not match.

        Refusing a mismatched token is the point: otherwise one worker's
        cleanup silently unlocks another worker's critical section.
        """
        key = f"_lock:{name}"
        current = read_state(self.source, key)
        if not current or (current["value"] or {}).get("token") != token:
            return False
        return self.cas_state(key, None, current["version"])

    @contextmanager
    def lock(self, name: str, ttl: float = 60.0):
        """`with vessel.lock("ledger"):` -- released even if the body raises."""
        token = self.acquire_lock(name, ttl=ttl)
        if token is None:
            raise LockUnavailable(f"lock {name!r} is held by another vessel")
        try:
            yield token
        finally:
            self.release_lock(name, token)


def deliver(source, from_id: str, to_id: str, body: str, summary=None) -> str:
    """Write one message into one vessel's inbox. Returns its msg_id.

    Resolution (who `to_id` is, whether it is alive) is the caller's job; this
    only writes, in the one shape every reader expects.
    """
    root = Path(source)
    msg_id = new_msg_id()
    inbox = root / "stream" / to_id
    inbox.mkdir(parents=True, exist_ok=True)
    _write_json(
        inbox / f"{msg_id}.json",
        {
            "msg_id": msg_id,
            "from": from_id,
            "to": to_id,
            "summary": summary,
            "body": body,
            "created_at": _now(),
            "provenance": PROVENANCE_PEER,
        },
    )
    return msg_id


def attach(
    source,
    host: str,
    session_id: str,
    cwd: str,
    alias=None,
    model=None,
    host_version=None,
    stale_after: float = DEFAULT_STALE_AFTER,
) -> Vessel:
    """Attach a session to a source. Raises NotASource if it is not one."""
    root = _require_source(source)
    vessel = Vessel(
        source=root,
        host=host,
        session_id=session_id,
        cwd=cwd,
        alias=alias,
        model=model,
        host_version=host_version,
        stale_after=stale_after,
    )
    vessel.heartbeat(status="idle")
    return vessel
