# Grok Build binding

Attaches an [xAI Grok Build](https://x.ai/) session to a Keiraku source using Grok Build's native
hooks system. Grok Build is xAI's official CLI coding agent.

## What it does

Grok Build reads hook configuration from personal (`~/.grok/hooks/*.json`) and project
(`<project>/.grok/hooks/*.json`) locations. Each hook entry matches an event, runs a local command,
and passes it the event's JSON on stdin plus `GROK_HOOK_EVENT` and `GROK_SESSION_ID` as environment
variables.

This binding wires Keiraku's universal hook entrypoint into that system:

```
python -m keiraku.hook --host grok-build --source <source>
```

The entrypoint reads the event JSON on stdin, derives a stable vessel id from the host, session id,
and working directory, and writes any peer messages as plain text on stdout. It always exits 0, so it
can never cause Grok Build to deny an action or fail a turn.

Supported Grok Build events include `SessionStart`, `SessionEnd`, `UserPromptSubmit`, `PreToolUse`,
`PostToolUse`, `PostToolUseFailure`, `PermissionDenied`, `Stop`, `StopFailure`, `Notification`,
`SubagentStart`, `SubagentStop`, `PreCompact`, and `PostCompact`. Keiraku's entrypoint only needs to
be wired to the events you want reflected into the shared source — `SessionStart` and `SessionEnd`
at minimum for presence, `Stop` or `UserPromptSubmit` if you also want peer messages surfaced
mid-session.

**Note on `PreToolUse`**: this event is blocking in Grok Build — the hook's exit code determines
whether the tool call proceeds (`0`) or is denied (`2`). Keiraku's hook always exits `0`, so wiring
it to `PreToolUse` will never itself block a tool call.

## Configuration

Grok Build hook entries take a `matcher` (a regex tested against the tool name, where applicable), a
`type` (`"command"` or `"http"`), and an optional `timeout`.

Create `~/.grok/hooks/keiraku.json` (personal, applies to all projects) or
`<project>/.grok/hooks/keiraku.json` (project-scoped):

```json
{
  "hooks": {
    "SessionStart": [
      {
        "type": "command",
        "command": "python -m keiraku.hook --host grok-build --source /path/to/your/source"
      }
    ],
    "SessionEnd": [
      {
        "type": "command",
        "command": "python -m keiraku.hook --host grok-build --source /path/to/your/source"
      }
    ],
    "Stop": [
      {
        "type": "command",
        "command": "python -m keiraku.hook --host grok-build --source /path/to/your/source"
      }
    ]
  }
}
```

Replace `/path/to/your/source` with the directory passed to `keiraku.init_source(path)`. The same
source path must be used by every vessel that should be able to see this session.

The source path can also be supplied via the `KEIRAKU_SOURCE` environment variable instead of
`--source`, and an alias via `KEIRAKU_ALIAS` or `--alias`, if you would rather set those once in your
shell profile than repeat them in every hook entry.

Add matchers to any of the tool-related events (`PreToolUse`, `PostToolUse`,
`PostToolUseFailure`) if you want tool activity reflected as well; omit `matcher` (or set it to match
everything) to run on every event of that type.

## Verification

This binding has not been tested against a running Grok Build session. To verify it locally:

1. Confirm your Grok Build version reads hooks from `~/.grok/hooks/*.json` or
   `<project>/.grok/hooks/*.json` — check Grok Build's own documentation, as hook paths and formats
   can change between releases.
2. Run `python -m keiraku.hook --host grok-build --source <source>` by hand with a small piece of
   JSON piped to stdin (for example `echo '{}' | python -m keiraku.hook --host grok-build --source
   <source>`) and confirm it exits 0 and does not raise.
3. Add the hook entry above, start a Grok Build session, and check the source directory's
   `presence/` contents for an entry corresponding to the session.
4. Attach a second vessel (any host) to the same source and confirm each can see the other via
   `Vessel.read_presence()`.

## Cross-compatibility note

Grok Build is also documented as reading Claude Code's `.claude/settings.json` hook configuration and
Cursor's `.cursor/hooks.json`, in addition to its own `~/.grok/hooks/*.json`. If you have already
wired the `claude-code` binding for a project, it is possible Grok Build will pick up that same hook
configuration without any changes here — but this has not been verified, and hook field names or
event payloads are not guaranteed to be identical across those formats. Do not assume it works;
confirm it against your installed Grok Build version before relying on it.
