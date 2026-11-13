"""Writing down how to do something as a skill, when the user asks (see skills.py)"""

from langchain.tools import tool
from langchain_core.runnables import RunnableConfig

from labb import skills

# For the web chat's agent editor (it goes with the agent's skills choice)
LABEL = "Save skills"
DESCRIPTION = "Write a new skill when the user asks"


@tool
def save_skill(name: str, description: str, instructions: str, project: bool = False, replace: bool = False,
               config: RunnableConfig = None) -> str:
    """Save a way of doing a task as a skill, for this and later conversations. Only when the
    user asks for a skill (e.g. "save this as a skill", "make a skill for our release notes").

    Args:
        name: Lowercase words joined by hyphens, e.g. "release-notes"
        description: What it does and when to use it, in one or two sentences
        instructions: The steps, as Markdown, specific enough to follow without this conversation
        project: In the terminal, save it for this project only instead of for all of the user's projects
        replace: Replace the user's skill with this name, if they asked to change it
    """
    try:
        where = skills.save(config, name, description, instructions, personal=not project, replace=replace)
    except skills.SkillError as e:
        return f"Error: {e}"
    except OSError as e:
        return f"Error: couldn't write it ({e})"
    return f"Saved the skill `{name.strip()}` ({where}). It's used from now on when a task matches, or with /{name.strip()}."


TOOLS = [save_skill]

PROMPT = (
    "When the user asks you to save how to do something as a skill, write it with `save_skill`: "
    "a short hyphenated name, a description of what it does and when to use it, and clear steps. "
    "Don't save skills they didn't ask for."
)
