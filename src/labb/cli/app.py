"""labb: the agent in your terminal, working in the directory you start it in.

Each start is a new conversation for the current directory; --continue resumes
the latest one there and --resume picks one. Replies stream as Markdown, Esc or
Ctrl-C stops a reply (like the web chat's Stop) and every reply ends with its stats.
"""

import argparse
import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import httpx
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.text import Text

# Importing agent loads the project's .env and builds the graph
from labb.agent import DATA_DIR, SOURCE_DIR, agent, chat_agent
from labb.cli.input import LOOP_DUE, ReplyKeys, copy_to_clipboard, make_prompt, prompt_until
from labb.context import compact, context_used
from labb.export import file_name, format_stats, to_markdown
from labb.cli.mcp_servers import McpServers
from labb.model import inspect_context, model_with_tools, summary_prompt, system_prompt
from labb.runner import (ReplyStats, attachments_of, can_take_back, run_reply, split_user_message, take_back, thinking_of, user_message,
                         user_turns, with_attachments)
from labb import tools as tools_module
from labb.tools import ToolRules, available, rules_for, set_extra_tools, tools_for
from labb.tools import browser as browser_tool
from labb.tools import goal as goals
from labb.tools import shell, todo
from labb.tools.loop import FAILURE_WAITS, MAX_UNSCHEDULED, UNSCHEDULED_SECONDS, Loop, duration, parse_interval
from labb.tools.edits import RULE as EDITS_RULE
from labb.tools.files import list_files, read_file, resolve
from labb.tools.shell import shorten
from labb import chats
from labb import costs
from labb import work
from labb import engine as engine_store
from labb import llm
from labb import memories
from labb import permissions
from labb import remote
from labb import settings
from labb import skills
from labb.cli import render, session_lock, snapshots, sync, workflows
from labb import inspection
from labb.cli.channel import Channel, Message as ChannelMessage, write_line as channel_line
from labb.cli.account import install_browser, login, logout, server_status, server_version, update
from labb.cli.render import CONTINUE_REASONS, ReplyView, console, explain, format_args, markdown, plural, relative_time, tool_title

# Tool lines and stats when stdout isn't a terminal (e.g. `labb -p ... > answer.md`)
stderr = Console(stderr=True)
HISTORY_FILE = Path.home() / ".labb_history"

# Ring the terminal's bell when a reply took longer than this, in case you looked away
BELL_AFTER_SECONDS = 15
# Ctrl-O shows at most this many lines of each tool output or thought
EXPANDED_LINES = 300

# What /init sends; labb reads AGENTS.md (or CLAUDE.md) into the system prompt
INIT_PROMPT = """\
Look through this project and write an AGENTS.md file in the working directory, \
for coding agents that will work on it later. If there's one already, improve it instead.

Find out with your file tools, and from the README and the build or package files: \
what the project is, how it's laid out (the main folders and files and what they're for), \
how to install, build, run and test it (the exact commands), and the conventions the code \
follows. Keep it short and specific to this project, as Markdown with a few headings, \
and leave out anything an agent could guess. Then write the file with run_command."""

# For /help and completion
COMMANDS = {
    "/new": "start a new conversation in this directory",
    "/clear": "same as /new",
    "/resume": "pick a conversation of this directory to continue",
    "/init": "have the agent write an AGENTS.md describing this project",
    "/plan": "plan mode on or off: the agent looks around and plans, changing nothing (Shift-Tab goes through default, accept edits and plan); /plan show prints the approved plan",
    "/goal": "keep the agent working until a goal is done: /goal [--calls N] <objective>; /goal shows it; /goal pause|resume|clear",
    "/undo": "take back the last message and its reply, and put back the files it changed",
    "/rewind": "go back to an earlier message: the conversation and the files",
    "/compact": "summarize the conversation now, to free up context",
    "/context": "what the model sees now and what each part costs (/context full for the exact input)",
    "/model": "show the model and the ones available, or switch: /model <name>",
    "/loop": "run a prompt again and again: /loop 5m <prompt>, or /loop <prompt> and the model says when; /loop stop",
    "/answer": "how much the model thinks first: /answer fast, auto or thinking",
    "/num-ctx": "the tokens Ollama keeps in memory for the model: /num-ctx <tokens>, or /num-ctx reset",
    "/engine": "the agent's core as a saved option: /engine, /engine use|show|diff <name>, /engine fork <new> [from]",
    "/thinking": "show or hide the model's reasoning as it streams (also Ctrl-T)",
    "/cost": "tokens and time used in this conversation (also /stats)",
    "/stats": "same as /cost",
    "/copy": "copy the last reply to the clipboard",
    "/export": "write the conversation to a Markdown file (/export [file])",
    "/permissions": "list the commands allowed without asking here (/permissions remove N)",
    "/mcp": "the MCP servers from the settings and their tools",
    "/browser": "let the agent use a browser in this conversation, or stop: /browser on|auto|off (auto: it doesn't ask)",
    "/tools": "the agent's tools; turn some off or on for this session (/tools off|on <name> …, /tools reset)",
    "/todos": "the agent's todo list for this conversation",
    "/jobs": "the commands running in the background, or stop one: /jobs stop <id>",
    "/memory": "what labb remembers about you (/memory add|keep|pin|unpin|forget …)",
    "/skills": "the skills the agent can use; /<skill> uses one on purpose",
    "/workflows": "your Studio workflows on the server",
    "/run": "run a Studio workflow: /run <workflow> [name=value …]",
    "/help": "show this help",
    "/exit": "quit (also exit, quit or Ctrl-D)",
}

HELP = "[bold]Commands[/]\n" + "\n".join(f"  {name:<14}{description}" for name, description in COMMANDS.items()) + """

[bold]Input[/]
  @path         attach a file (or a folder's listing) of the working directory; Tab completes
  ! command     run a command yourself; its output goes with your next message
  Alt-Enter     new line (also Ctrl-J, or \\ at the end of the line)
  Esc Esc       edit your last message and send it again, replacing its reply (files stay)
  Shift-Tab     plan mode on or off
  Ctrl-O        show the last reply's thinking and tool outputs in full (during a reply: when it ends)
  Ctrl-T        show or hide the model's thinking as it streams
  Ctrl-R        search the history

Esc or Ctrl-C stops the reply being written. Commands that change something ask before
they run; "always" remembers them in .labb/permissions.json. Start with [bold]labb -c[/]
to continue the latest conversation here, or [bold]labb -r[/] to pick one. Settings are in
~/.labb/settings.json and .labb/settings.json, and your own commands are Markdown
files in ~/.labb/commands/ and .labb/commands/. Skills are folders with a SKILL.md in
.labb/skills/ or ~/.labb/skills/ (.claude/skills/ too), and your server's."""


# Shift-Tab goes through these
MODES = ("default", "acceptEdits", "plan")

# /model <provider>:<model> for these; anything else is a model of the current provider
PROVIDERS = ("ollama", llm.CLOUD, "openai", "anthropic", "google_genai", "mistralai", "groq")

# An @path in a message: not part of a word (so e-mail addresses don't count)
MENTION = re.compile(r"(?<!\S)@(\S+)")


@dataclass
class Session:
    """The interactive chat's state"""

    workdir: str
    thread_id: str | None = None
    allow_all: bool = False
    model: str = llm.DEFAULT_MODEL
    # "plan": read-only commands, and the agent ends with a plan to approve; "acceptEdits": edits
    # in the working directory go through without asking (commands still ask)
    mode: str = "default"
    # How much the model thinks before it answers (llm.ANSWER_MODES)
    answer_mode: str = "auto"
    # The repository's branch, status and recent commits when the session started, for the model
    git_context: str | None = None
    # Save the files before each message, for /undo and /rewind; turned off if that fails
    snapshots: bool = True
    # Put in the input at the next prompt, e.g. the message /undo took back
    prefill: str = ""
    show_thinking: bool = False
    # Output of `!` commands, sent with the next message
    shell_outputs: list[str] = field(default_factory=list)
    # What was typed last, to edit with Esc Esc; `editing` means the next message replaces it
    last_typed: str | None = None
    editing: bool = False
    # How full the context window is, from 0 to 1, and the git branch, for the status line
    context: float | None = None
    branch: str | None = None
    # From the settings: hooks around tool calls, and the commands in .labb/commands
    hooks: dict = field(default_factory=dict)
    custom_commands: dict = field(default_factory=dict)
    mcp: McpServers | None = None
    # Which tools are on: from --tools/--disallowed-tools and the settings, changed with /tools
    tool_rules: ToolRules | None = None
    # Whether the agent has the browser: --browser or the settings' `browser`, changed with /browser
    browser: bool = False
    # Whether it does so without asking: --browser-auto or /browser auto, never from the settings
    browser_auto: bool = False
    # Sites it doesn't ask about first: the settings' `browserSites`
    browser_sites: list[str] = field(default_factory=list)
    # The engine in use, from --engine or the settings, changed with /engine
    engine: engine_store.Engine = engine_store.DEFAULT
    # The model to go back to when leaving an engine that has its own
    model_before_engine: str | None = None
    # The settings' MCP servers; an engine can add more
    mcp_servers: dict = field(default_factory=dict)
    # More models to offer in /model
    models: list[str] = field(default_factory=list)
    # From the settings: $ per million tokens for /cost, and the most a session may spend (costs.py)
    prices: dict = field(default_factory=dict)
    max_cost: float | None = None
    # The prompt /loop runs again and again, if there's one
    loop: Loop | None = None
    # Sends the sessions to the server, if there's one (LABB_SERVER)
    syncer: sync.Syncer | None = None
    unsynced: int = 0

    def changed(self):
        """The conversation changed: send it to the server"""
        if self.syncer and self.thread_id:
            self.syncer.changed(self.thread_id)

    def config(self) -> dict:
        return {"configurable": {
            "thread_id": self.thread_id, "workdir": self.workdir, "model": self.model,
            "mode": self.mode, "answer_mode": self.answer_mode, "git_context": self.git_context, "hooks": self.hooks,
            "tools": self.tool_rules, "engine": self.engine,
        }}

    def messages(self) -> list:
        if not self.thread_id:
            return []
        return chat_agent.get_state(self.config()).values.get("messages", [])


def diff_text(diff: str) -> Text:
    """A unified diff with added lines green and removed ones red"""
    text = Text()
    for line in diff.splitlines():
        style = "dim" if line.startswith(("---", "+++")) else "green" if line.startswith("+") else "red" if line.startswith("-") else "cyan" if line.startswith("@@") else ""
        text.append(line + "\n", style=style)
    text.rstrip()
    return text


def ask_approval(event: dict, stop_requested: threading.Event):
    """Ask whether a command may run: "yes", "always" or ("no", reason). Ctrl-C answers no
    and stops the reply."""

    console.bell()
    title = event.get("title", "Run this command?")
    body = diff_text(event["command"]) if event.get("needed") == [EDITS_RULE] else Text(event["command"])
    console.print(Panel(body, title=title, title_align="left", border_style="yellow", expand=False))
    can_remember = event["needed"] and not event["always_ask"]
    if event["always_ask"]:
        console.print(f"[dim]Asking every time because {event['always_ask']}.[/]")
    options = "[bold]y[/] yes"
    if can_remember:
        names = ", ".join(f'"{name}"' for name in event["needed"])
        options += f"   [bold]a[/] always allow {names} in this directory"
    options += "   [bold]n[/] no"
    while True:
        try:
            choice = console.input(f"{options}\n[bold yellow]?[/] ").strip().lower()
            if choice in ("y", "yes"):
                return "yes"
            if can_remember and choice in ("a", "always"):
                return "always"
            if choice in ("n", "no"):
                reason = console.input("[dim]Tell the agent why or what to do instead (optional):[/] ").strip()
                return ("no", reason)
        except (KeyboardInterrupt, EOFError):
            stop_requested.set()
            console.print("\n[red]Not allowed; stopping.[/]")
            return ("no", None)


def ask_question(event: dict, stop_requested: threading.Event) -> str | None:
    """The agent's question (the ask_user tool): pick an option by number or type an answer.
    Ctrl-C leaves it unanswered and stops the reply."""

    console.bell()
    options = event["options"]
    lines = Text(event["question"] + "\n")
    for i, option in enumerate(options, 1):
        lines.append(f"\n{i}. ", style="bold")
        lines.append(option + (" (recommended)" if i == 1 else ""))
    console.print(Panel(lines, title="Question", title_align="left", border_style="yellow", expand=False))
    while True:
        try:
            choice = console.input(f"[dim]Pick 1-{len(options)} or type your answer[/]\n[bold yellow]?[/] ").strip()
        except (KeyboardInterrupt, EOFError):
            stop_requested.set()
            console.print("\n[red]Not answered; stopping.[/]")
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(options):
            return options[int(choice) - 1]
        if choice and not choice.isdigit():
            return choice


def ask(thread_id: str, text: str | list, workdir: str, **options) -> dict | None:
    """Send one message, unless another process is answering in this session (see session_lock)"""

    with session_lock.held(thread_id) as free:
        if free:
            return write_reply(thread_id, text, workdir, **options)
    why = "This session is answering in another labb on this PC; try again when it's done."
    if options.get("channel"):
        channel_line({"type": "error", "message": why})
        channel_line({"type": "stopped", "stats": ReplyStats(options.get("model") or llm.default.name).to_dict(True)})
    else:
        console.print(f"[yellow]{why}[/]")
    return None


def run_configurable(workdir: str, *, allow_all: bool = False, mode: str = "default", git_context: str | None = None,
                     hooks: dict | None = None, tool_rules: ToolRules | None = None, answer_mode: str = "auto",
                     engine: engine_store.Engine | None = None, loop: Loop | None = None, browser: bool = False,
                     browser_auto: bool = False, browser_sites: list[str] | None = None, model: str | None = None) -> dict:
    """What a reply in the terminal runs with (the graph's `configurable`), before what only a
    running reply has: its stop flag, budget and the callbacks that ask"""

    configurable = {"workdir": workdir, "allow_all": allow_all, "mode": mode, "git_context": git_context, "hooks": hooks or {},
                    "tools": tool_rules, "answer_mode": answer_mode, "engine": engine}
    if loop:
        configurable["loop"] = loop
    if browser:
        configurable["browser"] = True
    if browser_auto:
        configurable["browser_auto"] = True
    if browser_sites:
        configurable["browser_sites"] = browser_sites
    if model:
        configurable["model"] = model
    return configurable


def write_reply(thread_id: str, text: str | list, workdir: str, *, interactive: bool = False, allow_all: bool = False,
        model: str | None = None, mode: str = "default", git_context: str | None = None,
        hooks: dict | None = None, show_thinking: bool = False, output_format: str = "text",
        name: bool = True, tool_rules: ToolRules | None = None, answer_mode: str = "auto",
        engine: engine_store.Engine | None = None,
        loop: Loop | None = None, budget: tuple[float, float, dict] | None = None, browser: bool = False,
        browser_auto: bool = False, browser_sites: list[str] | None = None, channel: Channel | None = None,
        message: ChannelMessage | None = None, replace_from: int | None = None) -> dict | None:
    """Send one message and show the reply as it streams. Esc or Ctrl-C stops it.

    In the interactive chat, commands that need approval are asked about here.
    Returns the reply's final event (done or stopped, with the stats, and `failed` if
    there was an error). `output_format` "json" or "stream-json" prints the reply as JSON
    instead (labb -p --output-format). `name`: have the model title a new chat.
    """
    events: queue.Queue = queue.Queue()
    stop_requested = threading.Event()
    configurable = run_configurable(workdir, allow_all=allow_all, mode=mode, git_context=git_context, hooks=hooks, tool_rules=tool_rules,
                                    answer_mode=answer_mode, engine=engine, loop=loop, browser=browser, browser_auto=browser_auto,
                                    browser_sites=browser_sites, model=model)
    if budget:
        # (limit, spent so far, prices): stops the reply once the session reaches its limit
        limit, spent, prices = budget
        configurable["budget"] = costs.Budget(limit, spent, model or llm.default.spec, prices, stop_requested)

    if interactive and console.is_terminal:
        # `view` and `handover` are the web chat's: here the question itself says where to look
        def approve(command: str, needed: list[str], always_ask: str | None, title: str = "Run this command?", view: str | None = None, handover: bool = False):
            # Runs in the reply's thread: hand the question to the main thread and wait
            answer: queue.Queue = queue.Queue()
            events.put({"type": "approval", "command": command, "needed": needed, "always_ask": always_ask,
                        "title": title, "answer": answer})
            return answer.get()

        configurable["approve"] = approve

        def ask_user(question: str, options: list[str]) -> str | None:
            # Like approve: the main thread asks, this one waits
            answer: queue.Queue = queue.Queue()
            events.put({"type": "question", "question": question, "options": options, "answer": answer})
            return answer.get()

        configurable["ask_user"] = ask_user

    if channel:
        channel.attach(message, configurable, events.put, stop_requested)

    def run():
        try:
            run_reply(thread_id, text, stop_requested, events.put, configurable=configurable, name=name, replace_from=replace_from)
        finally:
            events.put(None)

    threading.Thread(target=run, daemon=True).start()
    if channel:
        # The server has the session before it hears the reply ended
        return channel.relay(events, lambda: sync.send(thread_id))
    if output_format != "text":
        return print_json(events, stop_requested, stream=output_format == "stream-json")
    if not console.is_terminal:
        return print_plain(events, stop_requested)

    view = ReplyView(show_thinking)
    keys = ReplyKeys() if interactive else None
    final = None
    # Whether the reply ended with an error (it still ends with "done")
    failed = False

    def stop():
        stop_requested.set()
        view.status = "stopping…"

    # Transient: when it stops, the live view is erased and what's left is printed for good
    new_live = lambda: Live(view.render(), console=console, refresh_per_second=12, transient=True)

    def print_ready(final: bool = False):
        for part in view.ready(console, final):
            console.print(part)

    live = new_live()
    live.start()
    if keys:
        keys.start()
    try:
        while True:
            pressed = keys.pressed() if keys else set()
            if "esc" in pressed and not stop_requested.is_set():
                stop()
            if "ctrl-o" in pressed:
                view.expand = True
            if "ctrl-t" in pressed:
                view.show_thinking = not view.show_thinking
            try:
                event = events.get(timeout=0.1)
            except queue.Empty:
                print_ready()
                live.update(view.render())
                continue
            except KeyboardInterrupt:
                if stop_requested.is_set():
                    # A second Ctrl-C while stopping: give up on the reply
                    raise
                stop()
                continue
            if event is None:
                break
            if event["type"] in ("approval", "question") and stop_requested.is_set():
                # Asked by a call the stop gave up on: nobody's waiting for the answer
                event["answer"].put(("no", None) if event["type"] == "approval" else None)
                continue
            if event["type"] in ("approval", "question"):
                # Leave what's shown so far on screen, ask, then go on below it
                view.end_thinking()
                view.end_text()
                live.stop()
                print_ready(final=True)
                if keys:
                    keys.stop()
                asking = ask_approval if event["type"] == "approval" else ask_question
                event["answer"].put(asking(event, stop_requested))
                if keys:
                    keys.start()
                live = new_live()
                live.start()
                continue
            if event["type"] in ("done", "stopped"):
                final = {**event, "failed": failed}
            else:
                failed = failed or event["type"] == "error"
                view.handle(event)
            print_ready()
            live.update(view.render())
    finally:
        if keys:
            keys.stop()
        view.end_thinking()
        view.end_text()
        live.stop()
        print_ready(final=True)

    if final:
        stopped = final["type"] == "stopped"
        console.print(Text.assemble(("Stopped  " if stopped else "", "red"), (format_stats(final["stats"]), "dim")))
        if unfinished := UNFINISHED.get(final["stats"].get("end_reason")):
            console.print(f"[yellow]The reply may be unfinished: {unfinished}.[/] Say “go on” to continue.")
        if final["stats"]["duration_ms"] > BELL_AFTER_SECONDS * 1000:
            console.bell()
        if (spending := configurable.get("budget")) and spending.reached:
            console.print(f"[red]Stopped: this session has cost ${spending.spent:.2f}, its limit "
                          f"(maxCostPerSession is ${spending.limit:g}).[/]")
    if view.expand:
        show_last_reply(chat_agent.get_state({"configurable": {"thread_id": thread_id}}).values.get("messages", []))
    if final:
        # Ctrl-T during the reply stays for the next ones
        final["show_thinking"] = view.show_thinking
    return final


def print_plain(events: queue.Queue, stop_requested: threading.Event) -> dict | None:
    """A reply as plain text for pipes: the raw Markdown on stdout, everything else on stderr"""

    final = None
    failed = False
    while True:
        try:
            event = events.get()
        except KeyboardInterrupt:
            stop_requested.set()
            continue
        if event is None:
            break
        match event["type"]:
            case "token":
                print(event["content"], end="", flush=True)
            case "tool_call" if event["name"] == "run_command":
                stderr.print(f"$ {event['args'].get('command', '')}", style="dim")
            case "tool_call":
                stderr.print(f"🔧 {event['name']}({format_args(event['args'])})", style="dim")
            case "tool_result" if event["name"] == "run_command":
                stderr.print(f"  {event['summary']}", style="dim")
            case "continue" if event["reason"] != "truncated":
                stderr.print(f"↻ {CONTINUE_REASONS.get(event['reason'], 'going on')}", style="dim")
            case "retry":
                stderr.print(f"↻ The model call failed, trying again in {event['wait']:g}s: {event['message']}", style="dim")
            case "error":
                failed = True
                stderr.print(f"Error: {explain(event['message'])}", style="red")
            case "done" | "stopped":
                final = {**event, "failed": failed}
    # Flush before the stats go to stderr, so they don't land on the reply's last line
    print(flush=True)
    if final:
        stderr.print(("Stopped  " if final["type"] == "stopped" else "") + format_stats(final["stats"]), style="dim")
    return final


# Why a reply that ended still looks unfinished (ReplyStats.end_reason), after it went round
# again as often as the engine lets it
UNFINISHED = {
    "truncated": "it was cut off at the model's length limit",
    "empty": "the model replied with nothing",
    "announced": "it said what it'd do next and stopped",
}


def print_json(events: queue.Queue, stop_requested: threading.Event, stream: bool) -> dict | None:
    """A reply as JSON on stdout, for scripts: each event as a line (stream), or at the end
    one object with the reply's text, its tool calls, any error and the stats"""

    final = None
    error = None
    text = []
    tools = []
    while True:
        try:
            event = events.get()
        except KeyboardInterrupt:
            stop_requested.set()
            continue
        if event is None:
            break
        if stream:
            print(json.dumps(event, ensure_ascii=False), flush=True)
        match event["type"]:
            case "token":
                text.append(event["content"])
            case "tool_call":
                tools.append({"id": event.get("id"), "name": event["name"], "args": event["args"]})
            case "tool_result":
                for tool in tools:
                    if tool["id"] == event.get("id"):
                        tool.update(chars=event["chars"], error=event["error"], first_line=event["first_line"])
            case "error":
                error = event["message"]
            case "done" | "stopped":
                final = {**event, "failed": error is not None}
    if final and not stream:
        print(json.dumps({
            "result": "".join(text),
            "stopped": final["type"] == "stopped",
            "error": error,
            "tool_calls": tools,
            "stats": final["stats"],
        }, ensure_ascii=False, indent=2))
    return final


def show_last_exchange(thread_id: str):
    """When resuming, show where the conversation left off"""

    messages = chat_agent.get_state({"configurable": {"thread_id": thread_id}}).values.get("messages", [])
    question = next((m for m in reversed(messages) if m.type == "human"), None)
    answer = next((m for m in reversed(messages) if m.type == "ai" and m.text), None)
    console.print(f"[dim]Resuming a conversation with {len(messages)} messages. Last exchange:[/]")
    if question:
        console.print(Text(f"> {question.text}", style="bold"))
    if answer:
        console.print(markdown(answer.text))


def pick_session(workdir: str, thread_id: str | None) -> str | None:
    """--resume and /resume: the given session, or one picked from this directory's sessions"""

    sessions = chats.list_cli(workdir)
    if thread_id:
        if any(s["thread_id"] == thread_id for s in sessions):
            return thread_id
        console.print(f"[red]No conversation {thread_id} in this directory.[/]")
        return None
    if not sessions:
        console.print("[dim]No conversations in this directory yet; starting a new one.[/]")
        return None
    for i, session in enumerate(sessions[:20], 1):
        console.print(f"  [bold]{i:>2}[/]  {session['title']}  [dim]{relative_time(session['updated_at'])}[/]")
    choice = console.input("Pick a conversation (Enter for a new one): ").strip()
    if choice.isdigit() and 1 <= int(choice) <= min(len(sessions), 20):
        return sessions[int(choice) - 1]["thread_id"]
    return None


def show_permissions(workdir: str, argument: str):
    """/permissions: list the remembered commands, or remove one"""

    rules = permissions.load_allowed(workdir)
    if argument.startswith("remove"):
        number = argument.removeprefix("remove").strip()
        if not number.isdigit() or not 1 <= int(number) <= len(rules):
            console.print(f"[red]Give the number of the rule to remove (1-{len(rules)}).[/]" if rules else "[dim]No rules to remove.[/]")
            return
        removed = rules.pop(int(number) - 1)
        permissions.save_allowed(workdir, rules)
        console.print(f'[dim]Removed "{removed}"; it will ask again.[/]')
        return
    from_settings = [rule for rule in permissions.allowed(workdir) if rule not in rules]
    if not rules and not from_settings:
        console.print("[dim]No commands are allowed without asking here, besides the read-only ones.[/]")
        return
    if rules:
        console.print(f"[dim]Allowed without asking ({permissions.permissions_file(workdir)}):[/]")
        for i, rule in enumerate(rules, 1):
            console.print(f"  [bold]{i:>2}[/]  {rule}")
    if from_settings:
        console.print("[dim]Allowed by the settings (edit them there):[/]")
        for rule in from_settings:
            console.print(f"      {rule}")


def show_skills(session: Session):
    """/skills: the skills the agent can use here, and where each comes from"""

    found = skills.available(session.config())
    for problem in skills.problems(session.workdir):
        console.print(f"[yellow]Skipped {escape(problem)}[/]")
    if not found:
        console.print("[dim]No skills. A skill is a folder with a SKILL.md (a front matter with name and description, "
                      "then instructions) in .labb/skills/ or ~/.labb/skills/; the server's are here too.[/]")
        return
    for skill in found:
        where = skill.get("path") or "the server"
        console.print(f"  [bold]/{skill['name']}[/] [dim]{skill['source']} · {escape(where)}[/]")
        console.print(f"    {escape(skill['description'][:160])}")
    console.print("[dim]The agent loads one when a task matches it; /<name> \\[what to do] uses it on purpose.[/]")


def skill_attachment(session: Session, name: str) -> dict[str, str] | None:
    """/<skill> [what to do]: the skill's instructions, to attach to the message ({} if they
    can't be loaded); None if there's no such skill"""

    config = session.config()
    skill = skills.find(config, name)
    if skill is None:
        return None
    try:
        return skills.invoke_note(skill["name"], skills.instructions(config, skill))
    except Exception as e:
        console.print(f"[red]Couldn't load the skill {name}:[/] {escape(str(e))}")
        return {}


def show_memories(session: Session, argument: str):
    """/memory: list what labb remembers about you, or add, pin, unpin or forget one"""

    action, _, rest = argument.partition(" ")
    rest = rest.strip()
    config = session.config()
    try:
        if action == "add" and rest:
            memory = memories.remember(config, rest)
            console.print(f"[dim]{'Already remembered' if memory.get('existed') else 'Remembered'} as \\[{memory['id']}].[/]")
            return
        if action in ("pin", "unpin", "forget", "keep") and rest:
            known = memories.all_memories(config)
            # A number from the list, or an id
            memory_id = known[int(rest) - 1]["id"] if rest.isdigit() and 1 <= int(rest) <= len(known) else rest.strip("[]")
            if action == "forget":
                memories.forget(config, memory_id)
                console.print(f"[dim]Forgot \\[{escape(memory_id)}].[/]")
            elif action == "keep":
                memories.update(config, memory_id, reviewed=True)
                console.print(f"[dim]Kept \\[{escape(memory_id)}].[/]")
            else:
                memories.update(config, memory_id, pinned=action == "pin")
                console.print(f"[dim]{'Pinned' if action == 'pin' else 'Unpinned'} \\[{escape(memory_id)}].[/]")
            return
        if action:
            console.print("[red]Use /memory, /memory add <fact>, or /memory keep|pin|unpin|forget <number or id>.[/]")
            return
        known = memories.all_memories(config)
    except memories.MemoryError as e:
        console.print(f"[red]{escape(str(e))}[/]")
        return
    except httpx.HTTPError as e:
        console.print(f"[red]Couldn't reach the server: {e}[/]")
        return
    where = f"on {remote.server()}" if memories.asks_server() else "on this PC"
    if not known:
        console.print(f"[dim]No memories yet ({where}). labb saves lasting facts and preferences you share; /memory add <fact> adds one.[/]")
        return
    console.print(f"[dim]What labb remembers about you ({where}); pinned ones are in every conversation:[/]")
    for i, memory in enumerate(known, 1):
        review = " [yellow](to review: /memory keep or forget)[/]" if not memory.get("reviewed", True) else ""
        console.print(f"  [bold]{i:>2}[/]  {'📌 ' if memory['pinned'] else ''}{escape(memory['text'])} [dim]\\[{memory['id']}][/]{review}")


def last_turn(messages: list) -> list:
    """The messages of the last turn: the last user message and everything after it"""

    start = next((i for i in range(len(messages) - 1, -1, -1) if messages[i].type == "human"), None)
    return [] if start is None else messages[start:]


def show_lines(text: str, style: str):
    lines = text.splitlines()
    for line in lines[:EXPANDED_LINES]:
        console.print(Text(f"    {line}", style=style), soft_wrap=True)
    if len(lines) > EXPANDED_LINES:
        console.print(Text(f"    … {len(lines) - EXPANDED_LINES:,} more lines", style="dim"))


def show_last_reply(messages: list):
    """Ctrl-O: the last reply's thinking and tool calls with their outputs, in full and in order"""

    turn = last_turn(messages)
    calls = {call["id"]: call for m in turn if m.type == "ai" for call in m.tool_calls}
    shown = False
    for message in turn:
        if message.type == "ai" and (thinking := thinking_of(message).strip()):
            console.print(Text("✻ Thinking", style="dim italic"))
            show_lines(thinking, "dim italic")
            shown = True
        elif message.type == "tool":
            call = calls.get(message.tool_call_id, {"name": message.name or "tool", "args": {}})
            console.print(tool_title(call["name"], call["args"]))
            show_lines(message.text, "dim")
            shown = True
    if not shown:
        console.print("[dim]The last reply didn't think out loud or use any tools.[/]")


def show_cost(session: Session):
    """/cost: the totals of this conversation's replies"""

    stats = list(chats.get_stats(session.thread_id).values()) if session.thread_id else []
    if not stats:
        console.print("[dim]No replies in this conversation yet.[/]")
        return
    total = lambda key: sum(s.get(key) or 0 for s in stats)
    seconds = total("duration_ms") / 1000
    duration = f"{seconds:.0f}s" if seconds < 60 else f"{seconds // 60:.0f}m {seconds % 60:.0f}s"
    console.print(
        f"[dim]{plural(len(stats), 'reply')} · {duration} · {total('input_tokens'):,} in / "
        f"{total('output_tokens'):,} out · {plural(total('llm_calls'), 'model call')} · "
        f"{plural(total('tool_calls'), 'tool call')}[/]"
    )
    cost, unpriced = costs.of_stats(stats, session.prices)
    if session.prices:
        limit = f" of the ${session.max_cost:g} limit (maxCostPerSession)" if session.max_cost is not None else ""
        free = f"; no price for {', '.join(sorted(unpriced))}, counted as free" if unpriced else ""
        console.print(f"[dim]About ${cost:.4f}{limit}, from the prices in the settings{free}.[/]")
    else:
        console.print('[dim]Add "prices" to the settings to see what this costs (see /help).[/]')
    console.print("[dim]Input tokens count every model call, so the same messages count again in each one.[/]")


def copy_last_reply(session: Session):
    """/copy: the text of the last reply"""

    reply = next((m for m in reversed(session.messages()) if m.type == "ai" and m.text), None)
    if reply is None:
        console.print("[dim]No reply to copy yet.[/]")
        return
    how = copy_to_clipboard(reply.text)
    console.print(f"[dim]Copied the last reply ({len(reply.text):,} characters) with {how}.[/]")


def loop_command(session: Session, argument: str, interactive: bool):
    """/loop [interval] <prompt>, /loop stop, or /loop for what's looping"""

    if argument in ("", "stop"):
        if session.loop and argument == "stop":
            end_loop(session, "you stopped it")
        elif session.loop:
            loop = session.loop
            console.print(f"[dim]Looping {loop.kind()} ({plural(loop.runs, 'run')} so far, the next in "
                          f"{duration(max(0, loop.next_at - time.time()))}):[/] {loop.prompt}\n[dim]/loop stop ends it.[/]")
        else:
            console.print("[dim]No loop. /loop 5m <prompt> runs it now and every 5 minutes (s, m, h or d); "
                          "/loop <prompt> lets the model say when to run it again.[/]")
        return
    if not interactive:
        console.print("[red]Loops need an interactive terminal.[/]")
        return
    first, _, rest = argument.partition(" ")
    every = parse_interval(first)
    text = rest.strip() if every else argument
    if every is not None and not 10 <= every <= 86400:
        console.print("[red]The interval has to be between 10s and 1d.[/]")
        return
    if not text:
        console.print("[red]What should it run?[/] e.g. /loop 5m check whether the deploy finished")
        return
    if text.partition(" ")[0] in COMMANDS:
        console.print(f"[red]/loop runs a message, one of your commands or a skill, not {text.partition(' ')[0]}.[/]")
        return
    if session.loop:
        console.print(f"[dim]The loop of “{session.loop.prompt}” is replaced.[/]")
    # Its first run is now
    session.loop = Loop(text, every)
    console.print(f"[dim]Looping {session.loop.kind()}. Esc during a run, Ctrl-C at the prompt or /loop stop ends it.[/]")


def end_loop(session: Session, why: str):
    loop, session.loop = session.loop, None
    console.print(f"[dim]↻ Loop ended after {plural(loop.runs, 'run')}: {why}.[/]")


def after_loop_run(session: Session, final: dict | None):
    """Once a loop's run ends: when the next is, or the loop's end"""

    loop = session.loop
    scheduled, reason, loop.scheduled, loop.reason = loop.scheduled, loop.reason, None, ""
    if not final or final["type"] == "stopped":
        end_loop(session, "the run was stopped")
        return
    if final.get("failed"):
        # The model or the connection may be back in a moment
        loop.failures += 1
        if loop.failures > len(FAILURE_WAITS):
            end_loop(session, f"the run failed {loop.failures} times in a row")
            return
        wait = max(loop.every or 0, FAILURE_WAITS[loop.failures - 1])
        loop.next_at = time.time() + wait
        console.print(f"[dim]↻ The run failed: trying again in {duration(wait)} "
                      f"(the loop ends if it fails {len(FAILURE_WAITS) + 1 - loop.failures} more times in a row).[/]")
        return
    loop.failures = 0
    if loop.ended:
        end_loop(session, loop.ended)
    elif loop.every:
        loop.next_at = time.time() + loop.every
        console.print(f"[dim]↻ Next run in {duration(loop.every)}.[/]")
    elif scheduled:
        loop.unscheduled, loop.last_wait = 0, scheduled
        loop.next_at = time.time() + scheduled
        console.print(f"[dim]↻ Next run in {duration(scheduled)}{': ' + reason if reason else ''}.[/]")
    else:
        loop.unscheduled += 1
        if loop.unscheduled >= MAX_UNSCHEDULED:
            end_loop(session, f"the model didn't say when to run it again, {loop.unscheduled} times in a row")
            return
        wait = loop.last_wait or UNSCHEDULED_SECONDS
        loop.next_at = time.time() + wait
        console.print(f"[dim]↻ The model didn't say when to run it again: the next run is in {duration(wait)} "
                      "(the loop ends if that happens again).[/]")


def switch_answer_mode(session: Session, argument: str):
    """/answer: show the answer mode, or switch to another for this session"""

    if not argument:
        for mode, description in llm.ANSWER_MODES.items():
            mark = "●" if mode == session.answer_mode else " "
            console.print(f"  {mark} [bold]{mode:<9}[/][dim]{description}[/]")
        console.print("[dim]Switch with /answer <mode>.[/]")
        return
    if argument not in llm.ANSWER_MODES:
        console.print(f"[red]There's no answer mode {argument}:[/] it's fast, auto or thinking.")
        return
    session.answer_mode = argument
    console.print(f"[dim]Answer mode {argument}: {llm.ANSWER_MODES[argument]}.[/]")


def set_num_ctx(tokens: int | None) -> str | None:
    """Have Ollama models use this context size from now on; what's wrong with it, if anything"""

    try:
        llm.set_num_ctx(tokens)
    except ValueError as e:
        return str(e)
    return None


def switch_num_ctx(session: Session, argument: str):
    """/num-ctx: show Ollama's context size, or change it for this session"""

    current = llm.load(session.model)
    if argument:
        try:
            tokens = None if argument in ("reset", "default") else parse_tokens(argument)
        except ValueError:
            console.print(f"[red]{argument} isn't a number of tokens[/] [dim](e.g. /num-ctx 65536 or /num-ctx 64k)[/]")
            return
        if problem := set_num_ctx(tokens):
            console.print(f"[red]{problem}.[/]")
            return
        current = llm.load(session.model)
    console.print(f"[dim]Ollama context size:[/] [bold]{llm.ollama_num_ctx:,}[/] [dim]tokens"
                  f"{' (OLLAMA_NUM_CTX)' if llm.ollama_num_ctx == llm.ENV_NUM_CTX else ''}[/]")
    if current.kind == "ollama":
        console.print(f"[dim]{current.spec} compacts within {current.context_window:,} tokens.[/]")
    else:
        console.print(f"[dim]{current.spec} isn't an Ollama model: this applies when you switch to one.[/]")
    if not argument:
        console.print("[dim]Change it with /num-ctx <tokens> (e.g. 64k), or /num-ctx reset. It's kept for this session; "
                      'for every session, set "ollamaNumCtx" in the settings.[/]')


def parse_tokens(text: str) -> int:
    """'65536', '64k' or '1m' as tokens (k is 1024); ValueError if it's none of those"""

    text = text.strip().lower().replace(",", "").replace("_", "")
    scale = {"k": 1024, "m": 1024 * 1024}.get(text[-1:], 1)
    number = text[:-1] if scale > 1 else text
    if not number.isdigit():
        raise ValueError(text)
    return int(number) * scale


def tokens_argument(text: str) -> int:
    try:
        return parse_tokens(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text} isn't a number of tokens (e.g. 65536 or 64k)")


def switch_model(session: Session, argument: str):
    """/model: show the model and the Ollama models, or switch to one for this session"""

    current = llm.load(session.model)
    if not argument:
        console.print(f"[dim]Model:[/] [bold]{current.spec}[/] [dim]· context window {current.context_window:,} tokens[/]")
        if session.models:
            console.print("[dim]From the settings:[/]")
            for spec in session.models:
                console.print(f"  {'●' if spec == current.spec else ' '} {spec}")
        names = llm.ollama_models()
        if names:
            console.print("[dim]On the Ollama server:[/]")
            for name in names:
                mark = "●" if current.provider == "ollama" and name == current.name else " "
                console.print(f"  {mark} {name}")
        if not llm.listed_models() and (cloud := llm.provider_models()):
            console.print("[dim]In the cloud (Ollama Cloud, and the providers with your API keys):[/]")
            for spec in cloud:
                console.print(f"  {'●' if spec == current.spec else ' '} {spec}")
        console.print('[dim]Switch with /model <name> (an Ollama model, or a cloud one) '
                      'or /model <provider>:<model>, e.g. openai:gpt-5.5.[/]')
        return

    if argument.partition(":")[0] in PROVIDERS and ":" in argument:
        spec = argument
    else:
        # A bare name (Ollama names have colons too, like gemma4:e4b) is a model of the current provider
        spec = f"{current.provider}:{argument}"
    if spec.startswith("ollama:"):
        name = spec.removeprefix("ollama:")
        names = llm.ollama_models()
        # Cloud models (like gpt-oss:120b-cloud) run without being pulled, so aren't in the list; Ollama still knows them
        if names and name not in names and not llm.ollama_info(name):
            console.print(f"[red]The Ollama server has no model {name}.[/] /model lists the ones it has; a cloud model's name ends in -cloud.")
            return
    try:
        models = llm.load(spec)
    except ValueError as e:
        console.print(f"[red]{e}[/]")
        return
    session.model = models.spec
    update_context(session)
    console.print(f"[dim]Using {models.spec} for this session.[/]")
    if warning := llm.no_tools_warning(models.spec):
        console.print(f"[yellow]{warning}[/]")


def compact_now(session: Session):
    """/compact: summarize the conversation so far"""

    messages = session.messages()
    if not messages:
        console.print("[dim]Nothing to summarize yet.[/]")
        return
    config = session.config()
    state = chat_agent.get_state(config).values
    with console.status("Summarizing the conversation…"):
        try:
            update = compact(state, system_prompt=system_prompt(config), tools=tools_for(True, None, rules_for(config)),
                             models=llm.for_config(config), force=True, summary_prompt=summary_prompt(config), engine=session.engine)
        except Exception as e:
            console.print(f"[red]Couldn't summarize: {e}[/]")
            return
    if not update:
        console.print("[dim]Everything is summarized already.[/]")
        return
    # As llm_call, so the graph sees a finished reply and the next message starts a new run
    chat_agent.update_state(config, update, as_node="llm_call")
    before = session.context
    update_context(session)
    freed = f" (context {before:.0%} → {session.context:.0%})" if before is not None and session.context is not None else ""
    console.print(f"[dim]Summarized {plural(len(messages), 'message')}{freed}. The full conversation is still saved.[/]")


# The colours of /context's parts, in its bar and table
PART_STYLES = {"system": "blue", "tools": "magenta", "summary": "yellow", "messages": "green", "recalled": "cyan"}
CONTEXT_BAR = 50


def show_context(session: Session, full: bool = False):
    """/context: what the next model call gets (after compaction and clearing), and what each
    part costs; `full` pages through the exact input"""

    config = session.config()
    state = chat_agent.get_state(config).values if session.thread_id else {}
    try:
        seen = inspect_context(state, config)
    except Exception as e:
        console.print(f"[red]Couldn't inspect the context: {e}[/]")
        return
    if full:
        with console.pager(styles=True):
            for section in seen["system"]:
                console.print(Text(f"── system: {section['label']} · {section['tokens']:,} tokens", style="bold blue"))
                console.print(Text(section["text"]))
            if seen["summary"]:
                console.print(Text("── summary" + (f" of messages 1–{seen['summarized']}" if seen["summarized"] else ""), style="bold yellow"))
                console.print(Text(seen["summary"]))
            for m in seen["messages"]:
                console.print(Text(f"── #{m['index'] + 1} {m['role']}{f' ({m['name']})' if m['name'] else ''} · {m['tokens']:,} tokens"
                                   + (" · cleared" if m["cleared"] else "") + (" · with recalled memories" if m["recalled"] else ""),
                                   style="bold green"))
                if m["text"]:
                    console.print(Text(m["text"]))
                for call in m["tool_calls"]:
                    console.print(Text(f"→ {call['name']}({json.dumps(call['args'], ensure_ascii=False, default=str)})", style="dim"))
        return

    window = seen["window"]
    bar = Text()
    for part in seen["parts"]:
        bar.append("█" * max(1, round(part["tokens"] / window * CONTEXT_BAR)), style=PART_STYLES[part["kind"]])
    bar.append("░" * max(0, CONTEXT_BAR - len(bar)), style="dim")
    bar.append(f" {seen['used']:,} / {window:,} tokens ({seen['share']:.0%}) · {seen['model']}", style="dim")
    console.print(bar, no_wrap=True, overflow="ellipsis")
    for part in seen["parts"]:
        line = Text("  ■ ", style=PART_STYLES[part["kind"]])
        line.append(f"{part['label']:<18} {part['tokens']:>8,}  {part['tokens'] / window:>4.0%}")
        console.print(line)
    console.print(Text(f"    {'Free':<18} {max(0, window - seen['used']):>8,}", style="dim"))
    for section in seen["system"]:
        console.print(Text(f"      {section['label']:<40} {section['tokens']:>8,}", style="dim"), no_wrap=True, overflow="ellipsis")

    notes = [f"Summarizes at {seen['compact_at']:,} tokens (75%)" + (": the next message will be summarized first" if seen["will_compact"] else "")]
    if seen["summarized"]:
        notes.append(f"the summary stands in for the first {plural(seen['summarized'], 'message')} of {seen['total_messages']}")
    if seen["cleared"]["count"]:
        notes.append(f"{plural(seen['cleared']['count'], 'old tool result')} cleared ({seen['cleared']['chars']:,} characters)")
    if seen["scale"] != 1:
        notes.append(f"estimates scaled ×{seen['scale']} to the last reply's real input tokens")
    console.print(f"[dim]{escape('; '.join(notes))}.[/]")

    if seen["messages"]:
        largest = sorted(seen["messages"], key=lambda m: m["tokens"], reverse=True)[:5]
        console.print("[dim]Largest messages:[/]")
        for m in largest:
            label = f"#{m['index'] + 1} {m['role']}" + (f" {m['name']}" if m["name"] else "")
            marks = (" cleared" if m["cleared"] else "") + (" +memories" if m["recalled"] else "")
            preview = " ".join(m["text"].split())[:60]
            console.print(Text(f"  {label:<22} {m['tokens']:>8,}{marks}  {preview}", style="dim"), no_wrap=True, overflow="ellipsis")
    console.print("[dim]/context full shows the exact input.[/]")


def take_back_last_turn(session: Session) -> bool:
    """Esc Esc: remove the last message and its reply, so an edited message replaces them"""

    turns = len(user_turns(session.messages()))
    if not turns:
        return False
    if reason := can_take_back(session.thread_id, turns - 1):
        console.print(f"[red]Can't replace that message: {reason}. Sending it as a new one.[/]")
        return False
    take_back(session.thread_id, turns - 1)
    return True


def typed_text(message) -> str:
    """What the user typed in a saved message, without the attached files and command outputs"""
    return split_user_message(message)[0]


STATUS_WORDS = {"A": ("delete", "red"), "M": ("restore", "yellow"), "D": ("bring back", "green")}


def rewind_to(session: Session, turn: int, what: str):
    """/undo and /rewind: go back to before the `turn`-th user message, the conversation and
    the files, after showing what changes and asking"""

    if not session.thread_id:
        console.print("[dim]Nothing to undo yet.[/]")
        return
    if reason := can_take_back(session.thread_id, turn):
        console.print(f"[red]Can't go back: {reason}.[/]")
        return
    messages = session.messages()
    start = user_turns(messages)[turn]
    snapshot = chats.get_snapshot(session.thread_id, turn)
    changes = []
    if snapshot:
        try:
            changes = snapshots.changes_since(session.workdir, snapshot)
        except snapshots.SnapshotError as e:
            console.print(f"[red]Can't compare the files: {e}[/]")
            return

    console.print(f"[dim]This takes back {what} ({plural(len(messages) - start, 'message')}).[/]")
    if not snapshot:
        console.print("[dim]There's no snapshot of the files from then, so only the conversation goes back.[/]")
    elif not changes:
        console.print("[dim]No files changed since then.[/]")
    else:
        console.print("[dim]Files to put back as they were (your own changes to them since then go too):[/]")
        for status, path in changes:
            word, style = STATUS_WORDS.get(status, ("restore", "yellow"))
            console.print(Text.assemble((f"  {word:<10}", style), (path, "")))
    try:
        if console.input("Go back? [bold]y[/] yes   [bold]n[/] no\n[bold yellow]?[/] ").strip().lower() not in ("y", "yes"):
            console.print("[dim]Nothing changed.[/]")
            return
    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Nothing changed.[/]")
        return

    if changes:
        try:
            snapshots.restore(session.workdir, snapshot, changes)
        except (snapshots.SnapshotError, OSError) as e:
            console.print(f"[red]Couldn't put the files back: {e}. The conversation is unchanged.[/]")
            return
    session.prefill = typed_text(messages[start])
    take_back(session.thread_id, turn)
    session.changed()
    session.last_typed = None
    update_context(session)
    restored = f" and {plural(len(changes), 'file')}" if changes else ""
    console.print(f"[dim]↺ Went back: the conversation{restored}. Your message is in the input to edit or send again.[/]")


def pick_rewind(session: Session):
    """/rewind: pick the message to go back to"""

    messages = session.messages()
    starts = user_turns(messages)
    if not starts:
        console.print("[dim]Nothing to rewind yet.[/]")
        return
    shown = list(enumerate(starts))[-10:]
    for turn, index in shown:
        text = " ".join(typed_text(messages[index]).split())
        console.print(f"  [bold]{turn + 1:>2}[/]  {text[:80] + ('…' if len(text) > 80 else '')}")
    try:
        choice = console.input("Go back to before which message? (Enter to cancel) ").strip()
    except (KeyboardInterrupt, EOFError):
        return
    if not choice.isdigit() or not shown[0][0] + 1 <= int(choice) <= len(starts):
        console.print("[dim]Nothing changed.[/]")
        return
    turn = int(choice) - 1
    count = len(starts) - turn
    rewind_to(session, turn, f"message {choice}" + (f" and the {count - 1} after it" if count > 1 else ""))


def save_snapshot(session: Session):
    """Before a message: save the files, so /undo can put them back"""

    if not session.snapshots:
        return
    if not snapshots.can_snapshot(session.workdir):
        session.snapshots = False
        console.print("[dim]Files aren't saved for /undo in your home folder or /, which are too big.[/]")
        return
    try:
        tree = snapshots.take(session.workdir)
    except snapshots.SnapshotError as e:
        session.snapshots = False
        console.print(f"[dim]Files aren't saved for /undo in this session: {e}.[/]")
        return
    chats.save_snapshot(session.thread_id, len(user_turns(session.messages())), tree)


def run_git(workdir: str, *args: str) -> str | None:
    try:
        result = subprocess.run(["git", *args], cwd=workdir, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    # Only the end: `git status --short` lines start with a space
    return result.stdout.rstrip() if result.returncode == 0 else None


GIT_STATUS_LINES = 20


def git_context(workdir: str) -> str | None:
    """The repository's branch, status and recent commits, for the system prompt"""

    if run_git(workdir, "rev-parse", "--is-inside-work-tree") != "true":
        return None
    branch = run_git(workdir, "branch", "--show-current") or "(detached HEAD)"
    status = (run_git(workdir, "status", "--short") or "").splitlines()
    if len(status) > GIT_STATUS_LINES:
        status = status[:GIT_STATUS_LINES] + [f"… {len(status) - GIT_STATUS_LINES} more"]
    commits = run_git(workdir, "log", "--oneline", "-5") or "(no commits yet)"
    return (
        f"The working directory is in a git repository, on branch `{branch}`. As of the start of "
        f"this session (it may have changed since):\n\nStatus:\n{chr(10).join(status) or '(clean)'}"
        f"\n\nRecent commits:\n{commits}"
    )


def approve_plan() -> str | None:
    """After a reply in plan mode: go ahead with the plan? The mode to go ahead in, or None"""

    console.bell()
    try:
        choice = console.input("Go ahead with this plan? [bold]y[/] yes   [bold]a[/] yes, and accept its edits without asking   "
                               "[bold]n[/] keep planning (then say what to change)\n[bold yellow]?[/] ")
    except (KeyboardInterrupt, EOFError):
        return None
    return {"y": "default", "yes": "default", "a": "acceptEdits"}.get(choice.strip().lower())


def attach_mentions(text: str, workdir: str, out: Console = console) -> str:
    """Add the files (or folder listings) mentioned as @path to the message, listing them on `out`"""

    config = {"configurable": {"workdir": workdir}}
    attached = {}
    for match in MENTION.finditer(text):
        path = match.group(1).rstrip(".,;:!?)\"'")
        if path in attached:
            continue
        try:
            _, target = resolve(config, path)
        except ValueError:
            continue
        if target.is_file():
            content = read_file.invoke({"path": path}, config)
            kind = plural(len(content.splitlines()), "line")
        elif target.is_dir():
            content = list_files.invoke({"path": path}, config)
            kind = "folder"
        else:
            continue
        if content.startswith("Error:"):
            out.print(f"[red]Can't attach {path}: {content.removeprefix('Error: ')}[/]")
            continue
        attached[path] = content
        out.print(Text.assemble(("  📎 ", ""), (path, "bold"), (f" ({kind})", "dim")))
    return with_attachments(text, attached)


def run_shell(session: Session, command: str):
    """`! command`: run it yourself, show its output, and keep it for the next message"""

    process = subprocess.Popen(
        ["bash", "-c", command],
        cwd=session.workdir,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    output = []
    head = None
    try:
        for line in process.stdout:
            line = line.decode("utf-8", errors="replace")
            output.append(line)
            console.print(Text(line.rstrip("\n")), soft_wrap=True)
        process.wait()
    except KeyboardInterrupt:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        head = "Stopped by the user."
    head = head or f"Exit code {process.returncode}"
    console.print(Text(f"  ⎿ {head}", style="green" if process.returncode == 0 else "red"))

    text = shorten("".join(output)) or "(no output)"
    session.shell_outputs.append(f"<my-command>\nI ran this command myself:\n$ {command}\n{head}\n{text}\n</my-command>")


def update_context(session: Session):
    """Estimate how full the context window is, for the status line"""

    try:
        state = chat_agent.get_state(session.config()).values if session.thread_id else {}
        if not state.get("messages"):
            session.context = None
            return
        config = session.config()
        session.context = context_used(state, system_prompt=system_prompt(config), tools=tools_for(True, None, rules_for(config)),
                                       models=llm.load(session.model), engine=session.engine)
    except Exception:
        session.context = None


def status_line(session: Session) -> str:
    """Under the prompt: model · directory · branch · context, and what's waiting to be sent"""

    home = str(Path.home())
    directory = "~" + session.workdir[len(home):] if session.workdir.startswith(home) else session.workdir
    parts = [llm.load(session.model).name, directory]
    if session.branch:
        parts.append(f"⎇ {session.branch}")
    if session.context is not None:
        parts.append(f"{session.context:.0%} context")
    if session.answer_mode != "auto":
        parts.append(f"{session.answer_mode} answers")
    if session.engine is not engine_store.DEFAULT:
        parts.append(f"{session.engine.name} engine")
    if session.loop:
        parts.append(f"↻ loop {session.loop.kind()}, next in {duration(max(0, session.loop.next_at - time.time()))}")
    if goal := goal_status(session):
        parts.append(goal)
    if session.mode == "plan":
        parts.append("⏸ plan mode (shift+tab to leave)")
    elif session.mode == "acceptEdits":
        parts.append("⏵⏵ accept edits (shift+tab for plan mode)")
    if session.allow_all:
        parts.append("allow-all")
    if session.tool_rules:
        off = sum(not session.tool_rules.is_on(tool.name) for tool in tools_for(True))
        parts.append(f"{plural(off, 'tool')} off")
    if session.shell_outputs:
        parts.append(f"{plural(len(session.shell_outputs), 'command output')} to send")
    if session.editing:
        parts.append("editing your last message: Enter replaces it and its reply")
    if session.unsynced:
        parts.append(f"{plural(session.unsynced, 'session')} not on the server yet")
    return " " + " · ".join(parts)


def load_custom_commands(workdir: str) -> dict[str, dict]:
    """Your own commands: each Markdown file in ~/.labb/commands/ and .labb/commands/
    (the project's win) is a /name prompt. A `description:` in a front matter block
    (between --- lines) shows in /help, or else the start of the prompt. Built-in
    commands can't be replaced."""

    found = {}
    for folder in (settings.USER_DIR / "commands", Path(workdir) / ".labb" / "commands"):
        for path in sorted(folder.glob("*.md")) if folder.is_dir() else []:
            name = "/" + path.stem
            if name in COMMANDS:
                continue
            try:
                text = path.read_text(errors="replace")
            except OSError:
                continue
            description = ""
            if text.startswith("---\n"):
                header, sep, body = text[4:].partition("\n---\n")
                if sep:
                    text = body
                    for line in header.splitlines():
                        key, _, value = line.partition(":")
                        if key.strip() == "description":
                            description = value.strip().strip("\"'")
            first_line = text.strip().partition("\n")[0]
            description = description or (first_line[:60] + ("…" if len(first_line) > 60 else ""))
            found[name] = {"prompt": text.strip(), "description": description}
    return found


def expand_command(prompt: str, argument: str) -> str:
    """A custom command's prompt with what was typed after it in place of $ARGUMENTS
    (or after the prompt, if it has no $ARGUMENTS)"""

    if "$ARGUMENTS" in prompt:
        return prompt.replace("$ARGUMENTS", argument)
    return f"{prompt}\n\n{argument}" if argument else prompt


def start_mcp(servers: dict, workdir: str, out: Console) -> McpServers | None:
    """Connect to the settings' MCP servers and offer their tools to the model"""

    if not servers:
        return None
    with out.status(f"Connecting to {plural(len(servers), 'MCP server')}…"):
        mcp = McpServers(servers, workdir)
    set_extra_tools(mcp.tools)
    # The model was bound to the tools there were before
    model_with_tools.cache_clear()
    for name, status in mcp.status.items():
        if isinstance(status, int):
            out.print(f"[dim]MCP {name}: {plural(status, 'tool')}[/]")
        else:
            out.print(f"[red]MCP {name} isn't available:[/] {status}")
    return mcp


def show_mcp(session: Session):
    """/mcp: the servers and their tools"""

    if not session.mcp:
        console.print('[dim]No MCP servers. Add them to "mcpServers" in ~/.labb/settings.json or .labb/settings.json.[/]')
        return
    for name, status in session.mcp.status.items():
        if isinstance(status, str):
            console.print(f"[bold]{name}[/] [red]not available:[/] {status}")
            continue
        console.print(f"[bold]{name}[/] [dim]· {plural(status, 'tool')}[/]")
        for tool in session.mcp.tools:
            if tool.name.startswith(f"mcp__{name}__"):
                description = tool.description.partition(") ")[2].split("\n")[0][:80]
                console.print(f"  {tool.name} [dim]{description}[/]")
    console.print("[dim]They ask before they run; allow them in the settings' permissions.allow (e.g. mcp__name__*).[/]")


def apply_settings(workdir: str, model_flag: str | None, out: Console) -> dict:
    """Load the settings, report problems, and set what applies to the whole process"""

    config, problems = settings.load(workdir)
    for problem in problems:
        out.print(f"[yellow]Settings: {problem}[/]")
    if theme := config.get("theme"):
        try:
            from pygments.styles import get_style_by_name
            get_style_by_name(theme)
            render.code_theme = theme
        except Exception:
            out.print(f'[yellow]Settings: theme "{theme}" isn\'t a Pygments style; using {render.code_theme}.[/]')

    if "browserSites" in config:
        cleaned = {entry: browser_tool.clean_site(entry) for entry in map(str, config["browserSites"])}
        for entry, site in cleaned.items():
            if not site:
                out.print(f'[yellow]Settings: browserSites: "{entry}" isn\'t a site; ignoring it.[/]')
        config["browser_sites"] = [site for site in cleaned.values() if site]

    if "parallelTools" in config:
        limit = config["parallelTools"]
        if isinstance(limit, int) and not isinstance(limit, bool) and limit >= 1:
            tools_module.parallel_limit = lambda: limit
        else:
            out.print(f'[yellow]Settings: parallelTools should be a whole number, 1 or more; using {tools_module.DEFAULT_PARALLEL}.[/]')

    if "ollamaNumCtx" in config:
        if problem := set_num_ctx(config["ollamaNumCtx"]):
            out.print(f"[yellow]Settings: ollamaNumCtx: {problem}; using {llm.ENV_NUM_CTX:,}.[/]")

    if config.get("answerMode", "auto") not in llm.ANSWER_MODES:
        out.print(f'[yellow]Settings: answerMode "{config.pop("answerMode")}" isn\'t fast, auto or thinking; using auto.[/]')

    # -m beats the settings, which beat MODEL in .env
    spec = model_flag or config.get("model")
    config["model"] = llm.DEFAULT_MODEL
    if spec:
        try:
            config["model"] = llm.load(spec).spec
        except ValueError as e:
            out.print(f"[red]{e}[/] [dim]Using {llm.DEFAULT_MODEL}.[/]")
    return config


def names(text: str) -> list[str]:
    return [name for name in re.split(r"[,\s]+", text) if name]


def tool_rules(from_settings: dict, only: str | None, disallowed: str | None) -> ToolRules | None:
    """The session's tool rules: --tools beats the settings' tools.allow, and the
    denied tools of both are off"""

    allow = names(only) if only is not None else from_settings.get("allow")
    return ToolRules.of(allow, [*from_settings.get("deny", []), *names(disallowed or "")])


def tool_row(name: str, tools: list, rules: ToolRules) -> Text:
    """"  files   list_files read_file …", the tools that are off crossed out"""
    shown = Text(" ").join(Text(tool.name, style="" if rules.is_on(tool.name) else "dim strike") for tool in tools)
    return Text.assemble((f"  {name:<14}", "bold"), shown)


def engine_entry_line(entry: engine_store.Entry, current: engine_store.Engine) -> str:
    mark = "●" if entry.engine is not None and entry.engine == current else " "
    if entry.problems:
        return f"  {mark} [bold]{entry.name}[/] [dim]({entry.scope})[/] [red]can't be used: {entry.problems[0]}[/]"
    count = len(engine_store.changes(entry.engine.overlay or {}))
    what = entry.engine.description or ("what labb does as it comes" if entry.scope == "built-in" else "")
    return f"  {mark} [bold]{entry.name}[/] [dim]({entry.scope}, {plural(count, 'change')}){' · ' + what if what else ''}[/]"


def shown_value(value) -> str:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return text if len(text) <= 100 else text[:100] + f"… ({len(text):,} characters)"


def show_engine_changes(engine_name: str, overlay: dict, base: dict | None, base_name: str):
    found = engine_store.changes(overlay, base)
    if not found:
        console.print(f"[dim]{engine_name} is the same as {base_name}.[/]")
    for where, old, new in found:
        console.print(f"  [bold]{where}[/]")
        console.print(Text(f"    - {shown_value(old)}", style="red"))
        console.print(Text(f"    + {shown_value(new)}", style="green"))


def restart_mcp(session: Session):
    """Connect again to the settings' MCP servers and the engine's"""

    if session.mcp:
        session.mcp.close()
    session.mcp = start_mcp({**session.mcp_servers, **session.engine.mcp}, session.workdir, console)
    if not session.mcp:
        set_extra_tools([])
    model_with_tools.cache_clear()


def use_engine(session: Session, entry: engine_store.Entry):
    """Switch to an engine that can be used. The conversation goes on as it is; only what
    happens from the next message on changes."""

    changed_servers = session.engine.mcp != entry.engine.mcp
    session.engine = entry.engine
    if changed_servers:
        restart_mcp(session)
    if session.model_before_engine:
        session.model, session.model_before_engine = session.model_before_engine, None
    if spec := entry.engine.model("model"):
        try:
            model = llm.load(spec).spec
        except ValueError as e:
            console.print(f"[yellow]The engine's model can't be used: {e}[/]")
        else:
            session.model_before_engine, session.model = session.model, model
    console.print(f"[dim]Using the {entry.name} engine from the next message on.[/]")


def engine_command(session: Session, argument: str):
    """/engine: list the engines, use one, show one, or fork one to change"""

    words = argument.split()
    action = words[0] if words else ""
    wanted = words[1] if len(words) > 1 else None
    if not words:
        for entry in engine_store.entries(session.workdir):
            console.print(engine_entry_line(entry, session.engine))
        console.print(f"[dim]Files are in {engine_store.user_folder()} and .labb/engines. /engine fork <new> starts one.[/]")
        return
    if action == "fork" and wanted:
        source = engine_store.find(words[2], session.workdir) if len(words) > 2 else None
        if len(words) > 2 and (source is None or source.engine is None):
            console.print(f"[red]There's no engine {words[2]} to fork that can be used.[/]")
            return
        origin = source or engine_store.Entry(session.engine.name, "", None, session.engine, [])
        try:
            path = engine_store.fork(wanted, origin)
        except ValueError as e:
            console.print(f"[red]{e}[/]")
            return
        console.print(f"[dim]Forked {origin.name} to {path}. Edit it, then /engine use {wanted}.[/]")
        return
    if action not in ("use", "show", "diff") or (action == "use" and not wanted):
        console.print("[red]Use /engine, /engine use <name>, /engine show [name], /engine diff [name] or /engine fork <new> [from].[/]")
        return
    entry = engine_store.find(wanted, session.workdir) if wanted else engine_store.Entry(session.engine.name, "", None, session.engine, [])
    if entry is None:
        console.print(f"[red]There's no engine {wanted}.[/] [dim]/engine lists them.[/]")
        return
    if entry.problems:
        console.print(f"[red]The {entry.name} engine can't be used:[/]")
        for problem in entry.problems:
            console.print(Text(f"  {problem}"))
        return
    if action == "use":
        if agree_to_engine(entry, console, True, True):
            use_engine(session, entry)
        return
    if action == "diff":
        show_engine_changes(entry.name, entry.engine.overlay or {}, None, "the default")
        return
    console.print(f"[bold]{entry.name}[/] [dim]{entry.scope or 'in use'}{' · ' + entry.engine.description if entry.engine.description else ''}[/]")
    show_engine_changes(entry.name, entry.engine.overlay or {}, None, "the default")


def agree_to_engine(entry: engine_store.Entry, out: Console, interactive: bool, named: bool) -> bool:
    """Whether this engine may be used: it needs no asking, was agreed to with this content, or
    the user agrees now"""

    if not engine_store.needs_trust(entry, named) or engine_store.is_trusted(entry):
        return True
    if not interactive:
        out.print(Text(f"The {entry.name} engine needs your agreement first, which takes a terminal to ask in: "
                       f"run labb here and /engine use {entry.name}.", style="yellow"))
        return False
    overlay = entry.engine.overlay or {}
    if engine_store.runs_code(overlay):
        out.print(f"[yellow]The {entry.name} engine ({entry.scope}) runs code on this machine:[/]")
        for what in engine_store.terminal_only(overlay) or ["it runs Python"]:
            out.print(Text(f"  {what}"))
    else:
        out.print(f"[yellow]This project asks for its own engine, {entry.name}. It changes how the agent is instructed:[/]")
    for where, _, new in engine_store.changes(overlay):
        out.print(Text(f"  {where}: {shown_value(new)}"))
    if input("Use it? It's asked again if it changes. [y/N] ").strip().lower() not in ("y", "yes"):
        return False
    engine_store.trust(entry)
    return True


def agree_to_project(workdir: str, out: Console):
    """Ask about what this folder's .labb wants to run or allow without asking (settings.untrusted),
    once for its content; without agreeing, labb leaves it out"""

    asked = settings.untrusted(workdir)
    if not asked:
        return
    out.print(f"[yellow]This folder's .labb asks for things that run code or skip asking ({workdir}):[/]")
    found = asked.get("settings", {})
    for event, entries in found.get("hooks", {}).items():
        for entry in entries if isinstance(entries, list) else [entries]:
            command = entry.get("command") if isinstance(entry, dict) else entry
            out.print(Text(f"  hook {event}: {command}"))
    for name, server in found.get("mcpServers", {}).items():
        what = server.get("url") or " ".join([str(server.get("command", "")), *map(str, server.get("args", []))]) if isinstance(server, dict) else server
        out.print(Text(f"  MCP server {name}: {what}"))
    for rule in found.get("permissions", {}).get("allow", []):
        out.print(Text(f"  allowed without asking (settings): {rule}"))
    if found.get("defaultMode") == "acceptEdits":
        out.print(Text("  edits without asking (defaultMode acceptEdits)"))
    for rule in asked.get("permissions", []):
        out.print(Text(f"  allowed without asking (.labb/permissions.json): {rule}"))
    if input("Allow these here? It's asked again if they change. [y/N] ").strip().lower() in ("y", "yes"):
        settings.trust_all(workdir, asked)
    else:
        out.print("[dim]Left out; labb asks again next time.[/]")


def pick_engine(flag: str | None, config: dict, workdir: str, out: Console, interactive: bool) -> engine_store.Engine:
    """The engine to start with: --engine, else the settings'. A flag that can't be followed
    stops labb; a setting that can't only warns, and the default is used."""

    name = flag or config.get("engine")
    if not name:
        return engine_store.DEFAULT
    entry = engine_store.find(name, workdir)
    problem = None
    if entry is None:
        problem = f"there's no engine {name}"
    elif entry.problems:
        problem = f"the {name} engine can't be used: {'; '.join(entry.problems)}"
    if problem:
        if flag:
            raise SystemExit(f"labb: {problem}")
        out.print(Text(f"Settings: {problem}; using the default.", style="yellow"))
        return engine_store.DEFAULT
    if not agree_to_engine(entry, out, interactive, bool(flag)):
        if flag:
            raise SystemExit(f"labb: the {name} engine wasn't agreed to")
        return engine_store.DEFAULT
    return entry.engine


def browser_command(session: Session, argument: str):
    """/browser: whether the agent has the browser in this conversation"""

    if argument not in ("", "on", "auto", "off"):
        console.print("[red]Use /browser, /browser on, /browser auto or /browser off.[/]")
        return
    if argument:
        if argument != "off" and not browser_tool.enabled():
            console.print("[red]There's no browser yet.[/] Run [bold]labb --install-browser[/], or log in to a server that has one.")
            return
        session.browser = argument != "off"
        session.browser_auto = argument == "auto"
    state = "on" if session.browser else "off"
    where = "" if not session.browser else (" (here, in a window)" if browser_tool.is_local() else " (the server's)")
    if session.browser_auto:
        console.print(f"The browser is {state}{where}, and doesn't ask: it can send, pay and delete, and a page could trick it into doing so. Passwords, cards and codes stay with you.")
    else:
        console.print(f"The browser is {state}{where}. It asks before anything that sends, pays or deletes.")


def tools_command(session: Session, argument: str):
    """/tools: list the tools, which are on, and turn some off or on for this session"""

    action, _, rest = argument.partition(" ")
    rules = session.tool_rules or ToolRules()
    if action == "reset":
        session.tool_rules = None
        console.print("[dim]Every tool is on again for this session.[/]")
    elif action in ("off", "on") and names(rest):
        given = names(rest)
        if action == "off":
            rules = ToolRules(rules.allow, (*rules.deny, *given))
        else:
            deny = tuple(name for name in rules.deny if name not in given)
            allow = None if rules.allow is None else (*rules.allow, *given)
            rules = ToolRules(allow, deny)
        session.tool_rules = ToolRules.of(rules.allow, rules.deny)
        still = [name for name in given if (session.tool_rules or ToolRules()).is_on(name) != (action == "on")]
        if still:
            console.print(f"[yellow]Still {'off' if action == 'on' else 'on'}: {', '.join(still)} (a wildcard or toolset rule covers it; /tools reset clears them).[/]")
    elif argument:
        console.print("[red]Use /tools, /tools off <name> …, /tools on <name> … or /tools reset.[/]")
        return
    rules = rules_for(session.config()) or ToolRules()
    for toolset in available(True):
        name = toolset.__name__.rsplit(".", 1)[-1]
        console.print(tool_row(name, toolset.TOOLS, rules))
    if extra := [tool for tool in tools_for(True) if tool.name.startswith("mcp__")]:
        console.print(tool_row("mcp", extra, rules))
    if session.tool_rules:
        console.print(f"[dim]Off for this session (crossed out): {', '.join(session.tool_rules.deny) or '-'}"
                      + (f"; only on: {', '.join(session.tool_rules.allow)}" if session.tool_rules.allow is not None else "") + "[/]")
    if session.engine.allow is not None or session.engine.deny:
        console.print(f"[dim]The {session.engine.name} engine keeps some off too; /tools on can't change those.[/]")
    console.print('[dim]Names can be tools, toolsets or wildcards. To keep a choice, put it in "tools" in the settings.[/]')


def show_jobs(argument: str):
    """/jobs: the background commands (run_command with background=True); /jobs stop <id> stops one"""

    words = argument.split()
    if words[:1] == ["stop"] and len(words) == 2:
        console.print(f"[dim]{shell.stop_command.invoke({'job_id': words[1]}).splitlines()[0]}[/]")
        return
    if words:
        console.print("[red]Use /jobs, or /jobs stop <id>.[/]")
        return
    if not shell.jobs:
        console.print("[dim]No commands in the background.[/]")
        return
    for job in list(shell.jobs.values()):
        console.print(f"  [bold]{job.id}[/] {job.command} [dim]· {job.status()} · started {duration(time.time() - job.started)} ago[/]")
    console.print("[dim]Stop one with /jobs stop <id>; they're all stopped when labb exits.[/]")


def work_config(session: Session) -> dict:
    """Where this conversation's todos, goal and plan are (work.py)"""
    return {"configurable": {"workdir": session.workdir, "thread_id": session.thread_id}}


def save_plan(session: Session) -> str | None:
    """After a plan is approved: the plan (the agent's last reply) with the request it answers, in
    .labb/plans/<thread>.md, so it outlasts summaries and sessions. Its path in the project."""

    messages = session.messages()
    plan = next((m.text for m in reversed(messages) if m.type == "ai" and m.text.strip()), "")
    if not plan or not session.thread_id:
        return None
    request = next((split_user_message(m)[0] for m in reversed(messages) if m.type == "human"), "")
    try:
        work.write(work_config(session), "plan", f"# Approved plan ({datetime.now():%Y-%m-%d})\n\nRequest: {request.strip()}\n\n{plan.strip()}\n")
    except OSError as e:
        console.print(f"[yellow]Couldn't save the plan: {e}[/]")
        return None
    return str(work.file_of(session.workdir, session.thread_id, "plan").relative_to(session.workdir))


def show_plan(session: Session):
    """/plan show: the plan approved in this conversation"""
    text = work.read(work_config(session), "plan") if session.thread_id else None
    if not text:
        console.print("[dim]No approved plan in this conversation.[/]")
        return
    console.print(Markdown(text))


GOAL_HELP = ("[dim]/goal <objective> keeps the agent working until it's done (at most "
             f"{goals.DEFAULT_BUDGET} model calls; /goal --calls N <objective> for another budget). "
             "/goal pause, /goal resume, /goal clear.[/]")


def goal_command(session: Session, argument: str) -> str | None:
    """/goal: set, show, pause, resume or clear the conversation's goal; the message to send now, if any"""

    if session.thread_id is None and argument and argument not in ("pause", "resume", "clear"):
        session.thread_id = f"cli-{uuid4()}"
    current = goals.get(work_config(session)) if session.thread_id else None
    if not argument:
        if current is None:
            console.print("[dim]No goal in this conversation.[/] " + GOAL_HELP)
        else:
            console.print(goals.describe(current))
        return None
    if argument in ("pause", "resume", "clear"):
        if current is None:
            console.print("[dim]No goal in this conversation.[/]")
            return None
        if argument == "clear":
            goals.save(work_config(session), None)
            console.print("[dim]Goal cleared.[/]")
            return None
        if argument == "pause":
            goals.set_status(work_config(session), "paused")
            console.print("[dim]Goal paused: /goal resume to go on.[/]")
            return None
        if current["status"] == "complete":
            console.print("[dim]The goal is complete. Set a new one with /goal <objective>.[/]")
            return None
        if current["status"] == "budget_limited":
            # Another budget like the first
            current["budget"] = current.get("used", 0) + (current.get("budget") or goals.DEFAULT_BUDGET)
            goals.save(work_config(session), current)
        goals.set_status(work_config(session), "active")
        return "Keep working toward the goal."
    budget = goals.DEFAULT_BUDGET
    if match := re.match(r"--calls\s+(\d+)\s+(.*)", argument, re.DOTALL):
        budget, argument = int(match.group(1)), match.group(2)
    try:
        goals.start(work_config(session), argument, budget=max(budget, 1))
    except ValueError as e:
        console.print(f"[red]{e}[/]")
        return None
    console.print(f"[dim]Goal set ({budget} model calls at most). The agent keeps working until it says it's done; Esc stops it.[/]")
    return argument


def goal_status(session: Session) -> str | None:
    """For the status line: the goal and what it used"""
    goal = goals.get(work_config(session)) if session.thread_id else None
    if goal is None or goal["status"] == "complete":
        return None
    return f"◎ goal {goal['status'].replace('_', ' ')}, {goals.usage(goal)}"


def show_todos(session: Session):
    """/todos: the agent's todo list for this conversation"""

    items = todo.load(session.workdir, session.thread_id) if session.thread_id else []
    if not items:
        console.print("[dim]No todo list in this conversation.[/]")
        return
    for line in render.todo_lines(todo.show(items).splitlines()[1:]):
        console.print(line)


def chat_loop(workdir: str, thread_id: str | None, allow_all: bool = False, config: dict | None = None,
              mcp: McpServers | None = None, syncer: sync.Syncer | None = None):
    """The interactive chat. A new conversation gets its thread id with its first message."""

    config = config or {}
    session = Session(
        workdir=workdir, thread_id=thread_id, allow_all=allow_all, git_context=git_context(workdir),
        model=config.get("model", llm.DEFAULT_MODEL), mode=config.get("defaultMode", "default"),
        answer_mode=config.get("answerMode", "auto"),
        show_thinking=config.get("showThinking", False),
        hooks=config.get("hooks", {}), custom_commands=load_custom_commands(workdir), mcp=mcp,
        tool_rules=config.get("tool_rules"), browser=config.get("browser_in_use", False), browser_auto=config.get("browser_auto_in_use", False),
        browser_sites=config.get("browser_sites", []), engine=config.get("engine_in_use", engine_store.DEFAULT),
        model_before_engine=config.get("model_before_engine"), mcp_servers=config.get("mcpServers", {}),
        models=llm.listed_models() + [spec for spec in config.get("models", []) if isinstance(spec, str)],
        syncer=syncer, prices=config.get("prices", {}), max_cost=config.get("maxCostPerSession"),
    )

    def toggle_mode():
        session.mode = MODES[(MODES.index(session.mode) + 1) % len(MODES)] if session.mode in MODES else "default"

    def toggle_thinking():
        session.show_thinking = not session.show_thinking
        console.print(f"[dim]The model's reasoning is {'shown' if session.show_thinking else 'hidden'} from now on.[/]")

    def edit_last() -> str | None:
        messages = session.messages()
        if not last_turn(messages):
            return None
        session.editing = True
        return session.last_typed or last_turn(messages)[0].text

    prompt = make_prompt(
        history_file=HISTORY_FILE,
        commands={**COMMANDS, **{name: c["description"] for name, c in session.custom_commands.items()},
                  **{f"/{s['name']}": s["description"][:60] for s in skills.available(session.config())
                     if f"/{s['name']}" not in COMMANDS and f"/{s['name']}" not in session.custom_commands}},
        workdir=workdir,
        toolbar=lambda: status_line(session),
        on_edit_last=edit_last,
        on_expand=lambda: show_last_reply(session.messages()),
        on_toggle_mode=toggle_mode,
        on_toggle_thinking=toggle_thinking,
    ) if sys.stdin.isatty() else None

    console.print(f"[bold]labb[/] [dim]· {llm.load(session.model).spec} · {workdir}[/]")
    if warning := llm.no_tools_warning(session.model):
        console.print(f"[yellow]{warning}[/]")
    console.print("[dim]Type /help for commands, @ to attach a file, ! to run a command. Esc stops a reply, Ctrl-D exits.[/]")
    if allow_all:
        console.print("[bold red]--allow-all: commands run without asking.[/]")
    if thread_id:
        show_last_exchange(thread_id)
        update_context(session)
        if (goal := goals.get(work_config(session))) and goal["status"] in ("active", "paused", "budget_limited"):
            console.print(f"[dim]This conversation has a goal ({goal['status'].replace('_', ' ')}, {goals.usage(goal)}): "
                          f"{goal['objective'][:120]}. /goal resume to keep working on it.[/]")

    interrupted = False
    # A message to send without asking, e.g. after a plan is approved
    queued = None
    while True:
        session.branch = run_git(workdir, "branch", "--show-current") or None
        session.unsynced = syncer.unsent() if syncer else 0
        # This message is a run of the loop
        looping = False
        try:
            console.print()
            if queued:
                text, queued = queued, None
                console.print(Text(f"> {text}", style="bold"))
            elif session.loop and session.loop.next_at <= time.time():
                text, looping = session.loop.prompt, True
            else:
                prefill, session.prefill = session.prefill, ""
                answer = prompt_until(prompt, session.loop.next_at if session.loop else None, default=prefill) if prompt else input("> ")
                if answer is LOOP_DUE:
                    text, looping = session.loop.prompt, True
                else:
                    text = answer.strip()
            if looping:
                session.loop.runs += 1
                # Until the run says otherwise, so one that never reaches the model doesn't spin
                session.loop.next_at = time.time() + (session.loop.every or 60)
                console.print(Text(f"↻ Loop run {session.loop.runs}: {text}", style="bold"))
            interrupted = False
        except EOFError:
            break
        except KeyboardInterrupt:
            session.editing = False
            if session.loop:
                end_loop(session, "you stopped it")
                continue
            if interrupted:
                break
            interrupted = True
            console.print("[dim](Press Ctrl-C again or Ctrl-D to exit)[/]")
            continue

        # A loop's run never replaces the message being edited
        editing, session.editing = session.editing and not looping, session.editing and looping
        if not text:
            continue
        command, _, argument = text.partition(" ")
        argument = argument.strip()
        if text in ("/exit", "exit", "quit"):
            break
        if text == "/help":
            console.print(HELP)
            if session.custom_commands:
                console.print("\n[bold]Your commands[/]")
                for name, custom in session.custom_commands.items():
                    console.print(f"  {name:<14}{custom['description']}")
            continue
        if text == "/mcp":
            show_mcp(session)
            continue
        if text in ("/new", "/clear"):
            session.thread_id = None
            session.context = None
            session.last_typed = None
            session.shell_outputs.clear()
            console.print("[dim]New conversation.[/]")
            continue
        if text == "/resume":
            if chosen := pick_session(workdir, None):
                session.thread_id = chosen
                session.last_typed = None
                show_last_exchange(chosen)
                update_context(session)
            continue
        if text == "/compact":
            compact_now(session)
            continue
        if command == "/context":
            show_context(session, full=text.split()[1:] == ["full"])
            continue
        if text == "/plan":
            session.mode = "default" if session.mode == "plan" else "plan"
            console.print(f"[dim]Plan mode {'on: the agent plans and changes nothing' if session.mode == 'plan' else 'off'}.[/]")
            continue
        if text == "/plan show":
            show_plan(session)
            continue
        if command == "/goal":
            if start := goal_command(session, argument):
                queued = start
            continue
        if text == "/undo":
            turns = len(user_turns(session.messages()))
            if turns:
                rewind_to(session, turns - 1, "your last message and its reply")
            else:
                console.print("[dim]Nothing to undo yet.[/]")
            continue
        if text == "/rewind":
            pick_rewind(session)
            continue
        if command == "/model":
            switch_model(session, argument)
            continue
        if command == "/answer":
            switch_answer_mode(session, argument)
            continue
        if command == "/num-ctx":
            switch_num_ctx(session, argument)
            continue
        if command == "/engine":
            engine_command(session, argument)
            continue
        if command == "/loop":
            loop_command(session, argument, prompt is not None)
            continue
        if text == "/thinking":
            toggle_thinking()
            continue
        if text in ("/cost", "/stats"):
            show_cost(session)
            continue
        if text == "/copy":
            copy_last_reply(session)
            continue
        if command == "/export":
            export_conversation(session, argument)
            continue
        if command == "/permissions":
            show_permissions(workdir, argument)
            continue
        if command == "/tools":
            tools_command(session, argument)
            continue
        if command == "/browser":
            browser_command(session, argument)
            continue
        if text == "/todos":
            show_todos(session)
            continue
        if command == "/jobs":
            show_jobs(argument)
            continue
        if command == "/memory":
            show_memories(session, argument)
            continue
        if command == "/skills":
            show_skills(session)
            continue
        if command in ("/workflows", "/run"):
            workflows.command(text)
            continue
        if text.startswith("!"):
            if shell_command := text[1:].strip():
                run_shell(session, shell_command)
            continue

        title = text
        skill_files = {}
        if text == "/init":
            title, text = "/init", INIT_PROMPT
        elif command in session.custom_commands:
            text = expand_command(session.custom_commands[command]["prompt"], argument)
        elif text.startswith("/") and (skill_files := skill_attachment(session, command[1:])) is not None:
            if not skill_files:
                continue
        elif text.startswith("/"):
            console.print(f"[red]Unknown command {command}.[/] Type /help for the commands.")
            continue

        if editing and session.thread_id and take_back_last_turn(session):
            console.print("[dim]↺ Replaced your last message and its reply.[/]")
        session.last_typed = title if title.startswith("/") else text
        message = attach_mentions(text, workdir)
        if skill_files:
            # After the mentions, so the skill's own @s aren't taken for files
            message = with_attachments(message, skill_files)
        if session.shell_outputs:
            message = "\n\n".join(session.shell_outputs) + "\n\n" + message
            session.shell_outputs.clear()

        budget = None
        if session.max_cost is not None:
            spent = costs.of_stats(list(chats.get_stats(session.thread_id).values()) if session.thread_id else [], session.prices)[0]
            if spent >= session.max_cost:
                console.print(f"[red]This session has cost ${spent:.2f}, its limit (maxCostPerSession is ${session.max_cost:g}).[/] "
                              "Start a new one with /new, or raise the limit in the settings.")
                continue
            budget = (session.max_cost, spent, session.prices)
        if session.thread_id is None:
            session.thread_id = f"cli-{uuid4()}"
        chats.touch_cli(session.thread_id, workdir, title)
        save_snapshot(session)
        console.print()
        try:
            final = ask(session.thread_id, message, workdir, interactive=True, allow_all=allow_all,
                        model=session.model, mode=session.mode, git_context=session.git_context,
                        hooks=session.hooks, show_thinking=session.show_thinking, tool_rules=session.tool_rules,
                        answer_mode=session.answer_mode, loop=session.loop if looping else None, engine=session.engine,
                        budget=budget, browser=session.browser, browser_auto=session.browser_auto, browser_sites=session.browser_sites)
        except KeyboardInterrupt:
            console.print("\n[red]Interrupted.[/]")
            break
        session.changed()
        if final:
            session.show_thinking = final.get("show_thinking", session.show_thinking)
        if looping and session.loop:
            after_loop_run(session, final)
        update_context(session)
        if session.mode == "plan" and final and final["type"] == "done" and (approved := approve_plan()):
            session.mode = approved
            if saved := save_plan(session):
                queued = f"The plan is approved and saved in `{saved}`. Write its steps with `update_todos`, then go ahead with it."
            else:
                queued = "The plan is approved. Go ahead with it."
        elif keeps_going(session, final, looping):
            queued = "Keep working toward the goal."


def keeps_going(session: Session, final: dict | None, looping: bool) -> bool:
    """After a reply: whether the goal sends the agent on by itself. Only after a reply that
    finished (not stopped or failed), outside plan mode and loops, while the goal is active and
    the agent didn't end with a question for you."""

    if not final or final["type"] != "done" or looping or session.mode == "plan" or not session.thread_id:
        return False
    if goals.active(work_config(session)) is None:
        return False
    last = next((m.text for m in reversed(session.messages()) if m.type == "ai" and m.text.strip()), "")
    return not last.strip().endswith("?")


def version() -> str:
    return remote.version()


def export_conversation(session: Session, argument: str):
    """/export [file]: the conversation as Markdown, by default in a file named after its title"""

    messages = session.messages()
    if not messages:
        console.print("[dim]Nothing to export yet.[/]")
        return
    title = next((s["title"] for s in chats.list_all_cli() if s["thread_id"] == session.thread_id), "Conversation")
    path = Path(session.workdir) / (argument or file_name(title))
    if path.exists() and not argument:
        path = path.with_stem(f"{path.stem}-{datetime.now():%Y%m%d-%H%M%S}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(to_markdown(messages, title, chats.get_stats(session.thread_id)))
    except OSError as e:
        console.print(f"[red]Couldn't write {path}: {e}[/]")
        return
    console.print(f"[dim]Exported {plural(len(user_turns(messages)), 'message')} to {path}.[/]")


def main():
    # labb run <workflow> and labb workflows: Studio's workflows, on the server
    if len(sys.argv) > 1 and sys.argv[1] in ("run", "workflows"):
        raise SystemExit(workflows.main(sys.argv[1:]))
    # labb eval tools: how well models call the tools (see cli/evals.py); labb eval long: longer jobs (cli/long_evals.py)
    if len(sys.argv) > 1 and sys.argv[1] == "eval":
        from labb.cli import evals
        raise SystemExit(evals.main(sys.argv[2:]))
    # labb connect: lets the server's web page start labb here (see cli/connect.py)
    if len(sys.argv) > 1 and sys.argv[1] == "connect":
        from labb.cli import connect
        raise SystemExit(connect.main(sys.argv[2:]))
    parser = argparse.ArgumentParser(
        prog="labb",
        description="Chat with the agent in your terminal, in the current directory.",
    )
    parser.add_argument("question", nargs="?", help="ask one question, print the answer and exit (same as -p)")
    parser.add_argument("-p", "--print", dest="prompt", metavar="QUESTION", help="ask one question, print the answer and exit; nothing is saved")
    parser.add_argument("-c", "--continue", dest="resume_latest", action="store_true", help="continue the latest conversation in this directory")
    parser.add_argument("-r", "--resume", nargs="?", const="", metavar="ID", help="pick a conversation in this directory to continue (or give its id)")
    parser.add_argument("-m", "--model", metavar="MODEL", help='the model to use, e.g. "ollama:qwen3:8b" or "anthropic:claude-sonnet-5-5" (default: the settings, or MODEL in .env)')
    parser.add_argument("--answer", choices=list(llm.ANSWER_MODES), help="how much the model thinks first: fast, auto (the default) or thinking")
    parser.add_argument("--num-ctx", metavar="TOKENS", type=tokens_argument,
                        help="the tokens Ollama keeps in memory for the model, e.g. 65536 or 64k (default: the settings, or OLLAMA_NUM_CTX)")
    parser.add_argument("--engine", metavar="NAME", help="the engine to use: a saved version of the agent's core (see /engine)")
    parser.add_argument("--browser", action="store_true", help="let the agent use a browser: open pages, click, fill in and submit forms")
    parser.add_argument("--browser-auto", action="store_true", help="--browser, without asking first: it can send, pay and delete. Other tools still ask")
    parser.add_argument("--install-browser", action="store_true", help="install the browser (Playwright and Chromium) on this PC")
    parser.add_argument("--allow-all", action="store_true", help="run every command and edit without asking (careful: nothing is checked)")
    parser.add_argument("--tools", metavar="NAMES",
                        help='only these tools, by name, toolset or wildcard, comma-separated, e.g. "files,shell,update_todos" ("" for none)')
    parser.add_argument("--disallowed-tools", metavar="NAMES", help='turn these tools off, e.g. "write_file,edit_file,mcp__*"')
    parser.add_argument("--output-format", choices=["text", "json", "stream-json"], default="text",
                        help="with -p: the answer as Markdown (text), one JSON object (json) or the events as JSON lines (stream-json)")
    parser.add_argument("--version", action="version", version=f"labb {version()}")
    parser.add_argument("--update", action="store_true", help="install the version the server (LABB_SERVER) has, if it's different")
    parser.add_argument("--login", action="store_true", help="log in to the server: approve this PC in the browser, and save its API key")
    parser.add_argument("--logout", action="store_true", help="revoke this PC's API key and forget it")
    parser.add_argument("--graph", action="store_true", help="print the agent's graph as a Mermaid diagram and exit")
    # How labb connect starts a run (see cli/channel.py)
    parser.add_argument("--channel", action="store_true", help=argparse.SUPPRESS)
    parser.epilog = ("Also: labb connect [folder …] (start labb here from the server's web page), "
                     "labb workflows (your Studio workflows), labb run <workflow> [-i name=value …], "
                     "labb eval tools [-m model …] (how well models call the tools) and labb eval long [-m model …] (longer jobs, run for real).")
    args = parser.parse_args()

    if args.graph:
        print(agent.get_graph(xray=True).draw_mermaid())
        return
    if args.update:
        raise SystemExit(update())
    if args.install_browser:
        raise SystemExit(install_browser())
    if args.login:
        raise SystemExit(login())
    if args.logout:
        raise SystemExit(logout())

    workdir = os.path.realpath(os.getcwd())
    channel = Channel(sys.stdin) if args.channel else None
    if channel:
        args.output_format = "stream-json"
    question = args.prompt or args.question
    if args.prompt and not sys.stdin.isatty() and stdin_has_data():
        # `git diff | labb -p "review this"`: what's piped in goes with the question
        question = with_stdin(question, sys.stdin.read(MAX_STDIN_CHARS + 1))
    # In one-shot mode, notes go to stderr so they stay out of piped output (and JSON)
    notes = stderr if (question or channel) and (not console.is_terminal or args.output_format != "text") else console
    if not question and not channel and console.is_terminal and sys.stdin.isatty():
        agree_to_project(workdir, console)
    config = apply_settings(workdir, args.model, notes)
    if args.answer:
        config["answerMode"] = args.answer
    if args.num_ctx is not None and (problem := set_num_ctx(args.num_ctx)):
        parser.error(f"--num-ctx: {problem}")
    config["tool_rules"] = tool_rules(config.get("tools", {}), args.tools, args.disallowed_tools)
    config["engine_in_use"] = pick_engine(args.engine, config, workdir, notes, not question and console.is_terminal)
    if not args.model and (spec := config["engine_in_use"].model("model")):
        # -m beats the engine, which beats the settings
        try:
            config["model_before_engine"], config["model"] = config["model"], llm.load(spec).spec
        except ValueError as e:
            notes.print(f"[yellow]The engine's model can't be used: {e}[/]")
    use_browser = (args.browser or args.browser_auto or bool(config.get("browser"))) and browser_tool.enabled()
    if (args.browser or args.browser_auto) and not use_browser:
        notes.print("[yellow]There's no browser yet: run labb --install-browser, or log in to a server that has one.[/]")
    mcp = start_mcp({**config.get("mcpServers", {}), **config["engine_in_use"].mcp}, workdir, notes)
    try:
        if channel:
            serve_channel(channel, workdir, config, args.allow_all, use_browser)
            return
        if question:
            # One-shot: run it like a chat message, then forget it
            thread_id = f"cli-{uuid4()}"
            try:
                message = attach_mentions(question, workdir, out=notes)
                final = ask(thread_id, message, workdir, allow_all=args.allow_all, model=config["model"], browser=use_browser,
                            browser_auto=use_browser and args.browser_auto, browser_sites=config.get("browser_sites"),
                            git_context=git_context(workdir), hooks=config.get("hooks", {}),
                            output_format=args.output_format, name=False, tool_rules=config.get("tool_rules"),
                            answer_mode=config.get("answerMode", "auto"), engine=config["engine_in_use"])
            except KeyboardInterrupt:
                final = None
            finally:
                chats.delete_chat(thread_id)
            raise SystemExit(0 if final and final["type"] == "done" and not final["failed"] else 1)
        config["browser_in_use"] = use_browser
        config["browser_auto_in_use"] = use_browser and args.browser_auto
        run_chat(args, workdir, config, mcp)
    finally:
        if mcp:
            mcp.close()


def channel_problem(thread_id: str, workdir: str, message: ChannelMessage, config: dict) -> str | None:
    """Why this message can't be answered here"""

    session = next((s for s in chats.list_all_cli() if s["thread_id"] == thread_id), None)
    if session and session["directory"] != workdir:
        return f"That session belongs to {session['directory']}."
    if (limit := config.get("maxCostPerSession")) is not None:
        spent = costs.of_stats(list(chats.get_stats(thread_id).values()), config.get("prices", {}))[0]
        if spent >= limit:
            return f"This session has cost ${spent:.2f}, its limit (maxCostPerSession is ${limit:g}). Start a new one, or raise the limit on the PC."
    return None


def channel_content(thread_id: str, message: ChannelMessage) -> str | list:
    """What to send: an edited message keeps the attachments it had, which only this PC has"""

    if not message.keep_attachments or message.replace_from is None:
        return message.content
    try:
        attached, images = attachments_of(user_message(thread_id, message.replace_from))
    except IndexError:
        # run_reply says what's wrong with replacing it
        return message.content
    if isinstance(message.content, str):
        text, added = message.content + attached, []
    else:
        text, added = message.content[0]["text"] + attached, message.content[1:]
    return [{"type": "text", "text": text}, *images, *added] if images or added else text


def serve_channel(channel: Channel, workdir: str, config: dict, allow_all: bool, use_browser: bool):
    """A session labb connect started: answer its messages until the channel closes. @ mentions aren't
    read, since they'd skip asking about a path."""

    def options(model: str | None = None, answer_mode: str | None = None) -> dict:
        """What the session's replies run with; a message can pick the model and answer mode"""
        return dict(allow_all=allow_all, model=model or config["model"], browser=use_browser, browser_sites=config.get("browser_sites"),
                    git_context=git_context(workdir), hooks=config.get("hooks", {}), tool_rules=config.get("tool_rules"),
                    answer_mode=answer_mode or config.get("answerMode", "auto"), engine=config["engine_in_use"])

    channel.on_context = lambda question: answer_context(channel.thread_id, workdir, question,
                                                         options(question.get("model"), question.get("answer_mode")))
    while (message := channel.next_message()) is not None:
        thread_id = channel.thread_id
        if problem := channel_problem(thread_id, workdir, message, config):
            channel_line({"type": "error", "message": problem})
            channel_line({"type": "stopped", "stats": ReplyStats(message.model or config["model"] or llm.default.name).to_dict(True)})
            continue
        chats.touch_cli(thread_id, workdir, message.title)
        budget = None
        if (limit := config.get("maxCostPerSession")) is not None:
            prices = config.get("prices", {})
            budget = (limit, costs.of_stats(list(chats.get_stats(thread_id).values()), prices)[0], prices)
        ask(thread_id, channel_content(thread_id, message), workdir, name=False, budget=budget, channel=channel, message=message,
            replace_from=message.replace_from, **options(message.model, message.answer_mode))


def answer_context(thread_id: str, workdir: str, question: dict, options: dict) -> dict:
    """The web page's context panel, about a session labb connect runs here: answered from what its
    replies run with, which the server can't rebuild. {"result"}, {"missing"} or {"error"}."""

    if question.get("thread_id") != thread_id:
        return {"error": "That's another session."}
    configurable = run_configurable(workdir, **options)
    if question.get("web") is False:
        rules = configurable.get("tools") or ToolRules()
        configurable["tools"] = ToolRules(rules.allow, (*rules.deny, "web"), rules.within)
    config = {"configurable": {**configurable, "thread_id": thread_id}}
    match question.get("what"):
        case "seen":
            scale = question.get("scale")
            return {"result": inspection.seen(config, float(scale) if isinstance(scale, (int, float)) else None)}
        case "raw":
            return {"result": inspection.raw(config)}
        case "message":
            number = question.get("number")
            found = inspection.message(thread_id, number) if isinstance(number, int) else None
            return {"result": found} if found is not None else {"missing": "No such message"}
        case "traces":
            return {"result": inspection.traces(thread_id)}
        case "work":
            return {"result": inspection.work(config)}
        case "summarize":
            try:
                return {"result": inspection.summarize(config)}
            except inspection.Busy as e:
                return {"error": str(e), "conflict": True}
    return {"error": f"Can't answer {question.get('what')!r} about the context."}


# What -p takes from stdin; more is cut, so a huge pipe can't fill the context
MAX_STDIN_CHARS = 200_000


def stdin_has_data(wait: float = 1.0) -> bool:
    """Whether something was piped in: a pipe left open with nothing in it (some CI runners)
    mustn't make -p wait forever"""
    if os.name == "nt":
        return True
    import select
    try:
        return bool(select.select([sys.stdin], [], [], wait)[0])
    except (OSError, ValueError):
        return False


def with_stdin(question: str, piped: str) -> str:
    """The question with what was piped in after it, if anything was"""
    if not piped.strip():
        return question
    if len(piped) > MAX_STDIN_CHARS:
        piped = piped[:MAX_STDIN_CHARS] + "\n… (cut)"
    return f"{question}\n\n<stdin>\n{piped.rstrip()}\n</stdin>"


def run_chat(args, workdir: str, config: dict, mcp: McpServers | None):
    """The interactive chat, resuming a conversation if asked"""

    # An installed copy says when the server has another version (a quick check: a second at most)
    if DATA_DIR != SOURCE_DIR and (info := server_version(timeout=1)) and info["version"] != version():
        console.print(f"[yellow]labb {info['version']} is on the server (this is {version()}): run labb --update.[/]")
    # Through a server, nothing works without a valid key (a quick check: a second at most)
    if (status := server_status(timeout=1)) and status["user"] is None:
        if remote.api_key():
            console.print("[yellow]This PC's API key doesn't work any more (revoked, or the account is disabled): run labb --login.[/]")
        else:
            console.print("[yellow]Not logged in to the server yet: run labb --login.[/]")

    thread_id = None
    if args.resume_latest:
        sessions = chats.list_cli(workdir)
        if sessions:
            thread_id = sessions[0]["thread_id"]
        else:
            console.print("[dim]No conversations in this directory yet; starting a new one.[/]")
    elif args.resume is not None:
        thread_id = pick_session(workdir, args.resume or None)

    syncer = sync.start()
    try:
        chat_loop(workdir, thread_id, allow_all=args.allow_all, config=config, mcp=mcp, syncer=syncer)
    finally:
        if syncer:
            syncer.finish()


if __name__ == "__main__":
    main()
