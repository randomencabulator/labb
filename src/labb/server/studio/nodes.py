"""The kinds of steps a workflow is made of, and the checks a workflow has to pass.

A workflow's definition is JSON:

    {"nodes": [{"id": "n1", "key": "research", "type": "agent", "label": "Research",
                "position": {"x": 0, "y": 0}, "config": {...}}, ...],
     "edges": [{"id": "e1", "source": "n1", "target": "n2", "handle": null}, ...]}

`key` names a step's output for the steps after it (`{{research}}`). Edges leave a step from one
of its handles: most steps have one (`null`); a condition has one per branch, an approval
"approved" and "rejected". The catalog is also what the web chat's builder draws its palette and
forms from (`GET /studio/catalog`).
"""

import re

from labb.server.studio.template import FILTERS, references, roots
from labb.server import limits

KEY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
MAX_TEXT = 20_000
MAX_CODE = 20_000
TRIGGER_KEY = "trigger"
# Names a template can use besides step keys
BUILT_IN = {"trigger", "run", "now"}
LOOP_NAMES = {"item", "index"}
EVENTS = {"document.added": "A document is added"}
INPUT_TYPES = ("text", "long_text", "number", "boolean")
OPERATORS = {
    "equals": "is", "not_equals": "isn't", "contains": "contains", "not_contains": "doesn't contain",
    "starts_with": "starts with", "empty": "is empty", "not_empty": "isn't empty",
    "greater": "is more than", "less": "is less than",
}
# What a for-each can do for each item
LOOP_STEPS = ("agent", "prompt", "skill", "tool", "action", "python", "transcribe", "speak")
# The tools a tool step may call (the web chat's; no files or shell on the server)
TOOLS = ("web_search", "fetch_page", "search_documents", "save_document", "remember", "forget")


def field(name: str, label: str, kind: str, *, required: bool = False, help: str = "", default=None, **extra) -> dict:
    return {"name": name, "label": label, "kind": kind, "required": required, "help": help, "default": default, **extra}


TEMPLATE_HELP = "Use {{trigger.name}} for the run's inputs, or {{step_key}} for a step's output."

CATALOG: dict[str, dict] = {
    "trigger": {
        "label": "Start", "category": "trigger", "icon": "play",
        "description": "How the workflow starts: by hand with these inputs, and optionally on a schedule, from a webhook or on an event.",
        "fields": [
            field("inputs", "Inputs", "inputs", help="Asked for when it's run by hand; a webhook's JSON fills them by name."),
            field("schedule", "Schedule", "schedule", help="Runs the published version at these times."),
            field("webhook", "Webhook", "boolean", default=False, help="A secret URL that starts a run with a POST."),
            field("events", "Events", "events", default=[], help="Start a run when something happens."),
        ],
    },
    "agent": {
        "label": "Agent", "category": "ai", "icon": "agent",
        "description": "One of your agents (or labb) answers a message, with its own tools, documents and skills.",
        "fields": [
            field("agent_id", "Agent", "agent", help="Empty: labb, the default assistant."),
            field("prompt", "Message", "template", required=True, rows=6, help=TEMPLATE_HELP),
            field("model", "Model", "model", help="Empty: the agent's model."),
        ],
    },
    "prompt": {
        "label": "Prompt", "category": "ai", "icon": "sparkle",
        "description": "A single model call: no tools, quick and predictable. Can answer in fields for the next steps.",
        "fields": [
            field("prompt", "Prompt", "template", required=True, rows=6, help=TEMPLATE_HELP),
            field("system", "Instructions", "template", rows=3, help="How to answer (optional)."),
            field("model", "Model", "model", help="Empty: the server's default."),
            field("format", "Answer as", "select", default="text", options=[["text", "Text"], ["json", "Fields (JSON)"]]),
            field("fields", "Fields", "fields", help="With 'Fields': what the answer has, e.g. title, summary, urgent."),
        ],
    },
    "skill": {
        "label": "Skill", "category": "ai", "icon": "skill",
        "description": "labb follows one of your skills for this message.",
        "fields": [
            field("skill", "Skill", "skill", required=True),
            field("prompt", "Message", "template", required=True, rows=5, help=TEMPLATE_HELP),
            field("model", "Model", "model"),
        ],
    },
    "transcribe": {
        "label": "Transcribe", "category": "ai", "icon": "transcribe",
        "description": "Write down what's said in a recording, with the server's model that listens.",
        "fields": [
            field("audio", "Recording", "template", required=True, rows=2,
                  help="A link to it (https://…), a Speak step's {{key.url}}, or a data: URL from a webhook."),
            field("language", "Language", "template", help="e.g. en or de; empty: it hears which."),
        ],
    },
    "speak": {
        "label": "Speak", "category": "ai", "icon": "speak",
        "description": "Read text aloud with the server's model that speaks. The output's url plays in a chat message.",
        "fields": [
            field("text", "Text", "template", required=True, rows=5, help=TEMPLATE_HELP),
            field("voice", "Voice", "template", help="Empty: the server's voice."),
        ],
    },
    "tool": {
        "label": "Tool", "category": "tools", "icon": "tool",
        "description": "Call one of labb's tools directly: search the web, read a page, search or save your documents, remember.",
        "fields": [
            field("tool", "Tool", "tool", required=True),
            field("args", "Arguments", "args", help=TEMPLATE_HELP),
        ],
    },
    "action": {
        "label": "Action", "category": "tools", "icon": "bolt",
        "description": "One of the server's custom actions: an HTTP request or a saved prompt.",
        "fields": [
            field("action_id", "Action", "action", required=True),
            field("args", "Inputs", "args", help=TEMPLATE_HELP),
        ],
    },
    "python": {
        "label": "Python", "category": "tools", "icon": "code",
        "description": "Run Python in the sandbox (no network, limited time and memory). `inputs` has the run so far; set `result`.",
        "fields": [
            field("code", "Code", "code", required=True, rows=12,
                  default="# inputs: the trigger's inputs and every step's output so far\nresult = inputs[\"trigger\"]\n"),
            field("timeout", "Time limit (seconds)", "number", default=30, min=1, max=120),
        ],
    },
    "condition": {
        "label": "If / else", "category": "logic", "icon": "branch",
        "description": "Go one way or another: by rules, or by asking a model to choose.",
        "fields": [
            field("mode", "Decide by", "select", default="rules", options=[["rules", "Rules"], ["model", "Asking a model"]]),
            field("rules", "Rules", "rules", default=[{"left": "", "op": "contains", "right": ""}], show={"mode": "rules"}),
            field("match", "Go 'true' when", "select", default="all", options=[["all", "All rules match"], ["any", "Any rule matches"]],
                  show={"mode": "rules"}),
            field("question", "Question", "template", rows=3, show={"mode": "model"}, help=TEMPLATE_HELP),
            field("choices", "Choices", "choices", default=["yes", "no"], show={"mode": "model"},
                  help="One branch per choice; the model picks one."),
            field("model", "Model", "model", show={"mode": "model"}),
        ],
    },
    "foreach": {
        "label": "For each", "category": "logic", "icon": "repeat",
        "description": "Do a step for every item of a list. Use {{item}} and {{index}} in it; the output is the list of results.",
        "fields": [
            field("items", "Items", "template", required=True, rows=2,
                  help="A list: {{step}} with a list, or text with one item per line."),
            field("step_type", "For each item", "select", default="prompt",
                  options=[["agent", "Agent"], ["prompt", "Prompt"], ["skill", "Skill"], ["tool", "Tool"],
                           ["action", "Action"], ["python", "Python"], ["transcribe", "Transcribe"], ["speak", "Speak"]]),
            field("step", "Step", "loop_step"),
            field("parallel", "Several at once", "boolean", default=False),
            field("max", "At most", "number", default=20, min=1, max=50),
        ],
    },
    "merge": {
        "label": "Merge", "category": "logic", "icon": "merge",
        "description": "Waits for the steps before it and puts their outputs together, by key.",
        "fields": [],
    },
    "approval": {
        "label": "Approval", "category": "logic", "icon": "check",
        "description": "Pause until you approve or reject in the web chat; each answer has its own way out.",
        "fields": [field("message", "What to approve", "template", required=True, rows=4, help=TEMPLATE_HELP)],
    },
    "wait": {
        "label": "Wait", "category": "logic", "icon": "clock",
        "description": "Pause for a while, then go on (also after a restart).",
        "fields": [field("minutes", "Minutes", "number", required=True, default=60, min=1, max=60 * 24 * 30)],
    },
    "transform": {
        "label": "Transform", "category": "logic", "icon": "shuffle",
        "description": "Make text or JSON out of the outputs so far, without a model.",
        "fields": [
            field("template", "Result", "template", required=True, rows=6, help=TEMPLATE_HELP + " Filters: | " + " | ".join(FILTERS)),
            field("format", "As", "select", default="text", options=[["text", "Text"], ["json", "JSON"]]),
        ],
    },
    "chat": {
        "label": "Write to chat", "category": "output", "icon": "chat",
        "description": "Put a message in a new chat in your list, marked unread.",
        "fields": [
            field("title", "Chat title", "template", required=True, help=TEMPLATE_HELP),
            field("message", "Message", "template", required=True, rows=6),
        ],
    },
    "document": {
        "label": "Save document", "category": "output", "icon": "document",
        "description": "Save text as one of your documents, searchable like the others.",
        "fields": [
            field("name", "Name", "template", required=True, help="e.g. Report {{now}}.md"),
            field("content", "Content", "template", required=True, rows=6),
        ],
    },
    "page": {
        "label": "Web page", "category": "output", "icon": "page",
        "description": "labb builds a web page (a dashboard, a report, a diagram) from your prompt and publishes it. "
                       "The output has its url, for a later step to send. Needs the server's spawner and labb-sites.",
        "fields": [
            field("prompt", "What it shows", "template", required=True, rows=6,
                  help="What the page is and the data it shows, e.g. a chart of {{sales}}. " + TEMPLATE_HELP),
            field("title", "Title", "template", help="Its title; empty: labb names it."),
            field("same_page", "Keep one page", "boolean", default=False,
                  help="Each run updates the same page, so its link stays the same; off: a new page each run."),
            field("model", "Model", "model", help="Empty: the server's default."),
        ],
    },
    "respond": {
        "label": "Result", "category": "output", "icon": "flag",
        "description": "The run's result: what the terminal prints and a webhook caller gets back.",
        "fields": [field("value", "Result", "template", required=True, rows=4)],
    },
}
CATEGORIES = [["trigger", "Start"], ["ai", "AI"], ["tools", "Tools"], ["logic", "Logic"], ["output", "Output"]]


def handles(node: dict) -> list[str | None]:
    """The ways out of a step: None for the plain one"""

    config = node.get("config") or {}
    if node.get("type") == "condition":
        if config.get("mode") == "model":
            return [str(c) for c in config.get("choices") or []]
        return ["true", "false"]
    if node.get("type") == "approval":
        return ["approved", "rejected"]
    return [None]


def defaults(node_type: str) -> dict:
    return {f["name"]: f["default"] for f in CATALOG[node_type]["fields"] if f["default"] is not None}


# --- Graph ---------------------------------------------------------------------------

def index(definition: dict) -> tuple[dict, dict, dict]:
    """Nodes by id, and each node's incoming and outgoing edges"""

    nodes = {n["id"]: n for n in definition.get("nodes", [])}
    incoming = {nid: [] for nid in nodes}
    outgoing = {nid: [] for nid in nodes}
    for edge in definition.get("edges", []):
        if edge.get("source") in nodes and edge.get("target") in nodes:
            outgoing[edge["source"]].append(edge)
            incoming[edge["target"]].append(edge)
    return nodes, incoming, outgoing


def ancestors(definition: dict) -> dict[str, set[str]]:
    """For each node, the ids of the nodes before it"""

    nodes, incoming, _ = index(definition)
    found: dict[str, set[str]] = {}

    def visit(nid: str, seen: frozenset) -> set[str]:
        if nid in found:
            return found[nid]
        result = set()
        for edge in incoming[nid]:
            if edge["source"] not in seen:
                result |= {edge["source"]} | visit(edge["source"], seen | {nid})
        found[nid] = result
        return result

    for nid in nodes:
        visit(nid, frozenset())
    return found


def has_cycle(definition: dict) -> bool:
    nodes, _, outgoing = index(definition)
    state: dict[str, int] = {}

    def visit(nid: str) -> bool:
        state[nid] = 1
        for edge in outgoing[nid]:
            target = edge["target"]
            if state.get(target) == 1 or (target not in state and visit(target)):
                return True
        state[nid] = 2
        return False

    return any(nid not in state and visit(nid) for nid in nodes)


def trigger_of(definition: dict) -> dict | None:
    return next((n for n in definition.get("nodes", []) if n.get("type") == "trigger"), None)


# --- Checks --------------------------------------------------------------------------

def check(definition: dict, lookups: "Lookups | None" = None) -> list[dict]:
    """What's wrong with a workflow, as [{"node": id or None, "message": ...}]; empty if it can run.
    `lookups` checks that the agents, skills, actions and models it names exist."""

    problems: list[dict] = []
    say = lambda node, message: problems.append({"node": node, "message": message})
    nodes_list = definition.get("nodes", [])
    if not isinstance(nodes_list, list) or not isinstance(definition.get("edges", []), list):
        return [{"node": None, "message": "A workflow is a list of nodes and a list of edges"}]
    if len(nodes_list) > (most := limits.get("workflow_nodes")):
        say(None, f"A workflow has at most {most} steps")
    triggers = [n for n in nodes_list if n.get("type") == "trigger"]
    if len(triggers) != 1:
        say(None, "A workflow has exactly one Start step")
    nodes, incoming, outgoing = index(definition)
    keys: dict[str, str] = {}
    for node in nodes_list:
        nid = node.get("id")
        if node.get("type") not in CATALOG:
            say(nid, f"There's no kind of step called {node.get('type')}")
            continue
        key = node.get("key", "")
        if not KEY.match(key or ""):
            say(nid, "A step's key is lowercase letters, digits and underscores, starting with a letter")
        elif key in keys:
            say(nid, f"Two steps are called {key}")
        elif key in BUILT_IN - {TRIGGER_KEY} or key in LOOP_NAMES or (key == TRIGGER_KEY and node["type"] != "trigger"):
            say(nid, f"{key} is a name labb uses; pick another")
        keys[key] = nid
    if has_cycle(definition):
        say(None, "The steps go round in a circle; a workflow has to go one way")
        return problems
    for edge in definition.get("edges", []):
        if edge.get("source") not in nodes or edge.get("target") not in nodes:
            say(None, "A connection points to a step that isn't there")
        elif nodes[edge["target"]].get("type") == "trigger":
            say(edge["target"], "Nothing can lead into the Start step")
        elif edge.get("handle") not in handles(nodes[edge["source"]]):
            say(edge["source"], f"This step has no way out called {edge.get('handle')}")
    before = ancestors(definition)
    for nid, node in nodes.items():
        if node.get("type") not in CATALOG:
            continue
        if node["type"] != "trigger" and not incoming[nid]:
            say(nid, "Nothing leads to this step; connect it")
        _check_config(node, say, lookups)
        visible = {nodes[a].get("key") for a in before.get(nid, set())} | BUILT_IN
        if node["type"] == "foreach":
            visible = visible | LOOP_NAMES
        settings = scanned(node)
        for name in sorted(roots(settings) - visible):
            if name in keys:
                say(nid, f"{{{{{name}}}}} comes after this step; a step can only use the ones before it")
            elif name == "secrets":
                say(nid, "Secrets are only for HTTP actions, which fill them in themselves")
            else:
                say(nid, f"{{{{{name}}}}}: there's no step called {name}")
        for path in references(settings):
            if path.startswith("trigger.") and triggers:
                wanted = path.split(".")[1]
                names = {i.get("name") for i in (triggers[0].get("config") or {}).get("inputs") or []}
                if wanted not in names and wanted not in ("body", "event"):
                    say(nid, f"{{{{{path}}}}}: the Start step has no input called {wanted}")
    return problems


def scanned(node: dict) -> dict:
    """A step's settings that can hold {{references}}: not Python code, where {{ is just text"""

    config = dict(node.get("config") or {})
    config.pop("code", None)
    if isinstance(config.get("step"), dict):
        config["step"] = {k: v for k, v in config["step"].items() if k != "code"}
    return config


def _blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or value == [] or value == {}


def _check_config(node: dict, say, lookups: "Lookups | None"):
    nid, kind = node["id"], node["type"]
    config = node.get("config") or {}
    if not isinstance(config, dict):
        say(nid, "This step's settings aren't readable")
        return
    for f in CATALOG[kind]["fields"]:
        shown = all(config.get(k, defaults(kind).get(k)) == v for k, v in (f.get("show") or {}).items())
        if f["required"] and shown and _blank(config.get(f["name"])):
            say(nid, f"{CATALOG[kind]['label']}: {f['label']} is missing")
        value = config.get(f["name"])
        if isinstance(value, str) and len(value) > (MAX_CODE if f["kind"] == "code" else MAX_TEXT):
            say(nid, f"{f['label']} is too long")
    if kind == "trigger":
        names = set()
        for item in config.get("inputs") or []:
            name = item.get("name", "")
            if not KEY.match(name or ""):
                say(nid, "An input's name is lowercase letters, digits and underscores")
            elif name in names:
                say(nid, f"Two inputs are called {name}")
            names.add(name)
            if item.get("type", "text") not in INPUT_TYPES:
                say(nid, f"An input is {', '.join(INPUT_TYPES)}")
        for event in config.get("events") or []:
            if event not in EVENTS:
                say(nid, f"There's no event called {event}")
    elif kind == "condition":
        if config.get("mode", "rules") == "model":
            choices = [str(c).strip() for c in config.get("choices") or []]
            if len(choices) < 2 or len(set(choices)) != len(choices) or any(not c for c in choices):
                say(nid, "A choice needs at least two different, non-empty options")
        else:
            for rule in config.get("rules") or []:
                if rule.get("op") not in OPERATORS:
                    say(nid, f"There's no rule '{rule.get('op')}'")
    elif kind == "tool" and config.get("tool") and config["tool"] not in TOOLS:
        say(nid, f"There's no tool called {config['tool']} for workflows")
    elif kind == "foreach":
        if config.get("step_type", "prompt") not in LOOP_STEPS:
            say(nid, "A for-each runs an agent, prompt, skill, tool, action, Python, Transcribe or Speak")
        else:
            inner = {"id": nid, "type": config.get("step_type", "prompt"), "config": config.get("step") or {}}
            _check_config(inner, say, lookups)
    if lookups:
        lookups.check(node, config, say)


class Lookups:
    """What a check looks up: the agents, skills, actions and models a user can use"""

    def __init__(self, agents=None, skills=None, actions=None, models=None, sandbox: bool = True, speech: dict | None = None):
        self.agents, self.skills, self.actions, self.models, self.sandbox = agents, skills, actions, models, sandbox
        # What speech.available() says: {"listen": bool, "speak": bool}; None doesn't check
        self.speech = speech

    def check(self, node: dict, config: dict, say):
        nid, kind = node["id"], node["type"]
        if kind == "agent" and config.get("agent_id") and self.agents is not None and config["agent_id"] not in self.agents:
            say(nid, "That agent isn't there any more, or isn't shared with you")
        if kind == "skill" and config.get("skill") and self.skills is not None and config["skill"] not in self.skills:
            say(nid, f"You have no skill called {config['skill']}")
        if kind == "action" and config.get("action_id") and self.actions is not None and config["action_id"] not in self.actions:
            say(nid, "That action isn't there any more")
        if kind == "transcribe" and self.speech is not None and not self.speech.get("listen"):
            say(nid, "Transcribe steps need a model that listens: an admin picks one in Settings → Models → Speech")
        if kind == "speak" and self.speech is not None and not self.speech.get("speak"):
            say(nid, "Speak steps need a model that speaks: an admin picks one in Settings → Models → Speech")
        if kind == "python" and not self.sandbox:
            say(nid, "Python steps need the sandbox, which this server doesn't have (SANDBOX_URL)")
        model = config.get("model")
        if model and ":" not in model:
            say(nid, f'"{model}" isn\'t "<provider>:<model>"')

