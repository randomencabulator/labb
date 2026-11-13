"""An engine's own tools (engine.py's `tools.custom`), and running Python for an engine.

Each is offered as custom__<name> and goes through tool_node like any other. What they do goes
through what's there already: a command tool calls `run_command`, so read-only checks, approvals,
plan mode and stopping all apply; a prompt tool's model is checked like any model of the run; an
action runs as whoever is chatting. This isn't a toolset: it's only offered by an engine.
"""

import functools
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from pydantic import Field, create_model

from labb import engine, llm

# Set by the server: Python in its sandbox as (code, inputs, timeout) -> {"result", "stdout"}, and
# a Studio action as (config, action id, inputs) -> text. The server never runs Python itself.
sandbox = None
action = None

PYTHON_SECONDS = 30
MAX_RESULT_CHARS = 20_000
TEMPLATE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\}\}")

# Runs the code with `inputs`, and writes what it set as `result`
PYTHON_WRAPPER = """\
import json, sys
code, inputs_file, result_file = sys.argv[1:4]
scope = {"__name__": "__main__", "inputs": json.load(open(inputs_file)), "result": None}
exec(compile(open(code).read(), "engine", "exec"), scope)
json.dump(scope.get("result"), open(result_file, "w"), default=str)
"""


class EngineRunError(RuntimeError):
    """An engine's step or tool that failed, said for whoever wrote it"""


def fill(text: str, values: dict, quote=str) -> str:
    """A template with `{{name}}` and `{{vars.name}}` filled in; anything unknown stays as it is"""

    def value(match):
        found = values
        for part in match.group(1).split("."):
            if not isinstance(found, dict) or part not in found:
                return match.group(0)
            found = found[part]
        return quote(found if isinstance(found, str) else json.dumps(found, ensure_ascii=False, default=str))

    return TEMPLATE.sub(value, text)


def run_python(code: str, inputs: dict, config: dict | None, timeout: int = PYTHON_SECONDS) -> object:
    """The `result` of Python code: in the server's sandbox, or in a process of its own in the terminal"""

    inputs = json.loads(json.dumps(inputs, default=str))
    if sandbox is not None:
        try:
            return sandbox(code, inputs, timeout)["result"]
        except Exception as e:
            raise EngineRunError(f"Python failed: {e}") from e
    configurable = (config or {}).get("configurable") or {}
    if not (workdir := configurable.get("workdir")):
        raise EngineRunError("Python needs the terminal, or a server with a sandbox")
    with tempfile.TemporaryDirectory(prefix="labb-engine-") as folder:
        paths = {name: Path(folder) / name for name in ("code.py", "inputs.json", "result.json")}
        paths["code.py"].write_text(code)
        paths["inputs.json"].write_text(json.dumps(inputs))
        finished, output = run_process([sys.executable, "-I", "-c", PYTHON_WRAPPER, *map(str, paths.values())], workdir, timeout,
                                       configurable.get("stop_requested"))
        if not finished:
            raise EngineRunError(f"Python {output}")
        if not paths["result.json"].exists():
            raise EngineRunError(f"Python failed:\n{output[-2000:]}")
        return json.loads(paths["result.json"].read_text())


def run_process(command: list[str], workdir: str, timeout: int, stop_requested) -> tuple[bool, str]:
    """Run a process in a group of its own, until it ends, times out or the reply is stopped: (whether
    it exited with 0, its output, or how it ended)"""

    from labb.tools.shell import kill

    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(command, cwd=workdir, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True, env=os.environ.copy())
        started = time.monotonic()
        while process.poll() is None:
            stopped = stop_requested is not None and stop_requested.is_set()
            if stopped or time.monotonic() - started > timeout:
                kill(process)
                return False, "was stopped" if stopped else f"timed out after {timeout}s"
            time.sleep(0.05)
        log.seek(0)
        text = log.read().decode("utf-8", errors="replace")
    return process.returncode == 0, text[-MAX_RESULT_CHARS:]


def _call(spec: dict, config: RunnableConfig, args: dict) -> str:
    kind = spec["kind"]
    configurable = (config or {}).get("configurable") or {}
    if kind == "command":
        from labb.tools import is_on
        from labb.tools.shell import run_command

        # Turning commands off turns this off too; it's a command, by another name
        if not is_on("run_command", config):
            return "Error: commands are turned off in this session"
        return run_command.invoke({"command": fill(spec["command"], args, shlex.quote)}, config)
    if kind == "prompt":
        spec_model = spec.get("model")
        models = llm.for_config({"configurable": {**configurable, **({"model": spec_model} if spec_model else {})}})
        reply = models.model.invoke([HumanMessage(fill(spec["prompt"], args))], config)
        if count := configurable.get("count_usage"):
            count(reply)
        return reply.text
    if kind == "action":
        if action is None:
            return "Error: actions only work on the server"
        return action(config, spec["action"], args)
    result = run_python(spec["code"], {"args": args, "workdir": configurable.get("workdir")}, config)
    return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)


def _build(spec: dict) -> StructuredTool:
    fields = {}
    for param in spec.get("params", []):
        kind = engine.PARAM_TYPES[param.get("type", "string")]
        required = param.get("required", True)
        fields[param["name"]] = (kind if required else kind | None, Field(... if required else None, description=param.get("description", "")))

    def run(config: RunnableConfig = None, **args) -> str:
        try:
            return _call(spec, config, {key: value for key, value in args.items() if value is not None})[:MAX_RESULT_CHARS]
        except EngineRunError as e:
            return f"Error: {e}"

    return StructuredTool.from_function(func=run, name=engine.CUSTOM_PREFIX + spec["name"], description=spec["description"],
                                        args_schema=create_model(f"{spec['name']}_args", **fields))


@functools.cache
def _built(specs: str) -> tuple[StructuredTool, ...]:
    return tuple(_build(spec) for spec in json.loads(specs))


def tools_of(specs: list[dict]) -> list[StructuredTool]:
    """The tools of an engine's `tools.custom`"""
    return list(_built(json.dumps(specs, sort_keys=True))) if specs else []
