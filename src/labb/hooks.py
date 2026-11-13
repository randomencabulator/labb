"""Hooks: shell commands the settings or the engine run around labb's work.

    "hooks": {
      "PreToolUse": [{"matcher": "run_command", "command": "./check.sh"}],
      "PostToolUse": [{"matcher": "run_command", "command": "ruff format . >/dev/null", "timeout": 30}]
    }

`matcher` is a regular expression for the tool's name (all tools if it's missing, empty
or "*"). The command runs with bash in the working directory and gets the call as JSON
on stdin: {"event", "tool", "args", "workdir"}, plus "result" after the call.

Exit code 2 means "no": before a call, the call doesn't run and the hook's stderr goes
to the model as the reason; after a call, its stderr is added to the result for the
model to see. Other exit codes don't change anything.

The other events work the same way, with no tool (a matcher other than "*" never matches), and
for RunStart, PreModel and PostModel a hook's stderr with exit code 2 is a note for the model's
next call. Stop runs when a reply is about to end, with {"reply", "todos", "goal", "continued"}
added ("continued": how many times a Stop hook already sent the model back in this reply, so a
hook can let it go): exit code 2 sends the model back to work with the hook's stderr as the
reason (continuation.py, at most `continue.hook` times). They run only in the terminal, like all hooks.
"""

import json
import re
import subprocess

from labb import engine

EVENTS = ("RunStart", "PreModel", "PostModel", "PreToolUse", "PostToolUse", "PreCompact", "Stop", "RunEnd")
DEFAULT_TIMEOUT = 60


def matches(entry: dict, tool: str) -> bool:
    matcher = entry.get("matcher") or "*"
    if matcher == "*":
        return True
    try:
        return re.fullmatch(matcher, tool) is not None
    except re.error:
        return False


def run(event: str, hooks: dict, tool: str, args: dict, workdir: str, result: str | None = None,
        extra: dict | None = None) -> list[str]:
    """Run the hooks of `event` that match the tool; returns the stderr of those that
    exited with 2 (their "no", or their feedback). `extra` is added to what they're sent."""

    entries = [e for e in hooks.get(event, []) if isinstance(e, dict) and isinstance(e.get("command"), str)]
    payload = {"event": event, "tool": tool, "args": args, "workdir": workdir, **(extra or {})}
    if result is not None:
        payload["result"] = result
    said = []
    for entry in entries:
        if not matches(entry, tool):
            continue
        try:
            process = subprocess.run(
                ["bash", "-c", entry["command"]],
                cwd=workdir,
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                timeout=entry.get("timeout", DEFAULT_TIMEOUT),
            )
        except (subprocess.SubprocessError, OSError):
            continue
        if process.returncode == 2:
            said.append(process.stderr.strip() or f"(the hook `{entry['command']}` gave no reason)")
    return said


def of(config: dict | None) -> dict:
    """The run's hooks: the settings', then its engine's"""

    configurable = (config or {}).get("configurable") or {}
    own, extra = configurable.get("hooks") or {}, engine.of(config).hooks
    if not extra:
        return own
    return {event: [*own.get(event, []), *extra.get(event, [])] for event in {*own, *extra}}


def around(event: str, config: dict | None, result: str | None = None, extra: dict | None = None) -> list[str]:
    """Run an event's hooks that aren't about a tool; what they said with exit code 2"""

    entries = of(config)
    workdir = ((config or {}).get("configurable") or {}).get("workdir")
    if not entries.get(event) or not workdir:
        return []
    return run(event, entries, "", {}, workdir, result=result, extra=extra)


def has(event: str, config: dict | None) -> bool:
    """Whether the run has hooks for this event (which run only with a working directory)"""
    return bool(of(config).get(event)) and bool(((config or {}).get("configurable") or {}).get("workdir"))
