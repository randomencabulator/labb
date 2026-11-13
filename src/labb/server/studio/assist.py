"""A model helps with a workflow: the questions a description leaves open, changes said in words,
a fix for a failed run, and what a workflow does in plain words.

Changes come back as a list of edits (update, add, remove, connect, disconnect) that `apply` makes
to the workflow, so steps nobody mentioned stay exactly as they were. Each edit's settings are
cleaned like a draft's (`describe.clean_config`), and the result is checked like any workflow."""

import copy
import re
import json
import threading
import uuid

from labb.server.studio import describe, engine, nodes
from labb.server.studio.steps import Step, StepError, ask_model

MAX_PROMPT = 2000
MAX_QUESTIONS = 3
MAX_OPTIONS = 4
MAX_EDITS = 30
SHOWN_CHARS = 80


class AssistError(ValueError):
    """Something the model couldn't do, said for the person"""


def _step(user: dict, key: str) -> Step:
    return Step(key, user["id"], "Studio", key, threading.Event())


def _ask(prompt: str, user: dict, key: str, *, system: str = "", model: str | None = None, fields: list | None = None):
    try:
        return ask_model(prompt, _step(user, key), system=system, model=model, fields=fields).output
    except StepError as e:
        raise AssistError(f"The model couldn't do it: {e}") from e
    except Exception as e:  # the model's provider: a wrong key, no connection
        raise AssistError(f"The model couldn't be reached: {e}") from e


def _short(value, most: int = 200) -> str:
    text = describe._text(value) or ""
    text = " ".join(text.split())
    return text if len(text) <= most else text[: most - 1] + "…"


# --- Questions before a draft ----------------------------------------------------------

QUESTION_RULES = """Before you design a workflow, find what its description leaves open that would change
the design: when it runs (and at what time), where the result goes, which agent or model does the
work, whether to ask the person before it sends or changes anything. Only ask what matters; if the
description is clear enough, ask nothing. Each question gets 2 to 4 short answers the person can pick,
the likeliest first."""


def questions(description: str, user: dict, model: str | None = None, tz: str | None = None) -> list[dict]:
    """Up to MAX_QUESTIONS questions about a description, each {question, why, options}; [] if it's clear"""

    choices = describe.choices_for(user, tz)
    answer = _ask(f"The description:\n\n{description}\n\nThe person's time zone is {choices.tz}.", user, "questions",
                  system=describe.system_prompt(choices) + "\n\n" + QUESTION_RULES, model=model,
                  fields=[{"name": "questions", "description": 'a list of {"question", "why" (one short line), "options" (a list of short answers)}, or []'}])
    found = []
    raw = answer.get("questions") if isinstance(answer, dict) else None
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or not _short(item.get("question")):
            continue
        options = [_short(o, 60) for o in item.get("options") or [] if _short(o, 60)] if isinstance(item.get("options"), list) else []
        found.append({"question": _short(item.get("question")), "why": _short(item.get("why")), "options": options[:MAX_OPTIONS]})
    return found[:MAX_QUESTIONS]


def with_answers(description: str, answers: list[dict]) -> str:
    lines = [f"- {_short(a.get('question'))}: {_short(a.get('answer'))}" for a in answers
             if isinstance(a, dict) and _short(a.get("answer"))]
    return description + ("\n\nThe person's answers to questions about it:\n" + "\n".join(lines) if lines else "")


# --- Changes in words ------------------------------------------------------------------

EDIT_RULES = """You change an existing workflow, shown below as JSON with each step by its key. Answer
with the edits that make the change, and nothing for the steps that stay as they are:
- {"op": "update", "key": "<step>", "label": "<new name, if it changes>", "config": {<only the settings that change>}, "why": "..."}
- {"op": "add", "step": {"key", "type", "label", "config"}, "after": "<the step it comes after>", "handle": "<that step's way out, if it has several>", "why": "..."}
  (it goes between that step and the ones after it)
- {"op": "remove", "key": "<step>", "why": "..."} (the steps before it are then connected to the ones after it)
- {"op": "connect", "source": "<step>", "target": "<step>", "handle": null, "why": "..."}
- {"op": "disconnect", "source": "<step>", "target": "<step>", "why": "..."}
"why" is a short line for the person, e.g. "Runs at 7:00 instead of 8:00". The trigger can be updated
but never removed or added. Keep keys as they are. Make the smallest change that does what's asked."""


def as_seen(definition: dict) -> dict:
    """A workflow as the model sees it: steps by key, without ids and positions"""

    by_id = {n["id"]: n for n in definition.get("nodes", [])}
    return {
        "steps": [{"key": n["key"], "type": n["type"], "label": n.get("label", ""), "config": n.get("config", {})}
                  for n in definition.get("nodes", [])],
        "connections": [{"source": by_id[e["source"]]["key"], "target": by_id[e["target"]]["key"], "handle": e.get("handle")}
                        for e in definition.get("edges", []) if e["source"] in by_id and e["target"] in by_id],
    }


def _ways(node: dict) -> list[str | None]:
    if node["type"] == "condition":
        config = node.get("config") or {}
        return [str(c) for c in config.get("choices") or []] if config.get("mode") == "model" else ["true", "false"]
    if node["type"] == "approval":
        return ["approved", "rejected"]
    return [None]


def _edge(source: str, handle: str | None, target: str) -> dict:
    return {"id": f"{source}-{handle or 'out'}-{target}", "source": source, "target": target, "handle": handle}


def _reaches(definition: dict, start: str, goal: str) -> bool:
    todo, seen = [start], set()
    while todo:
        nid = todo.pop()
        if nid == goal:
            return True
        if nid in seen:
            continue
        seen.add(nid)
        todo += [e["target"] for e in definition["edges"] if e["source"] == nid]
    return False


def _connect(definition: dict, source: str, handle: str | None, target: str) -> bool:
    if source == target or _reaches(definition, target, source):
        return False
    if any(e["source"] == source and e["target"] == target and e.get("handle") == handle for e in definition["edges"]):
        return False
    definition["edges"].append(_edge(source, handle, target))
    return True


def _key(definition: dict, base: str) -> str:
    clean = re.sub(r"[^a-z0-9_]", "_", base.lower()).strip("_") or "step"
    clean = clean if clean[0].isalpha() else f"s_{clean}"
    taken, key, i = {n["key"] for n in definition["nodes"]}, clean, 2
    while key in taken or key in nodes.BUILT_IN:
        key, i = f"{clean}_{i}", i + 1
    return key


def _field_label(node_type: str, name: str) -> str:
    return next((f["label"] for f in nodes.CATALOG[node_type]["fields"] if f["name"] == name), name)


def _details(node_type: str, before: dict, after: dict) -> list[dict]:
    """Which settings changed, with their values before and after, short"""

    return [{"field": _field_label(node_type, name), "before": _short(before.get(name), SHOWN_CHARS),
             "after": _short(after.get(name), SHOWN_CHARS)}
            for name in dict.fromkeys([*before, *after]) if before.get(name) != after.get(name)]


def apply(definition: dict, edits: list, choices: describe.Choices) -> tuple[dict, list[dict]]:
    """The workflow with the edits made, and what changed: [{kind, node, label, text, details}]"""

    result = copy.deepcopy(definition)
    result.setdefault("edges", [])
    changes: list[dict] = []

    def by_key(key) -> dict | None:
        return next((n for n in result["nodes"] if n["key"] == str(key or "")), None)

    def note(kind: str, node: dict, why, fallback: str, details: list | None = None):
        changes.append({"kind": kind, "node": node["id"], "label": node.get("label") or node["key"],
                        "text": _short(why) or fallback, "details": details or []})

    for edit in edits[:MAX_EDITS] if isinstance(edits, list) else []:
        if not isinstance(edit, dict):
            continue
        op, why = edit.get("op"), edit.get("why")
        if op == "update" and (node := by_key(edit.get("key"))):
            before, label = dict(node.get("config") or {}), _short(edit.get("label"), 60)
            if isinstance(edit.get("config"), dict):
                node["config"] = describe.clean_config(node["type"], {**before, **edit["config"]}, choices)
            details = _details(node["type"], before, node["config"])
            if label and label != node.get("label"):
                details.insert(0, {"field": "Name", "before": node.get("label", ""), "after": label})
                node["label"] = label
            if details:
                note("change", node, why, f"Changed {node['label']}", details)
        elif op == "add" and isinstance(raw := edit.get("step"), dict) and raw.get("type") in nodes.CATALOG \
                and raw["type"] != "trigger":
            after = by_key(edit.get("after"))
            node = {"id": f"n{uuid.uuid4().hex[:7]}", "type": raw["type"], "key": _key(result, str(raw.get("key") or raw["type"])),
                    "label": _short(raw.get("label"), 60) or nodes.CATALOG[raw["type"]]["label"],
                    "config": describe.clean_config(raw["type"], raw.get("config") if isinstance(raw.get("config"), dict) else {}, choices),
                    "position": {"x": 0, "y": 0}}
            result["nodes"].append(node)
            if after:
                handle = edit.get("handle") if edit.get("handle") in _ways(after) else _ways(after)[0]
                node["position"] = {"x": after["position"]["x"] + describe.X_STEP, "y": after["position"]["y"]}
                moved = [e for e in result["edges"] if e["source"] == after["id"] and e.get("handle") == handle]
                later = {e["target"] for e in moved}
                for other in result["nodes"]:
                    if other["id"] != node["id"] and any(_reaches(result, t, other["id"]) for t in later):
                        other["position"] = {**other["position"], "x": other["position"]["x"] + describe.X_STEP}
                result["edges"] = [e for e in result["edges"] if e not in moved]
                _connect(result, after["id"], handle, node["id"])
                for target in later:
                    _connect(result, node["id"], _ways(node)[0], target)
            else:
                right = max((n["position"]["x"] for n in result["nodes"] if n is not node), default=0)
                node["position"] = {"x": right + describe.X_STEP, "y": 0}
            note("add", node, why, f"Added {node['label']}")
        elif op == "remove" and (node := by_key(edit.get("key"))) and node["type"] != "trigger":
            incoming = [e for e in result["edges"] if e["target"] == node["id"]]
            outgoing = [e for e in result["edges"] if e["source"] == node["id"]]
            result["nodes"] = [n for n in result["nodes"] if n is not node]
            result["edges"] = [e for e in result["edges"] if node["id"] not in (e["source"], e["target"])]
            for e in incoming:
                for o in outgoing:
                    _connect(result, e["source"], e.get("handle"), o["target"])
            note("remove", node, why, f"Removed {node['label']}")
        elif op in ("connect", "disconnect") and (source := by_key(edit.get("source"))) and (target := by_key(edit.get("target"))):
            if op == "connect" and target["type"] != "trigger":
                handle = edit.get("handle") if edit.get("handle") in _ways(source) else _ways(source)[0]
                if _connect(result, source["id"], handle, target["id"]):
                    note("change", target, why, f"Connected {source['label']} → {target['label']}")
            elif op == "disconnect":
                kept = [e for e in result["edges"] if not (e["source"] == source["id"] and e["target"] == target["id"])]
                if len(kept) != len(result["edges"]):
                    result["edges"] = kept
                    note("change", target, why, f"Disconnected {source['label']} → {target['label']}")
    return result, changes


def suggest(definition: dict, request: str, user: dict, *, model: str | None = None, tz: str | None = None,
            selected: list[str] | None = None, failure: dict | None = None) -> dict:
    """The workflow changed as asked (not saved): {definition, changes, reply, problems}"""

    request = (request or "").strip()
    if not request and not failure:
        raise AssistError("Say what to change")
    if len(request) > MAX_PROMPT:
        raise AssistError(f"Say it in at most {MAX_PROMPT} characters")
    choices = describe.choices_for(user, tz)
    keys = [n["key"] for n in definition.get("nodes", []) if n["id"] in set(selected or [])]
    parts = [f"The workflow now:\n{json.dumps(as_seen(definition), ensure_ascii=False, indent=1)}"]
    if keys:
        parts.append("Change only these steps (add steps right after them if needed): " + ", ".join(keys))
    if failure:
        parts.append("Its last run failed:\n" + json.dumps(failure, ensure_ascii=False, indent=1)
                     + "\nChange the workflow so it works next time.")
    if request:
        parts.append(f"The change the person asks for: {request}")
    answer = _ask("\n\n".join(parts), user, "suggest", system=describe.system_prompt(choices) + "\n\n" + EDIT_RULES,
                  model=model, fields=[{"name": "reply", "description": "one or two sentences for the person: what you changed, and anything they must do"},
                                       {"name": "edits", "description": "the list of edits"}])
    answer = answer if isinstance(answer, dict) else {}
    changed, changes = apply(definition, answer.get("edits"), choices)
    return {"definition": changed, "changes": changes, "reply": _short(answer.get("reply"), 600),
            "problems": nodes.check(changed, engine.lookups(user["id"]))}


def failure_of(run: dict, steps: list[dict]) -> dict:
    """What a model needs to know about a failed run"""

    failed = [s for s in steps if s["status"] == "failed"]
    return {"error": run.get("error"),
            "failed_steps": [{"key": s["key"], "error": s.get("error"), "input": _short(s.get("input"), 1500)} for s in failed]}


def explain(definition: dict, user: dict, model: str | None = None) -> str:
    """What a workflow does, in a few plain sentences"""

    text = _ask(f"The workflow:\n{json.dumps(as_seen(definition), ensure_ascii=False, indent=1)}", user, "explain",
                system="You explain labb workflows to the person who owns them. Say in two to four plain sentences "
                       "what it does: when it runs, what it looks at, what it makes and where that goes, and when it "
                       "asks them. Name agents and steps as the person would. No lists, no Markdown, no step keys.",
                model=model)
    return _short(text, 1200)
