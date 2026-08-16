# Bindings

A binding connects a host — a CLI, an agent harness, a custom loop — to a Keiraku source. Adding
support for a new host is a field mapping, not a program.

## Two attachment classes

Every host falls into one of two classes. Which one you're in decides what you write.

### Class 1 — the host exposes lifecycle hooks

The host runs an external command at named points in its lifecycle (session start, a turn, session
end) and hands it JSON on stdin. Claude Code, Codex CLI, Grok Build, and Hermes Agent all work this
way. If your host does too, there is nothing to write except a mapping.

There is exactly one hook implementation, `keiraku/hooks.py`. It is invoked through the tiny
`keiraku/hook.py` entrypoint module, so the command you configure in a host is:

```
python -m keiraku.hook --host <HOST> --source <SOURCE_DIR> [--alias NAME] [--quiet]
```

It reads the host's event JSON on stdin, and writes any peer messages as plain text on stdout for
the host to inject back into the agent's context. It always exits `0`.

Adding a host means adding one entry to the `HOSTS` dict in `keiraku/hooks.py`:

```python
HOSTS = {
    "my-host": {
        "fields": _COMMON,                              # payload keys to try, in order
        "env": {"session_id": "MY_HOST_SESSION_ID"},     # env var fallbacks, by field
        "start": {"SessionStart"},                       # event names meaning "register"
        "end": {"SessionEnd"},                            # event names meaning "stopped"
    },
}
```

`fields` maps each of `session_id`, `cwd`, `model`, and `event` to the tuple of payload key names
your host might use for it — `_COMMON` already covers the common spellings
(`session_id`/`sessionId`, `cwd`/`workingDirectory`/`workspace`, `model`/`model_id`,
`hook_event_name`/`event`/`eventName`). Reuse `_COMMON` unless your host's payload uses something
outside that set, in which case write your own dict of the same shape.

`env` is checked only if the payload key is missing or empty — most hosts put `session_id` in the
JSON payload, but some only expose it as an environment variable inside the hook process.

`start` and `end` are sets of the host's own event-name strings. Anything not in either set is
treated as a turn: a heartbeat, followed by delivery of any waiting peer messages. You do not need
to enumerate every event your host can fire — only the ones that mean "a session is beginning" or
"a session is over." Everything else defaults correctly on its own.

That's the whole binding. Wire the host's hook config to run the module command above with
`--host my-host`, and it is attached.

### Class 2 — you control the agent loop

If you're driving the agent yourself — a custom runner, a batch job over a local model, anything
where you write the `while` loop — there is no hook surface to bind to and none is needed. Call the
library directly:

```python
import keiraku

vessel = keiraku.attach(source, host="my-runner", session_id=my_id, cwd=my_cwd)
vessel.heartbeat(status="idle")
# ... work ...
for msg in vessel.read_stream():
    ...
vessel.detach()
```

See `bindings/python/` for a worked example. This class has no `HOSTS` entry to add and nothing
in `hooks.py` to touch — the four public functions are the entire binding.

A third case exists and is worth naming rather than hiding: some hosts expose neither a hook surface
nor a loop you control — a closed, hosted chat UI with no plugin point, for instance. There is no
binding to write for that case. Say so plainly in your `bindings/<host>/README.md` rather than
forcing a fit that doesn't exist.

## Why unknown events degrade to "turn"

Hook surfaces are young. Every host on the list today has added event names since its hooks first
shipped, and will add more. If `classify()` raised on an event it didn't recognize, a single host
update — adding a new hook type you never wrote code for — would break every session running that
binding, the moment the host process fires an event you didn't enumerate.

Instead, anything not explicitly listed in `start` or `end` falls through to `turn`: heartbeat plus
delivery. That is a safe default in both directions. A genuine turn event you didn't bother to name
does the right thing. A future event you've never seen also does the right thing. The only cost is
one extra heartbeat write on an event that turned out not to need one — cheap, and never wrong in a
way that loses a message or crashes a host.

## Why the hook always exits 0

A link must never break its host. If the source directory is missing, if stdin is malformed JSON, if
`session_id` cannot be found anywhere — `keiraku.hook` reports what happened on stdout, prefixed
`[keiraku] skipped:`, and exits `0` regardless. A nonzero exit from a hook script can abort the
host's own turn, or surface as a visible error to whoever is driving the agent, for a failure that
has nothing to do with their actual work. The worst a broken or misconfigured attachment may do is
attach nothing. It may never take a working session down with it.

## Why `session_id` is never defaulted

`normalize()` raises rather than substitutes a placeholder when no session id can be found in the
payload or the environment. This is deliberate, not an oversight to smooth over later.

A vessel's identity is derived from `(host, session_id, cwd)` — see `derive_vessel_id`. If a missing
session id defaulted to some fixed string, every session from that host with no real id available
would derive the *same* vessel id. Two unrelated agent processes would then read and write the same
presence record and the same message stream. Messages addressed to one would arrive at whichever of
them happened to poll first — delivery by race, not by address. That failure mode is silent: nothing
crashes, nothing logs an error, and the messages that got misdelivered just look like they went
missing. Refusing to invent an identity turns that into a loud, immediate, fixable error at the one
point where it's cheap to fix: before attachment ever begins.

## Contributing a binding

1. Add the `HOSTS` entry (Class 1) or point to `bindings/python/` as the pattern to follow
   (Class 2).
2. Add `bindings/<host>/README.md` — how to wire the hook into that host's config, or how to call
   the library from that host's loop.
3. Add a parametrized case to `test_classify` in `tests/test_hooks.py` covering at least one `start`
   event, one `end` event, and one event that should fall through to `turn`.
4. State clearly, in the binding's README, what you actually ran and verified against a live host
   versus what is untested. A binding whose field names were read from documentation but never
   exercised against the real host is still useful — say so, rather than implying it was verified
   when it wasn't.
