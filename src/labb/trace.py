"""What a reply did and when, for the web chat's waterfall: each model call, tool call and step of
the graph with its start and end. It's a callback handler, so it sees whatever runs under the
reply's config, nested calls and calls on other threads alike, and `parent` tells a call made from
inside another apart from one that merely ran at the same time. A failure in here can't fail a reply:
LangChain logs what a handler raises unless it sets `raise_error`.

LangChain gives a tool the config it was called with, so what a tool calls with that config (a
prompt tool's model, ask_agent) has the graph's step as its parent and not the tool. A tool holds
its thread until it returns, so what starts on that thread meanwhile is inside it.
"""

import threading
import time
from datetime import datetime, timezone

from langchain_core.callbacks import BaseCallbackHandler

# The graph's steps that only hold calls, which are shown on their own
CONTAINERS = {"llm_call", "tool_node"}
# A step quicker than this did nothing to show: `compact` runs before every model call
MIN_STEP_MS = 10
# A reply that makes more calls than this keeps its first ones; `truncated` says so
MAX_SPANS = 2000
MAX_DETAIL_CHARS = 300

# The replies being written, by thread, so they can be watched
live: dict[str, "Trace"] = {}


class Trace(BaseCallbackHandler):
    def __init__(self, asked: str = ""):
        self.asked = asked[:120]
        self.started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        self.zero = time.monotonic()
        self.spans: dict[str, dict] = {}
        # Every run's parent, the ones not shown too, to find the nearest one that is
        self.parents: dict[str, str | None] = {}
        # The tools running on each thread, innermost last
        self.open_tools: dict[int, list[str]] = {}
        self.truncated = False
        self.finished_ms: int | None = None
        self.lock = threading.Lock()

    def _now(self) -> int:
        return round((time.monotonic() - self.zero) * 1000)

    def _start(self, kind: str, run_id, parent_run_id, name: str, **fields):
        with self.lock:
            around = self.open_tools.get(threading.get_ident())
            parent = around[-1] if around else str(parent_run_id) if parent_run_id else None
            if kind == "tool":
                self.open_tools.setdefault(threading.get_ident(), []).append(str(run_id))
            if len(self.spans) >= MAX_SPANS:
                self.truncated = True
                return
            self.spans[str(run_id)] = {"id": str(run_id), "parent": parent, "kind": kind, "name": name,
                                       "start": self._now(), "end": None, "status": "running", **fields}

    def _end(self, run_id, status: str = "ok", **fields):
        with self.lock:
            around = self.open_tools.get(threading.get_ident())
            if around and around[-1] == str(run_id):
                around.pop()
            if span := self.spans.get(str(run_id)):
                span.update(end=self._now(), status=status, **fields)

    def _model_start(self, serialized, run_id, parent_run_id, metadata, kwargs):
        self.parents[str(run_id)] = str(parent_run_id) if parent_run_id else None
        model = (metadata or {}).get("ls_model_name")
        self._start("model", run_id, parent_run_id, kwargs.get("name") or model or (serialized or {}).get("name") or "model",
                    model=model, first_token=None, input_tokens=None, output_tokens=None)

    def on_chat_model_start(self, serialized, messages, *, run_id, parent_run_id=None, metadata=None, **kwargs):
        self._model_start(serialized, run_id, parent_run_id, metadata, kwargs)

    def on_llm_start(self, serialized, prompts, *, run_id, parent_run_id=None, metadata=None, **kwargs):
        self._model_start(serialized, run_id, parent_run_id, metadata, kwargs)

    def on_llm_new_token(self, token, *, run_id, **kwargs):
        with self.lock:
            span = self.spans.get(str(run_id))
            if span and span["first_token"] is None:
                span["first_token"] = self._now()

    def on_llm_end(self, response, *, run_id, **kwargs):
        usage = {}
        for generations in response.generations:
            for generation in generations:
                usage = getattr(getattr(generation, "message", None), "usage_metadata", None) or usage
        self._end(run_id, input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"))

    def on_llm_error(self, error, *, run_id, **kwargs):
        self._end(run_id, "error", error=str(error)[:MAX_DETAIL_CHARS])

    def on_tool_start(self, serialized, input_str, *, run_id, parent_run_id=None, **kwargs):
        self.parents[str(run_id)] = str(parent_run_id) if parent_run_id else None
        self._start("tool", run_id, parent_run_id, kwargs.get("name") or (serialized or {}).get("name") or "tool", detail=str(input_str)[:MAX_DETAIL_CHARS])

    def on_tool_end(self, output, *, run_id, **kwargs):
        self._end(run_id)

    def on_tool_error(self, error, *, run_id, **kwargs):
        self._end(run_id, "error", error=str(error)[:MAX_DETAIL_CHARS])

    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, metadata=None, **kwargs):
        self.parents[str(run_id)] = str(parent_run_id) if parent_run_id else None
        name = kwargs.get("name")
        if name and name == (metadata or {}).get("langgraph_node") and name not in CONTAINERS:
            self._start("step", run_id, parent_run_id, name)

    def on_chain_end(self, outputs, *, run_id, **kwargs):
        self._end(run_id)

    def on_chain_error(self, error, *, run_id, **kwargs):
        self._end(run_id, "error", error=str(error)[:MAX_DETAIL_CHARS])

    def finish(self):
        """The reply is over: what hadn't ended was cut off by a stop or a failure"""
        with self.lock:
            self.finished_ms = self._now()
            for span in self.spans.values():
                if span["end"] is None:
                    span.update(end=self.finished_ms, status="stopped")

    def to_dict(self) -> dict:
        with self.lock:
            spans = [dict(span) for span in self.spans.values()]
            parents = dict(self.parents)
            elapsed = self.finished_ms if self.finished_ms is not None else self._now()
        shown = {span["id"] for span in spans}
        for span in spans:
            # Up to the nearest run that's shown, past the graph's own wrapping
            parent = span["parent"]
            while parent and parent not in shown:
                parent = parents.get(parent)
            span["parent"] = parent
        holding = {span["parent"] for span in spans}
        quick = lambda span: (span["kind"] == "step" and span["status"] == "ok" and span["id"] not in holding
                              and span["end"] - span["start"] < MIN_STEP_MS)
        kept = sorted((span for span in spans if not quick(span)), key=lambda s: s["start"])
        return {"asked": self.asked, "started_at": self.started_at, "elapsed": elapsed, "live": self.finished_ms is None,
                "truncated": self.truncated, "spans": kept}
