#!/usr/bin/env python3
"""Focused tests for cladup's pure parser and transcript helpers."""

import io
import json
import os
import sys
import tempfile

import cladup


DONE = (
    "\u276f list two primes\n\n"
    "\u23fa 2\n  3\n\n"
    "\u273b Baked for 2s\n\n"
    "--------\n\u276f \n--------\n"
    "  Model: Opus 4.8 | Ctx: 0 | Ctx Used: 0.0%\n  -- INSERT --\n"
)

WORKING = (
    "\u276f do a thing\n\n"
    "\u23fa Bash(sleep 8 && echo hi)\n  Running\u2026\n\n"
    "Imagining\u2026 (3s)\n\n"
    "--------\n\u276f \n--------\n  Model: Opus 4.8 | Ctx: 0\n"
)

TRUST = (
    "Quick safety check: Is this a project you created or one you trust?\n"
    "1. Yes, I trust this folder\n2. No, exit\n"
)

MCP_SELECT = (
    "2 new MCP servers found in this project\n"
    "Select any you wish to enable.\n\n"
    "\u276f [\u2714] code-review-graph\n"
    "  [\u2714] mempalace\n"
    "Space to select \u00b7 Enter to confirm \u00b7 Esc to reject all\n"
)

GENERIC_CONFIRM = (
    "Accessing workspace:\n\n"
    " /home/coder/abc\n\n"
    "\u276f 1. Yes, continue\n"
    "  2. No, exit\n\n"
    "Enter to confirm \u00b7 Esc to cancel\n"
)

WHATS_NEW_IDLE = (
    "Added a prompt before writing to shell startup files that could otherwise lead to unintended command \u2026\n"
    "acceptEdits mode now prompts before writing build-tool config files that grant code execution \u2026\n"
    "--------\n\u276f \n--------\n"
    "  Model: Sonnet 4.6 | Ctx: 0\n"
)

NO_MODEL_IDLE = (
    "Welcome back Riajul!\n"
    "--------\n"
    "\u276f Try \"refactor <filepath>\"\n"
    "--------\n"
    "  \u23f5\u23f5 accept edits on (shift+tab to cycle) · \u2190 for agents        \u25cf high · /effort\n"
)

NEW_BULLET_REPLY = (
    "\u276f Let's build a simple snake game in html\n\n"
    "\u25cf The project directory is empty (just an initial commit). This is a greenfield snake game.\n"
    "  Want to try it?\n\n"
    "\u273b Cooked for 41s\n\n"
    "--------\n\u276f \n--------\n"
    "  Model: Sonnet 4.6 | Ctx: 0\n"
)


def check(name, cond):
    assert cond, "FAILED: " + name
    print("ok:", name)


check("no -p passthrough", cladup.should_passthrough_to_claude(["--model", "opus"]))
check("help passthrough", cladup.should_passthrough_to_claude(["-p", "--help"]))
check("print intercepted", not cladup.should_passthrough_to_claude(["-p", "hi"]))

opts = cladup.parse_print_argv(
    [
        "-p",
        "--model",
        "opus",
        "--allowed-tools",
        "Bash(git *)",
        "Edit",
        "--output-format",
        "json",
        "--input-format=text",
        "--include-partial-messages",
        "--json-schema",
        '{"type":"object"}',
        "return status",
    ]
)
check("output format parsed", opts.output_format == "json")
check("input format parsed", opts.input_format == "text")
check("partial flag accepted", opts.include_partial_messages)
check("json schema stored", opts.json_schema == '{"type":"object"}')
check("model passed through", opts.passthrough[:2] == ["--model", "opus"])
check(
    "variadic allowed-tools preserved",
    opts.passthrough[2:5] == ["--allowed-tools", "Bash(git *)", "Edit"],
)
check("prompt positional kept", opts.positionals == ["return status"])

opts = cladup.parse_print_argv(["--print", "-c", "--fork-session", "continue this"])
check("continue parsed", opts.continue_latest)
check("fork parsed", opts.fork_session)
check("continue prompt kept", opts.positionals == ["continue this"])

opts = cladup.parse_print_argv(["-p", "--permission-mode", "plan", "q"])
check("permission mode seen", opts.permission_mode_seen)
check(
    "permission mode preserved",
    opts.passthrough == ["--permission-mode", "plan"],
)

opts = cladup.parse_print_argv(["-p", "--verbose", "q"])
check("verbose parsed", opts.verbose)
check("verbose preserved", opts.passthrough == ["--verbose"])

opts = cladup.parse_print_argv(["-p", "--unknown-new-flag", "value", "q"])
check(
    "unknown option value preserved",
    opts.passthrough == ["--unknown-new-flag", "value"],
)
check("unknown option leaves prompt", opts.positionals == ["q"])

stream = {
    "type": "user",
    "message": {"role": "user", "content": [{"type": "text", "text": "hello"}]},
}
opts = cladup.PrintOptions()
opts.input_format = "stream-json"
stdin_before = sys.stdin
try:
    sys.stdin = io.StringIO(json.dumps(stream) + "\n")
    check("stream-json prompt extracted", cladup.build_prompt(opts) == "hello")
finally:
    sys.stdin = stdin_before

opts = cladup.PrintOptions()
opts.input_format = "stream-json"
stdin_before = sys.stdin
try:
    sys.stdin = io.StringIO(json.dumps(stream) + "\n" + json.dumps(stream) + "\n")
    check("duplicate stream-json prompt deduped", cladup.build_prompt(opts) == "hello")
finally:
    sys.stdin = stdin_before

meta_stream = {
    "type": "user",
    "isMeta": True,
    "message": {
        "role": "user",
        "content": [{"type": "text", "text": "Continue from where you left off."}],
    },
}
opts = cladup.PrintOptions()
opts.input_format = "stream-json"
stdin_before = sys.stdin
try:
    sys.stdin = io.StringIO(json.dumps(meta_stream) + "\n" + json.dumps(stream) + "\n")
    check("meta stream-json user ignored", cladup.build_prompt(opts) == "hello")
finally:
    sys.stdin = stdin_before

opts = cladup.PrintOptions()
opts.positionals = ["hello"]
stdin_before = sys.stdin
try:
    sys.stdin = io.StringIO("hello")
    check("duplicate stdin and argv prompt deduped", cladup.build_prompt(opts) == "hello")
finally:
    sys.stdin = stdin_before

check("spinner detected", cladup.has_spinner(WORKING))
check("done has no spinner", not cladup.has_spinner(DONE))
check("release note ellipses are not spinner", not cladup.has_spinner(WHATS_NEW_IDLE))
check("trust is blocked", cladup.looks_blocked(TRUST))
check("trust prompt auto-confirmable", cladup.is_startup_confirm_prompt(TRUST))
check("mcp prompt auto-confirmable", cladup.is_startup_confirm_prompt(MCP_SELECT))
check(
    "enter to confirm prompt auto-confirmable",
    cladup.is_startup_confirm_prompt(GENERIC_CONFIRM),
)
SCROLLBACK_DIALOG = TRUST + ("\nold history\n" * 50) + DONE
check("old dialog scrollback ignored", not cladup.looks_blocked(SCROLLBACK_DIALOG))
check("done idle", cladup.at_idle_prompt(DONE))
check("no-model status screen is idle", cladup.at_idle_prompt(NO_MODEL_IDLE))
check("scrape reply", cladup.scrape_reply(DONE) == "2\n3")
check(
    "scrape reply supports v2.1.173 bullet",
    cladup.scrape_reply(NEW_BULLET_REPLY)
    == "The project directory is empty (just an initial commit). This is a greenfield snake game.\nWant to try it?",
)
TOOL_THEN_REPLY = (
    "\u23fa Bash(git diff HEAD)\n"
    "  \u23bf diff --git a/file b/file\n\n"
    "\u23fa Here's a summary of the diff:\n"
    "  - README changed\n\n"
    "--------\n\u276f \n--------\n"
    "  Model: Sonnet 4.6 | Ctx: 0\n"
)
check(
    "scrape reply uses last assistant block",
    cladup.scrape_reply(TOOL_THEN_REPLY) == "Here's a summary of the diff:\n- README changed",
)
VISIBLE_INPUT = DONE + "\n\n--------\n\u276f CLADUP_DEBUG_DO_NOT_SUBMIT\n--------\n  Model: Sonnet\n"
check(
    "prompt visible in input area",
    cladup.prompt_visible(VISIBLE_INPUT, "CLADUP_DEBUG_DO_NOT_SUBMIT"),
)
check(
    "prompt is current input",
    cladup.prompt_in_current_input(VISIBLE_INPUT, "CLADUP_DEBUG_DO_NOT_SUBMIT"),
)
HISTORY_ONLY = "\u276f CLADUP_DEBUG_DO_NOT_SUBMIT\n\n" + ("\n" * 30) + "  Model: Sonnet\n"
check(
    "prompt history is not treated as input",
    not cladup.prompt_visible(HISTORY_ONLY, "CLADUP_DEBUG_DO_NOT_SUBMIT"),
)
SUBMITTED_HISTORY = (
    "\u276f write a long poem\n\n"
    "\u273b Thinking\u2026\n\n"
    "--------\n"
    "\u276f \n"
    "--------\n"
    "  Model: Sonnet 4.6 | Ctx: 0\n"
)
check(
    "submitted history is not current input",
    not cladup.prompt_in_current_input(SUBMITTED_HISTORY, "write a long poem"),
)


def asst(stop_reason, blocks):
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "stop_reason": stop_reason,
            "content": blocks,
        },
    }


def txt(text):
    return {"type": "text", "text": text}


THINK = {"type": "thinking", "thinking": "private"}
TOOL_USE = {"type": "tool_use", "name": "Bash", "input": {}}
NO_RESPONSE = asst("stop_sequence", [txt("No response requested.")])
NO_RESPONSE["message"]["model"] = "<synthetic>"

check("terminal end_turn", cladup.is_terminal_assistant(asst("end_turn", [txt("x")])))
check("tool_use not terminal", not cladup.is_terminal_assistant(asst("tool_use", [TOOL_USE])))
check("synthetic no-response detected", cladup.is_no_response_record(NO_RESPONSE))
check("synthetic no-response not terminal", not cladup.is_terminal_assistant(NO_RESPONSE))
META_CONTINUE = {
    "type": "user",
    "isMeta": True,
    "message": {
        "role": "user",
        "content": [{"type": "text", "text": "Continue from where you left off."}],
    },
}
REAL_USER = {
    "type": "user",
    "message": {"role": "user", "content": [{"type": "text", "text": "hello"}]},
}
check("meta continue detected", cladup.is_meta_continue_record(META_CONTINUE))
check("real user is not meta continue", not cladup.is_meta_continue_record(REAL_USER))
check(
    "api error record detected",
    cladup.is_api_error_record({"type": "assistant", "isApiErrorMessage": True}),
)
check(
    "assistant text excludes thinking",
    cladup.assistant_text(asst("end_turn", [THINK, txt("answer")])) == "answer",
)
check(
    "final answer chooses terminal",
    cladup.final_answer([asst("tool_use", [TOOL_USE]), asst("end_turn", [txt("ok")])])
    == "ok",
)
terminal = asst("end_turn", [txt("ok")])
terminal["message"]["usage"] = {"input_tokens": 1, "output_tokens": 2}
check("final terminal record found", cladup.final_terminal_record([terminal]) == terminal)
result = cladup.result_object("sid", "ok", 1.2, False, terminal)
check("result includes stop reason", result["stop_reason"] == "end_turn")
check("result includes usage", result["usage"] == {"input_tokens": 1, "output_tokens": 2})
with tempfile.NamedTemporaryFile("w+", encoding="utf-8", delete=False) as handle:
    transcript_path = handle.name
    handle.write(json.dumps(asst("tool_use", [TOOL_USE])) + "\n")
    handle.write(json.dumps(asst("end_turn", [txt("done")])) + "\n")
try:
    check(
        "final answer read from transcript path",
        cladup.final_answer_from_path(transcript_path, 0) == "done",
    )
finally:
    os.unlink(transcript_path)
check(
    "final answer skips synthetic no-response",
    cladup.final_answer([asst("end_turn", [txt("ok")]), NO_RESPONSE]) == "ok",
)
check(
    "assistant activity ignores transcript noise",
    not cladup.has_assistant_activity(
        [{"type": "user", "message": {"content": "x"}}, {"type": "attachment"}]
    ),
)
check(
    "assistant activity detects assistant records",
    cladup.has_assistant_activity(
        [{"type": "user", "message": {"content": "x"}}, asst("end_turn", [txt("ok")])]
    ),
)

stream_opts = cladup.PrintOptions()
assistant_raw = asst("end_turn", [txt("hi")])
assistant_raw["sessionId"] = "sid"
assistant_raw["requestId"] = "req"
check(
    "assistant stream emits normalized record",
    cladup.stream_event(assistant_raw, stream_opts)
    == {
        "type": "assistant",
        "message": assistant_raw["message"],
        "session_id": "sid",
        "request_id": "req",
    },
)
tool_raw = asst("tool_use", [TOOL_USE])
tool_raw["sessionId"] = "sid"
check(
    "assistant tool use stream emitted",
    cladup.stream_event(tool_raw, stream_opts)["message"]["content"] == [TOOL_USE],
)
user_raw = {
    "type": "user",
    "message": {"role": "user", "content": [{"type": "text", "text": "hi"}]},
    "sessionId": "sid",
}
check("user stream skipped without replay", cladup.stream_event(user_raw, stream_opts) is None)
stream_opts.replay_user_messages = True
check("user stream emitted with replay", cladup.stream_event(user_raw, stream_opts)["session_id"] == "sid")
stream_opts.replay_user_messages = False
tool_result_raw = {
    "type": "user",
    "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]},
    "toolUseResult": {"success": True},
}
check(
    "tool result key normalized",
    cladup.stream_event(tool_result_raw, stream_opts)["tool_use_result"] == {"success": True},
)
check("noise stream skipped", cladup.stream_event({"type": "system"}, stream_opts) is None)
check("synthetic no-response stream skipped", cladup.stream_event(NO_RESPONSE, stream_opts) is None)
hook_raw = {
    "type": "attachment",
    "sessionId": "sid",
    "uuid": "hook-uuid",
    "attachment": {
        "type": "hook_success",
        "hookName": "SessionStart:startup",
        "hookEvent": "SessionStart",
        "toolUseID": "hook-id",
        "content": "ok",
        "stdout": "ok\n",
        "stderr": "",
        "exitCode": 0,
    },
}
check("hook skipped without verbose", cladup.stream_event(hook_raw, stream_opts) is None)
stream_opts.verbose = True
check(
    "hook normalized with verbose",
    cladup.stream_event(hook_raw, stream_opts)
    == {
        "type": "system",
        "subtype": "hook_response",
        "hook_name": "SessionStart:startup",
        "hook_event": "SessionStart",
        "uuid": "hook-uuid",
        "session_id": "sid",
        "hook_id": "hook-id",
        "output": "ok",
        "stdout": "ok\n",
        "stderr": "",
        "exit_code": 0,
        "outcome": "success",
    },
)
synthetic = cladup.synthetic_assistant_event("sid", "answer", "2.1.173")
check("synthetic assistant has text", cladup.assistant_text(synthetic) == "answer")
check("synthetic assistant terminal", cladup.is_terminal_assistant(synthetic))

with tempfile.NamedTemporaryFile("w+", encoding="utf-8", delete=False) as handle:
    transcript_path = handle.name
    handle.write(json.dumps(REAL_USER) + "\n")
    handle.write(json.dumps(META_CONTINUE) + "\n")
    handle.write(json.dumps(NO_RESPONSE) + "\n")
try:
    check(
        "trailing resume noops stripped",
        cladup.strip_trailing_resume_noops(transcript_path, 1) == 2,
    )
    with open(transcript_path, encoding="utf-8") as handle:
        remaining = [json.loads(line) for line in handle if line.strip()]
    check("real transcript record preserved", remaining == [REAL_USER])
finally:
    os.unlink(transcript_path)

with tempfile.NamedTemporaryFile("w+", encoding="utf-8", delete=False) as handle:
    transcript_path = handle.name
    handle.write(json.dumps(REAL_USER) + "\n")
    handle.write(json.dumps(META_CONTINUE) + "\n")
    handle.write(json.dumps(NO_RESPONSE) + "\n")
try:
    check(
        "resume noops before min boundary preserved",
        cladup.strip_trailing_resume_noops(transcript_path, 3) == 0,
    )
finally:
    os.unlink(transcript_path)

check("parse Claude Code banner version", cladup.parse_claude_cli_version("Claude Code v2.1.159") == "2.1.159")
check("unversioned package", cladup.claude_code_npx_package("") == "@anthropic-ai/claude-code")
check(
    "versioned package",
    cladup.claude_code_npx_package("2.1.159")
    == "@anthropic-ai/claude-code@2.1.159",
)
env_before = os.environ.get("CLADUP_PACKAGE_RUNNER")
try:
    os.environ["CLADUP_PACKAGE_RUNNER"] = "npx"
    check(
        "npx runner command",
        cladup.package_runner_command("@anthropic-ai/claude-code")
        == ["npx", "-y", "@anthropic-ai/claude-code"],
    )
    os.environ["CLADUP_PACKAGE_RUNNER"] = "bunx"
    check(
        "bunx runner command",
        cladup.package_runner_command("@anthropic-ai/claude-code")
        == ["bunx", "@anthropic-ai/claude-code"],
    )
finally:
    if env_before is None:
        os.environ.pop("CLADUP_PACKAGE_RUNNER", None)
    else:
        os.environ["CLADUP_PACKAGE_RUNNER"] = env_before

with tempfile.TemporaryDirectory() as directory:
    with open(os.path.join(directory, ".claude.json"), "w", encoding="utf-8") as handle:
        json.dump({"lastReleaseNotesSeen": "2.1.160"}, handle)
    check("claude json fallback", cladup.claude_json_version(directory) == "2.1.160")

version_before = os.environ.get("CLADUP_CLAUDE_VERSION")
tested_before = os.environ.get("CLADUP_USE_TESTED_VERSION")
try:
    os.environ.pop("CLADUP_CLAUDE_VERSION", None)
    os.environ["CLADUP_USE_TESTED_VERSION"] = "1"
    check(
        "tested Claude version selected",
        cladup.selected_claude_version("/tmp/cladup") == cladup.TESTED_CLAUDE_CODE_VERSION,
    )
    os.environ["CLADUP_CLAUDE_VERSION"] = "2.1.999"
    check(
        "explicit Claude version wins",
        cladup.selected_claude_version("/tmp/cladup") == "2.1.999",
    )
    stripped = cladup.apply_global_cladup_flags(
        ["--cladup-tested-version", "--cladup-claude-version=2.1.998", "-p", "q"]
    )
    check("global cladup flags stripped", stripped == ["-p", "q"])
    check(
        "global cladup version applied",
        os.environ.get("CLADUP_CLAUDE_VERSION") == "2.1.998"
        and os.environ.get("CLADUP_USE_TESTED_VERSION") == "1",
    )
finally:
    if version_before is None:
        os.environ.pop("CLADUP_CLAUDE_VERSION", None)
    else:
        os.environ["CLADUP_CLAUDE_VERSION"] = version_before
    if tested_before is None:
        os.environ.pop("CLADUP_USE_TESTED_VERSION", None)
    else:
        os.environ["CLADUP_USE_TESTED_VERSION"] = tested_before

with tempfile.TemporaryDirectory() as directory:
    fake = os.path.join(directory, "claude")
    with open(fake, "w", encoding="utf-8") as handle:
        handle.write("#!/bin/sh\n")
    os.chmod(fake, 0o755)
    path_before = os.environ.get("PATH", "")
    force_before = os.environ.get("CLADUP_FORCE_PACKAGE_RUNNER")
    runner_before = os.environ.get("CLADUP_PACKAGE_RUNNER")
    version_before = os.environ.get("CLADUP_CLAUDE_VERSION")
    tested_before = os.environ.get("CLADUP_USE_TESTED_VERSION")
    try:
        os.environ["PATH"] = directory
        os.environ.pop("CLADUP_FORCE_PACKAGE_RUNNER", None)
        os.environ.pop("CLADUP_CLAUDE_VERSION", None)
        os.environ.pop("CLADUP_USE_TESTED_VERSION", None)
        check(
            "interactive launcher prefers real claude",
            cladup.claude_interactive_command("/tmp/cladup") == [fake],
        )
        os.environ["CLADUP_FORCE_PACKAGE_RUNNER"] = "1"
        os.environ["CLADUP_PACKAGE_RUNNER"] = "npx"
        check(
            "interactive launcher can force package runner",
            cladup.claude_interactive_command("/tmp/cladup")[0:2] == ["npx", "-y"],
        )
    finally:
        os.environ["PATH"] = path_before
        if force_before is None:
            os.environ.pop("CLADUP_FORCE_PACKAGE_RUNNER", None)
        else:
            os.environ["CLADUP_FORCE_PACKAGE_RUNNER"] = force_before
        if runner_before is None:
            os.environ.pop("CLADUP_PACKAGE_RUNNER", None)
        else:
            os.environ["CLADUP_PACKAGE_RUNNER"] = runner_before
        if version_before is None:
            os.environ.pop("CLADUP_CLAUDE_VERSION", None)
        else:
            os.environ["CLADUP_CLAUDE_VERSION"] = version_before
        if tested_before is None:
            os.environ.pop("CLADUP_USE_TESTED_VERSION", None)
        else:
            os.environ["CLADUP_USE_TESTED_VERSION"] = tested_before

auth_preflight_before = os.environ.get("CLADUP_AUTH_PREFLIGHT")
try:
    os.environ.pop("CLADUP_AUTH_PREFLIGHT", None)
    check(
        "auth preflight is disabled by default",
        cladup.auth_preflight_command(["/does/not/exist"], "/tmp") is True,
    )
finally:
    if auth_preflight_before is None:
        os.environ.pop("CLADUP_AUTH_PREFLIGHT", None)
    else:
        os.environ["CLADUP_AUTH_PREFLIGHT"] = auth_preflight_before

with tempfile.TemporaryDirectory() as directory:
    default_db = os.path.join(directory, "cladup.sqlite")
    cladup._log_turn(
        "",
        "turn",
        "session",
        "prompt",
        "reply",
        1.0,
        cladup._meta(False, False),
    )
    check("turn logging disabled without db path", not os.path.exists(default_db))

    explicit_db = os.path.join(directory, "nested", "history.sqlite")
    cladup._log_turn(
        explicit_db,
        "turn",
        "session",
        "prompt",
        "reply",
        1.0,
        cladup._meta(False, False),
    )
    check("turn logging creates explicit db", os.path.exists(explicit_db))

print("\nall passed")
