"""A conversation as Markdown, for labb's /export and the web chat's download button."""

import json
import re
from datetime import date

from labb.runner import split_user_message, user_turns

# Arguments longer than this are cut in the tool lines
MAX_ARGS_CHARS = 200


def format_stats(stats: dict) -> str:
    """"gemma4:e4b · 12.4s · first token 1.8s · 3,688 in / 412 out · 39 tok/s · 2 tool calls"
    (the same as the web chat's stats line, frontend/web/src/format.ts)"""

    seconds = lambda ms: f"{ms / 1000:.1f}s" if ms < 10_000 else f"{ms / 1000:.0f}s"
    parts = [stats["model"], seconds(stats["duration_ms"])]
    if stats.get("first_token_ms") is not None:
        parts.append(f"first token {seconds(stats['first_token_ms'])}")
    # A stopped model call reports no usage: its input is unknown and its output is estimated
    output = f"{'~' if stats['stopped'] else ''}{stats['output_tokens']:,} out"
    if stats["input_tokens"]:
        parts.append(f"{stats['input_tokens']:,} in / {output}")
    elif stats["output_tokens"]:
        parts.append(output)
    if stats.get("generation_ms"):
        parts.append(f"{round(stats['output_tokens'] / (stats['generation_ms'] / 1000))} tok/s")
    if stats["tool_calls"]:
        parts.append(f"{stats['tool_calls']} tool call{'' if stats['tool_calls'] == 1 else 's'}")
    return " · ".join(parts)


def file_name(title: str) -> str:
    """"Fix the login bug" → "fix-the-login-bug.md\""""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60].rstrip("-")
    return f"{slug or 'conversation'}.md"


def tool_line(call: dict, result) -> str:
    args = json.dumps(call["args"], ensure_ascii=False)
    if len(args) > MAX_ARGS_CHARS:
        args = args[:MAX_ARGS_CHARS] + "…"
    line = f"- 🔧 `{call['name']}` `{args}`"
    if result is None:
        return line + " (no result)"
    failed = result.status == "error" or result.text.startswith(("Error:", "Not run:"))
    first = result.text.partition("\n")[0][:120]
    return line + (f" → ✗ {first}" if failed else f" → {len(result.text):,} characters")


def to_markdown(messages: list, title: str, stats: dict[str, dict] | None = None) -> str:
    """The conversation: each message you sent, then the reply's text, tool calls and stats"""

    stats = stats or {}
    results = {m.tool_call_id: m for m in messages if m.type == "tool"}
    turns = len(user_turns(messages))
    lines = [f"# {title}", "", f"_Exported on {date.today():%B %-d, %Y} · {turns} message{'' if turns == 1 else 's'}_", ""]
    in_reply = False
    for message in messages:
        if message.type == "human":
            typed, files, images = split_user_message(message)
            lines += ["## You", "", typed.strip(), ""]
            attached = files + ([f"{len(images)} image{'' if len(images) == 1 else 's'}"] if images else [])
            if attached:
                lines += [f"_Attached: {', '.join(attached)}_", ""]
            in_reply = False
        elif message.type == "ai":
            if not in_reply:
                lines += ["## Reply", ""]
                in_reply = True
            if message.text.strip():
                lines += [message.text.strip(), ""]
            if message.tool_calls:
                lines += [tool_line(call, results.get(call["id"])) for call in message.tool_calls] + [""]
            if message.id in stats:
                lines += [f"_{format_stats(stats[message.id])}_", ""]
    return "\n".join(lines).rstrip() + "\n"
