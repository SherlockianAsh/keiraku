# Keiraku binding — Hermes Agent

Attach a Nous Research Hermes Agent session to a Keiraku source.

## What this does

Hermes Agent is a self-hosted CLI plus a persistent agent runtime and messaging gateway. Its state
lives under `~/.hermes/`. It supports two extension points that can carry the Keiraku hook:

- **Shell hooks** — declared under a `hooks:` key in `~/.hermes/config.yaml`, keyed by event name.
- **Python plugins** — dropped into `~/.hermes/plugins/`.

This binding uses the shell hook path. There is no separate script to write — Keiraku ships a single
universal hook entrypoint that every binding calls the same way:

```
python -m keiraku.hook --host hermes-agent --source <SOURCE_DIR> [--alias NAME] [--quiet]
```

The hook reads the event JSON Hermes Agent writes to stdin, and writes any peer messages as plain
text to stdout. It always exits `0`, so a hook failure cannot take down the Hermes Agent session that
called it.

## Hook contract

Each entry under `hooks:` in `config.yaml` supports:

| Field | Required | Notes |
|-------|----------|-------|
| `command` | yes | The command to run. |
| `matcher` | no | `pre_tool_call` / `post_tool_call` only. |
| `timeout` | no | Seconds. Default `60`, max `300`. |
| `fail_closed` | no | `pre_tool_call` only. |

Scripts receive JSON on stdin. Upstream documents hook scripts as returning JSON on stdout.

**Keiraku's hook writes plain text, not JSON**, because that text is meant to be read by the agent
rather than parsed by the host. Two consequences worth knowing before you wire this up:

- The hook always exits `0`, so a host that ignores unparseable stdout will simply carry on.
- Whether Hermes Agent *tolerates* non-JSON stdout, or logs an error, or drops the output entirely,
  is **not verified here**. If your host is strict about it, run the hook with `--quiet`: it will
  still register presence and heartbeat, and simply not emit peer messages.

If you determine which of those Hermes actually does, that is a useful contribution — see
[BINDINGS.md](../../BINDINGS.md).

Relevant events: `on_session_start`, `on_session_end`, `on_session_finalize`, `pre_llm_call`,
`post_llm_call`, `pre_tool_call`, `post_tool_call`, `subagent_start`, `subagent_stop`.

## Configuration

Add to `~/.hermes/config.yaml`:

```yaml
hooks:
  on_session_start:
    - command: python -m keiraku.hook --host hermes-agent --source /path/to/your/keiraku-source
  on_session_end:
    - command: python -m keiraku.hook --host hermes-agent --source /path/to/your/keiraku-source
  on_session_finalize:
    - command: python -m keiraku.hook --host hermes-agent --source /path/to/your/keiraku-source
  pre_tool_call:
    - command: python -m keiraku.hook --host hermes-agent --source /path/to/your/keiraku-source
  post_tool_call:
    - command: python -m keiraku.hook --host hermes-agent --source /path/to/your/keiraku-source
```

Wire only the events you need. `on_session_start` and `on_session_end` are the minimum for presence
to work; add `pre_tool_call` / `post_tool_call` if you want tool activity reflected in heartbeats.

The source path can also be supplied via the `KEIRAKU_SOURCE` environment variable instead of
`--source`, and an alias via `KEIRAKU_ALIAS` instead of `--alias`.

### Python plugin alternative

If a shell hook is not suitable for a given deployment, the same entrypoint can be invoked from a
Python plugin under `~/.hermes/plugins/` by shelling out to it, or by calling the `keiraku` library
functions directly from the plugin process. The shell hook path above is the one Keiraku bindings
document as the default.

## Verifying the attachment

1. Configure at least `on_session_start` as above and start a Hermes Agent session.
2. Confirm a vessel entry appears under `presence/` in the configured source directory.
3. From a second attached vessel, call `publish` addressed to the Hermes Agent vessel's ID or alias,
   and confirm the message appears in that vessel's `read_stream()` output.
4. Repeat the check for each execution mode you actually use — interactive CLI, gateway, and
   subagent — rather than assuming one verified mode covers the others. See the caveat below.

## Reliability caveat

There is a report — surfaced in a search result and **not confirmed against the upstream tracker by
anyone who wrote this document** — that shell hooks for `pre_tool_call` and `on_session_finalize`
may not fire reliably in the kanban-worker `chat -q` execution context on some releases around
v0.13.0.

Treat that as a rumour worth testing, not as a known defect. It is repeated here only because the
failure mode it describes — hooks firing in one execution mode and silently not in another — is the
one that would quietly break this binding, and it is cheap to check before you rely on it.

Practical consequence: do not assume a hook verified in one execution mode (for example, interactive
CLI) behaves the same way in another (gateway or subagent contexts). Verify `on_session_start` and
`on_session_end` at minimum for the execution mode actually in use, and treat `pre_tool_call` /
`on_session_finalize` firing as something to confirm per mode rather than assume.

If those two events do not fire in your deployment, presence and heartbeats driven by
`on_session_start` / `on_session_end` will still work; only the tool-call-scoped signal is affected.
