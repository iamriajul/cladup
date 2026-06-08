#!/usr/bin/env python3
"""cladup - a drop-in Claude CLI shim.

Non-print invocations exec Claude Code through:

    npx -y @anthropic-ai/claude-code[@detected-version]

Print invocations (`-p` / `--print`) are emulated through the interactive
Claude Code TUI in a detached tmux pane. The prompt is pasted into the TUI and
the answer is read from Claude's transcript jsonl, avoiding a direct call to
`claude -p`.
"""

from __future__ import annotations

import contextlib
import datetime
import fcntl
import glob
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid


REAL_CLAUDE_ENV = "CLADUP_CLAUDE_BIN"
PACKAGE_RUNNER_ENV = "CLADUP_PACKAGE_RUNNER"
CLAUDE_CODE_PACKAGE = "@anthropic-ai/claude-code"
AUTH_PREFLIGHT_ENV = "CLADUP_AUTH_PREFLIGHT"


def env_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, ""))
    except ValueError:
        return default
    return value if value > 0 else default

POLL = 0.6
STABLE_NEEDED = 3
STALL_SECS = 180
MAX_TURN = 1800
READY_TIMEOUT = env_int("CLADUP_READY_TIMEOUT", 120)
ASSISTANT_START_TIMEOUT = env_int("CLADUP_ASSISTANT_START_TIMEOUT", 90)
SEND_SETTLE = 0.8
PANE_W, PANE_H = 220, 50

ASSIST_RE = re.compile(r"^\u23fa\s?")
PROMPT_RE = re.compile(r"^\u276f\s")
DONE_RE = re.compile(r"\bfor\s+\d+s\b")
VERSION_RE = re.compile(r"Claude Code v([\d.]+)")
CLI_VERSION_RE = re.compile(r"(?<!\d)(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)")
UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

DIALOG_MARKERS = (
    "Do you want",
    "don't ask again",
    "tell Claude what to do differently",
    "trust this folder",
    "Quick safety check",
    "No, and tell Claude",
)

TERMINAL_STOP = {"end_turn", "max_tokens", "stop_sequence", "refusal"}
NOISE_TYPES = {
    "last-prompt",
    "mode",
    "permission-mode",
    "attachment",
    "file-history-snapshot",
    "ai-title",
    "system",
    "queue-operation",
    "progress",
}

BOOL_FLAGS = {
    "--allow-dangerously-skip-permissions",
    "--bare",
    "--brief",
    "--chrome",
    "--dangerously-skip-permissions",
    "--disable-slash-commands",
    "--exclude-dynamic-system-prompt-sections",
    "--fork-session",
    "--ide",
    "--include-hook-events",
    "--include-partial-messages",
    "--mcp-debug",
    "--no-chrome",
    "--no-session-persistence",
    "--print",
    "--replay-user-messages",
    "--strict-mcp-config",
    "--verbose",
}

VALUE_FLAGS = {
    "--agent",
    "--agents",
    "--append-system-prompt",
    "--append-system-prompt-file",
    "--cladup-db",
    "--cladup-history-limit",
    "--cwd",
    "--debug-file",
    "--effort",
    "--fallback-model",
    "--input-format",
    "--json-schema",
    "--max-budget-usd",
    "--max-turns",
    "--model",
    "--name",
    "--output-format",
    "--permission-mode",
    "--permission-prompt-tool",
    "--remote-control-session-name-prefix",
    "--session-id",
    "--setting-sources",
    "--settings",
    "--system-prompt",
    "--system-prompt-file",
}

VARIADIC_FLAGS = {
    "--add-dir",
    "--allowedTools",
    "--allowed-tools",
    "--betas",
    "--disallowedTools",
    "--disallowed-tools",
    "--file",
    "--mcp-config",
    "--tools",
}

REPEAT_VALUE_FLAGS = {
    "--plugin-dir",
    "--plugin-url",
}

OPTIONAL_FLAGS = {
    "--debug",
    "--from-pr",
    "--remote-control",
    "--resume",
    "--tmux",
    "--worktree",
}

SHORT_BOOL_FLAGS = {"-c", "-p"}
SHORT_VALUE_FLAGS = {"-n"}
SHORT_OPTIONAL_FLAGS = {"-d", "-r", "-w"}
HELP_VERSION_FLAGS = {"-h", "--help", "-v", "--version"}

OWNED_VALUE_FLAGS = {
    "--cladup-db",
    "--cladup-history-limit",
    "--cwd",
    "--fallback-model",
    "--input-format",
    "--json-schema",
    "--max-budget-usd",
    "--max-turns",
    "--output-format",
    "--session-id",
}

OWNED_BOOL_FLAGS = {
    "--cladup-history",
    "--full-auto",
    "--include-hook-events",
    "--include-partial-messages",
    "--no-session-persistence",
    "--replay-user-messages",
}

OWNED_OPTIONAL_FLAGS = {"--from-pr", "--resume", "-r"}


class CwError(SystemExit):
    def __init__(self, code: int, msg: str):
        super().__init__(code)
        sys.stderr.write(f"[cladup] {msg}\n")


class PrintOptions:
    def __init__(self) -> None:
        self.output_format = "text"
        self.input_format = "text"
        self.session_id = ""
        self.resume = ""
        self.continue_latest = False
        self.from_pr = ""
        self.fork_session = False
        self.cwd = os.getcwd()
        self.db = os.environ.get(
            "CLADUP_DB", os.path.join(os.getcwd(), "cladup.sqlite")
        )
        self.history = False
        self.history_limit = 10
        self.full_auto = False
        self.include_hook_events = False
        self.include_partial_messages = False
        self.no_session_persistence = False
        self.replay_user_messages = False
        self.json_schema = ""
        self.max_turns = ""
        self.fallback_model = ""
        self.max_budget_usd = ""
        self.permission_mode_seen = False
        self.passthrough: list[str] = []
        self.positionals: list[str] = []
        self.replay_events: list[dict] = []


def tmux(*args: str, stdin_text: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["tmux", *args], input=stdin_text, capture_output=True, text=True
    )


def tmux_checked(*args: str, stdin_text: str | None = None) -> subprocess.CompletedProcess:
    cp = tmux(*args, stdin_text=stdin_text)
    if cp.returncode != 0:
        detail = (cp.stderr or cp.stdout or "").strip()
        suffix = f": {detail}" if detail else ""
        raise CwError(5, f"tmux {' '.join(args)} failed{suffix}")
    return cp


def has_session(name: str) -> bool:
    return tmux("has-session", "-t", name).returncode == 0


def capture(name: str) -> str:
    return tmux("capture-pane", "-p", "-S", "-", "-t", name).stdout


def bottom_lines(screen: str, n: int = 12) -> list[str]:
    lines = screen.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    return lines[-n:]


def has_spinner(screen: str) -> bool:
    active_markers = ("Running\u2026", "Thinking\u2026", "Searching\u2026")
    for line in bottom_lines(screen):
        if any(marker in line for marker in active_markers):
            return True
        stripped = line.strip()
        if "\u2026" in stripped and stripped[:1] in {"\u2733", "\u2732", "\u273d", "\u2736", "\u2739"}:
            return True
    return False


def looks_blocked(screen: str) -> bool:
    return any(marker in screen for marker in DIALOG_MARKERS)


def at_idle_prompt(screen: str) -> bool:
    if looks_blocked(screen):
        return False
    bottom = "\n".join(bottom_lines(screen, 16))
    has_prompt = "\u276f" in bottom
    has_status = any(
        marker in bottom
        for marker in ("Model:", "accept edits", "for agents", "/effort", "Ctx:")
    )
    return has_prompt and has_status


def ensure_session(
    name: str, cwd: str, claude_cmd: list[str], claude_args: list[str]
) -> None:
    if has_session(name):
        return
    tmux(
        "new-session",
        "-d",
        "-s",
        name,
        "-x",
        str(PANE_W),
        "-y",
        str(PANE_H),
        "-c",
        cwd,
        *claude_cmd,
        *claude_args,
    )
    deadline = time.time() + READY_TIMEOUT
    trusted = False
    while time.time() < deadline:
        screen = capture(name)
        if not trusted and (
            "trust this folder" in screen or "Quick safety check" in screen
        ):
            tmux("send-keys", "-t", name, "Enter")
            trusted = True
            time.sleep(1.0)
            continue
        if at_idle_prompt(screen) and not has_spinner(screen):
            return
        time.sleep(POLL)
    raise TimeoutError(
        f"claude session '{name}' did not become ready in {READY_TIMEOUT}s"
    )


def clear_input(name: str) -> None:
    tmux_checked("send-keys", "-t", name, "Escape")
    tmux_checked("send-keys", "-t", name, "C-u")


def compact_visible(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def prompt_visible(screen: str, text: str) -> bool:
    compact_screen = compact_visible("\n".join(bottom_lines(screen, 20)))
    compact_text = compact_visible(text)
    if not compact_text:
        return False
    probes = [compact_text[:40]]
    if len(compact_text) > 40:
        probes.append(compact_text[-40:])
    return any(probe and probe in compact_screen for probe in probes)


def current_input_line(screen: str) -> str:
    lines = bottom_lines(screen, 30)
    status_idx = None
    for idx in range(len(lines) - 1, -1, -1):
        if any(
            marker in lines[idx]
            for marker in ("Model:", "accept edits", "for agents", "/effort", "Ctx:")
        ):
            status_idx = idx
            break
    search = lines[:status_idx] if status_idx is not None else lines
    for line in reversed(search):
        stripped = line.strip()
        if stripped.startswith("\u276f"):
            return stripped
    return ""


def prompt_in_current_input(screen: str, text: str) -> bool:
    line = current_input_line(screen)
    compact_line = compact_visible(line)
    compact_text = compact_visible(text)
    if not compact_line or not compact_text:
        return False
    probes = [compact_text[:40]]
    if len(compact_text) > 40:
        probes.append(compact_text[-40:])
    return any(probe and probe in compact_line for probe in probes)


def submit_current_input(name: str, text: str) -> None:
    tmux_checked("send-keys", "-t", name, "Enter")
    time.sleep(1.5)
    screen = capture(name)
    if prompt_in_current_input(screen, text) and at_idle_prompt(screen) and not has_spinner(screen):
        tmux_checked("send-keys", "-t", name, "C-j")
        time.sleep(1.5)
        screen = capture(name)
    if prompt_in_current_input(screen, text) and at_idle_prompt(screen) and not has_spinner(screen):
        raise CwError(
            5,
            f"prompt pasted into tmux session '{name}' but did not submit",
        )


def load_tmux_buffer(text: str) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
        handle.write(text)
        buffer_path = handle.name
    try:
        tmux_checked("load-buffer", buffer_path)
    finally:
        try:
            os.unlink(buffer_path)
        except OSError:
            pass


def paste_prompt(name: str, text: str) -> None:
    load_tmux_buffer(text)
    tmux_checked("paste-buffer", "-p", "-t", name)
    time.sleep(0.4)
    if prompt_in_current_input(capture(name), text):
        return

    clear_input(name)
    load_tmux_buffer(text)
    tmux_checked("paste-buffer", "-t", name)
    time.sleep(0.4)
    if prompt_in_current_input(capture(name), text):
        return

    if "\n" not in text:
        clear_input(name)
        tmux_checked("send-keys", "-l", "-t", name, text)
        time.sleep(0.4)
        if prompt_in_current_input(capture(name), text):
            return

    raise CwError(
        5,
        f"prompt did not appear after tmux paste into session '{name}'",
    )


def send_text(name: str, text: str) -> None:
    clear_input(name)
    paste_prompt(name, text)
    submit_current_input(name, text)


def scrape_reply(screen: str) -> str:
    lines = screen.splitlines()
    idx = next((i for i, line in enumerate(lines) if ASSIST_RE.match(line)), None)
    if idx is None:
        return ""
    out = [ASSIST_RE.sub("", lines[idx], count=1)]
    for line in lines[idx + 1 :]:
        stripped = line.strip()
        if (
            stripped.startswith("----")
            or stripped.startswith("\u2500\u2500\u2500\u2500")
            or PROMPT_RE.match(stripped)
            or DONE_RE.search(stripped)
            or stripped.startswith("Model:")
            or "-- INSERT --" in stripped
        ):
            break
        out.append(line[2:] if line.startswith("  ") else line)
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out).strip()


def find_transcript(session_id: str) -> str | None:
    if not session_id:
        return None
    hits = glob.glob(
        os.path.expanduser(f"~/.claude/projects/*/{session_id}.jsonl")
    )
    return hits[0] if hits else None


def all_transcripts() -> list[str]:
    return glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl"))


def transcript_snapshot() -> dict[str, tuple[float, int]]:
    snapshot = {}
    for path in all_transcripts():
        try:
            snapshot[path] = (os.path.getmtime(path), len(read_records(path)))
        except OSError:
            continue
    return snapshot


def changed_transcript(
    snapshot: dict[str, tuple[float, int]], since: float
) -> tuple[str | None, int]:
    candidates = []
    for path in all_transcripts():
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        previous_mtime, previous_count = snapshot.get(path, (0.0, 0))
        if mtime > previous_mtime or mtime >= since:
            candidates.append((mtime, path, previous_count))
    if not candidates:
        return None, 0
    candidates.sort(reverse=True)
    _mtime, path, previous_count = candidates[0]
    return path, previous_count


def session_id_from_transcript(path: str | None) -> str:
    if not path:
        return ""
    return os.path.splitext(os.path.basename(path))[0]


def read_records(path: str | None) -> list[dict]:
    out = []
    if not path or not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                break
    return out


def is_compaction(rec: dict) -> bool:
    if rec.get("subtype") == "compact_boundary":
        return True
    return bool(rec.get("isCompactSummary"))


def is_noise(rec: dict) -> bool:
    return rec.get("type") in NOISE_TYPES or is_compaction(rec)


def is_terminal_assistant(rec: dict) -> bool:
    if rec.get("type") != "assistant":
        return False
    return (rec.get("message") or {}).get("stop_reason") in TERMINAL_STOP


def is_api_error_record(rec: dict) -> bool:
    return bool(rec.get("isApiErrorMessage") or rec.get("apiErrorStatus"))


def assistant_text(rec: dict) -> str:
    blocks = (rec.get("message") or {}).get("content") or []
    parts = [
        block.get("text", "")
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "".join(parts).strip()


def final_answer(records: list[dict]) -> str:
    for rec in reversed(records):
        if is_terminal_assistant(rec):
            return assistant_text(rec)
    return ""


def has_assistant_activity(records: list[dict]) -> bool:
    return any(rec.get("type") == "assistant" for rec in records)


def is_tool_result_user(rec: dict) -> bool:
    if rec.get("type") != "user":
        return False
    content = (rec.get("message") or {}).get("content")
    return isinstance(content, list) and any(
        isinstance(block, dict) and block.get("type") == "tool_result"
        for block in content
    )


def stream_event(rec: dict, opts: PrintOptions) -> dict | None:
    if is_noise(rec):
        return None
    if rec.get("type") == "assistant":
        return rec if assistant_text(rec) or opts.include_partial_messages else None
    if rec.get("type") == "user":
        if opts.replay_user_messages or is_tool_result_user(rec):
            return rec
        return None
    if opts.include_hook_events:
        return rec
    return None


def claude_version(screen: str) -> str:
    match = VERSION_RE.search(screen)
    return match.group(1) if match else ""


@contextlib.contextmanager
def session_lock(name: str):
    path = os.path.join("/tmp", f"cladup-{name}.lock")
    handle = open(path, "w", encoding="utf-8")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CwError(
                3,
                f"pane '{name}' is busy (another cladup turn is running)",
            )
        yield
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def init_db(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.execute(
        """CREATE TABLE IF NOT EXISTS responses(
        id TEXT PRIMARY KEY, ts TEXT, session TEXT, prompt TEXT, reply TEXT,
        seconds REAL, low_fidelity INTEGER, timed_out INTEGER, blocked INTEGER,
        note TEXT)"""
    )
    con.commit()
    return con


def _meta(
    low_fidelity: bool, timed_out: bool, blocked: bool = False, note: str = ""
) -> dict[str, int | str]:
    return {
        "low_fidelity": int(low_fidelity),
        "timed_out": int(timed_out),
        "blocked": int(blocked),
        "note": note,
    }


def _log_turn(
    db_path: str,
    turn_id: str,
    session_id: str,
    prompt: str,
    reply: str,
    seconds: float,
    meta: dict[str, int | str],
) -> None:
    con = init_db(db_path)
    con.execute(
        "INSERT INTO responses VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            turn_id,
            datetime.datetime.now(datetime.timezone.utc).isoformat(),
            session_id,
            prompt,
            reply,
            seconds,
            meta["low_fidelity"],
            meta["timed_out"],
            meta["blocked"],
            meta["note"],
        ),
    )
    con.commit()
    con.close()


def show_history(db_path: str, n: int) -> int:
    if not os.path.exists(db_path):
        sys.stderr.write("[cladup] no database yet\n")
        return 0
    con = sqlite3.connect(db_path)
    query = (
        "SELECT ts, session, seconds, low_fidelity, timed_out, blocked, "
        "substr(prompt,1,70) FROM responses ORDER BY ts DESC LIMIT ?"
    )
    for ts, sess, secs, low, timed_out, blocked, prompt in con.execute(query, (n,)):
        flags = [
            name
            for name, value in (
                ("low_fidelity", low),
                ("timed_out", timed_out),
                ("blocked", blocked),
            )
            if value
        ]
        suffix = f"  [{', '.join(flags)}]" if flags else ""
        print(f"{ts}  {sess}  {secs}s{suffix}  {prompt!r}")
    con.close()
    return 0


def _same_file(a: str, b: str) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _self_paths(argv0: str) -> set[str]:
    paths = {__file__, os.path.realpath(__file__)}
    if argv0:
        paths.add(argv0)
        paths.add(os.path.realpath(argv0))
    return {os.path.abspath(path) for path in paths if path}


def _is_self(candidate: str, self_paths: set[str]) -> bool:
    candidate = os.path.abspath(candidate)
    candidate_real = os.path.realpath(candidate)
    return any(
        _same_file(candidate, path) or candidate_real == os.path.realpath(path)
        for path in self_paths
    )


def find_real_claude(argv0: str) -> str | None:
    self_paths = _self_paths(argv0)
    env_path = os.environ.get(REAL_CLAUDE_ENV)
    if env_path:
        path = os.path.abspath(os.path.expanduser(env_path))
        if _is_self(path, self_paths):
            raise CwError(5, f"{REAL_CLAUDE_ENV} points to cladup itself")
        if not os.path.isfile(path) or not os.access(path, os.X_OK):
            raise CwError(5, f"{REAL_CLAUDE_ENV} is not executable: {env_path}")
        return path

    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = os.path.join(directory or ".", "claude")
        if not os.path.isfile(candidate) or not os.access(candidate, os.X_OK):
            continue
        if _is_self(candidate, self_paths):
            continue
        return candidate
    return None


def parse_claude_cli_version(text: str) -> str:
    match = CLI_VERSION_RE.search(text or "")
    return match.group(1) if match else ""


def claude_json_version(home: str | None = None) -> str:
    path = os.path.join(home or os.path.expanduser("~"), ".claude.json")
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return ""
    return parse_claude_cli_version(str(data.get("lastReleaseNotesSeen", "")))


def installed_claude_version(argv0: str, home: str | None = None) -> str:
    real = find_real_claude(argv0)
    if real:
        try:
            cp = subprocess.run(
                [real, "--version"], capture_output=True, text=True, timeout=8
            )
        except (OSError, subprocess.TimeoutExpired):
            cp = None
        if cp is not None:
            version = parse_claude_cli_version(
                (cp.stdout or "") + "\n" + (cp.stderr or "")
            )
            if version:
                return version
    return claude_json_version(home)


def claude_code_package(version: str) -> str:
    return f"{CLAUDE_CODE_PACKAGE}@{version}" if version else CLAUDE_CODE_PACKAGE


def claude_code_npx_package(version: str) -> str:
    return claude_code_package(version)


def package_runner_command(package: str) -> list[str]:
    runner = os.environ.get(PACKAGE_RUNNER_ENV, "npx").strip().lower()
    if runner == "npx":
        return ["npx", "-y", package]
    if runner == "bunx":
        return ["bunx", package]
    if runner == "bun":
        return ["bun", "x", package]
    if runner == "auto":
        # Bun currently fails to resolve @anthropic-ai/claude-code's `claude`
        # bin reliably on this machine, so auto prefers the known-good runner.
        if shutil.which("npx"):
            return ["npx", "-y", package]
        if shutil.which("bunx"):
            return ["bunx", package]
        if shutil.which("bun"):
            return ["bun", "x", package]
    raise CwError(
        5,
        f"unsupported {PACKAGE_RUNNER_ENV}={runner!r}; use npx, bunx, bun, or auto",
    )


def claude_code_command(argv0: str) -> list[str]:
    package = claude_code_package(installed_claude_version(argv0))
    return package_runner_command(package)


def claude_interactive_command(argv0: str) -> list[str]:
    real = find_real_claude(argv0)
    if real and os.environ.get("CLADUP_FORCE_PACKAGE_RUNNER") != "1":
        return [real]
    return claude_code_command(argv0)


def auth_preflight_command(cmd: list[str], cwd: str) -> bool:
    if os.environ.get(AUTH_PREFLIGHT_ENV, "0") != "1":
        return True
    try:
        cp = subprocess.run(
            [*cmd, "config", "list"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return True
    if cp.returncode == 0:
        return True
    output = (cp.stdout or "") + (cp.stderr or "")
    if "401" in output or "Invalid authentication credentials" in output:
        raise CwError(
            6,
            "Claude auth preflight failed before sending the prompt: "
            "run `claude config list` in this same environment, then refresh "
            "Claude auth with `claude auth login` or `/login`",
        )
    return True


def exec_claude_code(argv0: str, argv: list[str]) -> None:
    cmd = claude_code_command(argv0)
    try:
        os.execvp(cmd[0], [*cmd, *argv])
    except OSError as exc:
        raise CwError(5, f"failed to exec Claude Code via {' '.join(cmd)}: {exc}")


def has_print_flag(argv: list[str]) -> bool:
    return any(arg in ("-p", "--print") or arg.startswith("--print=") for arg in argv)


def should_passthrough_to_claude(argv: list[str]) -> bool:
    if any(arg in HELP_VERSION_FLAGS for arg in argv):
        return True
    return not has_print_flag(argv)


def is_option_like(token: str) -> bool:
    return token.startswith("-") and token != "-"


def split_long_option(arg: str) -> tuple[str, str | None]:
    if "=" not in arg:
        return arg, None
    name, value = arg.split("=", 1)
    return name, value


def option_arity(name: str) -> str:
    if name in BOOL_FLAGS or name in OWNED_BOOL_FLAGS:
        return "bool"
    if name in VALUE_FLAGS:
        return "value"
    if name in VARIADIC_FLAGS:
        return "variadic"
    if name in REPEAT_VALUE_FLAGS:
        return "value"
    if name in OPTIONAL_FLAGS:
        return "optional"
    return "unknown"


def short_option_arity(name: str) -> str:
    if name in SHORT_BOOL_FLAGS:
        return "bool"
    if name in SHORT_VALUE_FLAGS:
        return "value"
    if name in SHORT_OPTIONAL_FLAGS:
        return "optional"
    return "unknown"


def consume_option(
    argv: list[str], i: int, name: str, arity: str, inline_value: str | None
) -> tuple[list[str], int]:
    if inline_value is not None:
        return [inline_value], i + 1
    if arity == "bool":
        return [], i + 1
    if arity == "value":
        if i + 1 >= len(argv):
            raise CwError(2, f"{name} requires a value")
        return [argv[i + 1]], i + 2
    if arity == "optional":
        if i + 1 < len(argv) and not is_option_like(argv[i + 1]):
            return [argv[i + 1]], i + 2
        return [], i + 1
    if arity == "variadic":
        values = []
        j = i + 1
        while j < len(argv) and not is_option_like(argv[j]):
            values.append(argv[j])
            j += 1
        if not values:
            raise CwError(2, f"{name} requires at least one value")
        return values, j
    if inline_value is not None:
        return [inline_value], i + 1
    if i + 1 < len(argv) and not is_option_like(argv[i + 1]):
        return [argv[i + 1]], i + 2
    return [], i + 1


def option_tokens(original: str, values: list[str], inline_value: str | None) -> list[str]:
    if inline_value is not None:
        return [original]
    return [original, *values]


def set_owned_option(opts: PrintOptions, name: str, values: list[str]) -> None:
    value = values[0] if values else ""
    if name == "--output-format":
        if value not in {"text", "json", "stream-json"}:
            raise CwError(2, "--output-format must be text, json, or stream-json")
        opts.output_format = value
    elif name == "--input-format":
        if value not in {"text", "stream-json"}:
            raise CwError(2, "--input-format must be text or stream-json")
        opts.input_format = value
    elif name == "--session-id":
        opts.session_id = value
    elif name in ("--resume", "-r"):
        if not value:
            raise CwError(2, f"{name} needs a session id/search value in print mode")
        opts.resume = value
    elif name == "--from-pr":
        if not value:
            raise CwError(2, "--from-pr needs a PR number/URL in print mode")
        opts.from_pr = value
    elif name == "--cwd":
        opts.cwd = os.path.abspath(os.path.expanduser(value))
    elif name == "--cladup-db":
        opts.db = os.path.abspath(os.path.expanduser(value))
    elif name == "--cladup-history-limit":
        try:
            opts.history_limit = int(value)
        except ValueError:
            raise CwError(2, "--cladup-history-limit must be an integer")
    elif name == "--json-schema":
        opts.json_schema = value
    elif name == "--max-turns":
        opts.max_turns = value
    elif name == "--fallback-model":
        opts.fallback_model = value
    elif name == "--max-budget-usd":
        opts.max_budget_usd = value
    elif name == "--include-hook-events":
        opts.include_hook_events = True
    elif name == "--include-partial-messages":
        opts.include_partial_messages = True
    elif name == "--no-session-persistence":
        opts.no_session_persistence = True
    elif name == "--replay-user-messages":
        opts.replay_user_messages = True
    elif name == "--cladup-history":
        opts.history = True
    elif name == "--full-auto":
        opts.full_auto = True


def parse_print_argv(argv: list[str]) -> PrintOptions:
    opts = PrintOptions()
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--":
            opts.positionals.extend(argv[i + 1 :])
            break
        if arg in ("-p", "--print") or arg.startswith("--print="):
            i += 1
            continue
        if arg == "-c":
            opts.continue_latest = True
            i += 1
            continue
        if arg == "--continue":
            opts.continue_latest = True
            i += 1
            continue
        if arg == "--fork-session":
            opts.fork_session = True
            i += 1
            continue
        if arg.startswith("--"):
            name, inline_value = split_long_option(arg)
            arity = option_arity(name)
            values, next_i = consume_option(argv, i, name, arity, inline_value)
            if name in OWNED_VALUE_FLAGS or name in OWNED_BOOL_FLAGS or name in OWNED_OPTIONAL_FLAGS:
                set_owned_option(opts, name, values)
            else:
                if name == "--permission-mode":
                    opts.permission_mode_seen = True
                if name == "--dangerously-skip-permissions":
                    opts.permission_mode_seen = True
                opts.passthrough.extend(option_tokens(arg, values, inline_value))
            i = next_i
            continue
        if arg.startswith("-") and arg != "-":
            name = arg
            arity = short_option_arity(name)
            values, next_i = consume_option(argv, i, name, arity, None)
            if name in OWNED_OPTIONAL_FLAGS:
                set_owned_option(opts, name, values)
            else:
                opts.passthrough.extend(option_tokens(arg, values, None))
            i = next_i
            continue
        opts.positionals.append(arg)
        i += 1
    return opts


def text_from_content(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "".join(parts)


def prompt_from_stream_json(stdin_text: str, opts: PrintOptions) -> str:
    parts = []
    for line in stdin_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            raise CwError(2, "--input-format stream-json received invalid JSONL")
        if opts.replay_user_messages:
            opts.replay_events.append(rec)
        if rec.get("type") != "user":
            continue
        message = rec.get("message") or {}
        text = text_from_content(message.get("content"))
        if text:
            parts.append(text)
    return "\n\n".join(parts).strip()


def build_prompt(opts: PrintOptions) -> str:
    stdin_text = "" if sys.stdin.isatty() else sys.stdin.read()
    positional = " ".join(part for part in opts.positionals if part != "-").strip()
    if opts.input_format == "stream-json":
        prompt = prompt_from_stream_json(stdin_text, opts)
        if positional:
            prompt = f"{prompt}\n\n{positional}".strip()
    else:
        stdin_body = stdin_text.rstrip("\n")
        if positional and stdin_body:
            prompt = f"{stdin_body}\n\n{positional}"
        elif positional:
            prompt = positional
        else:
            prompt = stdin_body.strip()
    if opts.json_schema:
        prompt = (
            f"{prompt}\n\nReturn only valid JSON matching this JSON Schema:\n"
            f"{opts.json_schema}"
        ).strip()
    if not prompt:
        raise CwError(2, "no prompt (positional arg or stdin)")
    return prompt


def build_launch_args(opts: PrintOptions, session_id: str) -> list[str]:
    args = []
    if opts.continue_latest:
        args.append("--continue")
    elif opts.from_pr:
        args.extend(["--from-pr", opts.from_pr])
    elif opts.resume:
        args.extend(["--resume", opts.resume])
    else:
        args.extend(["--session-id", session_id])
    if opts.fork_session:
        args.append("--fork-session")

    if not opts.permission_mode_seen:
        mode = (
            "bypassPermissions"
            if opts.full_auto
            else os.environ.get("CLADUP_DEFAULT_PERMISSION_MODE", "acceptEdits")
        )
        if mode:
            args.extend(["--permission-mode", mode])
    args.extend(opts.passthrough)
    return args


def result_object(
    session_id: str, answer: str, seconds: float, is_error: bool = False
) -> dict:
    return {
        "type": "result",
        "subtype": "success" if not is_error else "error",
        "session_id": session_id,
        "result": answer,
        "is_error": is_error,
        "duration_ms": int(seconds * 1000),
    }


def safe_pane_name(seed: str) -> str:
    safe = re.sub(r"[^0-9A-Za-z_-]", "", seed)
    if not safe:
        safe = uuid.uuid4().hex
    return "cladup-" + safe[:12]


def run_print_turn(opts: PrintOptions, argv0: str) -> int:
    prompt = build_prompt(opts)
    explicit_session = opts.session_id
    resumable_session = opts.resume if UUID_RE.match(opts.resume or "") else ""
    generated_session = explicit_session or resumable_session or str(uuid.uuid4())
    session_id = generated_session
    name = safe_pane_name(session_id)
    claude_cmd = claude_interactive_command(argv0)
    auth_preflight_command(claude_cmd, opts.cwd)
    claude_args = build_launch_args(opts, session_id)

    fmt = opts.output_format
    stream = fmt == "stream-json"
    t0 = time.time()
    turn_id = (
        datetime.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    )
    answer = ""
    turn_error = False
    meta = _meta(False, False)
    seconds = 0.0
    path = find_transcript(session_id)
    before = transcript_snapshot()
    offset = len(read_records(path)) if path else 0

    with session_lock(name):
        try:
            try:
                ensure_session(name, opts.cwd, claude_cmd, claude_args)
            except TimeoutError as exc:
                meta = _meta(False, True, note="launch failed")
                raise CwError(5, str(exc))

            send_text(name, prompt)
            if stream and opts.replay_user_messages:
                for rec in opts.replay_events:
                    print(json.dumps(rec), flush=True)
            time.sleep(SEND_SETTLE)

            start = time.time()
            last_screen = None
            stable = 0
            last_change = start
            seen = offset
            assistant_started = False
            while time.time() - start < MAX_TURN:
                if path is None:
                    path, seen = changed_transcript(before, t0)
                    if path:
                        session_id = session_id_from_transcript(path)
                records = read_records(path)
                new = records[seen:]
                done = False
                for rec in new:
                    seen += 1
                    if rec.get("type") == "assistant":
                        assistant_started = True
                    if is_terminal_assistant(rec):
                        answer = assistant_text(rec)
                        turn_error = is_api_error_record(rec)
                        if stream:
                            event = stream_event(rec, opts)
                            if event is not None:
                                print(json.dumps(event), flush=True)
                        done = True
                        break
                    if stream:
                        event = stream_event(rec, opts)
                        if event is not None:
                            print(json.dumps(event), flush=True)
                if done:
                    break

                screen = capture(name)
                now = time.time()
                if screen == last_screen:
                    stable += 1
                else:
                    stable = 1
                    last_screen = screen
                    last_change = now
                if looks_blocked(screen):
                    meta = _meta(False, False, blocked=True, note="blocked")
                    raise CwError(
                        3,
                        "blocked: permission/trust dialog "
                        "(use --full-auto or --permission-mode bypassPermissions)",
                    )
                if now - last_change >= STALL_SECS:
                    meta = _meta(False, True, note="stalled")
                    raise CwError(4, "stall: screen frozen, no terminal stop_reason")
                if has_spinner(screen):
                    time.sleep(POLL)
                    continue
                if not assistant_started and now - start >= ASSISTANT_START_TIMEOUT:
                    meta = _meta(False, True, note="no assistant response")
                    raise CwError(
                        4,
                        "timeout: prompt was submitted but no assistant response "
                        f"appeared within {ASSISTANT_START_TIMEOUT}s",
                    )
                if assistant_started and stable >= STABLE_NEEDED and at_idle_prompt(screen):
                    break
                time.sleep(POLL)
            else:
                meta = _meta(False, True, note="max turn")
                raise CwError(4, "timeout: turn exceeded MAX_TURN")

            if not answer:
                answer = final_answer(read_records(path)[offset:])
            if not answer:
                version = claude_version(capture(name))
                suffix = f" on Claude Code v{version}" if version else ""
                if fmt == "text":
                    answer = scrape_reply(capture(name))
                    meta = _meta(True, False, note="schema unrecognized" + suffix)
                else:
                    raise CwError(
                        5,
                        "transcript schema unrecognized"
                        + suffix
                        + " (zero usable answer; json/stream-json cannot be scraped)",
                    )
        finally:
            seconds = round(time.time() - t0, 1)
            _log_turn(opts.db, turn_id, session_id, prompt, answer, seconds, meta)
            tmux("kill-session", "-t", name)

    if meta["low_fidelity"] or meta["note"]:
        sys.stderr.write(
            f"[cladup] {meta['note'] or 'low_fidelity'} (session={session_id})\n"
        )
    if fmt == "text":
        print(answer)
    else:
        print(
            json.dumps(result_object(session_id, answer, seconds, turn_error)),
            flush=stream,
        )
    return 1 if turn_error else 0


def cladup_help() -> str:
    return """Usage: cladup [claude options] [command] [prompt]

Drop-in Claude CLI shim.

Normal invocations are passed through to Claude Code via:
  npx -y @anthropic-ai/claude-code[@detected-version]

Only -p/--print is intercepted and emulated through the interactive TUI.

cladup-specific options:
  --cladup-help                 Show this help
  --cladup-db <path>            SQLite turn log for emulated print mode
  --cladup-history              Show recent emulated print-mode turns
  --cladup-history-limit <n>    History rows to show (default: 10)
  --cwd <dir>                   Run Claude from this directory in print mode
  --full-auto                   Alias for --permission-mode bypassPermissions

Environment:
  CLADUP_READY_TIMEOUT=120      Seconds to wait for the TUI to become ready
  CLADUP_ASSISTANT_START_TIMEOUT=90
                                  Seconds to wait for an assistant record
  CLADUP_PACKAGE_RUNNER=npx     Package runner: npx, bunx, bun, or auto
  CLADUP_AUTH_PREFLIGHT=1       Probe auth with `claude config list` first

Use `cladup --help` or `claude --help` for the official Claude CLI help.
"""


def main(argv: list[str] | None = None, argv0: str | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv0 is None:
        argv0 = sys.argv[0]

    if "--cladup-help" in argv:
        print(cladup_help(), end="")
        return 0

    if should_passthrough_to_claude(argv):
        exec_claude_code(argv0, argv)
        return 0

    opts = parse_print_argv(argv)
    if opts.history:
        return show_history(opts.db, opts.history_limit)
    return run_print_turn(opts, argv0)


if __name__ == "__main__":
    raise SystemExit(main())
