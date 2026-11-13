"""Running each kind of step. `run(node, context, step)` returns a Result: the output the next
steps see, the branch it took, its tokens and a trace for the run's view; or a Pause (an
approval, a wait) for the engine to keep the run waiting."""

import base64
import json
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from labb import chats, llm, skills, usage
from labb.agent import chat_agent
from labb.runner import StopOnRequest, run_reply, with_attachments
from labb.server import agents
from labb.server.studio import actions, nodes, sandbox
from labb.server.studio.template import TemplateError, as_text, render, render_text
from labb.server import limits

# An agent step's graph steps: recall, then compact, llm_call and tool_node for each model call
MAX_OUTPUT_CHARS = 200_000
# Set while a step saves something, so that doesn't start workflows on its own (triggers.py)
in_workflow = threading.local()


class StepError(Exception):
    """A step that failed, said for whoever built the workflow"""


class Stopped(Exception):
    """The run was stopped, or ran out of time"""


@dataclass
class Result:
    output: object = None
    handle: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    trace: list = field(default_factory=list)
    input: object = None
    steps: int = 1


@dataclass
class Pause:
    kind: str
    message: str = ""
    resume_at: str | None = None
    input: object = None


@dataclass
class Step:
    """What a step runs with: whose run it is, the workflow's name, and the stop flag"""

    run_id: str
    user_id: str
    workflow: str
    key: str
    stop: threading.Event


def chat_model(spec: str | None):
    """The model for a prompt step (the tests replace it)"""
    return llm.for_config({"configurable": {"model": spec}} if spec else None).model


def run(node: dict, context: dict, step: Step) -> Result | Pause:
    kind = node["type"]
    config = node.get("config") or {}
    try:
        runner = RUNNERS[kind]
    except KeyError:
        raise StepError(f"There's no kind of step called {kind}")
    try:
        return runner(config, context, step)
    except (TemplateError, actions.ActionError, sandbox.SandboxError) as e:
        raise StepError(str(e)) from e


def _check_stop(step: Step):
    if step.stop.is_set():
        raise Stopped()


# --- AI ------------------------------------------------------------------------------

def _agent_reply(content: str, step: Step, agent_id: str | None, model: str | None, suffix: str = "") -> Result:
    configurable = {"user_id": step.user_id, "task": {"id": step.run_id, "name": step.workflow}, "workflow": True}
    if agent_id:
        agent = agents.visible(agent_id, step.user_id)
        if agent is None:
            raise StepError("That agent isn't there any more, or isn't shared with you")
        configurable["agent"] = agents.for_run(agent)
        if agent["model"]:
            configurable["model"] = agent["model"]
    if model:
        configurable["model"] = model
    thread_id = f"wf-{step.run_id}-{step.key}{suffix}"
    events = []
    model_calls = limits.get("agent_step_model_calls")
    run_reply(thread_id, content, step.stop, events.append, configurable=configurable, name=False,
              recursion_limit=limits.recursion(model_calls), source="workflow")
    _check_stop(step)
    if error := next((e["message"] for e in events if e["type"] == "error"), None):
        if "recursion limit" in error.lower():
            error = f"Stopped after {model_calls} model calls"
        raise StepError(error)
    end = next((e for e in reversed(events) if e["type"] in ("done", "stopped")), {})
    stats = end.get("stats") or {}
    messages = chat_agent.get_state({"configurable": {"thread_id": thread_id}}).values.get("messages", [])
    reply = messages[-1].text if messages and messages[-1].type == "ai" else ""
    trace = [{"tool": e["name"], "args": e["args"]} for e in events if e["type"] == "tool_call"]
    return Result(reply, input_tokens=stats.get("input_tokens", 0), output_tokens=stats.get("output_tokens", 0),
                  trace=trace, input=content)


def agent_step(config: dict, context: dict, step: Step) -> Result:
    content = render_text(config.get("prompt", ""), context)
    return _agent_reply(content, step, config.get("agent_id"), config.get("model"), context.get("_suffix", ""))


def skill_step(config: dict, context: dict, step: Step) -> Result:
    skill_config = {"configurable": {"user_id": step.user_id}}
    skill = skills.find(skill_config, config.get("skill", ""))
    if skill is None:
        raise StepError(f"You have no skill called {config.get('skill')}")
    content = render_text(config.get("prompt", ""), context)
    attached = with_attachments(content, skills.invoke_note(skill["name"], skills.instructions(skill_config, skill)))
    result = _agent_reply(attached, step, None, config.get("model"), context.get("_suffix", ""))
    result.input = content
    return result


def _json_answer(text: str):
    """The JSON object in a model's answer, which may come in a code block or with words around it"""

    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("there's no JSON object in it")
    return json.loads(text[start:end + 1])


def _record(step: Step, model_name: str, input_tokens: int, output_tokens: int, started: float):
    stats = {"model": model_name, "duration_ms": int((time.monotonic() - started) * 1000), "input_tokens": input_tokens,
             "output_tokens": output_tokens, "llm_calls": 1}
    usage.record(f"wf-{step.run_id}", f"{step.key}-{uuid.uuid4().hex[:8]}", stats, user_id=step.user_id, source="workflow")


def ask_model(prompt: str, step: Step, *, system: str = "", model: str | None = None, fields: list | None = None) -> Result:
    """One model call; with `fields`, an answer in those fields (asked again once if it isn't JSON)"""

    chat = chat_model(model)
    instructions = system.strip()
    if fields is not None:
        wanted = ", ".join(f'"{f.get("name")}"' + (f" ({f['description']})" if f.get("description") else "")
                           for f in fields if f.get("name")) or "the fields the question asks for"
        instructions += ("\n\n" if instructions else "") + f"Answer with only a JSON object with {wanted}, nothing else."
    messages = ([SystemMessage(instructions)] if instructions else []) + [HumanMessage(prompt)]
    tokens_in = tokens_out = 0
    for attempt in range(2):
        _check_stop(step)
        started = time.monotonic()
        answer = chat.invoke(messages, config={"callbacks": [StopOnRequest(step.stop)]})
        meta = getattr(answer, "usage_metadata", None) or {}
        tokens_in += meta.get("input_tokens", 0)
        tokens_out += meta.get("output_tokens", 0)
        _record(step, model or llm.default.spec, meta.get("input_tokens", 0), meta.get("output_tokens", 0), started)
        text = answer.text if hasattr(answer, "text") else str(answer.content)
        if fields is None:
            return Result(text, input_tokens=tokens_in, output_tokens=tokens_out, input=prompt)
        try:
            return Result(_json_answer(text), input_tokens=tokens_in, output_tokens=tokens_out, input=prompt)
        except ValueError as e:
            if attempt:
                raise StepError(f"The model didn't answer in JSON ({e}): {text[:300]}")
            messages += [AIMessage(text), HumanMessage(f"That isn't valid JSON ({e}). Answer with only the JSON object.")]
    raise StepError("The model didn't answer")


def prompt_step(config: dict, context: dict, step: Step) -> Result:
    prompt = render_text(config.get("prompt", ""), context)
    system = render_text(config.get("system") or "", context)
    fields = (config.get("fields") or []) if config.get("format") == "json" else None
    return ask_model(prompt, step, system=system, model=config.get("model"), fields=fields)


# --- Tools, actions, Python ----------------------------------------------------------

def tool_step(config: dict, context: dict, step: Step) -> Result:
    from labb.tools import tools_by_name

    name = config.get("tool")
    if name not in nodes.TOOLS:
        raise StepError(f"There's no tool called {name} for workflows")
    args = {k: v for k, v in render(config.get("args") or {}, context).items() if v not in (None, "")}
    _check_stop(step)
    try:
        # A document saved here doesn't start the workflows waiting for new documents
        output = tools_by_name[name].invoke(args, config={"configurable": {"user_id": step.user_id, "workflow": True}})
    except Exception as e:
        raise StepError(f"{name}: {e}") from e
    return Result(as_text(output)[:MAX_OUTPUT_CHARS], input=args)


def action_step(config: dict, context: dict, step: Step) -> Result:
    action = actions.visible(config.get("action_id", ""), step.user_id)
    if action is None:
        raise StepError("That action isn't there any more")
    args = render(config.get("args") or {}, context)
    _check_stop(step)
    if action["kind"] == "http":
        output, recorded = actions.run_http(action, args)
        return Result(output, input=recorded)
    prompt_config = actions.render_prompt_config(action, args)
    fields = prompt_config["fields"] if prompt_config["format"] == "json" else None
    return ask_model(prompt_config["prompt"], step, system=prompt_config["system"], model=prompt_config["model"], fields=fields)


def _plain(value):
    """A value as JSON can carry it"""
    return json.loads(json.dumps(value, default=str))


def python_step(config: dict, context: dict, step: Step) -> Result:
    inputs = _plain({k: v for k, v in context.items() if not k.startswith("_")})
    _check_stop(step)
    ran = sandbox.run(config.get("code", ""), inputs, config.get("timeout") or 30)
    trace = [{"stdout": ran["stdout"][-4000:]}] if ran.get("stdout") else []
    return Result(ran["result"], trace=trace, input={"code": config.get("code", "")})


# --- Logic ---------------------------------------------------------------------------

def _number(value):
    try:
        return float(as_text(value).strip())
    except ValueError:
        return None


def _rule(rule: dict, context: dict) -> bool:
    left = render(rule.get("left", ""), context)
    right = render_text(rule.get("right", ""), context)
    op = rule.get("op")
    text = as_text(left).strip()
    if op == "empty":
        return left in (None, [], {}) or text == ""
    if op == "not_empty":
        return not (left in (None, [], {}) or text == "")
    if op in ("greater", "less"):
        a, b = _number(left), _number(right)
        if a is None or b is None:
            raise StepError(f"'{text[:40]}' and '{right[:40]}' aren't both numbers")
        return a > b if op == "greater" else a < b
    a, b = text.lower(), right.strip().lower()
    if op in ("equals", "not_equals"):
        same = a == b or (_number(left) is not None and _number(left) == _number(right))
        return same if op == "equals" else not same
    if op == "contains":
        return b in a
    if op == "not_contains":
        return b not in a
    if op == "starts_with":
        return a.startswith(b)
    raise StepError(f"There's no rule '{op}'")


def condition_step(config: dict, context: dict, step: Step) -> Result:
    if config.get("mode", "rules") == "model":
        choices = [str(c).strip() for c in config.get("choices") or []]
        question = render_text(config.get("question", ""), context)
        prompt = f"{question}\n\nAnswer with exactly one of these, and nothing else: {', '.join(choices)}"
        answer = ask_model(prompt, step, model=config.get("model"))
        said = as_text(answer.output).strip().strip(".\"'*").lower()
        chosen = next((c for c in choices if c.lower() == said), None)
        chosen = chosen or next((c for c in choices if re.search(rf"\b{re.escape(c.lower())}\b", said)), None)
        if chosen is None:
            raise StepError(f"The model answered '{said[:80]}', which isn't one of {', '.join(choices)}")
        answer.output, answer.handle = chosen, chosen
        return answer
    results = [_rule(rule, context) for rule in config.get("rules") or []]
    passed = any(results) if config.get("match") == "any" else all(results)
    return Result(passed, handle="true" if passed else "false", input={"rules": results})


def _items(value) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    text = as_text(value).strip()
    if text.startswith("["):
        try:
            loaded = json.loads(text)
            if isinstance(loaded, list):
                return loaded
        except ValueError:
            pass
    return [line.strip() for line in text.splitlines() if line.strip()]


def foreach_step(config: dict, context: dict, step: Step) -> Result:
    items = _items(render(config.get("items", ""), context))
    limit = max(1, min(int(config.get("max") or 20), 50))
    if len(items) > limit:
        raise StepError(f"There are {len(items)} items; this for-each takes at most {limit}")
    inner = {"type": config.get("step_type", "prompt"), "config": config.get("step") or {}}

    def one(index: int):
        _check_stop(step)
        result = run(inner, {**context, "item": items[index], "index": index, "_suffix": f"-{index}"}, step)
        if isinstance(result, Pause):
            raise StepError("A for-each can't wait or ask for approval")
        return result

    if config.get("parallel"):
        with ThreadPoolExecutor(limits.get("loop_parallel")) as pool:
            results = list(pool.map(one, range(len(items))))
    else:
        results = [one(i) for i in range(len(items))]
    return Result([r.output for r in results], input_tokens=sum(r.input_tokens for r in results),
                  output_tokens=sum(r.output_tokens for r in results),
                  trace=[{"item": i, "trace": r.trace} for i, r in enumerate(results) if r.trace],
                  input={"items": len(items)}, steps=len(items))


def merge_step(config: dict, context: dict, step: Step) -> Result:
    return Result(dict(context.get("_merged") or {}))


def approval_step(config: dict, context: dict, step: Step) -> Pause:
    message = render_text(config.get("message", ""), context)
    return Pause("approval", message=message, input={"message": message})


def wait_step(config: dict, context: dict, step: Step) -> Pause:
    minutes = max(1, int(config.get("minutes") or 1))
    until = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
    return Pause("wait", resume_at=until, input={"minutes": minutes})


def transform_step(config: dict, context: dict, step: Step) -> Result:
    value = render(config.get("template", ""), context)
    if config.get("format") == "json" and isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError as e:
            raise StepError(f"The result isn't JSON: {e}") from e
    return Result(value)


# --- Outputs -------------------------------------------------------------------------

def chat_step(config: dict, context: dict, step: Step) -> Result:
    title = render_text(config.get("title", ""), context).strip() or step.workflow
    message = render_text(config.get("message", ""), context)
    thread_id = f"workflow-{uuid.uuid4()}"
    chats.touch(thread_id, title, user_id=step.user_id)
    chat_agent.update_state({"configurable": {"thread_id": thread_id}},
                            {"messages": [HumanMessage(f"{step.workflow}: {title}"), AIMessage(message)]})
    chats.set_unread(thread_id, True)
    return Result({"thread_id": thread_id, "title": title}, input={"title": title})


with chats.lock, chats.conn:
    # A Web page step that keeps one page: which page it is, per workflow and step
    chats.conn.execute("CREATE TABLE IF NOT EXISTS studio_pages (workflow_id TEXT NOT NULL, step_key TEXT NOT NULL, "
                       "artifact TEXT NOT NULL, PRIMARY KEY (workflow_id, step_key))")


def _kept_page(workflow_id: str, key: str, user_id: str) -> str | None:
    """The page this step made before, if it's still there"""
    from labb.server import app_tools
    with chats.lock:
        row = chats.conn.execute("SELECT artifact FROM studio_pages WHERE workflow_id = ? AND step_key = ?",
                                 (workflow_id, key)).fetchone()
    if not row:
        return None
    try:
        app_tools.call("GET", f"/artifacts/{row[0]}", user_id)
    except ValueError:
        return None
    return row[0]


def page_step(config: dict, context: dict, step: Step) -> Result:
    """labb builds a web page (tools/artifacts.py) and publishes it: {"name", "title", "url"}"""

    from labb.server import app_tools, artifact_tools
    from labb.server.studio import store

    if not artifact_tools.enabled():
        raise StepError("This server can't build web pages: it needs the spawner and labb-sites (docker compose runs them)")
    want = render_text(config.get("prompt", ""), context).strip()
    title = render_text(config.get("title", ""), context).strip()
    if not want:
        raise StepError("Say what the page shows")
    run = store.get_run(step.run_id) or {}
    key = step.key + context.get("_suffix", "")
    kept = _kept_page(run.get("workflow_id", ""), key, step.user_id) if config.get("same_page") and run else None
    if kept:
        content = (f"Change the web page {kept!r} (it's made already: read its files with read_artifact_file) so it is this, "
                   f"then publish it with publish_artifact:\n\n{want}")
    else:
        content = (f"Make a web page{f' titled {title!r}' if title else ''} with new_artifact, write it, and publish it "
                   f"with publish_artifact:\n\n{want}")
    base = artifact_tools.server_base()
    thread_id = f"wf-{step.run_id}-{key}"
    configurable = {"user_id": step.user_id, "task": {"id": step.run_id, "name": step.workflow}, "workflow": True,
                    "artifacts": True, "sites_base": base, **({"model": config["model"]} if config.get("model") else {})}
    events = []
    model_calls = limits.get("agent_step_model_calls")
    run_reply(thread_id, content, step.stop, events.append, configurable=configurable, name=False,
              recursion_limit=limits.recursion(model_calls), source="workflow")
    _check_stop(step)
    if error := next((e["message"] for e in events if e["type"] == "error"), None):
        raise StepError(f"Stopped after {model_calls} model calls" if "recursion limit" in error.lower() else error)
    stats = (next((e for e in reversed(events) if e["type"] in ("done", "stopped")), {}).get("stats") or {})
    trace = [{"tool": e["name"], "args": e["args"]} for e in events if e["type"] == "tool_call"]
    # The page it worked on: the one kept, else the newest it made in this run
    name = kept
    if not name:
        made = app_tools.call("GET", "/artifacts", step.user_id, params={"thread": thread_id}).json()
        name = made[0]["name"] if made else None
    if not name:
        raise StepError("labb didn't make a page: " + (_last_reply(thread_id) or "it gave no reason"))
    about = app_tools.call("GET", f"/artifacts/{name}", step.user_id).json()
    if not about.get("site") or not any(t["tool"] == "publish_artifact" for t in trace):
        why = (about.get("build") or {}).get("error") or _last_reply(thread_id) or "it wasn't published"
        raise StepError(f"The page {name} didn't publish: {why[:1000]}")
    if config.get("same_page") and run:
        with chats.lock, chats.conn:
            chats.conn.execute("INSERT INTO studio_pages (workflow_id, step_key, artifact) VALUES (?, ?, ?) "
                               "ON CONFLICT (workflow_id, step_key) DO UPDATE SET artifact = excluded.artifact",
                               (run["workflow_id"], key, name))
    return Result({"name": name, "title": about.get("title") or title or name, "url": artifact_tools.url_of(base, name),
                   "updated": bool(kept)}, input_tokens=stats.get("input_tokens", 0), output_tokens=stats.get("output_tokens", 0),
                  trace=trace, input=want)


def _last_reply(thread_id: str) -> str:
    messages = chat_agent.get_state({"configurable": {"thread_id": thread_id}}).values.get("messages", [])
    return messages[-1].text if messages and messages[-1].type == "ai" else ""


def document_step(config: dict, context: dict, step: Step) -> Result:
    from labb.server import documents

    name = render_text(config.get("name", ""), context).strip()
    content = render_text(config.get("content", ""), context)
    in_workflow.on = True
    try:
        document = documents.add(step.user_id, name, content.encode())
    except documents.DocumentError as e:
        raise StepError(str(e)) from e
    finally:
        in_workflow.on = False
    return Result({"document_id": document["id"], "name": document["name"]}, input={"name": name})


# --- Speech ----------------------------------------------------------------------------

def _recording(reference: str, step: Step) -> tuple[bytes, str]:
    """A recording's audio and file name: a Speak step's clip, a data: URL, or one fetched from a
    public address (as HTTP actions are, address checked on every redirect)"""

    from urllib.parse import unquote, urljoin, urlsplit

    from labb.server import speech

    reference = reference.strip()
    if not reference:
        raise StepError("There's no recording")
    if (clip := speech.clip_of(reference, step.user_id)) is not None:
        return clip, "clip.mp3"
    if reference.startswith("data:"):
        header, _, data = reference.partition(",")
        mime = header[5:].split(";")[0].lower()
        try:
            audio = base64.b64decode(data, validate=True) if ";base64" in header else unquote(data).encode()
        except ValueError as e:
            raise StepError(f"The data: URL isn't valid base64 ({e})") from e
        return audio, "recording" + next((s for s, m in speech.AUDIO_TYPES.items() if m == mime), ".mp3")
    if not reference.startswith(("http://", "https://")):
        raise StepError("A recording is a link to it (https://…), a Speak step's {{key.url}}, or a data: URL")
    url = reference
    with httpx.Client(timeout=120, follow_redirects=False) as client:
        for _ in range(actions.MAX_REDIRECTS + 1):
            try:
                target, pinned, extensions = actions._check_address(url, False)
            except actions.ActionError as e:
                raise StepError(str(e)) from e
            with client.stream("GET", target, headers=pinned, extensions=extensions) as response:
                if response.is_redirect and "location" in response.headers:
                    url = urljoin(url, response.headers["location"])
                    continue
                if response.status_code >= 400:
                    raise StepError(f"{url}: {response.status_code}")
                audio = b""
                for chunk in response.iter_bytes():
                    audio += chunk
                    if len(audio) > speech.max_audio_bytes():
                        raise StepError(f"The recording is over {speech.max_audio_bytes() // 1_000_000} MB")
                name = urlsplit(url).path.rsplit("/", 1)[-1]
                if not speech.is_audio(name):
                    mime = response.headers.get("content-type", "").split(";")[0].strip().lower()
                    name = "recording" + next((s for s, m in speech.AUDIO_TYPES.items() if m == mime), ".mp3")
                return audio, name
    raise StepError("Too many redirects")


def transcribe_step(config: dict, context: dict, step: Step) -> Result:
    from labb.server import speech

    reference = render_text(config.get("audio", ""), context)
    language = render_text(config.get("language") or "", context).strip() or None
    audio, name = _recording(reference, step)
    _check_stop(step)
    try:
        text = speech.transcribe(audio, name, language)
    except speech.SpeechError as e:
        raise StepError(str(e)) from e
    return Result(text, input={"audio": reference[:300], "bytes": len(audio)})


def speak_step(config: dict, context: dict, step: Step) -> Result:
    from labb.server import speech

    text = speech.spoken(render_text(config.get("text", ""), context))
    voice = render_text(config.get("voice") or "", context).strip() or None
    _check_stop(step)
    try:
        clip = speech.save_clip(step.user_id, speech.speak(text, voice), len(text))
    except speech.SpeechError as e:
        raise StepError(str(e)) from e
    return Result(clip, input={"text": text[:2000], "voice": voice})


def respond_step(config: dict, context: dict, step: Step) -> Result:
    return Result(render(config.get("value", ""), context))


def trigger_step(config: dict, context: dict, step: Step) -> Result:
    return Result(context.get("trigger"))


RUNNERS = {
    "trigger": trigger_step, "agent": agent_step, "prompt": prompt_step, "skill": skill_step, "tool": tool_step,
    "action": action_step, "python": python_step, "condition": condition_step, "foreach": foreach_step,
    "merge": merge_step, "approval": approval_step, "wait": wait_step, "transform": transform_step,
    "chat": chat_step, "document": document_step, "page": page_step, "respond": respond_step,
    "transcribe": transcribe_step, "speak": speak_step,
}
