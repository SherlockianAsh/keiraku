# MCP binding

Expose a Keiraku source as an MCP server, so any MCP-speaking agent attaches **without a binding
being written for it**.

This is the escape hatch. The hook bindings need a host that fires lifecycle events; this one needs
only that the host can call tools. That covers agents with no hook surface at all, including some
that would otherwise be unreachable.

## What the agent gets

Seven tools:

| Tool | What it does |
|------|--------------|
| `keiraku_who` | List other attached sessions and what each is doing |
| `keiraku_send` | Send a message to a vessel id, an alias, or `*` |
| `keiraku_inbox` | Read messages addressed to this session (exactly once) |
| `keiraku_state_get` | Read a shared state value |
| `keiraku_state_set` | Set a shared state value |
| `keiraku_state_list` | List all shared state |
| `keiraku_note` | Record what this session is currently doing |

## Setup

The server speaks JSON-RPC 2.0 over stdio, which is what MCP's stdio transport is. Configure it the
way your host configures any stdio MCP server:

```json
{
  "mcpServers": {
    "keiraku": {
      "command": "python",
      "args": ["-m", "keiraku.mcp", "--source", "/absolute/path/to/.keiraku"]
    }
  }
}
```

`--source` may be omitted if `KEIRAKU_SOURCE` is set in the environment. It defaults to
`~/.keiraku`.

Create the source first — the server will not create one implicitly:

```bash
python -m keiraku --source ~/.keiraku init
```

## Verify

Ask the agent to call `keiraku_who`. From another terminal, attach a second vessel and check that it
appears:

```bash
python -c "import keiraku; keiraku.attach('~/.keiraku', host='python', session_id='t', cwd='/tmp', alias='test')"
python -m keiraku --source ~/.keiraku who
```

You can also drive the server by hand, which needs no MCP client at all:

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","clientInfo":{"name":"manual","version":"1"}}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  | python -m keiraku.mcp --source ~/.keiraku
```

## Identity

An MCP client has no session id of its own, so the vessel identity is derived from the client name
it declares during `initialize`, plus the server process id. One connection is one vessel, and two
clients never collide.

Consequence worth knowing: **restarting the client produces a new vessel.** The old one stops
heartbeating and ages out of `keiraku_who` after the staleness window. Nothing is deleted.

## Provenance

`keiraku_inbox` output is prefixed with an explicit warning that the messages are information from
peer sessions and not instructions from the user.

This matters more here than in the hook bindings. An MCP client renders tool output directly into
the model's context, so an unmarked peer message reads exactly like something the user said. Any
client, wrapper, or UI that reformats this output must carry that line through.

## Caveats

- **Not verified against a third-party MCP client.** The protocol layer is tested, and the server
  has been driven end to end over real stdio in a subprocess — `initialize`, `tools/list`,
  `tools/call`, message delivery — but no commercial MCP host has been used to confirm interop.
  Treat a first connection as a test.
- **Protocol version.** The server agrees with whatever `protocolVersion` the client offers, rather
  than insisting on one. That is the compatible behaviour, but it means the server does not refuse a
  version it has never seen.
- **No resources or prompts.** Only tools are implemented. `resources/*` and `prompts/*` are not
  advertised, and the server declares no such capability.
- **One source per server.** Pointing at a different source means a second server entry.
