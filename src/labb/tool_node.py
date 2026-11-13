import contextvars
import queue
import threading
from collections import deque

from langchain.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import StreamWriter

from labb import hooks
from labb import engine
from labb import tools as toolsets
from labb.tools import custom, is_allowed, is_on, tools_by_name

# What a tool call says when the reply stopped before it had finished
CANCELLED = "Cancelled: the user stopped the response."
# How often a batch of calls looks whether the reply was stopped
STOP_CHECK_SECONDS = 0.1


def _run(tool, args: dict, config: RunnableConfig) -> tuple:
    try:
        return tool.invoke(args, config), "success"
    except Exception as e:
        return f"Error: {e}", "error"


def _run_on_a_thread(tool, args: dict, config: RunnableConfig, done, i: int):
    """Always says how it ended: the reply is waiting for it"""
    try:
        done.put((i, _run(tool, args, config)))
    except BaseException as e:
        done.put((i, (f"Error: the call ended unexpectedly ({e.__class__.__name__})", "error")))


def batches(calls: list[dict], limit: int) -> list[list[int]]:
    """The calls' positions, in the order they run: neighbours that may run together share a
    batch, and any other call goes on its own, so nothing passes a call that changes something"""

    found: list[list[int]] = []
    for i, call in enumerate(calls):
        together = limit > 1 and call["name"] in toolsets.run_together
        if together and found and calls[found[-1][-1]]["name"] in toolsets.run_together:
            found[-1].append(i)
        else:
            found.append([i])
    return found


def tool_node(state: dict, config: RunnableConfig, writer: StreamWriter = lambda _: None):
    """Performs the tool calls; errors are returned to the model instead of crashing the run.

    Tools get the run's config, e.g. the file tools read the working directory from it.
    In the terminal, the settings' hooks run before and after each call (see hooks.py).

    Calls next to each other that only read (`tools.run_together`) run at the same time, at most
    `tools.parallel_limit()`. The results keep the calls' order, and each is reported through
    `writer` as it's ready. A stop gives up on what's still running, which is safe because those
    calls only read; a call running alone ends by itself, as run_command does on a stop.
    """

    configurable = config.get("configurable") or {}
    # look_back reads the saved conversation, which the model no longer sees all of; read_file
    # tells a result the model still has in full by where summaries and clearing end (context.py)
    config = {**config, "configurable": {**configurable, "history": state["messages"], "summarized": state.get("summarized", 0),
                                         "cleared_before": state.get("cleared_before")}}
    tool_hooks = hooks.of(config)
    own_tools = {tool.name: tool for tool in custom.tools_of(engine.of(config).custom_tools)}
    workdir = configurable.get("workdir")
    stop_requested = configurable.get("stop_requested")
    calls = state["messages"][-1].tool_calls
    limit = toolsets.parallel_limit()
    results: list[ToolMessage | None] = [None] * len(calls)

    def stopped() -> bool:
        return stop_requested is not None and stop_requested.is_set()

    def ready(i: int) -> tuple | None:
        """The call's tool if it may run: None once its result is in, because it may not"""
        name, args = calls[i]["name"], calls[i]["args"]
        tool = tools_by_name.get(name) or own_tools.get(name)
        blocked = hooks.run("PreToolUse", tool_hooks, name, args, workdir) if tool_hooks and workdir else []
        if tool is None:
            failed = f"Error: unknown tool `{name}`"
        elif not is_allowed(name, config):
            # An agent's model can still name a tool it wasn't given
            failed = f"Error: `{name}` isn't one of your tools"
        elif not is_on(name, config):
            failed = f"Error: `{name}` is turned off in this session"
        elif blocked:
            failed = "Not run: a hook blocked this call. " + " ".join(blocked)
        else:
            return tool
        finish(i, failed, "error", ran=False)
        return None

    def finish(i: int, observation, status: str, ran: bool = True):
        name, args = calls[i]["name"], calls[i]["args"]
        if ran and tool_hooks and workdir:
            if feedback := hooks.run("PostToolUse", tool_hooks, name, args, workdir, result=str(observation)):
                observation = f"{observation}\n\nFrom a hook after this call: {' '.join(feedback)}"
        results[i] = ToolMessage(content=observation, name=name, tool_call_id=calls[i]["id"], status=status)
        writer({"tool_result": results[i]})

    def run_batch(batch: list[int]):
        waiting = deque((i, tool) for i in batch if (tool := ready(i)))
        done: queue.SimpleQueue = queue.SimpleQueue()

        running = 0
        while waiting or running:
            while waiting and running < limit and not stopped():
                i, tool = waiting.popleft()
                # Daemon threads and not a pool's, whose workers would hold up the exit for a call given up on
                context = contextvars.copy_context()
                threading.Thread(target=context.run, args=(_run_on_a_thread, tool, calls[i]["args"], config, done, i),
                                 daemon=True, name=f"tool-{calls[i]['name']}").start()
                running += 1
            if stopped():
                break
            try:
                i, outcome = done.get(timeout=STOP_CHECK_SECONDS)
            except queue.Empty:
                continue
            running -= 1
            finish(i, *outcome)
        # What finished just as the stop came is kept
        while running:
            try:
                i, outcome = done.get_nowait()
            except queue.Empty:
                break
            running -= 1
            finish(i, *outcome)

    for batch in batches(calls, limit):
        if stopped():
            break
        if len(batch) > 1:
            run_batch(batch)
        elif tool := ready(batch[0]):
            finish(batch[0], *_run(tool, calls[batch[0]]["args"], config))

    for i, message in enumerate(results):
        if message is None:
            results[i] = ToolMessage(content=CANCELLED, name=calls[i]["name"], tool_call_id=calls[i]["id"], status="error")
    return {"messages": results}
