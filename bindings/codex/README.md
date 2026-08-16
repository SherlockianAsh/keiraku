# Codex CLI binding

Attaches an OpenAI Codex CLI session to a Keiraku source using Codex's hook system. No
Codex-specific script is required — this binding configures Codex to call the universal
Keiraku hook entrypoint.

## What it does

Codex CLI discovers hooks from one of the following locations:

- `~/.codex/hooks.json`
- `~/.codex/config.toml` (a `[hooks]` table)
- `<repo>/.codex/hooks.json`
- `<repo>/.codex/config.toml` (a `[hooks]` table)

Codex fires a set of lifecycle events during a session: `SessionStart`, `SessionEnd`,
`UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `PermissionRequest`, `PreCompact`,
`PostCompact`, `Stop`, `SubagentStart`, `SubagentStop`. For each configured event, Codex
writes a JSON payload to the hook command's stdin and (depending on the event) may read
JSON back from stdout.

Per the current upstream documentation, the payload carries `session_id`, `cwd`,
`hook_event_name`, `transcript_path`, and `model`; turn-scoped events additionally carry `turn_id`
and `permission_mode`.

Both this event list and the payload shape are taken from documentation, not from a run against a
live Codex install — and the hooks feature is young enough that both have already changed once (see
Caveats). Check them against your installed version rather than against this file. The binding
degrades safely if they differ: an unrecognised event is treated as a turn, and a missing
`session_id` causes the hook to skip and exit `0` rather than guess an identity.

Pointing these hooks at `python -m keiraku.hook --host codex --source <source>` is enough
to attach the session as a vessel — Keiraku heartbeats, delivers pending peer messages
onto the model's context, and detaches cleanly at session end. The hook entrypoint always
exits `0`, so a Keiraku failure cannot break a Codex session.

## Configuration

Add a `hooks.json` (or the equivalent `[hooks]` table in `config.toml`) at one of the
locations above.

```json
{
  "hooks": {
    "SessionStart": [
      {
        "command": ["python", "-m", "keiraku.hook", "--host", "codex", "--source", "<source>"]
      }
    ],
    "UserPromptSubmit": [
      {
        "command": ["python", "-m", "keiraku.hook", "--host", "codex", "--source", "<source>"]
      }
    ],
    "Stop": [
      {
        "command": ["python", "-m", "keiraku.hook", "--host", "codex", "--source", "<source>"]
      }
    ],
    "SessionEnd": [
      {
        "command": ["python", "-m", "keiraku.hook", "--host", "codex", "--source", "<source>"]
      }
    ]
  }
}
```

Replace `<source>` with the path to a directory initialised by `keiraku.init_source(path)`.

Alternatively, set the source via environment variable instead of passing `--source` on
every command:

```json
{
  "hooks": {
    "SessionStart": [
      { "command": ["python", "-m", "keiraku.hook", "--host", "codex"] }
    ]
  }
}
```

```bash
export KEIRAKU_SOURCE=/path/to/source
```

An optional stable display name can be set with `--alias NAME` or `KEIRAKU_ALIAS`,
instead of the derived vessel id. Add `--quiet` to suppress any non-essential stdout from
the hook.

Wiring every lifecycle event is not required to get a working vessel. `SessionStart`,
`UserPromptSubmit` (or `Stop`), and `SessionEnd` are the minimum set for presence and
detach to behave correctly; the remaining events add finer-grained heartbeats and are
optional.

### Windows: `commandWindows`

Codex's hook config supports a `commandWindows` override alongside `command`, for hosts
where the invocation differs on Windows (for example, `py` instead of `python`, or a
different shell quoting convention):

```json
{
  "hooks": {
    "SessionStart": [
      {
        "command": ["python", "-m", "keiraku.hook", "--host", "codex", "--source", "<source>"],
        "commandWindows": ["py", "-m", "keiraku.hook", "--host", "codex", "--source", "<source>"]
      }
    ]
  }
}
```

## Verification

1. Initialise a source: `python -c "import keiraku; keiraku.init_source('/path/to/source')"`.
2. Configure the hooks as above, pointing at that source.
3. Start a Codex CLI session in a repo where the hook config is discoverable.
4. Check that a presence entry appears under the source directory for this session (for
   example, by attaching a second vessel to the same source and calling
   `Vessel.read_presence()`).
5. Confirm the session ends cleanly and the vessel's presence entry reflects a detached
   state after the session closes.

If presence entries do not appear, confirm the hook command is actually being invoked —
run it manually with a sample event JSON on stdin and inspect the source directory
directly.

## Windows caveat — unverified

Codex CLI's documentation states hooks are enabled by default and lists Windows support,
including the `commandWindows` override shown above. However, hooks were introduced in
v0.114 where the feature was described as experimental, opt-in, and not available on
Windows. Whether hooks fire reliably on native Windows depends on the Codex CLI version in
use, and this has not been independently verified against a current build.

Do not assume this binding works unmodified on native Windows. Test it against your
installed Codex CLI version first — check `codex --version` and consult the Codex CLI
release notes for the hooks feature, then run through the verification steps above before
relying on it.
