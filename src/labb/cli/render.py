"""How the terminal shows a reply as it streams: Markdown, tool lines with their results,
thinking, and the spinner line. One `console` for all of labb's output."""

import re
import time
from dataclasses import dataclass
from datetime import datetime

from rich.console import Console, Group
from rich.markdown import Markdown
from rich.segment import Segment, Segments
from rich.spinner import Spinner
from rich.text import Text

from labb import remote

console = Console()

# Lines of a command's output shown under it; Ctrl-O shows the rest (and the thinking)
PREVIEW_LINES = 3
# Code highlighting in replies, a Pygments style; `theme` in the settings changes it
code_theme = "monokai"

# Tools shown as "● Label argument"
TOOL_LABELS = {
    "read_file": ("Read", "path"),
    "list_files": ("List", "path"),
    "glob": ("Glob", "pattern"),
    "grep": ("Grep", "pattern"),
    "web_search": ("Search", "query"),
    "fetch_page": ("Fetch", "url"),
    "search_documents": ("Search documents", "query"),
    "save_document": ("Save document", "name"),
    "remember": ("Remember", "text"),
    "forget": ("Forget", "memory_id"),
    "use_skill": ("Skill", "name"),
    "read_skill_file": ("Read skill file", "path"),
    "save_skill": ("Save skill", "name"),
    "edit_file": ("Edit", "path"),
    "write_file": ("Write", "path"),
    "update_todos": ("Update todos", None),
    "read_notes": ("Read notes", None),
    "write_notes": ("Write notes", None),
    "ask_user": ("Ask", "question"),
    "task": ("Task", "description"),
    "read_output": ("Read output", "job_id"),
    "stop_command": ("Stop", "job_id"),
}
# Tools whose result's first line is the summary to show ("Edited a.py: +3 -1 lines")
FIRST_LINE_TOOLS = {"edit_file", "write_file", "write_notes", "ask_user"}
# How each step of the todo list looks
TODO_STYLES = {"[x]": ("✔", "green"), "[>]": ("◼", "bold"), "[ ]": ("◻", ""), "[!]": ("✗", "red"), "[-]": ("–", "dim")}
# A step's number in the list (tools/todo.py), which the model uses and the terminal doesn't show
TODO_NUMBER = re.compile(r"^\d+\. ")
# What the lines of a tool's result are, for its summary ("120 lines", "4 matches")
RESULT_UNITS = {"read_file": "line", "list_files": "entry", "glob": "file", "grep": "match"}


# How each provider says it refused the key, and where labb read it
PROVIDER_KEYS = {
    "OpenAI": ("OPENAI_API_KEY", re.compile(r"invalid_api_key|platform\.openai\.com|Incorrect API key")),
    "Anthropic": ("ANTHROPIC_API_KEY", re.compile(r"authentication_error|x-api-key")),
}


def key_source(variable: str) -> str:
    from labb.agent import ENV_FILE, FROM_ENVIRONMENT

    if variable in FROM_ENVIRONMENT:
        return f"the environment of this shell, which wins over {ENV_FILE}"
    return str(ENV_FILE)


def explain(error: str) -> str:
    """An error, with what to do when a key was refused: a provider's (OpenAI, Anthropic), or
    the server's for this PC"""

    if not re.search(r"\b401\b", error):
        return error
    for provider, (variable, refused) in PROVIDER_KEYS.items():
        if refused.search(error):
            return (f"{error}\n{provider} didn't accept {variable}, which labb read from {key_source(variable)}. "
                    f"Put your {provider} key there.")
    if remote.server():
        what = "This PC's key doesn't work any more" if remote.api_key() else "Not logged in"
        return f"{error}\n{what}: run labb --login."
    return error


def markdown(text: str) -> Markdown:
    return Markdown(text, code_theme=code_theme)


def format_args(args: dict) -> str:
    return ", ".join(value if isinstance(value, str) and value else repr(value) for value in args.values())


def plural(count: int, unit: str) -> str:
    if count == 1:
        return f"1 {unit}"
    return f"{count:,} {unit[:-1] + 'ies' if unit.endswith('y') else unit + ('es' if unit.endswith('ch') else 's')}"


def relative_time(iso: str) -> str:
    minutes = (datetime.now().astimezone() - datetime.fromisoformat(iso)).total_seconds() / 60
    if minutes < 1:
        return "now"
    if minutes < 60:
        return f"{minutes:.0f}m ago"
    if minutes < 60 * 24:
        return f"{minutes / 60:.0f}h ago"
    return f"{minutes / 60 / 24:.0f}d ago"


def tool_title(name: str, args: dict) -> Text:
    """"● Read cli.py", "● $ git status", "● some_tool(args)\""""

    if name == "run_command":
        background = " &" if args.get("background") else ""
        return Text.assemble(("● ", "yellow"), ("$ " + args.get("command", "") + background, "bold"))
    if name in TOOL_LABELS:
        label, argument = TOOL_LABELS[name]
        return Text.assemble(("● ", "yellow"), (label, "bold"), (f" {args.get(argument, '')}" if argument else "", ""))
    return Text.assemble(("● ", "yellow"), (name, "bold"), (f"({format_args(args)})", "dim"))


def result_lines(name: str, result: dict) -> list[Text]:
    """The lines under a tool call: what it returned, in short"""

    first = result.get("first_line", "")
    if name == "run_command":
        return command_result(result)
    if result.get("error"):
        return [Text(f"  ⎿ {first.removeprefix('Error: ')}", style="red")]
    if name == "update_todos":
        return todo_lines(result.get("preview", []))
    if first.startswith("No ") or first.endswith(" is empty") or name in FIRST_LINE_TOOLS:
        return [Text(f"  ⎿ {first}", style="dim")]
    if unit := RESULT_UNITS.get(name):
        lines = result.get("lines", 0)
        # read_file and the listings end with a "… more" line when they're cut
        return [Text(f"  ⎿ {plural(lines, unit)}", style="dim")]
    return [Text(f"  ⎿ {result['chars']:,} characters", style="dim")]


def todo_lines(lines: list[str]) -> list[Text]:
    """The todo list under its call: ✔ done, ◼ in progress, ◻ to do, ✗ blocked, – skipped"""
    shown = []
    for line in lines:
        mark, task = line[:3], TODO_NUMBER.sub("", line[4:])
        if mark not in TODO_STYLES:
            continue
        symbol, style = TODO_STYLES[mark]
        shown.append(Text(f"  {'⎿' if not shown else ' '} {symbol} {task}", style=style))
    return shown or [Text("  ⎿ The todo list is empty", style="dim")]


def command_result(event: dict) -> list[Text]:
    """The lines under a `$ command`: its exit code, then the start of its output"""

    summary = event.get("summary", "")
    if summary == "Exit code 0":
        lines = [Text("  ⎿ ✓ exit 0", style="green")]
    elif summary.startswith("Exit code"):
        lines = [Text(f"  ⎿ ✗ exit {summary.removeprefix('Exit code ')}", style="red")]
    elif summary.startswith("Not run"):
        lines = [Text("  ⎿ ✗ not run", style="yellow")]
    else:
        lines = [Text(f"  ⎿ ✗ {summary}", style="red")]
    preview = [line for line in event.get("preview", []) if line != "(no output)"]
    for line in preview[:PREVIEW_LINES]:
        lines.append(Text(f"    {line}", style="dim"))
    more = event.get("more_lines", 0) + max(0, len(preview) - PREVIEW_LINES)
    if more:
        lines.append(Text(f"    … +{more} lines (ctrl+o to expand)", style="dim"))
    return lines


@dataclass
class ToolBlock:
    name: str
    args: dict
    result: dict | None = None
    # What labb fixed in the model's call (see tool_calls.py)
    fixed: list[str] | None = None
    id: str | None = None

    def render(self):
        title = tool_title(self.name, self.args)
        if self.fixed:
            title = Text.assemble(title, (f"  (fixed: {', '.join(self.fixed)})", "dim"))
        return Group(title, *result_lines(self.name, self.result)) if self.result else title


@dataclass
class ThoughtBlock:
    seconds: float
    text: str
    show: bool

    def render(self):
        line = Text(f"✻ Thought for {self.seconds:.1f}s", style="dim italic")
        if self.show and self.text.strip():
            return Group(line, Text(self.text.strip(), style="dim italic"))
        if self.text.strip():
            line.append(" (ctrl+o to expand)", style="dim")
        return line


def stable_end(text: str) -> int:
    """How much of a streaming Markdown text is finished: up to the last blank line outside a
    code block that's followed by an unindented line, so nothing after it can change it"""
    end = pos = 0
    fence = None
    blank_at = None
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if blank_at is not None and stripped and not line[0].isspace():
            end = blank_at
        blank_at = None
        pos += len(line)
        if fence:
            if stripped.startswith(fence):
                fence = None
        elif stripped.startswith(("```", "~~~")):
            fence = stripped[:3]
        elif not stripped and line.endswith("\n"):
            blank_at = pos
    return end


def as_segments(lines: list[list[Segment]]) -> Segments:
    return Segments([segment for line in lines for segment in (*line, Segment.line())])


class MarkdownRest:
    """Markdown without its first `skip` lines, which are on screen already"""

    def __init__(self, text: str, skip: int):
        self.text = text
        self.skip = skip

    def __rich_console__(self, console, options):
        yield as_segments(console.render_lines(markdown(self.text), options, pad=False)[self.skip:])


class Tail:
    """The bottom of a renderable that fits the terminal. Live can only redraw lines on screen,
    so a taller view would be printed into the scrollback again on every refresh."""

    def __init__(self, renderable):
        self.renderable = renderable

    def __rich_console__(self, console, options):
        lines = console.render_lines(self.renderable, options, pad=False)
        yield as_segments(lines[-max(console.size.height - 1, 1):])


# Why a reply went round again (continuation.py), as the terminal says it
CONTINUE_REASONS = {
    "empty": "The reply was empty: asking again",
    "announced": "It said what it'd do next and stopped: telling it to go on",
    "loop_unscheduled": "It didn't say when to run the loop again: asking",
}


class ReplyView:
    """What a reply looks like while it streams: what the model thought, text blocks as
    Markdown, tool lines, and a spinner with the elapsed time until it ends. Finished parts
    are taken out with `ready` and printed for good, so the live part stays small."""

    def __init__(self, show_thinking: bool = False):
        self.blocks: list = []
        self.text = ""
        # Lines of `text`'s Markdown that are printed already
        self.printed = 0
        self.show_thinking = show_thinking
        self.thinking: str | None = None
        self.thinking_started = 0.0
        self.started = time.monotonic()
        self.status = "working…"
        # Ctrl-O was pressed: the tool outputs are shown in full when the reply ends
        self.expand = False

    def handle(self, event: dict):
        match event["type"]:
            case "thinking":
                if self.thinking is None:
                    self.end_text()
                    self.thinking = ""
                    self.thinking_started = time.monotonic()
                self.thinking += event["content"]
                self.status = "thinking…"
            case "token":
                self.end_thinking()
                self.text += event["content"]
                self.status = "writing…"
            case "tool_call":
                self.end_thinking()
                self.end_text()
                self.blocks.append(ToolBlock(event["name"], event["args"], fixed=event.get("fixed"), id=event.get("id")))
                self.status = f"running {event['name']}…"
            case "tool_result":
                # Its call, by id: calls that run at the same time finish in any order
                block = next((b for b in self.blocks if isinstance(b, ToolBlock) and b.result is None and b.id == event.get("id")), None)
                if block:
                    block.result = event
                self.status = "working…"
            case "compact":
                self.end_text()
                self.blocks.append(Text("🗜 Summarized the earlier messages to fit the model's context", style="dim"))
            case "continue":
                # A cut-off reply goes on in the same text; the others start afresh
                self.end_thinking()
                if event["reason"] != "truncated":
                    self.end_text()
                    self.blocks.append(Text(f"↻ {CONTINUE_REASONS.get(event['reason'], 'going on')}", style="dim"))
                self.status = "going on…"
            case "retry":
                # What the failed call wrote comes again, unless it's on screen for good already
                self.end_thinking()
                if self.printed:
                    self.end_text()
                self.text, self.printed = "", 0
                self.blocks.append(Text(f"↻ The model call failed, trying again in {event['wait']:g}s: {event['message']}", style="dim"))
                self.status = "waiting to try again…"
            case "error":
                self.end_thinking()
                self.end_text()
                self.blocks.append(Text(f"Error: {explain(event['message'])}", style="red"))

    def end_thinking(self):
        if self.thinking is not None:
            self.blocks.append(ThoughtBlock(time.monotonic() - self.thinking_started, self.thinking, self.show_thinking))
            self.thinking = None

    def end_text(self):
        if self.text:
            self.blocks.append(MarkdownRest(self.text, self.printed))
            self.text = ""
            self.printed = 0

    def ready(self, console: Console, final: bool = False) -> list:
        """The parts that won't change any more, to print above the live view; with `final`,
        everything (the reply ended or waits for an approval)"""
        parts = []
        while self.blocks and (final or not (isinstance(self.blocks[0], ToolBlock) and self.blocks[0].result is None)):
            block = self.blocks.pop(0)
            parts.append(block.render() if isinstance(block, (ToolBlock, ThoughtBlock)) else block)
        if not self.blocks and (end := stable_end(self.text)):
            lines = console.render_lines(markdown(self.text[:end]), console.options, pad=False)
            # Trailing blank lines are left for later: the next block decides whether they stay
            while lines and not "".join(segment.text for segment in lines[-1]).strip():
                lines.pop()
            if len(lines) > self.printed:
                parts.append(as_segments(lines[self.printed:]))
                self.printed = len(lines)
        return parts

    def render(self):
        parts = [block.render() if isinstance(block, (ToolBlock, ThoughtBlock)) else block for block in self.blocks]
        if self.thinking is not None and self.show_thinking:
            parts.append(Text(self.thinking.strip(), style="dim italic"))
        if self.text:
            parts.append(MarkdownRest(self.text, self.printed))
        elapsed = time.monotonic() - self.started
        hints = ["esc to stop", "the full reply will show when it ends" if self.expand else "ctrl+o: expand"]
        if self.thinking is not None:
            hints.append("ctrl+t: hide thinking" if self.show_thinking else "ctrl+t: show thinking")
        parts.append(Spinner("dots", Text(f" {self.status} {elapsed:.1f}s · {' · '.join(hints)}", style="dim")))
        return Tail(Group(*parts))
