""""Describe it": a model drafts a workflow from a few sentences, for the builder to show and the
person to adjust. Bigger models (OpenAI, Anthropic) do this much better than small local ones.

The model is told each setting's exact form, and which agents, skills, actions and models the
person has. Models still answer with what they know from elsewhere ("default" for a model, an
agent's name, a cron schedule, `"mode": "ai"`), so `normalize` cleans every setting by its kind:
a value that names something real becomes its id, one that can't be used is left out so the
setting has its default, and the draft is then checked like any other."""

import json
import logging
import re
import threading
from datetime import datetime, timezone
from typing import NamedTuple
from zoneinfo import ZoneInfo

from labb.server.studio import engine, nodes, store
from labb.server.studio.steps import Step, StepError, ask_model

log = logging.getLogger(__name__)

MAX_DESCRIPTION = 4000
X_STEP, Y_STEP = 280, 160
# Words models use for "the default"
DEFAULTS = {"", "default", "none", "null", "auto", "labb", "default assistant", "the default assistant", "assistant"}


class DescribeError(ValueError):
    """A description that didn't give a workflow, said for the person"""


class Choices(NamedTuple):
    """What a draft may name: the person's agents, skills and actions, the models they can
    pick, the tools a tool step has (name -> argument names), and their time zone"""

    agents: list[dict] = []
    skills: list[dict] = []
    actions: list[dict] = []
    models: list[str] = []
    tools: dict[str, list[str]] = {}
    tz: str = "UTC"


def tool_args() -> dict[str, list[str]]:
    from labb.tools import tools_by_name

    return {name: [a for a in tools_by_name[name].args if a != "config"] for name in nodes.TOOLS}


def choices_for(user: dict, tz: str | None = None) -> Choices:
    from labb import skills
    from labb.server import agents, providers

    try:
        models = providers.offered(user["role"])
    except Exception:
        log.exception("Couldn't list the models for a draft")
        models = []
    return Choices(
        agents=[{"id": a["id"], "name": a["name"], "description": a.get("description", "")} for a in agents.list_for(user["id"])],
        skills=[{"name": s["name"], "description": s.get("description", "")}
                for s in skills.available({"configurable": {"user_id": user["id"]}})],
        actions=[{"id": a["id"], "name": a["name"], "description": a.get("description", ""),
                  "inputs": [i.get("name") for i in (a.get("config") or {}).get("inputs") or [] if i.get("name")]}
                 for a in store.list_actions(user["id"])],
        models=models, tools=tool_args(), tz=tz if _zone(tz) else "UTC",
    )


def _zone(tz: str | None) -> bool:
    try:
        return bool(tz) and ZoneInfo(tz) is not None
    except (ValueError, KeyError):
        return False


# --- What the model is told ------------------------------------------------------------

def _kind_text(f: dict, choices: Choices) -> str:
    """How to write one setting"""

    kind = f["kind"]
    if kind == "select":
        return "one of " + ", ".join(f'"{v}" ({label})' for v, label in f["options"])
    return {
        "template": "text; may use {{references}}",
        "code": "Python code",
        "number": "a number" + (f" from {f['min']} to {f['max']}" if "min" in f and "max" in f else ""),
        "boolean": "true or false",
        "model": 'leave it out for the default model; only if the description asks for a model: one of ' + (
            ", ".join(f'"{m}"' for m in choices.models) or "none"),
        "agent": 'leave it out for labb, the default assistant; otherwise the id of one of the agents below',
        "skill": "the name of one of the skills below",
        "tool": "one of the tools below",
        "action": "the id of one of the actions below",
        "args": 'an object of the tool\'s or action\'s arguments, e.g. {"query": "{{trigger.topic}}"}',
        "inputs": 'a list like [{"name": "topic", "label": "Topic", "type": "text", "required": true}]; '
                  "type is " + ", ".join(nodes.INPUT_TYPES),
        "schedule": 'leave it out unless it runs at set times; e.g. {"kind": "daily", "time": "08:00"}, '
                    '{"kind": "weekdays", "time": "08:00"}, {"kind": "weekly", "days": [0, 4], "time": "08:00"} '
                    '(Monday is 0), {"kind": "monthly", "day": 1, "time": "09:00"}, {"kind": "hours", "every": 2}, '
                    '{"kind": "minutes", "every": 30}',
        "events": "a list of: " + ", ".join(f'"{k}" ({v})' for k, v in nodes.EVENTS.items()),
        "fields": 'a list like [{"name": "total", "description": "the amount due"}]',
        "rules": 'a list like [{"left": "{{step}}", "op": "contains", "right": "urgent"}]; op is one of '
                 + ", ".join(nodes.OPERATORS),
        "choices": 'a list of at least two words, e.g. ["yes", "no"]',
        "loop_step": "the settings of the step to run for each item (the kind named by step_type), as an object",
    }.get(kind, "text")


def _catalog_text(choices: Choices) -> str:
    lines = []
    for kind, spec in nodes.CATALOG.items():
        lines.append(f"- {kind}: {spec['description']}")
        for f in spec["fields"]:
            extra = []
            if f["required"]:
                extra.append("required")
            if f["default"] is not None and f["kind"] not in ("code", "rules"):
                extra.append(f"default {json.dumps(f['default'])}")
            if f.get("show"):
                extra.append("only when " + ", ".join(f'{k} is "{v}"' for k, v in f["show"].items()))
            lines.append(f"    - {f['name']}: {_kind_text(f, choices)}" + (f" ({'; '.join(extra)})" if extra else ""))
    return "\n".join(lines)


def _listing(title: str, items: list[str]) -> str:
    return f"{title}:\n" + ("\n".join(f"- {item}" for item in items) if items else "- (none)")


def system_prompt(choices: Choices) -> str:
    tools = [f"{name}({', '.join(args)})" for name, args in choices.tools.items()]
    agents = [f'id "{a["id"]}": {a["name"]}' + (f" ({a['description']})" if a["description"] else "") for a in choices.agents]
    skills = [s["name"] + (f": {s['description']}" if s["description"] else "") for s in choices.skills]
    actions = [f'id "{a["id"]}": {a["name"]}' + (f" ({a['description']})" if a["description"] else "")
               + (f", inputs: {', '.join(a['inputs'])}" if a["inputs"] else "") for a in choices.actions]
    event = ", ".join(f"{{{{trigger.event.{k}}}}}" for k in ("type", "document.id", "document.name"))
    return f"""You design workflows for labb, an AI assistant. A workflow is a list of steps ("nodes") and
connections ("edges"). Each node has "key" (lowercase_with_underscores, unique), "type", "label" and
"config". Exactly one node has type "trigger" and key "trigger". Edges have "source" and "target" (node
keys) and "handle": null, except from a condition ("true"/"false" for rules, or one of its choices) or an
approval ("approved"/"rejected").

Steps use earlier steps' outputs with {{{{key}}}} and {{{{key.field}}}}, the Start step's inputs with
{{{{trigger.input_name}}}} (only inputs it has), and a step in a for-each uses {{{{item}}}} and {{{{index}}}}.
A run started by an event has {event}: a document's text isn't in it, so search it with a
search_documents tool step.

Kinds of steps, and their settings ("config"). Leave out a setting to use its default; never write
"default" or a value that isn't listed here.
{_catalog_text(choices)}

{_listing("Tools for tool steps", tools)}

{_listing("The person's agents", agents)}

{_listing("The person's skills", skills)}

{_listing("Actions", actions)}

Keep it as simple as the job allows. Prefer "prompt" steps for single model calls, "agent" when tools or
research are needed."""


# --- Cleaning the answer ---------------------------------------------------------------

def _text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _word(value) -> str:
    return re.sub(r"[\s_\-]+", " ", str(value)).strip().lower()


def _boolean(value) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str) and _word(value) in ("true", "yes", "on", "1", "false", "no", "off", "0"):
        return _word(value) in ("true", "yes", "on", "1")
    return None


def _number(value, f: dict) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    number = max(f.get("min", number), min(f.get("max", number), number))
    return int(number) if number.is_integer() else number


def _key(value) -> str:
    key = re.sub(r"[^a-z0-9_]", "_", str(value).strip().lower()).strip("_")[:40]
    return key if key and key[0].isalpha() else (f"x_{key}"[:40] if key else "")


# What models write for a select's values
SELECT_ALIASES = {
    ("condition", "mode"): {"ai": "model", "llm": "model", "ask": "model", "ask model": "model", "classify": "model",
                            "choice": "model", "rule": "rules", "if": "rules"},
    ("condition", "match"): {"and": "all", "every": "all", "or": "any", "some": "any"},
    ("prompt", "format"): {"json": "json", "fields": "json", "object": "json", "structured": "json",
                           "markdown": "text", "string": "text", "plain": "text"},
    ("transform", "format"): {"object": "json", "markdown": "text", "string": "text"},
}
OPERATOR_ALIASES = {
    "=": "equals", "==": "equals", "is": "equals", "eq": "equals", "equal": "equals",
    "!=": "not_equals", "<>": "not_equals", "is not": "not_equals", "ne": "not_equals", "not equal": "not_equals",
    "includes": "contains", "has": "contains", "in": "contains", "not in": "not_contains", "excludes": "not_contains",
    "startswith": "starts_with", "begins with": "starts_with", "is empty": "empty", "blank": "empty",
    "is not empty": "not_empty", "exists": "not_empty", "present": "not_empty",
    ">": "greater", "gt": "greater", "greater than": "greater", "more than": "greater",
    "<": "less", "lt": "less", "less than": "less",
}
INPUT_TYPE_ALIASES = {"string": "text", "str": "text", "url": "text", "list": "long_text", "array": "long_text",
                      "textarea": "long_text", "paragraph": "long_text", "multiline": "long_text", "long text": "long_text",
                      "int": "number", "integer": "number", "float": "number", "bool": "boolean", "checkbox": "boolean"}
WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _select(node_type: str, f: dict, value) -> str | None:
    options = {str(v): str(v) for v, _ in f["options"]}
    options.update({_word(v): str(v) for v, _ in f["options"]})
    options.update({_word(label): str(v) for v, label in f["options"]})
    options.update(SELECT_ALIASES.get((node_type, f["name"]), {}))
    return options.get(str(value)) or options.get(_word(value))


def _by_id_or_name(value, items: list[dict], id_key: str = "id") -> str | None:
    wanted = _word(value)
    for item in items:
        if str(value) == item[id_key]:
            return item[id_key]
    for item in items:
        if wanted in (_word(item[id_key]), _word(item.get("name", ""))):
            return item[id_key]
    return None


def _model(value, choices: Choices) -> str | None:
    if not isinstance(value, str) or _word(value) in DEFAULTS:
        return None
    if value in choices.models:
        return value
    # "gpt-4o" for "openai:gpt-4o", a different case
    found = [m for m in choices.models if m.lower() == value.lower() or m.split(":", 1)[-1].lower() == value.lower()]
    return found[0] if len(found) == 1 else None


def _cron(text: str) -> dict | None:
    """A schedule from a cron line ("0 8 * * 1-5")"""

    parts = text.split()
    if len(parts) != 5:
        return None
    minute, hour, day, month, weekday = parts
    if month != "*":
        return None
    if minute.startswith("*/") and hour == "*" and day == "*" and weekday == "*" and minute[2:].isdigit():
        return {"kind": "minutes", "every": int(minute[2:])}
    if minute.isdigit() and hour.startswith("*/") and hour[2:].isdigit() and day == "*" and weekday == "*":
        return {"kind": "hours", "every": int(hour[2:])}
    if not (minute.isdigit() and hour.isdigit()):
        return None
    time = f"{int(hour):02d}:{int(minute):02d}"
    if day == "*" and weekday == "*":
        return {"kind": "daily", "time": time}
    if day == "*" and weekday in ("1-5", "mon-fri", "MON-FRI"):
        return {"kind": "weekdays", "time": time}
    if day == "*":
        days = set()
        for part in weekday.lower().split(","):
            ends = [WEEKDAYS.get(p[:3], int(p) - 1 if p.isdigit() else None) for p in part.split("-")]
            if None in ends:
                return None
            # Cron's Sunday is 0 (or 7); ours is 6
            ends = [6 if e in (-1, 6) else e for e in ends]
            days |= set(range(ends[0], ends[-1] + 1)) if len(ends) == 2 else {ends[0]}
        return {"kind": "weekly", "days": sorted(days), "time": time}
    if weekday == "*" and day.isdigit():
        return {"kind": "monthly", "day": int(day), "time": time}
    return None


def _schedule(value, tz: str) -> dict | None:
    from labb.server import tasks

    if isinstance(value, str):
        value = _cron(value)
    if not isinstance(value, dict):
        return None
    value = dict(value)
    if isinstance(value.get("kind"), str):
        value["kind"] = {"weekday": "weekdays", "day": "daily", "every day": "daily", "hourly": "hours",
                         "month": "monthly", "week": "weekly"}.get(_word(value["kind"]), _word(value["kind"]))
    if isinstance(value.get("days"), list):
        value["days"] = [WEEKDAYS.get(str(d).lower()[:3], d) for d in value["days"]]
    try:
        return tasks.clean_schedule(value, datetime.now(timezone.utc), tz)
    except Exception:
        return None


def _inputs(value) -> list[dict]:
    found, names = [], set()
    for item in value if isinstance(value, list) else []:
        item = {"name": item} if isinstance(item, str) else item
        if not isinstance(item, dict) or not (name := _key(item.get("name") or item.get("key") or "")) or name in names:
            continue
        kind = _word(item.get("type") or "text")
        kind = kind.replace(" ", "_") if kind.replace(" ", "_") in nodes.INPUT_TYPES else INPUT_TYPE_ALIASES.get(kind, "text")
        clean = {"name": name, "label": _text(item.get("label")) or name.replace("_", " ").capitalize(), "type": kind,
                 "required": bool(_boolean(item.get("required")))}
        if item.get("default") not in (None, ""):
            clean["default"] = item["default"]
        found.append(clean)
        names.add(name)
    return found


def _rules(value) -> list[dict]:
    found = []
    for rule in value if isinstance(value, list) else []:
        if not isinstance(rule, dict):
            continue
        op = rule.get("op") or rule.get("operator") or "contains"
        op = op if op in nodes.OPERATORS else OPERATOR_ALIASES.get(_word(op), OPERATOR_ALIASES.get(str(op).strip(), "contains"))
        found.append({"left": _text(rule.get("left", rule.get("value", ""))) or "", "op": op,
                      "right": _text(rule.get("right", rule.get("compare", ""))) or ""})
    return found


def _fields(value) -> list[dict]:
    found = []
    for item in value if isinstance(value, list) else []:
        item = {"name": item} if isinstance(item, str) else item
        if isinstance(item, dict) and str(item.get("name") or "").strip():
            description = item.get("description") or item.get("type") or ""
            found.append({"name": str(item["name"]).strip(), "description": _text(description) or ""})
    return found


def _args(value, names: list[str] | None) -> dict:
    if not isinstance(value, dict):
        return {}
    args = {str(k): _text(v) for k, v in value.items() if v is not None}
    return {k: v for k, v in args.items() if names is None or k in names}


def clean_config(node_type: str, raw: dict, choices: Choices) -> dict:
    """A step's settings as the builder edits them: each by its kind, without settings the step
    doesn't have or values it can't use (they get their defaults)"""

    spec = nodes.CATALOG[node_type]
    # A for-each's step is sometimes a whole node ({"type", "config"}); its kind is then the step_type
    if node_type == "foreach" and isinstance(raw.get("step"), dict) and isinstance(raw["step"].get("config"), dict):
        raw = {**raw, "step": raw["step"]["config"], "step_type": raw.get("step_type") or raw["step"].get("type")}
    config: dict = {}
    for f in spec["fields"]:
        value, kind = raw.get(f["name"]), f["kind"]
        if value is None:
            continue
        match kind:
            case "template" | "code":
                cleaned = _text(value)
            case "number":
                cleaned = _number(value, f)
            case "boolean":
                cleaned = _boolean(value)
            case "select":
                cleaned = _select(node_type, f, value)
            case "model":
                cleaned = _model(value, choices)
            case "agent":
                cleaned = None if _word(value) in DEFAULTS else _by_id_or_name(value, choices.agents)
            case "skill":
                cleaned = _by_id_or_name(value, choices.skills, "name") or _text(value)
            case "action":
                cleaned = _by_id_or_name(value, choices.actions)
            case "tool":
                cleaned = next((t for t in choices.tools if _word(t) == _word(value)), _text(value))
            case "args":
                if node_type == "tool":
                    names = choices.tools.get(config.get("tool"))
                else:
                    names = next((a["inputs"] for a in choices.actions if a["id"] == config.get("action_id")), None) or None
                cleaned = _args(value, names)
            case "inputs":
                cleaned = _inputs(value)
            case "schedule":
                cleaned = _schedule(value, choices.tz)
            case "events":
                listed = value if isinstance(value, list) else [value]
                event = lambda e: _word(str(e).replace(".", " "))
                names = {event(k): k for k in nodes.EVENTS} | {event(v): k for k, v in nodes.EVENTS.items()}
                cleaned = list(dict.fromkeys(names[event(e)] for e in listed if event(e) in names))
            case "fields":
                cleaned = _fields(value)
            case "rules":
                cleaned = _rules(value)
            case "choices":
                listed = value if isinstance(value, list) else str(value).split(",")
                cleaned = list(dict.fromkeys(str(c).strip() for c in listed if str(c).strip()))
            case "loop_step":
                cleaned = value if isinstance(value, dict) else None
            case _:
                cleaned = _text(value)
        if cleaned is not None:
            config[f["name"]] = cleaned
    if node_type == "foreach":
        step_type = config.get("step_type", "prompt")
        config["step"] = clean_config(step_type, config.get("step") or {}, choices)
    if node_type == "trigger" and config.get("schedule"):
        config["tz"] = choices.tz
    if node_type == "prompt" and config.get("fields") and "format" not in config:
        config["format"] = "json"
    return {**nodes.defaults(node_type), **config}


def _layout(definition: dict):
    """Left to right by depth, stacked by order"""

    by_id, incoming, _ = nodes.index(definition)
    depth: dict[str, int] = {}

    def of(nid: str, seen: frozenset = frozenset()) -> int:
        if nid not in depth:
            parents = [e["source"] for e in incoming[nid] if e["source"] not in seen]
            depth[nid] = 1 + max((of(p, seen | {nid}) for p in parents), default=-1)
        return depth[nid]

    rows: dict[int, int] = {}
    for node in definition["nodes"]:
        column = of(node["id"])
        node["position"] = {"x": column * X_STEP, "y": rows.get(column, 0) * Y_STEP}
        rows[column] = rows.get(column, 0) + 1


def normalize(data: dict, choices: Choices | None = None) -> dict:
    """The model's answer as a definition the builder can open"""

    choices = choices or Choices(tools=tool_args())
    raw_nodes = data.get("nodes") if isinstance(data.get("nodes"), list) else []
    found, keys = [], {}
    for i, raw in enumerate(raw_nodes):
        if not isinstance(raw, dict) or raw.get("type") not in nodes.CATALOG:
            continue
        key = re.sub(r"[^a-z0-9_]", "_", str(raw.get("key") or f"step_{i}").lower()).strip("_") or f"step_{i}"
        if raw["type"] == "trigger":
            key = nodes.TRIGGER_KEY
        if not key[0].isalpha():
            key = f"s_{key}"
        while key in keys:
            key += "_2"
        node_id = f"n{i + 1}"
        keys[str(raw.get("key"))] = node_id
        keys[key] = node_id
        config = raw.get("config") if isinstance(raw.get("config"), dict) else {}
        found.append({"id": node_id, "type": raw["type"], "key": key, "label": str(raw.get("label") or key)[:60],
                      "position": {"x": 0, "y": 0}, "config": clean_config(raw["type"], config, choices)})
    if not any(n["type"] == "trigger" for n in found):
        found.insert(0, {"id": "n0", "type": "trigger", "key": nodes.TRIGGER_KEY, "label": "Start",
                         "position": {"x": 0, "y": 0}, "config": nodes.defaults("trigger")})
    edges = []
    for raw in data.get("edges") if isinstance(data.get("edges"), list) else []:
        if not isinstance(raw, dict):
            continue
        source, target = keys.get(str(raw.get("source"))), keys.get(str(raw.get("target")))
        if source and target and source != target:
            handle = raw.get("handle")
            edges.append({"id": f"e{len(edges) + 1}", "source": source, "target": target,
                          "handle": str(handle) if handle not in (None, "", "null") else None})
    definition = {"nodes": found, "edges": edges}
    _layout(definition)
    return definition


def draft(description: str, user: dict, model: str | None = None, tz: str | None = None) -> dict:
    description = description.strip()
    if not description:
        raise DescribeError("Say what the workflow should do")
    if len(description) > MAX_DESCRIPTION:
        raise DescribeError(f"Describe it in at most {MAX_DESCRIPTION} characters")
    choices = choices_for(user, tz)
    step = Step("describe", user["id"], "Describe", "describe", threading.Event())
    try:
        answer = ask_model(f"Design a workflow that does this:\n\n{description}", step, system=system_prompt(choices),
                           model=model,
                           fields=[{"name": "name", "description": "a short name"},
                                   {"name": "emoji", "description": "one emoji"},
                                   {"name": "nodes", "description": "the list of nodes"},
                                   {"name": "edges", "description": "the list of edges"}])
    except StepError as e:
        raise DescribeError(f"The model couldn't draft it: {e}") from e
    data = answer.output if isinstance(answer.output, dict) else {}
    definition = normalize(data, choices)
    return {"name": str(data.get("name") or "New workflow")[:80], "emoji": str(data.get("emoji") or "⚙️")[:16],
            "definition": definition, "problems": nodes.check(definition, engine.lookups(user["id"]))}
