"""Keiraku core — the L0 protocol client.

A source is a plain directory. A vessel is one attached agent session.
Everything here is standard library, on purpose: a vessel must be attachable
inside any agent harness without dragging a dependency tree into it.

See SPEC.md for the on-disk contract this implements.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
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


def _replace(src: Path, dst: Path, attempts: int = 12, delay: float = 0.004) -> None:
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
            time.sleep(delay * (attempt + 1))


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


def _read_json(path: Path):
    """Return the parsed record, or None if it is unreadable.

    One corrupt file must not blind a vessel to every other vessel.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
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
        sent = []
        for vid in self._resolve(to):
            msg_id = new_msg_id()
            inbox = self._inbox(vid)
            inbox.mkdir(parents=True, exist_ok=True)
            _write_json(
                inbox / f"{msg_id}.json",
                {
                    "msg_id": msg_id,
                    "from": self.vessel_id,
                    "to": vid,
                    "summary": summary,
                    "body": body,
                    "created_at": _now(),
                    "provenance": PROVENANCE_PEER,
                },
            )
            sent.append(msg_id)
        return sent

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
