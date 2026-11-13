"""Workflows to start from, offered by Studio's "New workflow"."""

import copy

from labb.server.studio import nodes


def _node(nid: str, kind: str, key: str, label: str, x: int, y: int, **config) -> dict:
    return {"id": nid, "type": kind, "key": key, "label": label, "position": {"x": x, "y": y},
            "config": {**nodes.defaults(kind), **config}}


def _edge(source: str, target: str, handle: str | None = None) -> dict:
    return {"id": f"{source}-{handle or 'out'}-{target}", "source": source, "target": target, "handle": handle}


def blank() -> dict:
    return {"nodes": [_node("start", "trigger", "trigger", "Start", 0, 0)], "edges": []}


TEMPLATES = [
    {
        "id": "daily-digest", "name": "Daily digest", "emoji": "📰",
        "description": "Every morning, search the web for a topic, summarize the best results and put them in a chat.",
        "definition": {
            "nodes": [
                _node("start", "trigger", "trigger", "Start", 0, 0,
                      inputs=[{"name": "topic", "label": "Topic", "type": "text", "required": True, "default": "local-first software"}],
                      schedule={"kind": "daily", "time": "08:00"}),
                _node("search", "tool", "search", "Search the web", 280, 0, tool="web_search",
                      args={"query": "{{trigger.topic}} news this week", "read": "3"}),
                _node("summary", "prompt", "summary", "Summarize", 560, 0,
                      prompt="Write a short digest of these results about {{trigger.topic}}: five bullets, each with its link.\n\n{{search}}"),
                _node("post", "chat", "post", "Write to chat", 840, 0, title="Digest: {{trigger.topic}}", message="{{summary}}"),
            ],
            "edges": [_edge("start", "search"), _edge("search", "summary"), _edge("summary", "post")],
        },
    },
    {
        "id": "research-write-review", "name": "Research, write, review", "emoji": "✍️",
        "description": "An agent researches, a model writes, another checks it; it's revised once if the check fails.",
        "definition": {
            "nodes": [
                _node("start", "trigger", "trigger", "Start", 0, 80,
                      inputs=[{"name": "topic", "label": "Topic", "type": "text", "required": True}]),
                _node("research", "agent", "research", "Research", 280, 80,
                      prompt="Research {{trigger.topic}} on the web. List the key facts with their sources."),
                _node("draft", "prompt", "draft", "Write", 560, 80,
                      prompt="Write a clear 300-word explainer about {{trigger.topic}} from these notes:\n\n{{research}}"),
                _node("review", "prompt", "review", "Review", 840, 80, format="json",
                      fields=[{"name": "ok", "description": "true if it's accurate and clear"},
                              {"name": "notes", "description": "what to fix, if anything"}],
                      prompt="Check this explainer against the notes.\n\nExplainer:\n{{draft}}\n\nNotes:\n{{research}}"),
                _node("check", "condition", "check", "Good enough?", 1120, 80,
                      rules=[{"left": "{{review.ok}}", "op": "equals", "right": "true"}]),
                _node("revise", "prompt", "revise", "Revise", 1400, 180,
                      prompt="Revise this explainer:\n\n{{draft}}\n\nFix: {{review.notes}}"),
                _node("result", "respond", "result", "Result", 1400, 0, value="{{draft}}"),
                _node("revised", "respond", "revised", "Revised result", 1680, 180, value="{{revise}}"),
            ],
            "edges": [_edge("start", "research"), _edge("research", "draft"), _edge("draft", "review"),
                      _edge("review", "check"), _edge("check", "result", "true"), _edge("check", "revise", "false"),
                      _edge("revise", "revised")],
        },
    },
    {
        "id": "triage", "name": "Triage incoming requests", "emoji": "📥",
        "description": "A webhook brings a request in; a model sorts it, and urgent ones wait for your approval.",
        "definition": {
            "nodes": [
                _node("start", "trigger", "trigger", "Start", 0, 80, webhook=True,
                      inputs=[{"name": "text", "label": "Request", "type": "long_text", "required": True}]),
                _node("sort", "prompt", "sort", "Sort it", 280, 80, format="json",
                      fields=[{"name": "urgency", "description": "high, normal or low"},
                              {"name": "summary", "description": "one sentence"}],
                      prompt="Sort this request:\n\n{{trigger.text}}"),
                _node("urgent", "condition", "urgent", "Urgent?", 560, 80,
                      rules=[{"left": "{{sort.urgency}}", "op": "equals", "right": "high"}]),
                _node("ask", "approval", "ask", "Approve", 840, 0, message="Urgent: {{sort.summary}}\n\n{{trigger.text}}"),
                _node("urgent_chat", "chat", "urgent_chat", "Urgent chat", 1120, 0, title="Urgent: {{sort.summary}}", message="{{trigger.text}}"),
                _node("inbox_chat", "chat", "inbox_chat", "Inbox chat", 840, 200, title="{{sort.summary}}", message="{{trigger.text}}"),
            ],
            "edges": [_edge("start", "sort"), _edge("sort", "urgent"), _edge("urgent", "ask", "true"),
                      _edge("urgent", "inbox_chat", "false"), _edge("ask", "urgent_chat", "approved")],
        },
    },
    {
        "id": "clean-list", "name": "Clean up a list", "emoji": "🧹",
        "description": "Python in the sandbox: one item per line in, sorted and without duplicates out.",
        "definition": {
            "nodes": [
                _node("start", "trigger", "trigger", "Start", 0, 0,
                      inputs=[{"name": "items", "label": "Items, one per line", "type": "long_text", "required": True}]),
                _node("clean", "python", "clean", "Clean up", 280, 0,
                      code="lines = [l.strip() for l in inputs[\"trigger\"][\"items\"].splitlines() if l.strip()]\n"
                           "result = sorted(set(lines), key=str.lower)\n"),
                _node("result", "respond", "result", "Result", 560, 0, value="{{clean | json}}"),
            ],
            "edges": [_edge("start", "clean"), _edge("clean", "result")],
        },
    },
]


def get(template_id: str) -> dict | None:
    found = next((t for t in TEMPLATES if t["id"] == template_id), None)
    return copy.deepcopy(found) if found else None


def listing() -> list[dict]:
    return [{k: t[k] for k in ("id", "name", "emoji", "description")} for t in TEMPLATES]
