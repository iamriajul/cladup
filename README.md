# cladup

Drop-in Claude CLI shim for harnesses that call `claude -p`.

`cladup` does not call native `claude -p`. For print mode it starts the Claude
Code TUI in `tmux`, sends the prompt, reads Claude's transcript JSONL, and emits
Claude-compatible text, JSON, or stream JSON output.

Tested Claude Code version: `2.1.173`

Package release version: `2.1.173`

## Install

Use directly from GitHub:

```sh
npx --yes github:iamriajul/cladup --help
npx --yes github:iamriajul/cladup -p "summarize this diff"
```

Use as a Vibe Kanban agent command:

```sh
npx --yes github:iamriajul/cladup
```

Install globally:

```sh
npm install -g github:iamriajul/cladup
```

The package exposes two binaries:

| Binary | Target |
| --- | --- |
| `cladup` | `./cladup.py` |
| `claude` | `./cladup.py` |

Put your npm global bin directory before the real Claude binary on `PATH` only
when you intentionally want `claude` to resolve to `cladup`.

## Behavior

| Command | Behavior |
| --- | --- |
| `cladup` | passthrough to Claude Code TUI |
| `cladup --help` | passthrough to Claude Code help |
| `cladup --version` | passthrough to Claude Code version |
| `cladup mcp list` | passthrough to Claude Code |
| `cladup -p "prompt"` | print-mode emulation through Claude Code TUI |
| `cladup -p --output-format json "prompt"` | Claude-style result JSON |
| `cladup -p --output-format stream-json "prompt"` | transcript-level JSONL stream plus final result |

`cladup` intercepts only `-p` / `--print`.

## Print Mode

Print mode steps:

1. Create or resume a Claude session ID.
2. Start Claude Code TUI in a detached `tmux` session.
3. Send the prompt to the TUI.
4. Read `~/.claude/projects/*/<session-id>.jsonl`.
5. Emit output in the requested format.
6. Kill the temporary `tmux` session.

The TUI command prefers the real installed `claude` binary. If no non-wrapper
Claude binary is found, `cladup` uses:

```sh
npx -y @anthropic-ai/claude-code[@detected-version]
```

Version detection order:

1. `CLADUP_CLAUDE_VERSION`
2. `CLADUP_USE_TESTED_VERSION=1`
3. real `claude --version`
4. `~/.claude.json` `lastReleaseNotesSeen`
5. unpinned `@anthropic-ai/claude-code`

Use the tested Claude Code version:

```sh
cladup --cladup-tested-version -p "question"
CLADUP_USE_TESTED_VERSION=1 cladup -p "question"
```

Pin the tested Claude Code version explicitly:

```sh
cladup --cladup-claude-version 2.1.173 -p "question"
CLADUP_CLAUDE_VERSION=2.1.173 cladup -p "question"
```

## Requirements

- macOS or Linux
- `python3`
- `tmux`
- `npx`
- working Claude Code auth

Check auth with a real request. `claude auth status` can show cached account
metadata while real requests fail. `cladup` does not run auth preflight by
default.

## Compatibility

Passthrough examples:

```sh
cladup auth status
cladup mcp list
cladup plugin list
cladup --model sonnet
```

Print-mode support:

- `--output-format text`
- `--output-format json`
- `--output-format stream-json`
- `--input-format text`
- `--input-format stream-json`
- `--resume`
- `--continue`
- `--session-id`
- `--fork-session`
- `--model`
- `--add-dir`
- `--permission-mode`
- `--allowed-tools`
- `--disallowed-tools`
- `--mcp-config`
- `--settings`
- `--agents`
- plugin flags

Accepted best-effort flags:

- `--include-partial-messages`
- `--include-hook-events`
- `--fallback-model`
- `--max-budget-usd`
- `--max-turns`
- `--no-session-persistence`
- `--json-schema`

`--json-schema` is appended to the prompt as an instruction.

## Streaming

`--output-format stream-json` emits transcript-level JSONL records.

It is not token-level streaming. Claude Code TUI transcripts persist full
message records, not native print-mode token events.

Known difference:

- native `claude -p --verbose --output-format stream-json` can emit thinking
  token events
- Claude Code TUI transcripts can contain empty signed thinking blocks
- `cladup` cannot recover thinking token text or exact thinking token counts
  when Claude did not persist them

## Environment

```sh
CLADUP_READY_TIMEOUT=180 cladup -p "question"
CLADUP_ASSISTANT_START_TIMEOUT=120 cladup -p "question"
CLADUP_CLAUDE_BIN=/path/to/claude cladup -p "question"
CLADUP_AUTH_PREFLIGHT=1 cladup -p "question"
CLADUP_FORCE_PACKAGE_RUNNER=1 cladup -p "question"
CLADUP_PACKAGE_RUNNER=npx cladup -p "question"
CLADUP_PACKAGE_RUNNER=bunx cladup --version
CLADUP_DEFAULT_PERMISSION_MODE=acceptEdits cladup -p "question"
CLADUP_DB="$HOME/.local/state/cladup/history.sqlite" cladup -p "question"
CLADUP_USE_TESTED_VERSION=1 cladup -p "question"
CLADUP_CLAUDE_VERSION=2.1.173 cladup -p "question"
```

`CLADUP_PACKAGE_RUNNER` accepts `npx`, `bunx`, `bun`, or `auto`.

`npx` is the default. Bun may be faster, but local verification did not
reliably resolve `@anthropic-ai/claude-code@2.1.173`.

Turn logging is disabled by default. Set `CLADUP_DB` or pass `--cladup-db` to
enable SQLite history.

## Startup Prompts

`cladup` automatically presses Enter when Claude Code shows `Enter to confirm`.

This handles prompts such as:

- project trust
- new MCP servers found

## Troubleshooting

Refresh Claude auth:

```sh
claude auth login
```

or open Claude Code TUI and run:

```text
/login
```

Increase startup wait:

```sh
CLADUP_READY_TIMEOUT=180 cladup -p "question"
```

Enable auth preflight:

```sh
CLADUP_AUTH_PREFLIGHT=1 cladup -p "question"
```

## Development

Run tests:

```sh
npm test
```

The test suite is pure Python and does not make Claude API calls.

Check package contents:

```sh
npm pack --dry-run
```

## Prior Art

Inspired by [`ishmum123/clawp`](https://github.com/ishmum123/clawp).

`clawp` proved the TUI-plus-transcript architecture. `cladup` keeps normal
Claude CLI passthrough and intercepts only print mode.

## License

MIT. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

## ⚠️ Disclaimer

> [!WARNING]
> For Educational Purposes Only
>
> This project is intended solely for educational and research purposes. It is
> provided "as-is" without any warranty. Commercial use is strictly prohibited.
>
> By using this software, you agree that you will not use it for any commercial
> purposes, and you are solely responsible for ensuring your use complies with
> all applicable laws and regulations. The authors and contributors are not
> responsible for any misuse or damages arising from the use of this software.
>
> If this project helps you, please give it a ⭐ Star!
