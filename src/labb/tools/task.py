"""`task`: hand a search or an investigation to a helper agent with a fresh context. The helper gets the read-only tools (files, read-only commands, the web), works
on its own, and only its conclusion comes back, so the main conversation stays small.

It's a plain loop rather than a graph: run inside a tool, a graph would join the reply's
checkpoints and stream. Its model calls aren't streamed, but they count in the reply's stats
(`count_usage`) and its budget. Its old results are cleared like the main conversation's, and
it can read them again with its own `look_back`, so 20 steps fit a small window too."""

from langchain.tools import tool
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from labb import context, llm, tool_calls

# Only offered to the model when there's a working directory (see tools/__init__.py)
NEEDS_WORKDIR = True

MAX_STEPS = 20
# Each tool result the helper gets, and the conclusion handed back
MAX_RESULT_CHARS = 12_000
MAX_ANSWER_CHARS = 8_000
# What the helper's tool calls may see of the run's settings; the mode is always plan (read-only)
PASSED_ON = ("workdir", "approve", "stop_requested", "allow_all", "tools", "hooks")

HELPER_PROMPT = """\
You're a helper agent for labb, a coding agent in the user's terminal. You were given one \
job: do it with your tools, on your own (you can't ask questions), then answer. The working \
directory is `{workdir}`. Your tools only read: you can't change files, and commands that \
change something won't run.

Look things up instead of guessing, and stop as soon as you can answer. Your answer is all \
that's passed on, so make it complete and short, at most about 300 words: what you found, with \
file paths and line numbers (`path:line`), and anything you couldn't find. Old tool results are \
cleared as you go: `look_back` reads one again by its #number."""


def helper_tools(config: RunnableConfig) -> list:
    """The read-only tools, minus those the session turned off"""

    from labb.tools import files, history, rules_for, shell, web

    rules = rules_for(config)
    tools = [*files.TOOLS, shell.run_command, *(web.TOOLS if getattr(web, "enabled", lambda: True)() else []), history.look_back]
    return [t for t in tools if rules is None or rules.is_on(t.name)]


def shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n… ({len(text) - limit:,} characters cut)"


@tool
def task(description: str, prompt: str, config: RunnableConfig = None) -> str:
    """Hand a search or investigation to a helper agent, which works in its own context and returns only its conclusion.

    Use it for jobs that need many searches and reads whose details you don't need, such as
    "where is X handled and how" across a big codebase, or "which files use this function".
    The helper can read files, run read-only commands and search the web, but can't change
    anything or see this conversation, so give it everything it needs in `prompt`.

    Args:
        description: What it's doing, in 3 to 6 words, for the user (e.g. "Find the login handlers")
        prompt: The whole job: what to find out, where to look, and what to report back
    """
    configurable = (config or {}).get("configurable") or {}
    workdir = configurable.get("workdir")
    if not workdir:
        return "Error: task only works in the terminal (labb), which has a working directory"
    stop = configurable.get("stop_requested")
    messages = [SystemMessage(HELPER_PROMPT.format(workdir=workdir)), HumanMessage(prompt)]
    # Its look_back reads its own messages, which are numbered like the main conversation's
    helper_config = {"configurable": {key: configurable[key] for key in PASSED_ON if key in configurable}
                     | {"mode": "plan", "history": messages}, "callbacks": []}
    tools = helper_tools(config)
    by_name = {t.name: t for t in tools}
    models = llm.for_config(config)
    model = models.model.bind_tools(tools)
    # No streaming callbacks: the helper's replies aren't the reply's. The budget still sees its cost.
    quiet = {"callbacks": [budget] if (budget := configurable.get("budget")) else [], "tags": ["nostream"]}
    count = configurable.get("count_usage")

    def ask(sent):
        answer = model.invoke(context.clear_old_tool_results(sent), quiet)
        if count:
            count(answer)
        return answer

    steps = 0
    for steps in range(1, MAX_STEPS + 1):
        if stop is not None and stop.is_set():
            return "Stopped by the user before the helper finished."
        reply = tool_calls.fix(ask(messages), tools)
        messages.append(reply)
        if not reply.tool_calls:
            return shorten(reply.text.strip() or "(The helper gave no answer.)", MAX_ANSWER_CHARS)
        for call in reply.tool_calls:
            if call["name"] not in by_name:
                result = f"Error: there's no tool {call['name']}; use one of {', '.join(by_name)}"
            else:
                try:
                    result = str(by_name[call["name"]].invoke(call["args"], helper_config))
                except Exception as e:
                    result = f"Error: {e}"
            messages.append(ToolMessage(shorten(result, MAX_RESULT_CHARS), tool_call_id=call["id"], name=call["name"]))

    # Out of steps: an answer from what it has
    messages.append(HumanMessage("You're out of steps. Answer now with what you found, and say what's left."))
    answer = models.model.invoke(context.clear_old_tool_results(messages), quiet)
    if count:
        count(answer)
    return shorten(f"(The helper stopped after {steps} steps.)\n{answer.text.strip()}", MAX_ANSWER_CHARS)


TOOLS = [task]

PROMPT = (
    "For a search or investigation that needs many reads whose details you won't need (where "
    "something is handled across a big codebase, which files use a function), use `task`: a "
    "helper with read-only tools does it in its own context and returns only its conclusion. "
    "It can't see this conversation, so give it a complete prompt. For a file or two, read "
    "them yourself."
)
