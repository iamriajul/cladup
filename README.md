# cladup

Drop-in Claude CLI compatibility for harnesses that expect `claude`, without
calling the expensive `claude -p` path.

`cladup` behaves like a Claude CLI shim. Normal interactive commands pass
through to Claude Code. Print-mode commands (`-p` / `--print`) are intercepted
and emulated through the interactive Claude Code TUI in a detached `tmux` pane,
then the answer is read back from Claude's transcript JSONL.

This is meant for tools such as Vibe Kanban that are built around a single
Claude command but may internally call print mode.

## Quick Start

Use directly from GitHub:

```sh
npx github:iamriajul/cladup --help
npx github:iamriajul/cladup -p "summarize this diff"
```

Use as the agent command in Vibe Kanban:

```sh
npx github:iamriajul/cladup
```

Install globally if you want a shell-level replacement:

```sh
npm install -g github:iamriajul/cladup
```

The package exposes both `cladup` and `claude` binaries. Put your npm global bin
directory before any existing Claude binary on `PATH` only if you intentionally
want the `claude` name to resolve to `cladup`.

## What It Does

| Command shape | Behavior |
| --- | --- |
| `cladup` | launches the normal Claude Code TUI |
| `cladup --help` | passes through to official Claude help |
| `cladup mcp list` | passes through to Claude Code |
| `cladup -p "prompt"` | emulates print mode through the interactive TUI |
| `cladup -p --output-format json "prompt"` | emits a Claude-style result object |
| `cladup -p --output-format stream-json "prompt"` | streams transcript-level JSONL records, then a final result |

`cladup` never calls `claude -p`.

## How Print Mode Works

1. Generate or resume a Claude session ID.
2. Launch the interactive Claude Code TUI in a detached `tmux` pane.
3. Prefer your installed `claude` binary for the TUI so Claude.ai auth behaves
   like your terminal.
4. Paste the prompt into the TUI and submit it.
5. Tail `~/.claude/projects/*/<session-id>.jsonl`.
6. Emit the final assistant text as text, JSON, or stream JSON.
7. Kill the temporary `tmux` session.

If no real non-self `claude` binary is available, `cladup` falls back to:

```sh
npx -y @anthropic-ai/claude-code[@detected-version]
```

The version is detected from `claude --version` first, then
`~/.claude.json`'s `lastReleaseNotesSeen`, then left unpinned.

## Requirements

- macOS or Linux with `tmux`
- `python3`
- `npx`
- Claude Code auth already working for real requests

Check auth with:

```sh
claude config list
```

`claude auth status` may show cached account metadata even when real API
requests fail. `cladup` does not run an auth preflight by default because some
Claude installs open the interactive UI for `config list`; expired sessions are
reported from Claude's transcript/API error output instead.

## Compatibility

The non-print surface is passthrough:

```sh
cladup --version
cladup auth status
cladup mcp list
cladup plugin list
cladup --model sonnet
```

Print mode supports:

- `--output-format text`
- `--output-format json`
- `--output-format stream-json`
- `--input-format text`
- `--input-format stream-json` as JSONL user-message extraction
- session flags such as `--resume`, `--continue`, `--session-id`, and
  `--fork-session`
- common Claude launch flags such as `--model`, `--add-dir`,
  `--permission-mode`, `--allowed-tools`, `--disallowed-tools`, `--mcp-config`,
  `--settings`, `--agents`, and plugin flags

Print-only features with no exact TUI equivalent are accepted best-effort:
`--include-partial-messages`, `--include-hook-events`, `--fallback-model`,
`--max-budget-usd`, `--max-turns`, `--no-session-persistence`, and
`--json-schema`. `--json-schema` is converted into an instruction appended to
the prompt.

## Streaming

`--output-format stream-json` is supported, but it is transcript-level
streaming, not token-level streaming. Claude's interactive transcript writes
whole message records, so `cladup` emits records as they appear and then emits a
final `result` object.

This is close enough for harnesses that need JSONL progress and a final result,
but it is not byte-for-byte identical to native `claude -p --output-format
stream-json`.

## Environment Knobs

```sh
CLADUP_READY_TIMEOUT=180 cladup -p "question"
CLADUP_ASSISTANT_START_TIMEOUT=120 cladup -p "question"
CLADUP_CLAUDE_BIN=/Users/riajul/.local/bin/claude cladup -p "question"
CLADUP_AUTH_PREFLIGHT=1 cladup -p "question"
CLADUP_FORCE_PACKAGE_RUNNER=1 cladup -p "question"
CLADUP_PACKAGE_RUNNER=npx cladup -p "question"
CLADUP_PACKAGE_RUNNER=bunx cladup --version
CLADUP_DEFAULT_PERMISSION_MODE=acceptEdits cladup -p "question"
```

`CLADUP_PACKAGE_RUNNER` can be `npx`, `bunx`, `bun`, or `auto`. `npx` is the
default because it reliably runs the current Claude Code package in tested
environments. Bun may be faster for some packages, but it did not reliably
resolve `@anthropic-ai/claude-code@2.1.150` during local verification.

## Troubleshooting

If print mode exits with an auth preflight error, run:

```sh
claude config list
```

If that command returns `401 Invalid authentication credentials`, refresh Claude
auth with:

```sh
claude auth login
```

or open the interactive TUI and run:

```text
/login
```

If a prompt appears in `tmux` but does not submit, upgrade to the latest
`cladup`. The send path tries bracketed paste, plain paste, and literal typing
for single-line prompts.

If startup is slow on a cold machine:

```sh
CLADUP_READY_TIMEOUT=180 cladup -p "question"
```

## Development

Run tests:

```sh
npm test
```

The test suite is pure Python and does not make Claude API calls.

## Prior Art

`cladup` is inspired by the useful architecture in
[`ishmum123/clawp`](https://github.com/ishmum123/clawp): drive the interactive
Claude Code TUI and read the persisted transcript. `cladup` changes the
contract: it is a full Claude CLI shim that only intercepts print mode.

## License

MIT. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
