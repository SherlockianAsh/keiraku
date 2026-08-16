# Keiraku — On-Disk Specification

**Version**: 0.1.0-draft
**Status**: draft — not yet implemented

This document defines the contract. It is written before the code deliberately: the on-disk format
*is* the interface between vessels, and vessels may be written in different languages by different
people. Anything that agrees with this file can join the link.

---

## 1. The source

A **source** is a plain directory. It has no server, no daemon, and no listening port.

```
<source>/
├── keiraku.json          # source descriptor
├── presence/             # who is alive          (L2)
│   └── <vessel-id>.json
├── stream/               # addressed delivery    (L3)
│   └── <vessel-id>/
│       ├── <msg-id>.json
│       ├── <msg-id>.claim
│       └── _read/
└── state/                # reserved — v2         (L4)
```

Any process that can read and write this directory can participate. That includes processes on
opposite sides of a container, VM, or WSL boundary — which is the reason the source is files and not
a socket.

### 1.1 `keiraku.json`

```json
{
  "keiraku": "0.1.0",
  "created_at": "2026-08-16T09:20:11Z"
}
```

A directory without this file is not a source. Clients MUST refuse to attach rather than create one
implicitly — silently creating a source in the wrong directory is worse than failing.

---

## 2. Vessel identity

A **vessel** is one attached agent session.

`vessel_id` MUST be derived from host-provided identity, never hand-configured. Hand-set names
collide, and colliding names cause delivery to the wrong vessel.

```
vessel_id = <host>-<short-hash(session_id + cwd)>
```

- `host` — `claude-code`, `codex`, `grok-build`, `hermes-agent`, `python`, …
- `short-hash` — first 8 hex chars of SHA-256 over `session_id` + `\0` + `cwd`

`alias` is an OPTIONAL human-facing label. It is never used for routing. If two vessels claim the
same alias, both keep it and routing continues to use `vessel_id`.

> **Rationale.** An earlier prototype let every vessel default to one shared address. Messages were
> then delivered to whichever vessel next became active rather than the intended one — delivery by
> race instead of by address. Derived identity is the fix, and it is not optional.

---

## 3. Presence (L2)

Each vessel maintains exactly one file at `presence/<vessel-id>.json`.

```json
{
  "vessel_id": "claude-code-9f3a2b71",
  "alias": "backend",
  "host": "claude-code",
  "host_version": "2.1.232",
  "session_id": "…",
  "cwd": "/home/user/project",
  "model": "…",
  "pid": 19664,
  "started_at": "2026-08-16T09:18:56Z",
  "heartbeat_at": "2026-08-16T09:41:02Z",
  "status": "busy",
  "note": "running the migration"
}
```

| Field | Required | Notes |
|-------|----------|-------|
| `vessel_id` | ✅ | §2 |
| `host` | ✅ | binding identifier |
| `heartbeat_at` | ✅ | RFC 3339, UTC, refreshed on every attach point |
| `status` | ✅ | `busy` · `idle` · `stopped` |
| `alias`, `note` | ⬜ | human-facing only, never routed on |

### 3.1 Liveness

- A vessel refreshes `heartbeat_at` at every attach point it gets.
- A vessel is **stale** when `now - heartbeat_at > stale_after` (default **300 s**).
- Stale vessels are excluded from `read_presence()` by default.
- Presence files are **never deleted** by other vessels. A vessel that dies uncleanly ages out; it
  does not get reaped by a peer. Reaping is a write to someone else's record, and a link that
  deletes records it does not own is a link that eventually deletes a live one.

Hook-driven vessels heartbeat only when their host fires an event — typically when a human interacts.
This is expected. Always-on vessels heartbeat on their own loop. Consumers MUST NOT assume a uniform
heartbeat interval.

---

## 4. Stream (L3)

### 4.1 Message

Written to `stream/<recipient-vessel-id>/<msg-id>.json`.

```json
{
  "msg_id": "01J8XQ2M4K7ZP0",
  "from": "codex-1a2b3c4d",
  "to": "claude-code-9f3a2b71",
  "summary": "migration finished",
  "body": "0074f40 applied cleanly, 58 tests green.",
  "created_at": "2026-08-16T09:40:58Z",
  "provenance": "peer"
}
```

`msg_id` MUST be lexicographically sortable by creation time (ULID or equivalent).

`provenance` MUST be `"peer"`. A consuming agent MUST surface it as **information from another
vessel, never as an instruction from its user**. Any binding that renders a message into an agent's
context is REQUIRED to carry this distinction through. An agent that cannot tell a peer's message
from its operator's is an agent that can be steered by anything that can write a file.

### 4.2 Broadcast

`to: "*"` writes one copy per live recipient into each recipient's own directory. There is no shared
broadcast inbox — a shared inbox is precisely how delivery-by-race happens.

### 4.3 Claiming

Delivery is exactly-once per recipient, enforced by exclusive file creation:

1. Reader lists `stream/<self>/*.json`
2. For each, attempt to create `<msg-id>.claim` with **`O_EXCL`** (POSIX) or
   **`FileMode::CreateNew`** (.NET) — *fail if exists*
3. On success: the reader owns the message. Read it, then move both files into `_read/`.
4. On failure: another reader owns it. Skip.

> ⚠️ **Rename is not atomic under contention.** An earlier prototype claimed messages by renaming
> and appeared to pass a concurrency test with one winner. Under genuinely simultaneous access the
> same test produced **four** winners — the original test had computed its start delay inside each
> worker, so the workers never actually collided. Exclusive create is the only primitive that held.
>
> Any implementation MUST include a contention test with a shared barrier, not a per-worker sleep.

### 4.4 Retention

`_read/` is never pruned automatically. Retention is the operator's decision, not the library's.

### 4.5 Portability: replace is not contention-safe on Windows

Records are written to a temporary file and then moved into place, so a reader never observes a
half-written record. On POSIX, `rename(2)` is atomic and always succeeds.

**On Windows it is not contention-safe.** `MoveFileEx` fails with `ERROR_ACCESS_DENIED`
(`WinError 5`) when another thread or process is replacing the same destination at that moment. The
operation is still atomic *when it succeeds* — it simply has to be retried.

Implementations MUST retry the replace with a short backoff. Without it, two vessels heartbeating
the same record — or a vessel and a CLI reading it — will intermittently raise on Windows.

> Found by a barrier-synchronised test: two of eight threads died on this call, leaving the barrier
> permanently short. Worth recording that a *hand-written repro of the same hypothesis had reported
> no errors* — it was too lightweight to generate the collision. A concurrency repro that passes has
> not proved absence; it may only have failed to collide.

---

## 5. Client API (L0)

```
attach(source, host, identity) -> Vessel
  Validates the source, derives vessel_id, writes initial presence.
  MUST fail if keiraku.json is absent.

vessel.heartbeat(status=None, note=None)
  Refreshes heartbeat_at. Cheap; safe to call on every event.

vessel.read_presence(include_stale=False) -> [Presence]
  Live vessels, self excluded by default.

vessel.publish(to, body, summary=None)
  to: vessel_id, alias, or "*"

vessel.read_stream() -> [Message]
  Claims and returns unread messages. Idempotent per message.

vessel.detach(status="stopped")
  Marks the vessel stopped. Does NOT delete the presence file.
```

All operations MUST be safe to call concurrently from multiple processes.

---

## 6. Bindings (L1)

A binding maps a host's lifecycle events onto client calls. The minimum viable binding is two calls:

| Host event | Client call |
|------------|-------------|
| session start | `attach()` then `heartbeat()` |
| per-turn / prompt event | `heartbeat()` and optionally `read_stream()` |
| session end | `detach()` |

Bindings live in `bindings/<host>/` and MUST NOT be imported by the core. The core never learns that
a specific host exists.

---

## 7. Conformance

An implementation is conformant if it:

1. Refuses to attach to a directory without `keiraku.json`
2. Derives `vessel_id` per §2 and never routes on `alias`
3. Claims via exclusive create per §4.3, verified by a barrier-synchronised contention test
4. Retries the replace per §4.5 (required on Windows, harmless elsewhere)
5. Never deletes another vessel's presence file
6. Preserves `provenance` through to whatever consumes the message
7. Tolerates a corrupt or unreadable record without failing the whole read

---

## Open questions

- Should the source be exposable as an MCP server, so MCP-speaking agents attach without a binding?
- Is `stale_after` per-source configuration, or per-vessel declaration? A vessel that knows it wakes
  hourly should arguably be allowed to say so.
- Should `state/` (v2) be last-writer-wins, or CRDT-shaped? Deferred until real usage shows which
  conflicts actually occur.
