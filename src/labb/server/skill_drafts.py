"""Writing a skill from a description: the Skills page's "Describe it" box. The model writes a
whole SKILL.md (or changes the one being edited), which comes back to look over before it's
saved: nothing is saved here. Its cost goes in the usage ledger like a web chat's."""

import re
import time
import uuid

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from labb import llm, usage
from labb import skills as skill_format
from labb.server import skills

MAX_ASK = 4000
# A SKILL.md being changed: more than this isn't sent
MAX_CURRENT = 40_000

SYSTEM = f"""You write skills for an AI assistant, in the open Agent Skills format. A skill teaches the assistant \
one kind of task; it's loaded when a task matches its description, or on purpose with /name.

Reply with the whole SKILL.md and nothing else: no code fence, no words before or after it. It is:

---
name: <lowercase words joined by hyphens, at most {skill_format.MAX_NAME} characters, e.g. release-notes>
description: <one or two sentences: what it does, then when to use it ("Use when …"), with the words someone would use to ask for it>
---
# <Title>

<the instructions, in Markdown>

What makes a good skill:
- Instructions specific enough to follow with no other context: numbered steps for a process, what to ask the user \
when something needed is missing, and what to check before finishing.
- The shape of the result: its sections, length and format, with a short example when the format matters.
- The judgment calls: what to leave out, what to do when sources disagree or a step fails, common mistakes to avoid.
- Short and direct: imperative sentences, no padding, no restating the description. Usually 15 to 60 lines.
- Only what the assistant can do in a chat: answer, search the web and read pages, read the user's documents, make \
web pages. It can't run scripts from the skill, so don't write any.
- The description stays under {skill_format.MAX_DESCRIPTION} characters and is valid YAML on one line (quote it if it has a colon)."""


class DraftError(ValueError):
    pass


def models_for(spec: str | None, user_id: str) -> llm.Models:
    """The model that writes it: the one picked, else the default (the tests replace it)"""
    configurable = {"user_id": user_id, **({"model": spec} if spec else {})}
    return llm.for_config({"configurable": configurable})


def _record(user_id: str, model: str, answer, started: float):
    meta = getattr(answer, "usage_metadata", None) or {}
    stats = {"model": model, "duration_ms": int((time.monotonic() - started) * 1000), "llm_calls": 1,
             "input_tokens": meta.get("input_tokens", 0), "output_tokens": meta.get("output_tokens", 0)}
    usage.record("skill-drafts", uuid.uuid4().hex, stats, user_id=user_id, source="web")


def _skill_md(text: str) -> str:
    """The SKILL.md in an answer, which may come in a code block or with words around it"""
    text = text.strip()
    if fenced := re.search(r"```(?:markdown|md|yaml)?\s*\n(---\n.*?)\n```", text, re.DOTALL):
        text = fenced.group(1)
    if (start := text.find("---\n")) > 0:
        text = text[start:]
    return text.strip() + "\n"


def draft(user_id: str, ask: str, current: str | None = None, model: str | None = None) -> dict:
    """A SKILL.md for `ask` (a new skill, or a change to `current`): {"skill_md", "name", "description"}.
    Asked again once if it isn't a valid skill; raises DraftError."""

    ask = ask.strip()
    if not ask:
        raise DraftError("Say what the skill should do")
    if len(ask) > MAX_ASK:
        raise DraftError(f"Describe it in at most {MAX_ASK:,} characters")
    if current and current.strip():
        prompt = (f"Here is a skill:\n\n{current.strip()[:MAX_CURRENT]}\n\nChange it: {ask}\n\n"
                  "Keep its name unless the change asks for another, and keep what the change doesn't touch.")
    else:
        taken = sorted(s["name"] for s in skills.list_all(user_id) if s.get("mine"))
        prompt = f"Write a skill that does this:\n\n{ask}"
        if taken:
            prompt += "\n\nThese names are taken, so pick another: " + ", ".join(taken)
    try:
        models = models_for(model, user_id)
    except Exception as e:
        raise DraftError(str(e)) from e
    chat, spec = models.model, models.spec
    messages = [SystemMessage(SYSTEM), HumanMessage(prompt)]
    for attempt in range(2):
        started = time.monotonic()
        try:
            answer = chat.invoke(messages)
        except Exception as e:
            raise DraftError(f"The model couldn't answer: {e}") from e
        _record(user_id, spec, answer, started)
        text = _skill_md(answer.text if hasattr(answer, "text") else str(answer.content))
        try:
            meta, body = skill_format.parse(text)
        except skill_format.SkillError as e:
            problem = str(e)
        else:
            if body:
                return {"skill_md": text, "name": meta["name"], "description": meta["description"]}
            problem = "It has no instructions after the front matter"
        if attempt:
            raise DraftError(f"The model didn't write a valid skill ({problem}). Try describing it differently.")
        messages += [AIMessage(text), HumanMessage(f"That isn't a valid SKILL.md: {problem}. Reply with the whole fixed SKILL.md only.")]
    raise DraftError("The model didn't answer")
