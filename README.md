# 経絡 Keiraku

**Attach any AI agent harness to a shared memory source.**

Keiraku is a small, host-agnostic link that lets multiple AI agent sessions — running in different
CLIs, on different machines, driven by different models — see each other and share a common source
of state.

Write the attachment once. Attach it everywhere.

> ⚠️ **Status: pre-alpha. Design stage — there is no working implementation yet.**
> This repository currently contains the specification and roadmap. Nothing here runs. If you found
> this looking for a working tool, come back later or watch the repo.

---

## The problem

You have three terminals open. One is running a coding agent on your backend, one on your frontend,
one is a local model chewing through a batch job.

None of them know the others exist.

Each agent host solves memory *within* a session, and some solve it across restarts of *their own*
sessions. None of them solve it **between different agents, in different harnesses, at the same
time**. There is no shared answer to "who else is working right now, on what, and what did they
just learn?"

Keiraku is that shared answer.

## The approach

A **hub**, not a mesh. Vessels attach to a source; they do not address each other directly. Two
vessels are connected by both being attached, not by knowing about each other.

```
        ┌──────────── SOURCE ────────────┐
        │  presence/   stream/   state/  │
        └────▲──────────▲──────────▲─────┘
             │          │          │
        ┌────┴───┐ ┌────┴───┐ ┌────┴───┐
        │ vessel │ │ vessel │ │ vessel │
        └────────┘ └────────┘ └────────┘
```

The source is a plain directory. No daemon, no server, no port. That is deliberate: a file-backed
source is the only kind that two processes on opposite sides of a container, VM, or WSL boundary can
both read without anything running in between.

## Layers

| Layer | What it does | Status |
|-------|--------------|--------|
| **L0** Protocol + client | `attach` · `heartbeat` · `publish` · `read` | 🔨 spec written |
| **L1** Bindings | Per-host adapters — hooks, or a direct library call | ⬜ planned |
| **L2** Presence | Who is alive, where, doing what | ⬜ planned |
| **L3** Stream | Addressed delivery between vessels | ⬜ planned |
| **L4** State | Shared condition + locks | ⬜ v2 |

**L0 is deliberately not hooks.** Hooks are one binding among several. If hooks were the foundation,
only hook-bearing hosts could ever attach — which would exclude every local-model runner.

## Attachment classes

Every agent host falls into one of three classes:

| Class | Meaning | How it attaches |
|-------|---------|-----------------|
| **1** | Host exposes lifecycle hooks | A hook script. Zero cooperation from the host. |
| **2** | You control the agent loop | A direct library call inside your loop. |
| **3** | Closed surface | ❌ Not possible. No attachment point exists. |

### Known hosts

| Host | Class | Surface |
|------|-------|---------|
| Claude Code | 1 | `SessionStart`, `UserPromptSubmit`, `PreToolUse`/`PostToolUse`, … |
| Codex CLI | 1 | `~/.codex/hooks.json` |
| Grok Build | 1 | `~/.grok/hooks/*.json` |
| Hermes Agent | 1 | `~/.hermes/config.yaml` shell hooks, or Python plugins |
| Ollama / custom loops | 2 | direct library call |
| Hosted chat UIs | 3 | unreachable |

Class 3 is stated plainly rather than papered over. Some things genuinely cannot be attached to, and
a tool that pretends otherwise wastes your afternoon.

### Why this is tractable

Independent vendors have converged on the same hook contract: named lifecycle events, a shell
command, JSON on stdin, JSON on stdout. One vendor's CLI even reads a competitor's hook config file
directly. The binding layer is therefore closer to **event-name mapping** than to real translation —
which is what makes "attach anywhere" a realistic goal rather than a marketing line.

## What Keiraku is not

- **Not a replacement for a host's own memory.** If your agent already persists its own state, keep
  it. Keiraku is about state *between* agents.
- **Not a chat system.** Messaging falls out of shared state; it is not the point.
- **Not a framework.** It does not want to own your agent loop. It wants four functions in it.

## Design commitments

- **Files, not a service.** Inspectable with `ls` and `cat`. Debuggable at 3am.
- **No daemon.** Nothing to start, supervise, or forget to restart.
- **Atomic claims.** Message claiming uses exclusive file creation (`O_EXCL` / `CreateNew`) — the
  only primitive that survives real contention. Rename is *not* atomic under concurrent writers, a
  lesson learned the expensive way.
- **Derived addressing.** Vessels are identified from their host-provided session identity, never a
  hand-set variable. A shared default address means delivery by race instead of by address.
- **Provenance is preserved.** A message arriving from a peer vessel is marked as *information from
  a peer*, never as an instruction from the user. This distinction is load-bearing for any agent
  that acts on what it reads.

## Roadmap

1. **L0** — protocol, client library, contention and staleness tests
2. **L2** — presence and derived addressing
3. **L1** — bindings, starting with Claude Code, then Codex, Grok Build, Hermes Agent, Python
4. **L3** — addressed delivery, including a poll loop for always-on vessels
5. **L4** *(v2)* — shared state and locks

## Name

経絡 *keiraku* — the meridian channels: the paths within a single body that carry what animates it,
invisible from the outside. The vessels are not separate things being wired together; they are one
system that needed a way to feel itself.

## Licence

MIT — see [LICENSE](LICENSE).

## Contributing

Not yet — there is nothing to contribute to. When L0 lands, the most useful contribution will be a
binding for a host that isn't listed above. `BINDINGS.md` will document the shape.
