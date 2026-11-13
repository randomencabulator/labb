"""A tool for asking the user a question with choices.

The terminal asks it through the `ask_user` function in the run's config and returns
what the user picked or typed. Where nobody can answer (`labb -p`, tasks), the
model is told to go on with the option it recommended.
"""

from langchain.tools import tool
from langchain_core.runnables import RunnableConfig

NEEDS_WORKDIR = True

MAX_OPTIONS = 4


@tool
def ask_user(question: str, options: list[str], config: RunnableConfig = None) -> str:
    """Ask the user one question and wait for their answer. Use it only when the answer
    changes what you'll do and you can't find it in the files, the code or the request.

    Args:
        question: One clear question, e.g. "Should the cache live in memory or in SQLite?"
        options: 2 to 4 short answers to choose from, the one you recommend first, e.g.
            ["In memory (simplest, lost on restart)", "SQLite (survives restarts)"].
            The user can also type an answer of their own.
    """
    options = [option.strip() for option in options if option and option.strip()][:MAX_OPTIONS]
    if len(options) < 2:
        return "Not asked: give 2 to 4 options, the one you recommend first."
    ask = ((config or {}).get("configurable") or {}).get("ask_user")
    if ask is None:
        return (
            "Nobody can answer here. Go on with the option you recommended "
            f"({options[0]!r}) and say in your reply that you chose it."
        )
    answer = ask(question, options)
    if not answer:
        return "The user didn't answer. Stop and ask them in your reply instead."
    return f"The user answered: {answer}"


TOOLS = [ask_user]

PROMPT = (
    "When something you can't find out yourself decides what you'll build (which of two "
    "behaviors they want, a library to add, changing a public interface, deleting data), ask "
    "with `ask_user`: one question, 2 to 4 options, your recommendation first. Look at the code "
    "before asking, and don't ask about what has a sensible default: pick it and say so."
)
