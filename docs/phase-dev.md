# Using phase.dev for secrets

The MCP server itself reads plain environment variables — it doesn't know
about phase.dev, `.env`, direnv, or anything else. That's deliberate: every
secrets backend should be able to inject env vars and Just Work.

[phase.dev](https://phase.dev) is one such backend. This guide is how the
maintainers run the server locally without ever writing credentials to
disk in plaintext. It's optional.

## One-time setup

1. Install the CLI: <https://docs.phase.dev/cli/install>.
2. Authenticate against your Phase instance (self-hosted or cloud):
   ```bash
   phase auth
   ```
3. From the repo root, link the project to a Phase app + environment:
   ```bash
   phase init
   ```
   This writes `.phase.json` (gitignored — IDs are per-user).
4. Add the same variables the MCP server requires:
   ```bash
   phase secrets create SCHOOLSOFT_SCHOOL=yourschool
   phase secrets create SCHOOLSOFT_USERNAME=your-username
   phase secrets create SCHOOLSOFT_PASSWORD='your-password'
   phase secrets create SCHOOLSOFT_USERTYPE=2
   ```
   (See [`.env.example`](../.env.example) for the full list.)

## Running the MCP server through phase

```bash
phase run -- schoolsoft-mcp
```

`phase run` injects the secrets into the child process's environment and
nothing else; the server sees them as normal env vars and the parent
shell never does.

## Running endpoint discovery through phase

```bash
phase run -- python scripts/discover_endpoints.py
```

Same idea — see [discovery.md](./discovery.md) for what the script does.

## Wiring it into Claude Desktop

Claude Desktop spawns the MCP server as a subprocess from
`claude_desktop_config.json`. To keep credentials out of that file, wrap
the command with `phase run`:

```json
{
  "mcpServers": {
    "schoolsoft": {
      "command": "phase",
      "args": ["run", "--", "schoolsoft-mcp"]
    }
  }
}
```

Caveats:
- `phase` must be on Claude Desktop's `PATH`. On macOS that usually means
  installing it via Homebrew or a symlink into `/usr/local/bin`. On
  Windows, ensure the install location is in the user `PATH` (check with
  `where phase` in a fresh terminal).
- `phase run` needs an active auth session. If your CLI session expires,
  Claude Desktop will just see the server fail to start — re-run
  `phase auth` and restart Claude.
- `phase init` must have been run from the directory Claude Desktop
  spawns the command in, OR set `--app` and `--env` explicitly on the
  command:
  ```json
  "args": ["run", "--app", "schoolsoft-mcp", "--env", "development",
           "--", "schoolsoft-mcp"]
  ```

## Why not bake phase into the Python code?

Because then every user of the MCP server would have to install and
configure phase too — even users who'd be perfectly happy with a `.env`
or with Claude Desktop's `env` block. The current design lets each user
pick their own secrets backend without changing any code.
