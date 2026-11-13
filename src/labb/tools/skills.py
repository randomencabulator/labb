"""Loading skills (see skills.py): the model sees their names and descriptions in the system
prompt, and loads one's instructions, then its other files, when a task calls for it."""

from langchain.tools import tool
from langchain_core.runnables import RunnableConfig

from labb import skills

# For the web chat's agent editor
LABEL = "Skills"
DESCRIPTION = "Load the skills of whoever chats (yours and the shared ones) when a task matches"


@tool
def use_skill(name: str, config: RunnableConfig = None) -> str:
    """Load a skill's instructions (and the list of its files) before doing a task it covers.

    Args:
        name: The skill's name, from the list in your instructions
    """
    skill = skills.find(config, name)
    if skill is None:
        names = ", ".join(s["name"] for s in skills.available(config)) or "none"
        return f"Error: no skill called `{name}`. The skills: {names}"
    try:
        body = skills.instructions(config, skill)
        others = [f for f in skills.files(config, skill) if f != skills.SKILL_FILE]
    except Exception as e:
        return f"Error: couldn't load the skill ({e})"
    parts = [f"# Skill: {skill['name']}", body]
    if others:
        parts.append("Its other files (read them with `read_skill_file` when the instructions point to them): "
                     + ", ".join(others))
    if "path" in skill:
        parts.append(f"Its folder is `{skill['path']}`: run its scripts from there with `run_command`.")
    return "\n\n".join(parts)


@tool
def read_skill_file(name: str, path: str, config: RunnableConfig = None) -> str:
    """Read one of a skill's files, e.g. a reference or template its instructions mention.

    Args:
        name: The skill's name
        path: The file's path in the skill, as `use_skill` listed it
    """
    skill = skills.find(config, name)
    if skill is None:
        return f"Error: no skill called `{name}`"
    try:
        text = skills.read(config, skill, path)
    except Exception as e:
        return f"Error: couldn't read it ({e})"
    if text is None:
        return f"Error: `{name}` has no file `{path}`"
    if len(text) > skills.MAX_READ_CHARS:
        text = text[:skills.MAX_READ_CHARS] + "\n… (cut)"
    return text


RUN_TOGETHER = ("use_skill", "read_skill_file")
TOOLS = [use_skill, read_skill_file]
