# Keiraku — On-Disk Specification

**Version**: 0.1.0
**Status**: implemented — the reference client in this repository conforms

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
│       ├── _unconfirmed/     # printed by a hook, not yet proven seen
│       └── _read/
└── state/                # shared state + leases (L4)
    ├── <slug>.<hash8>.json
    └── _cas/              # version tokens
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

**Hook delivery is confirmed late.** A hook only prints a message; the host may never show that
output to the model (a turn can die first). So a hook moves what it prints into `_unconfirmed/`,
and into `_read/` only when a later event from the same vessel proves the turn ran (`Stop`, or a
mid-turn event such as a tool call). If the next event is another prompt, the unconfirmed
messages are printed again, marked as possibly shown before. A duplicate is acceptable; a loss is
not. A reader that takes messages directly (the library call, the MCP inbox tool) confirms at once.

**A stale `busy` reads as `unknown`.** A turn that dies never reports `idle`. When presence is read,
a `busy` record whose heartbeat is past the stale threshold is reported as `unknown`. The file is
not rewritten.

> ⚠️ **Rename is not atomic under contention.** An earlier prototype claimed messages by renaming
> and appeared to pass a concurrency test with one winner. Under genuinely simultaneous access the
> same test produced **four** winners — the original test had computed its start delay inside each
> worker, so the workers never actually collided. Exclusive create is the only primitive that held.
>
> Any implementation MUST include a contention test with a shared barrier, not a per-worker sleep.

### 4.4 Retention

`_read/` is never pruned automatically. Retention is the operator's decision, not the library's.

### 4.5 Portability: the replace pattern is not contention-safe on Windows

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

**The reader must retry too, and this half is the dangerous one.** On Windows, *opening* a file
while another process replaces it also raises `PermissionError`. An implementation that treats every
`OSError` as "no such record" will transiently report an **existing** key as absent — and a caller
that believes it will recreate the key, destroying another vessel's value. Silent, intermittent, and
invisible in single-threaded testing.

Readers MUST therefore distinguish three outcomes rather than two:

| Condition | Correct behaviour |
|-----------|-------------------|
| File does not exist (`FileNotFoundError`) | Return "absent" immediately |
| File is locked (`PermissionError`) | **Retry** — it is mid-replace, not gone |
| File is unparseable | Return "absent"; one corrupt record must not blind the whole read |

---

## 4A. Shared state and leases (L4)

State is the answer to "what is true right now", as opposed to presence ("who is here") and stream
("what was said"). It lives in `state/`.

### 4A.1 Record

One file per key, at `state/<slug>.<hash8>.json`:

```json
{
  "key": "build",
  "value": { "status": "green", "tests": 94 },
  "version": 7,
  "updated_by": "codex-1a2b3c4d",
  "updated_at": "2026-08-16T14:41:02.113Z"
}
```

The filename carries **both** a slug and a hash of the key. A slug alone would let `a/b` and `a_b`
share a file; a hash alone would make `ls state/` unreadable, breaking the commitment that a source
is inspectable with ordinary tools. Keys are arbitrary strings; the slug is what makes them safe as
filenames, and implementations MUST ensure a key can never escape `state/`.

### 4A.2 Compare-and-set

`set` is last-writer-wins. Writers that cannot tolerate a lost update use compare-and-set, which
introduces **no new primitive**:

> To move a key from version *N* to *N+1*, a writer must exclusively create the token
> `state/_cas/<slug>.<hash8>.<N+1>`. Only one writer can. The winner then writes the record.

This is the same exclusive-create used for message claiming (§4.3). There is no lock involved, no
deletion, and no expiry.

The intended usage is read-modify-CAS-retry:

```python
while True:
    cur = vessel.get_state("n")
    if vessel.cas_state("n", cur["value"] + 1, cur["version"]):
        break
```

### 4A.3 Leases

**A lock is not a separate mechanism.** It is a state key `_lock:<name>` whose value is
`{owner, token, acquired_at, expires_at}`, acquired by CAS. Because CAS is exclusive-create
underneath, exactly one vessel wins — including the case several vessels notice the same dead holder
simultaneously and all attempt takeover at once.

- **Acquire** succeeds if the key is unset, or held with `expires_at` in the past.
- **Release** and **renew** MUST verify the caller's token. Refusing a mismatched token is the whole
  point: otherwise one worker's cleanup silently unlocks another worker's critical section.
- **Leases expire.** A vessel can die holding one, and nothing in Keiraku may delete another
  vessel's record to rescue it — so expiry is the only safe recovery, exactly as with presence
  staleness (§3.1).

Locks are advisory. Nothing enforces that a vessel holds one before writing.

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

vessel.set_state(key, value)                       # last writer wins
vessel.get_state(key) -> record | None
vessel.list_state() -> [record]
vessel.cas_state(key, value, expected_version) -> bool

vessel.acquire_lock(name, ttl=60.0) -> token | None
vessel.renew_lock(name, token, ttl=60.0) -> bool
vessel.release_lock(name, token) -> bool
with vessel.lock(name):                            # raises LockUnavailable
    ...

read_state(source, key)                            # read without attaching
list_state(source)
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

## 8. MCP (optional)

A source MAY be exposed as an MCP server, letting agents that have no hook surface attach without a
binding. This is OPTIONAL: it is a second door onto the same source, never a replacement for the
on-disk contract, and an MCP-attached vessel is an ordinary vessel with `host` = `mcp`.

- Transport is newline-delimited JSON-RPC 2.0 over stdio.
- Identity is derived from the client name declared in `initialize` plus the server process, since
  an MCP client has no session id of its own. One connection is one vessel.
- Tool output that renders peer messages MUST carry the provenance warning of §4.1. This obligation
  is stronger here than for hooks: an MCP client places tool output directly into the model's
  context, where an unmarked peer message is indistinguishable from something the user said.
- A server SHOULD agree with the client's offered `protocolVersion` rather than insisting on its
  own. Disagreeing over a version string is the most common way a working server appears broken.

## 7. Conformance

An implementation is conformant if it:

1. Refuses to attach to a directory without `keiraku.json`
2. Derives `vessel_id` per §2 and never routes on `alias`
3. Claims via exclusive create per §4.3, verified by a barrier-synchronised contention test
4. Retries BOTH the replace and the read per §4.5 (required on Windows, harmless elsewhere)
5. Never deletes another vessel's presence file
6. Preserves `provenance` through to whatever consumes the message
7. Tolerates a corrupt or unreadable record without failing the whole read
8. Implements compare-and-set via the version token in §4A.2, verified under barrier-synchronised
   contention, and never lets a state key escape `state/`
9. Verifies the caller's token before releasing or renewing a lease (§4A.3)
10. If it exposes MCP (§8), carries the §4.1 provenance warning into tool output

---

## Open questions


- Is `stale_after` per-source configuration, or per-vessel declaration? A vessel that knows it wakes
  hourly should arguably be allowed to say so.
- `state/` is last-writer-wins with compare-and-set as the opt-in safe path. Whether a CRDT shape is
  warranted is still open, and deliberately unanswered: it should be decided by conflicts that
  actually occur in use, not by anticipation.
- `state/_cas/` tokens accumulate, one per version. They are tiny and form a natural version log,
  but nothing prunes them.
