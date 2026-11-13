"""Runs the agent for one message, for both the web server and the terminal:
streams its events, stops it on request and saves the reply consistently."""

import re
import threading
import time
from collections import Counter
from uuid import uuid4

from langchain.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langchain_core.callbacks import BaseCallbackHandler
from langgraph.errors import GraphRecursionError

from labb.agent import chat_agent as agent, checkpointer
from labb.tool_node import CANCELLED
from labb import costs
from labb import engine_steps
from labb import hooks
from labb import chats
from labb import engine
from labb import llm
from labb import trace
from labb.engine import TITLE_PROMPT  # noqa: F401
from labb import usage

# A lock per chat, so a new message waits until the previous reply has stopped and been saved
thread_locks: dict[str, threading.Lock] = {}
thread_locks_lock = threading.Lock()


def lock_for(thread_id: str) -> threading.Lock:
    with thread_locks_lock:
        return thread_locks.setdefault(thread_id, threading.Lock())


class ReplyStats:
    """Tokens and timing of one reply, which can take several model calls"""

    def __init__(self, model_name: str, engine_label: str | None = None):
        self.model_name = model_name
        self.engine_label = engine_label
        self.start = time.monotonic()
        self.first_token = None
        self.input_tokens = 0
        self.output_tokens = 0
        # Of the input tokens: read from the provider's prompt cache, and written to it (they cost differently)
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0
        self.llm_calls = 0
        self.tool_calls = 0
        # Tool calls that needed fixing (see tool_calls.py): repaired, rewritten with constrained decoding, still broken
        self.tool_repaired = 0
        self.tool_constrained = 0
        self.tool_failed = 0
        # Calls per tool, for the usage stats
        self.tools: Counter[str] = Counter()
        # Time spent generating output, if the provider reports it (Ollama does), for tokens/s
        self.generation_ns = 0
        # Streamed chunks (about one token each, thinking included) of the model call in
        # progress; a stopped call reports no usage, so they stand in for its output tokens
        self.pending_chunks = 0
        # Why the reply ended (continuation.end_reason, or stopped, error, limit), to see why
        # one stopped early
        self.end_reason = None
        # How often it went round again because a reply ended too early
        self.continued = 0

    def started(self):
        if self.first_token is None:
            self.first_token = time.monotonic()

    def add(self, message: AIMessage):
        usage = message.usage_metadata or {}
        self.input_tokens += usage.get("input_tokens", 0)
        self.output_tokens += usage.get("output_tokens", 0)
        self.add_cached(usage)
        self.llm_calls += 1
        self.tool_calls += len(message.tool_calls)
        self.tools.update(call["name"] for call in message.tool_calls)
        fixes = message.response_metadata.get("tool_fixes") or {}
        self.tool_repaired += fixes.get("repaired", 0)
        self.tool_constrained += fixes.get("constrained", 0)
        self.tool_failed += fixes.get("failed", 0)
        self.generation_ns += message.response_metadata.get("eval_duration") or 0
        self.pending_chunks = 0

    def add_cached(self, usage: dict):
        read, write = costs.cached(usage)
        self.cache_read_tokens += read
        self.cache_write_tokens += write

    def add_side_call(self, message: AIMessage):
        """A model call that isn't the reply: an engine's step or tool. It costs the same."""
        usage = message.usage_metadata or {}
        self.input_tokens += usage.get("input_tokens", 0)
        self.output_tokens += usage.get("output_tokens", 0)
        self.add_cached(usage)
        self.llm_calls += 1

    def final_reason(self, stopped: bool, error: bool) -> str:
        if stopped:
            return "stopped"
        if error:
            return "limit" if self.end_reason == "limit" else "error"
        return self.end_reason or "done"

    def to_dict(self, stopped: bool, error: bool = False) -> dict:
        ms = lambda t: round((t - self.start) * 1000)
        return {
            "model": self.model_name,
            "duration_ms": ms(time.monotonic()),
            "first_token_ms": ms(self.first_token) if self.first_token else None,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens + self.pending_chunks,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "generation_ms": round(self.generation_ns / 1e6) if self.generation_ns else None,
            "llm_calls": self.llm_calls,
            "tool_calls": self.tool_calls,
            "tool_repaired": self.tool_repaired,
            "tool_constrained": self.tool_constrained,
            "tool_failed": self.tool_failed,
            "tools": dict(self.tools),
            "stopped": stopped,
            "error": error,
            "end_reason": self.final_reason(stopped, error),
            "continued": self.continued,
            # When the reply ended, for the usage stats
            "at": usage.stamp(),
            # Which engine wrote it, if not the default
            **({"engine": self.engine_label} if self.engine_label else {}),
        }


def save_stopped_reply(config: dict, partial: str) -> str | None:
    """Save a stopped reply consistently: what it wrote so far, the results of steps that
    finished, and a "Cancelled" result for tool calls that never ran.

    Returns the id of the saved partial reply, if there is one.
    """
    # A stopped run leaves the results of its last step uncommitted. get_state() shows
    # them, but the next run starts from the committed checkpoint, so commit them here.
    saved = checkpointer.get_tuple(config)
    committed = len(saved.checkpoint["channel_values"].get("messages", [])) if saved else 0
    # The run's own graph knows what its steps left uncommitted
    graph = engine_steps.graph_for(config)
    messages = graph.get_state(config).values.get("messages", [])
    new = list(messages[committed:])

    partial_id = None
    if partial:
        partial_id = str(uuid4())
        new.append(AIMessage(content=partial, id=partial_id))
    else:
        # Every tool call needs a result, or the next model call fails
        answered = {m.tool_call_id for m in messages if m.type == "tool"}
        last_ai = next((m for m in reversed(messages) if m.type == "ai"), None)
        for tool_call in last_ai.tool_calls if last_ai else []:
            if tool_call["id"] not in answered:
                new.append(ToolMessage(
                    content=CANCELLED,
                    name=tool_call["name"],
                    tool_call_id=tool_call["id"],
                    status="error",
                ))

    if new:
        graph.update_state(config, {"messages": new, "continuing": None}, as_node="tool_node" if new[-1].type == "tool" else "llm_call")
    elif graph.get_state(config).values.get("continuing"):
        # Stopped while going round again (continuation.py): the next message starts afresh
        graph.update_state(config, {"continuing": None}, as_node="llm_call")
    return partial_id


class StopRequested(Exception):
    pass


class StopOnRequest(BaseCallbackHandler):
    """Aborts the model call in progress when the reply is stopped.

    Closing the graph stream would wait for the call to finish, and the model would
    keep generating. Raising on the next streamed token (thinking included) ends the
    request instead.
    """

    raise_error = True

    def __init__(self, stop_requested: threading.Event):
        self.stop_requested = stop_requested

    def on_llm_new_token(self, token, **kwargs):
        if self.stop_requested.is_set():
            raise StopRequested()


# Files attached to a message (the terminal's @path, the web page's attachments) follow what was typed
ATTACHED = "\n\nAttached:\n\n"
ATTACHED_FILE = re.compile(r'<file path="([^"]*)">\n')
# The outputs of the terminal's `! command`s, at the start of a message
MY_COMMANDS = re.compile(r"\A(?:<my-command>\n.*?\n</my-command>\n\n)+", re.DOTALL)


def thinking_of(message) -> str:
    """A message's (or streamed chunk's) reasoning: Ollama's `reasoning_content`, or Anthropic's
    thinking blocks"""

    if thinking := message.additional_kwargs.get("reasoning_content"):
        return thinking
    if not isinstance(message.content, list):
        return ""
    return "".join(block.get("thinking", "") for block in message.content
                   if isinstance(block, dict) and block.get("type") == "thinking")


def with_attachments(text: str, files: dict[str, str]) -> str:
    """The message with the files' contents added after what was typed"""

    if not files:
        return text
    blocks = "\n\n".join(f'<file path="{path}">\n{content}\n</file>' for path, content in files.items())
    return f"{text}{ATTACHED}{blocks}"


def split_user_message(message: HumanMessage) -> tuple[str, list[str], list[dict]]:
    """What the user typed, the names of the attached files, and the attached images
    (as content blocks) of a saved message"""

    typed, _, attached = message.text.partition(ATTACHED + "<file ")
    files = ATTACHED_FILE.findall("<file " + attached) if attached else []
    images = [b for b in message.content if isinstance(b, dict) and b.get("type") == "image"] if isinstance(message.content, list) else []
    return MY_COMMANDS.sub("", typed), files, images


def attachments_of(message: HumanMessage) -> tuple[str, list[dict]]:
    """The attached files' section of a saved message (as it follows the text) and its images,
    to keep them when the message is edited"""

    _, sep, attached = message.text.partition(ATTACHED + "<file ")
    images = [b for b in message.content if isinstance(b, dict) and b.get("type") == "image"] if isinstance(message.content, list) else []
    return (sep + attached if sep else ""), images


def user_message(thread_id: str, turn: int) -> HumanMessage:
    messages = agent.get_state({"configurable": {"thread_id": thread_id}}).values.get("messages", [])
    return messages[user_turns(messages)[turn]]


def user_turns(messages: list) -> list[int]:
    """The index of each user message, i.e. where each turn starts"""
    return [i for i, m in enumerate(messages) if m.type == "human"]


def can_take_back(thread_id: str, turn: int) -> str | None:
    """Why the conversation can't go back to before its `turn`-th user message, if it can't"""

    state = agent.get_state({"configurable": {"thread_id": thread_id}}).values
    messages = state.get("messages", [])
    starts = user_turns(messages)
    if not 0 <= turn < len(starts):
        return "there's no such message"
    if starts[turn] < state.get("summarized", 0):
        return "that part of the conversation is summarized already"
    if any(m.id is None for m in messages[starts[turn]:]):
        return "those messages have no ids"
    return None


def take_back(thread_id: str, turn: int):
    """Remove the conversation from its `turn`-th user message on (check can_take_back first),
    with the stats of the removed replies and the terminal's snapshots from then on"""

    config = {"configurable": {"thread_id": thread_id}}
    values = agent.get_state(config).values
    messages = values.get("messages", [])
    start = user_turns(messages)[turn]
    removed = messages[start:]
    update = {"messages": [RemoveMessage(id=m.id) for m in removed]}
    # What comes in their place starts with its results in full (context.py's boundary only moves on)
    if (values.get("cleared_before") or 0) > start:
        update["cleared_before"] = start
    # As llm_call, so the graph sees a finished reply and the next message starts a new run
    agent.update_state(config, update, as_node="llm_call")
    chats.delete_stats(thread_id, [m.id for m in removed if m.type == "ai"])
    chats.delete_snapshots(thread_id, turn + 1)


PREVIEW_LINES = 5

# How much of the first message and reply the title is written from
TITLE_INPUT_CHARS = 2_000


def clean_title(text: str) -> str:
    """The model's title without quotes, "Title:", a final period or extra lines"""

    line = next((line for line in text.strip().splitlines() if line.strip()), "")
    line = re.sub(r"^(\*\*)?title(\*\*)?:\s*", "", line.strip(), flags=re.IGNORECASE)
    return line.strip(" \"'`*#").rstrip(".").strip()


def name_chat(thread_id: str, config: dict) -> str | None:
    """After the first reply: have the model write the chat's title. Returns the new title,
    or None if it kept its title (it was renamed, or the model gave nothing usable)."""

    messages = agent.get_state(config).values.get("messages", [])
    if len(user_turns(messages)) != 1:
        return None
    question = messages[user_turns(messages)[0]]
    answer = next((m.text for m in reversed(messages) if m.type == "ai" and m.text), "")
    typed = split_user_message(question)[0]
    try:
        reply = llm.for_config(config).summary_model.invoke([
            {"role": "system", "content": engine.of(config).prompt("title")},
            {"role": "user", "content": f"Message: {typed[:TITLE_INPUT_CHARS]}\n\nReply: {answer[:TITLE_INPUT_CHARS]}"},
        ])
    except Exception:
        return None
    title = clean_title(reply.text)
    if not title or len(title) > 80:
        return None
    return title if chats.retitle(thread_id, question.text, title) else None


def tool_result(message: ToolMessage, name: str | None) -> dict:
    """The event for a tool's result: its size, its first line (e.g. an error or "No matches")
    and line count, and for commands the exit code and the start of the output, for the
    terminal to show"""

    event = {
        "type": "tool_result",
        "id": message.tool_call_id,
        "name": name,
        "chars": len(message.content),
        "lines": len(message.content.splitlines()),
        "first_line": message.content.partition("\n")[0][:200],
        "error": message.status == "error" or message.content.startswith("Error:"),
    }
    if name == "update_todos":
        # The terminal shows the whole list
        event["preview"] = [line[:200] for line in message.content.splitlines()[1:]]
    if name == "run_command":
        summary, _, output = message.content.partition("\n")
        lines = output.splitlines()
        event["summary"] = summary
        event["preview"] = [line[:200] for line in lines[:PREVIEW_LINES]]
        event["more_lines"] = max(0, len(lines) - PREVIEW_LINES)
    return event


def step_events(step: dict) -> list[dict]:
    """What an engine's step did, as the tool call and result events the UIs show"""

    text = step.get("text") or ""
    step_id = f"step-{uuid4().hex[:8]}"
    name = f"step: {step.get('label') or step.get('id')}"
    return [
        {"type": "tool_call", "id": step_id, "name": name, "args": {}},
        {"type": "tool_result", "id": step_id, "name": name, "chars": len(text), "lines": len(text.splitlines()),
         "first_line": text.partition("\n")[0][:200], "error": bool(step.get("error"))},
    ]


def run_reply(thread_id: str, content: str | list, stop_requested: threading.Event, emit,
              configurable: dict | None = None, replace_from: int | None = None, name: bool = True,
              recursion_limit: int | None = None, source: str = "web"):
    """Run the agent for one message, passing its events to `emit`, and save the reply's stats.

    Meant to run in its own thread; it always finishes: if the reply is stopped it
    stops at the next event (or aborts the model call in progress), saves what was
    written so far and releases the chat's lock. `content` is the message's text, or
    content blocks with images. `configurable` is added to the graph config, e.g. the
    terminal's working directory or the model. With `replace_from`, the message replaces
    the conversation from that user message on (editing a message, or regenerating).
    After the first reply, the model titles the chat (unless `name` is False) and a
    `title` event says so. `recursion_limit` caps the graph's steps (a task runs unwatched),
    and `source` is what the usage stats count the reply as.
    """
    with lock_for(thread_id):
        if replace_from is not None:
            if reason := can_take_back(thread_id, replace_from):
                emit({"type": "error", "message": f"Can't replace that message: {reason}."})
                emit({"type": "stopped", "stats": ReplyStats(llm.for_config({"configurable": configurable}).name).to_dict(True)})
                return
            take_back(thread_id, replace_from)
        # Tools get the stop flag too, e.g. run_command kills its command when the reply is stopped
        config = {"configurable": {**(configurable or {}), "thread_id": thread_id, "stop_requested": stop_requested}}
        model_spec = llm.for_config(config).spec
        stats = ReplyStats(llm.for_config(config).name, engine.of(config).label())
        config["configurable"]["count_usage"] = stats.add_side_call
        recursion_limit, cap = engine_steps.limits_for(config, recursion_limit)
        config["configurable"]["model_call_cap"] = cap
        # Going round again (continuation.py) counts afresh for each reply
        start = {"messages": [HumanMessage(content=content)], "continuing": None, "continued": {}}
        # A loop of an engine counts afresh for each reply
        said = hooks.around("RunStart", config)
        if engine.of(config).loop or said:
            start |= {"visits": {}, "notes": said}
        recording = trace.Trace(split_user_message(HumanMessage(content=content))[0])
        stream = engine_steps.graph_for(config).stream(
            start,
            config={**config, "callbacks": [StopOnRequest(stop_requested), recording, *filter(None, [(configurable or {}).get("budget")])],
                    **({"recursion_limit": recursion_limit} if recursion_limit else {})},
            stream_mode=["messages", "updates", "custom"],
        )
        trace.live[thread_id] = recording
        tool_names = {}
        # The calls whose results were reported as they finished
        reported = set()
        # Text of the model call in progress, saved if the reply is stopped
        partial = ""
        last_reply_id = None
        stopped = False
        error = None

        try:
            for mode, chunk in stream:
                if mode == "custom":
                    # A tool call's result as it's ready. Before looking for a stop: leaving now would
                    # lose the step's results, and the step ends by itself on a stop
                    if message := (chunk or {}).get("tool_result"):
                        reported.add(message.tool_call_id)
                        emit(tool_result(message, tool_names.get(message.tool_call_id)))
                    elif retry := (chunk or {}).get("retry"):
                        # A model call failed for a moment (model.call_model): what it wrote is gone
                        partial = ""
                        stats.pending_chunks = 0
                        emit({"type": "retry", **retry})
                    continue
                if stop_requested.is_set():
                    stopped = True
                    break
                if mode == "messages":
                    # Reply tokens as the model writes them
                    message, metadata = chunk
                    if metadata.get("langgraph_node") != "llm_call":
                        continue
                    stats.pending_chunks += 1
                    # The model's reasoning comes apart from the reply
                    if thinking := thinking_of(message):
                        emit({"type": "thinking", "content": thinking})
                    if message.text:
                        stats.started()
                        partial += message.text
                        emit({"type": "token", "content": message.text})
                elif (chunk.get("compact") or {}).get("summary"):
                    emit({"type": "compact"})
                elif "llm_call" in chunk:
                    update = chunk["llm_call"]
                    partial = ""
                    if reason := update.get("continuing"):
                        stats.continued += 1
                        emit({"type": "continue", "reason": reason})
                    if not update["messages"]:
                        # An empty reply, dropped to be asked again (its cost went through count_usage)
                        stats.pending_chunks = 0
                        continue
                    message = update["messages"][-1]
                    stats.add(message)
                    stats.end_reason = message.response_metadata.get("end_reason")
                    last_reply_id = message.id
                    fixed = (message.response_metadata.get("tool_fixes") or {}).get("notes", {})
                    for tool_call in message.tool_calls:
                        stats.started()
                        tool_names[tool_call["id"]] = tool_call["name"]
                        event = {"type": "tool_call", "id": tool_call["id"], "name": tool_call["name"], "args": tool_call["args"]}
                        if notes := fixed.get(tool_call["id"]):
                            event["fixed"] = notes
                        emit(event)
                elif "tool_node" in chunk:
                    # Only send the size; articles are long
                    for message in chunk["tool_node"]["messages"]:
                        if message.tool_call_id not in reported:
                            emit(tool_result(message, tool_names.get(message.tool_call_id)))
                else:
                    # An engine's own step that has something to show: as a tool call, which the UIs know
                    for update in chunk.values():
                        if step := (update or {}).get("step"):
                            for event in step_events(step):
                                emit(event)
        except Exception as e:
            # A stop during a model call ends it with StopRequested
            if stop_requested.is_set():
                stopped = True
            elif isinstance(e, GraphRecursionError):
                stats.end_reason = "limit"
                # Tasks and workflow steps say this in their own words, by "recursion limit"
                error = ("Stopped: the reply made as many model calls as one may (the recursion limit; "
                         "an admin can change it in Settings → Limits"
                         + (", and the engine has its own in its loop)." if cap else ")."))
            else:
                error = llm.explain_error(e, model_spec)
        finally:
            stream.close()
            if stopped or error:
                last_reply_id = save_stopped_reply(config, partial) or last_reply_id
            summary = stats.to_dict(stopped, error=error is not None)
            if last_reply_id:
                chats.save_stats(thread_id, last_reply_id, summary)
                # The web chat says whose reply it was; labb's replies are counted when they're synced
                if user_id := (configurable or {}).get("user_id"):
                    usage.record(thread_id, last_reply_id, summary, user_id=user_id, source=source)
            recording.finish()
            chats.save_trace(thread_id, last_reply_id or str(uuid4()), recording.to_dict())
            trace.live.pop(thread_id, None)
            chats.prune(thread_id)
            hooks.around("RunEnd", config)

    if error:
        emit({"type": "error", "message": error})
    elif name and not stopped and (title := name_chat(thread_id, config)):
        emit({"type": "title", "title": title})
    emit({"type": "stopped" if stopped else "done", "stats": summary})
