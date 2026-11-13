"""Keeps what the model sees within its context window.

The saved conversation (state["messages"]) always stays complete, so the chats
can be shown in full. Only the messages sent to the model are compacted:

1. Old tool results are cleared: large results before state["cleared_before"]
   are replaced with a short note. The tool calls stay, so the model knows what
   it did and can call the tool again. The boundary moves in jumps (`clearing_boundary`,
   in the `compact` node): only once the large results still in full take more than
   `clear_at` of the window, and then far enough that only the last `keep_tool_results`
   stay. In between the input only grows at its end, so the provider's prompt cache
   holds. Results the model hasn't read yet are never cleared; when several don't fit,
   they share the room (fit_unread_results), and no one result takes more than
   `max_result_share` of the window (cap_results).
2. When the conversation still gets close to the window, old results are cleared
   first, and then the `compact` node summarizes the older turns into
   state["summary"]. The model then gets the summary, the user's own messages from
   those turns word for word (state["user_words"]) and the recent turns;
   state["summarized"] counts the messages the summary covers.
"""

import re

from langchain.messages import HumanMessage, SystemMessage
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.utils.function_calling import convert_to_openai_tool

from labb import llm
from labb.engine import (CODING_SUMMARY_PROMPT, COMPACT_AT, DEFAULT, KEEP_RECENT, KEEP_TOOL_RESULTS, MAX_SCALE,  # noqa: F401
                         MIN_CLEARED_CHARS, SUMMARY_PROMPT, Engine)

# How a note only the model sees starts (model.with_reminder), so /context can tell it from recalled memories
REMINDER_MARK = "[labb, for you only: "
# The approximate count's characters per token, and what each result keeps when several are cut to fit
CHARS_PER_TOKEN = 4
MIN_SHARE_CHARS = 1_000


def unread_from(messages: list) -> int:
    """Where the tool results the model hasn't seen yet begin: those after its latest reply"""

    first = len(messages)
    while first > 0 and messages[first - 1].type == "tool":
        first -= 1
    return first if first > 0 and messages[first - 1].type == "ai" else len(messages)


def _is_large(message, minimum: int = MIN_CLEARED_CHARS) -> bool:
    return message.type == "tool" and len(message.content) >= minimum


def clear_old_tool_results(messages: list, start: int = 0, engine: Engine = DEFAULT) -> list:
    """Replace all but the engine's last keep_tool_results large tool results with a short note, which
    gives the result's number in the saved conversation (`messages` begin at `start`) for look_back.
    Results the model hasn't read yet stay, and count towards those kept."""

    minimum = engine.context("min_cleared_chars")
    keep = engine.context("keep_tool_results")
    large = [i for i, m in enumerate(messages) if _is_large(m, minimum)]
    if keep:
        keep = max(keep, sum(1 for i in large if i >= unread_from(messages)))
    cleared = list(messages)
    for i in large[:-keep] if keep else large:
        message = messages[i]
        note = (
            f"[Cleared to save context: {len(message.content):,} characters returned by "
            f"{message.name or 'this tool'}, #{start + i}. look_back(number={start + i}) reads it again.]"
        )
        cleared[i] = message.model_copy(update={"content": note})
    return cleared


def unread_large(messages: list, engine: Engine = DEFAULT) -> list[int]:
    """The large tool results the model hasn't read yet, by position"""

    minimum = engine.context("min_cleared_chars")
    return [i for i in range(unread_from(messages), len(messages)) if _is_large(messages[i], minimum) and isinstance(messages[i].content, str)]


def fair_share(lengths: list[int], room: float) -> float:
    """How long each text may be to fit them all in `room`: short ones stay whole, and the longer
    ones get the same share of what's left"""

    left, count = room, len(lengths)
    for length in sorted(lengths):
        if length > left / count:
            return left / count
        left -= length
        count -= 1
    return max(lengths, default=0)


def fit_unread_results(messages: list, *, start: int, tools: list, window: int, scale: float, engine: Engine = DEFAULT) -> list:
    """Several large results at once may not fit: share the room the rest of the input leaves up to
    the engine's compact_at. `messages` is the model's input, the system message first, and
    `start` the saved number of the first one after it. Only the input is cut, so look_back can read on."""

    unread = unread_large(messages, engine)
    if len(unread) < 2:
        return messages
    rest = [m for i, m in enumerate(messages) if i not in unread]
    room = (engine.context("compact_at") * window - count_tokens_approximately(rest, tools=tools) * scale) / scale * CHARS_PER_TOKEN
    lengths = [len(messages[i].content) for i in unread]
    if sum(lengths) <= room:
        return messages
    share = max(round(fair_share(lengths, max(room, 0))), MIN_SHARE_CHARS)
    fitted = list(messages)
    for i in unread:
        message = messages[i]
        if len(message.content) > share:
            number = start + i - 1
            note = (f"\n\n[Cut to fit the context: this is the first {share:,} of {len(message.content):,} characters returned by "
                    f"{message.name or 'this tool'}, #{number}. look_back(number={number}) reads all of it, in pages.]")
            fitted[i] = message.model_copy(update={"content": message.content[:share] + note})
    return fitted


def with_summary(prompt: str, summary: str, summarized: int = 0, user_words: str = "") -> SystemMessage:
    """One system message with the summary (and the user's own words from what it covers)
    appended; some providers allow only one"""

    if summary:
        covers = f" (messages #0 to #{summarized - 1}; look_back searches them)" if summarized else ""
        prompt += f"\n\nSummary of the earlier conversation{covers}:\n{summary}"
    if user_words:
        prompt += f"\n\n{user_words}"
    return SystemMessage(content=prompt)


# What separates attached files from what the user typed (runner.with_attachments)
ATTACHED_FILE = re.compile(r'\s*<file path="([^"]*)">')
# The most one message keeps of itself among the user's words
MAX_WORDS_PER_MESSAGE = 4_000


def typed(message) -> str:
    """What the user typed in a message, attached files named rather than copied"""

    text = message.text
    match = ATTACHED_FILE.search(text)
    if not match:
        return text.strip()
    files = ATTACHED_FILE.findall(text)
    return f"{text[:match.start()].strip()} [attached: {', '.join(files)}]"


def user_words(messages: list, room_chars: int) -> str:
    """The user's messages among `messages` (numbered from 0), word for word, as many of the latest
    as fit in `room_chars`, oldest first: what a summary would otherwise paraphrase"""

    if room_chars <= 0:
        return ""
    found = [(i, typed(m)) for i, m in enumerate(messages) if m.type == "human"]
    found = [(i, text) for i, text in found if text]
    kept, used = [], 0
    for number, text in reversed(found):
        if len(text) > MAX_WORDS_PER_MESSAGE:
            text = text[:MAX_WORDS_PER_MESSAGE] + f"… (cut: look_back(number={number}) reads all of it)"
        if used + len(text) > room_chars and kept:
            break
        kept.append((number, text))
        used += len(text)
    if not kept:
        return ""
    left_out = len(found) - len(kept)
    lines = [f"#{number}: {text}" for number, text in reversed(kept)]
    more = f"\n({left_out} earlier message{'s' if left_out > 1 else ''} left out: look_back searches them)" if left_out else ""
    return "What the user wrote in the summarized part, word for word (oldest first):\n" + "\n\n".join(lines) + more


# The tools whose `path` says what was done to a file, for the summary's list of files
FILE_TOOLS = {"read_file": "read", "edit_file": "edited", "write_file": "written"}
RECENT_FILES = 8


def recent_files(messages: list, start: int = 0) -> str:
    """The files the summarized messages read or changed, the latest last, with what was last done
    and where: facts the summary model shouldn't have to get right"""

    last = {}
    for i, message in enumerate(messages):
        for call in getattr(message, "tool_calls", None) or []:
            if call["name"] in FILE_TOOLS and isinstance((call.get("args") or {}).get("path"), str):
                path = call["args"]["path"]
                last.pop(path, None)
                last[path] = (FILE_TOOLS[call["name"]], start + i)
    if not last:
        return ""
    shown = list(last.items())[-RECENT_FILES:]
    return "Files worked on (the latest last): " + "; ".join(f"{path} ({action}, #{number})" for path, (action, number) in shown)


def clear_before(messages: list, start: int, boundary: int, engine: Engine = DEFAULT) -> list:
    """Replace the large tool results whose saved number is below `boundary` with a note, except
    those the model hasn't read yet (`messages` begin at `start`)"""

    minimum = engine.context("min_cleared_chars")
    first_unread = unread_from(messages)
    cleared = list(messages)
    for i, message in enumerate(messages):
        if start + i < boundary and i < first_unread and _is_large(message, minimum):
            cleared[i] = message.model_copy(update={"content": cleared_note(message, start + i)})
    return cleared


def cleared_note(message, number: int) -> str:
    return (f"[Cleared to save context: {len(message.content):,} characters returned by "
            f"{message.name or 'this tool'}, #{number}. look_back(number={number}) reads it again.]")


def clearing_boundary(state: dict, window: int, scale: float, engine: Engine = DEFAULT, pressed: bool = False) -> int:
    """Where clearing old results begins now: where it was, or further on once the large results
    the model has read and still gets in full take more than `clear_at` of the window (or, when
    `pressed`, whenever there are more than `keep_tool_results` of them), leaving only those"""

    messages = state["messages"]
    start = state.get("summarized", 0)
    boundary = max(state.get("cleared_before") or 0, start)
    keep = engine.context("keep_tool_results")
    minimum = engine.context("min_cleared_chars")
    first_unread = start + unread_from(messages[start:])
    kept = [i for i in range(boundary, first_unread) if _is_large(messages[i], minimum)]
    if len(kept) <= keep:
        return boundary
    chars = sum(len(messages[i].content) for i in kept)
    if not pressed and chars / CHARS_PER_TOKEN * scale <= engine.context("clear_at") * window:
        return boundary
    return kept[-keep] if keep else first_unread


def model_messages(state: dict, system_prompt: str, engine: Engine = DEFAULT) -> list:
    """The messages to send to the model: system prompt with the summary, then the recent turns"""

    start = state.get("summarized", 0)
    recent = state["messages"][start:]
    boundary = state.get("cleared_before")
    # A chat the compact step hasn't seen yet (or an engine's loop without one), or an engine that
    # keeps no results at all: by count, as before the boundary existed
    if boundary is None or not engine.context("keep_tool_results"):
        cleared = clear_old_tool_results(recent, start, engine)
    else:
        cleared = clear_before(recent, start, boundary, engine)
    return [with_summary(system_prompt, state.get("summary", ""), start, state.get("user_words", ""))] + cleared


def cap_results(messages: list, *, start: int, window: int, scale: float, engine: Engine = DEFAULT) -> list:
    """No one tool result may take more than `max_result_share` of the window: a longer one is cut
    for the model, which can read on with look_back. `messages` is the model's input, the system
    message first, and `start` the saved number of the first one after it."""

    cap = max(int(engine.context("max_result_share") * window * CHARS_PER_TOKEN / max(scale, 1.0)), MIN_SHARE_CHARS)
    capped = list(messages)
    for i, message in enumerate(messages):
        if message.type == "tool" and isinstance(message.content, str) and len(message.content) > cap:
            number = start + i - 1
            note = (f"\n\n[Cut to fit the context: this is the first {cap:,} of {len(message.content):,} characters returned by "
                    f"{message.name or 'this tool'}, #{number}. look_back(number={number}) reads all of it, in pages.]")
            capped[i] = message.model_copy(update={"content": message.content[:cap] + note})
    return capped


def token_scale(state: dict, system_prompt: str, tools: list, engine: Engine = DEFAULT) -> float:
    """How much the approximate count (4 characters per token) undercounts this conversation.

    JSON and URLs take more tokens than plain text. The last model reply reports
    the real input tokens, so compare them with the estimate for that input.
    """
    messages = state["messages"]
    for i in range(len(messages) - 1, state.get("summarized", 0), -1):
        usage = messages[i].usage_metadata if messages[i].type == "ai" else None
        if usage and usage.get("input_tokens"):
            estimate = count_tokens_approximately(
                model_messages({**state, "messages": messages[:i]}, system_prompt, engine), tools=tools
            )
            return min(max(usage["input_tokens"] / estimate, 1.0), engine.context("max_scale"))
    return 1.0


def context_used(state: dict, *, system_prompt: str, tools: list, models: llm.Models | None = None,
                 engine: Engine = DEFAULT) -> float:
    """How full the model's context window is with this conversation, from 0 to 1 (or more)"""

    models = models or llm.default
    scale = token_scale(state, system_prompt, tools, engine)
    tokens = count_tokens_approximately(model_messages(state, system_prompt, engine), tools=tools) * scale
    return tokens / models.context_window


def image_count(message) -> int:
    return sum(isinstance(b, dict) and b.get("type") == "image" for b in message.content) if isinstance(message.content, list) else 0


def timeline(messages: list, start: int = 0) -> list[dict]:
    """Each saved model reply with the real tokens its call reported (None where it reported none).
    `start` is how many messages the summary covers. Only replies are saved, so a summary's own
    call and an engine's side steps aren't here."""

    calls = []
    for i, message in enumerate(messages):
        if message.type != "ai":
            continue
        usage = message.usage_metadata or {}
        details = usage.get("input_token_details") or {}
        calls.append({
            "index": i,
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "cached_tokens": details.get("cache_read"),
            "model": message.response_metadata.get("model_name") or message.response_metadata.get("model"),
            "tool_calls": [call["name"] for call in message.tool_calls],
            "summarized": i < start,
        })
    return calls


def inspect(state: dict, *, system_parts: list[tuple[str, str]], tools: list, models: llm.Models | None = None,
            sent: list | None = None, engine: Engine = DEFAULT, scale: float | None = None) -> dict:
    """What the model gets for this conversation, part by part, with estimated tokens (scaled
    like context_used). `system_parts` are the system prompt's (label, text) sections and `sent`
    the model's input if it isn't just model_messages' (model.model_input adds recalled memories).
    `scale` compares other choices with the input as it stands: token_scale measures against the
    input the last reply really had, so recomputing it without some tools would hide what they cost."""

    models = models or llm.default
    system_prompt = "\n\n".join(text for _, text in system_parts)
    summary = state.get("summary", "")
    start = state.get("summarized", 0)
    saved = state["messages"][start:]
    sent = sent if sent is not None else model_messages(state, system_prompt, engine)
    scale = token_scale(state, system_prompt, tools, engine) if scale is None else min(max(scale, 1.0), engine.context("max_scale"))

    # The system message holds the prompt's sections and the summary: share its tokens out by length
    words = state.get("user_words", "") if summary else ""
    pieces = list(system_parts) + ([("Summary", summary)] if summary else []) + ([("User's words", words)] if words else [])
    system_tokens = count_tokens_approximately(sent[:1]) * scale
    chars = sum(len(text) for _, text in pieces) or 1
    system = [{"label": label, "tokens": round(system_tokens * len(text) / chars), "text": text} for label, text in pieces]
    words_tokens = system.pop()["tokens"] if words else 0
    summary_tokens = system.pop()["tokens"] if summary else 0

    tool_list = []
    for tool in tools:
        shown = convert_to_openai_tool(tool)["function"]
        tool_list.append({"name": tool.name, "tokens": round(count_tokens_approximately([], tools=[tool]) * scale),
                          "description": shown.get("description", ""), "parameters": shown.get("parameters", {})})
    tools_tokens = round(count_tokens_approximately([], tools=tools) * scale) if tools else 0

    messages = []
    recalled_tokens = 0
    cleared_chars = 0
    # The model can get a message of its own after the saved ones (model.with_reminder's after_reply)
    for i, message in enumerate(sent[1:]):
        original = saved[i] if i < len(saved) else None
        changed = original is None or message.content != original.content
        # What was added to the saved text: recalled memories (only on a user's message), then the reminder
        kept = original is not None and message.text.startswith(original.text)
        added = message.text[len(original.text):] if changed and kept else ""
        memories, mark, reminded = added.partition(REMINDER_MARK)
        cleared = changed and message.type == "tool" and not kept
        recalled = changed and message.type == "human" and bool(memories.strip())
        reminder = bool(mark) or (original is None and REMINDER_MARK in message.text)
        tokens = round(count_tokens_approximately([message]) * scale)
        if recalled:
            # The memories count as their own part; a reminder beside them stays with the message
            extra = (count_tokens_approximately([message]) - count_tokens_approximately([original])) * scale
            extra = round(extra * len(memories) / len(added))
            recalled_tokens += extra
            tokens -= extra
        if cleared:
            cleared_chars += len(original.content) - len(message.content)
        messages.append({
            "index": start + i,
            "role": message.type,
            "name": message.name if message.type == "tool" else None,
            "tokens": tokens,
            "chars": len(message.text),
            "cleared": cleared,
            "original_chars": len(original.content) if cleared else None,
            "recalled": recalled,
            "reminder": reminder,
            "images": image_count(message),
            "text": message.text,
            "tool_calls": [{"name": c["name"], "args": c["args"]} for c in getattr(message, "tool_calls", [])],
        })

    # With every full result slot taken, the oldest result still in full goes at the next clearing
    keep = engine.context("keep_tool_results")
    large = [start + i for i, m in enumerate(saved) if m.type == "tool" and len(m.content) >= engine.context("min_cleared_chars")
             and 1 + i < len(sent) and m.content == sent[1 + i].content]

    parts = [
        {"kind": "system", "label": "System prompt", "tokens": sum(s["tokens"] for s in system)},
        {"kind": "tools", "label": "Tools", "tokens": tools_tokens},
        {"kind": "summary", "label": "Summary", "tokens": summary_tokens},
        {"kind": "user_words", "label": "User's words", "tokens": words_tokens},
        {"kind": "messages", "label": "Messages", "tokens": sum(m["tokens"] for m in messages)},
        {"kind": "recalled", "label": "Recalled memories", "tokens": recalled_tokens},
    ]
    parts = [p for p in parts if p["tokens"]]
    used = sum(p["tokens"] for p in parts)
    window = models.context_window
    # As compact() decides, without the recalled memories
    before_compact = count_tokens_approximately(model_messages(state, system_prompt, engine), tools=tools) * scale
    compact_at = engine.context("compact_at")
    return {
        "model": models.spec,
        "window": window,
        "used": used,
        "share": used / window,
        "compact_at": round(compact_at * window),
        "will_compact": before_compact > compact_at * window,
        "scale": round(scale, 2),
        "summarized": start,
        "total_messages": len(state["messages"]),
        "cleared": {"count": sum(m["cleared"] for m in messages), "chars": cleared_chars},
        "next_to_clear": large[0] if keep and len(large) >= keep else None,
        "clear_at": round(engine.context("clear_at") * window),
        "parts": parts,
        "system": system,
        "tools": tool_list,
        "summary": summary,
        "messages": messages,
        "timeline": timeline(state["messages"], start),
    }


def input_tokens(state: dict, system_prompt: str, tools: list, window: int, scale: float, engine: Engine = DEFAULT) -> float:
    """The model's input as compaction counts it: unread results whole, but none over its cap"""

    messages = cap_results(model_messages(state, system_prompt, engine), start=state.get("summarized", 0), window=window,
                           scale=scale, engine=engine)
    return count_tokens_approximately(messages, tools=tools) * scale


def compact(state: dict, *, system_prompt: str, tools: list, models: llm.Models | None = None, force: bool = False,
            summary_prompt: str = SUMMARY_PROMPT, engine: Engine = DEFAULT, before_summary=None, facts: str = "",
            flush=None):
    """Clear old tool results when they take too much room, and summarize older turns when the
    model's input gets close to the context window. The update for the state, {} for none.

    With `force` (the terminal's /compact), summarize every turn not summarized yet, now.
    `facts` is the job's state (todos, plan, notes' topics) for the summary model. `flush`, if
    given, is asked once before a summary: a note it returns goes to the model first, so it can
    save what it found out, and the summary waits for the next round.
    """
    models = models or llm.default
    context_window = models.context_window
    scale = token_scale(state, system_prompt, tools, engine)
    start = state.get("summarized", 0)
    update = {}
    if engine.context("keep_tool_results") and not force:
        boundary = clearing_boundary(state, context_window, scale, engine)
        if boundary != state.get("cleared_before"):
            update["cleared_before"] = boundary
            state = {**state, "cleared_before": boundary}
    recent = model_messages(state, system_prompt, engine)[1:]

    if force:
        if not recent:
            return {}
        if before_summary:
            before_summary()
        end = start + len(recent)
        return {"summary": summarize(state, recent, models, summary_prompt, facts, start, summary_tokens(context_window, engine)), "summarized": end,
                "user_words": user_words(state["messages"][:end], words_room(context_window, scale, engine)), "flushed": False}

    limit = engine.context("compact_at") * context_window
    tokens = input_tokens(state, system_prompt, tools, context_window, scale, engine)
    if tokens > limit and engine.context("keep_tool_results"):
        # Clearing old results is cheaper than a summary: first that, as far as it goes
        boundary = clearing_boundary(state, context_window, scale, engine, pressed=True)
        if boundary != state.get("cleared_before"):
            update["cleared_before"] = boundary
            state = {**state, "cleared_before": boundary}
            tokens = input_tokens(state, system_prompt, tools, context_window, scale, engine)
            recent = model_messages(state, system_prompt, engine)[1:]
    if tokens <= limit:
        return update

    # Cut at the start of a turn (a user message) so tool calls stay with their results,
    # keeping the most recent turns that fit; the current turn is always kept
    turn_starts = [i for i, m in enumerate(recent) if i > 0 and m.type == "human"]
    if not turn_starts:
        return update
    # Once per summary, the model may save what it found out first (if there's room for a round)
    if flush is not None and not state.get("flushed") and tokens < FLUSH_ROOM * context_window and (note := flush()):
        return {**update, "flushed": True, "notes": [*(state.get("notes") or []), note]}
    cut = next(
        (i for i in turn_starts if count_tokens_approximately(recent[i:]) * scale <= engine.context("keep_recent") * context_window),
        turn_starts[-1],
    )

    if before_summary:
        before_summary()
    end = start + cut
    return {**update, "summary": summarize(state, recent[:cut], models, summary_prompt, facts, start, summary_tokens(context_window, engine)),
            "summarized": end,
            "user_words": user_words(state["messages"][:end], words_room(context_window, scale, engine)), "flushed": False}


# Above this share of the window there's no room for a round to save notes before the summary
FLUSH_ROOM = 0.9
# How much of the summary model's window one round of summarizing may send it
SUMMARY_INPUT_SHARE = 0.6


# A summary's length in tokens, whatever the window
MIN_SUMMARY_TOKENS, MAX_SUMMARY_TOKENS = 1_024, 8_192


def summary_tokens(window: int, engine: Engine = DEFAULT) -> int:
    """How long a summary may be: the engine's share of the window"""
    return min(max(int(engine.context("summary_share") * window), MIN_SUMMARY_TOKENS), MAX_SUMMARY_TOKENS)


def words_room(window: int, scale: float, engine: Engine = DEFAULT) -> int:
    return int(engine.context("user_words") * window * CHARS_PER_TOKEN / max(scale, 1.0))


def chunks(messages: list, room_tokens: float) -> list[list]:
    """`messages` in parts that fit `room_tokens` each, cut only where a tool call isn't waiting
    for its result; a part that can't be cut smaller goes as it is (its results capped)"""

    parts, current, size = [], [], 0.0
    for message in messages:
        tokens = count_tokens_approximately([message])
        if current and size + tokens > room_tokens and message.type != "tool":
            parts.append(current)
            current, size = [], 0.0
        current.append(message)
        size += tokens
    return [*parts, current] if current else parts


def summarize(state: dict, messages: list, models: llm.Models, prompt: str = SUMMARY_PROMPT, facts: str = "",
              start: int = 0, max_tokens: int | None = None) -> str:
    """The previous summary merged with `messages` (numbered from `start`), in rounds if they're
    more than the summary model can take at once, with the files worked on added as a fact.
    `max_tokens` is how long it may be, for a summary model whose length is capped (Ollama's)."""

    window = models.context_window
    model = models.summary_model
    if max_tokens and getattr(model, "num_predict", None) is not None:
        model = model.model_copy(update={"num_predict": max_tokens})
    room = SUMMARY_INPUT_SHARE * window
    capped = cap_results([SystemMessage(""), *messages], start=start, window=window, scale=1.0)[1:]
    summary = state.get("summary", "")
    # The file list was added to the previous summary, not written by the model: it's made again below
    summary = summary.split("\n\nFiles worked on (the latest last): ")[0]
    ask = "Write the updated summary now." + (f"\n\nWhere the job stands (from labb, exact):\n{facts}" if facts else "")
    for part in chunks(capped, room):
        summary = model.invoke([with_summary(prompt, summary)] + part + [HumanMessage(content=ask)]).text
    files = recent_files(state["messages"][:start + len(messages)])
    return f"{summary.strip()}\n\n{files}" if files else summary
