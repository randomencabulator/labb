"""Making the model's tool calls valid before they run (see the tool-call accuracy plan).

Models sample tokens without checking them against the tools' schemas, and they use names
they learned elsewhere (`search_web` for `web_search`, `file_path` for `path`). So every
reply's tool calls go through `fix` after the model call (`model.llm_call`):

1. Repair what can be repaired without the model: names (case, prefixes, aliases, close
   matches) and arguments (aliases, one unknown key for one missing one, JSON in strings,
   None for optional arguments, which strict mode sends).
2. Ask again with constrained decoding for what still fails, where the provider can
   (Ollama's `format`: the output must match a JSON schema), through `constrain`.
3. Leave the rest: `tool_node` returns the error to the model, as before.

What happened is kept in the reply's `response_metadata["tool_fixes"]` (never sent back to
a provider), for the terminal, the usage stats and `labb eval tools`.

`strict_schema` makes a tool's schema fit providers' strict modes (OpenAI's and Anthropic's
`strict`), where the provider constrains the arguments itself.
"""

import ast
import copy
import difflib
import json
import re
from typing import Callable

from langchain_core.messages import AIMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel, ValidationError

# Names models use for our tools, from other engines and their training
NAME_ALIASES = {
    "search_web": "web_search", "websearch": "web_search", "search": "web_search", "google_search": "web_search",
    "fetch": "fetch_page", "fetch_url": "fetch_page", "web_fetch": "fetch_page", "read_url": "fetch_page", "browse": "fetch_page",
    "bash": "run_command", "shell": "run_command", "execute_command": "run_command", "run_shell_command": "run_command",
    "exec": "run_command", "terminal": "run_command", "run_terminal_cmd": "run_command",
    "read": "read_file", "cat": "read_file", "view": "read_file", "open_file": "read_file",
    "ls": "list_files", "list_dir": "list_files", "list_directory": "list_files",
    "find_files": "glob", "file_search": "glob", "search_files": "grep", "ripgrep": "grep", "grep_search": "grep",
    "edit": "edit_file", "str_replace": "edit_file", "replace_in_file": "edit_file", "str_replace_editor": "edit_file",
    "write": "write_file", "create_file": "write_file", "save_file": "write_file",
    "todowrite": "update_todos", "todo_write": "update_todos", "write_todos": "update_todos", "todo": "update_todos",
    "ask": "ask_user", "ask_question": "ask_user", "askuserquestion": "ask_user",
    "memorize": "remember", "save_memory": "remember",
}
# Argument names models use for ours
ARG_ALIASES = {
    "file_path": "path", "filepath": "path", "file": "path", "filename": "path", "file_name": "path",
    "directory": "path", "dir": "path", "folder": "path",
    "cmd": "command", "script": "command", "q": "query", "search_query": "query", "search": "query",
    "link": "url", "uri": "url", "old_str": "old_string", "old_text": "old_string", "new_str": "new_string",
    "new_text": "new_string", "contents": "content", "file_text": "content", "text_content": "content",
    "regex": "pattern", "items": "todos", "todo_list": "todos",
}
# Wrappers some models put around a tool's name
NAME_PREFIXES = re.compile(r"^(functions|function|tools|tool|default_api|api)[.:/]")
CLOSE_MATCH = 0.8
MAX_NOTES = 10


def schema_of(tool) -> dict:
    """The JSON schema of the arguments the model gives a tool (no injected ones, like config)"""
    return convert_to_openai_tool(tool)["function"].get("parameters") or {"type": "object", "properties": {}}


def check(tool, args: dict) -> str | None:
    """Why `args` don't fit the tool, or None if they do"""

    if not isinstance(args, dict):
        return "the arguments aren't a JSON object"
    model = getattr(tool, "tool_call_schema", None)
    if isinstance(model, type) and issubclass(model, BaseModel):
        try:
            model.model_validate(args)
        except ValidationError as e:
            return "; ".join(f"{'.'.join(map(str, err['loc'])) or 'arguments'}: {err['msg']}" for err in e.errors()[:5])
        return None
    # A plain JSON schema (MCP tools): the required arguments and, if it says so, no others
    schema = schema_of(tool)
    if missing := [key for key in schema.get("required", []) if key not in args]:
        return "missing " + ", ".join(missing)
    if schema.get("additionalProperties") is False and (extra := [k for k in args if k not in schema.get("properties", {})]):
        return "unknown " + ", ".join(extra)
    return None


def parse_args(raw) -> dict | None:
    """Arguments the provider couldn't parse: JSON in a code fence, with trailing commas, or
    Python's quotes and True/False/None"""

    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    if not text:
        return {}
    for attempt in (text, re.sub(r",\s*([}\]])", r"\1", text)):
        try:
            value = json.loads(attempt)
            return value if isinstance(value, dict) else None
        except ValueError:
            pass
    try:
        value = ast.literal_eval(text)
        return value if isinstance(value, dict) else None
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return None


def resolve_name(name: str, offered: dict) -> str | None:
    """The offered tool a model meant by `name`, if it's clear"""

    if name in offered:
        return name
    plain = NAME_PREFIXES.sub("", (name or "").strip())
    for candidate in (plain, plain.lower(), plain.lower().replace("-", "_"), re.sub(r"(?<!^)(?=[A-Z])", "_", plain).lower()):
        if candidate in offered:
            return candidate
        alias = NAME_ALIASES.get(candidate.replace("_", "")) or NAME_ALIASES.get(candidate)
        if alias in offered:
            return alias
    close = difflib.get_close_matches(plain.lower(), list(offered), n=1, cutoff=CLOSE_MATCH)
    return close[0] if close else None


def repair_args(tool, args: dict) -> tuple[dict, list[str]]:
    """The arguments with the usual mistakes fixed, and what was fixed"""

    schema = schema_of(tool)
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    args, notes = dict(args), []

    # Strict mode sends every argument; None means "not given" for the optional ones
    for key in [k for k, v in args.items() if v is None and k in properties and k not in required]:
        del args[key]
    # Names of arguments
    for key in [k for k in args if k not in properties]:
        target = ARG_ALIASES.get(key) or ARG_ALIASES.get(key.lower())
        if target is None or target not in properties:
            close = difflib.get_close_matches(key.lower(), list(properties), n=1, cutoff=CLOSE_MATCH)
            target = close[0] if close else None
        if target and target not in args:
            args[target] = args.pop(key)
            notes.append(f"{key} → {target}")
    # One unknown argument for one missing required one: that's the one
    unknown = [k for k in args if k not in properties]
    missing = [k for k in required if k not in args]
    if properties and len(unknown) == 1 and len(missing) == 1:
        args[missing[0]] = args.pop(unknown[0])
        notes.append(f"{unknown[0]} → {missing[0]}")
    # JSON in a string where a list or object belongs
    for key, value in list(args.items()):
        kind = properties.get(key, {}).get("type")
        if kind in ("array", "object") and isinstance(value, str):
            try:
                parsed = json.loads(value)
            except ValueError:
                continue
            if isinstance(parsed, list if kind == "array" else dict):
                args[key] = parsed
                notes.append(f"{key} parsed from a string")
    # Plain strings in a list of objects with one required text field (["Fix it"] for [{"task": "Fix it"}])
    for key, value in list(args.items()):
        items = properties.get(key, {}).get("items", {})
        if isinstance(value, list) and items.get("type") == "object" and any(isinstance(v, str) for v in value):
            needed = items.get("required", [])
            if len(needed) == 1 and items.get("properties", {}).get(needed[0], {}).get("type") == "string":
                args[key] = [{needed[0]: v} if isinstance(v, str) else v for v in value]
                notes.append(f"{key}: text turned into {needed[0]}")
    return args, notes


# What `constrain` gets: ("name", [names]) for a tool's name, or ("args", tool name, JSON schema)
# for its arguments; it returns the constrained output, or None if it can't
Constrain = Callable[..., object]


def fix(message: AIMessage, tools: list, constrain: Constrain | None = None) -> AIMessage:
    """The reply with its tool calls repaired where possible (see the module's docstring)"""

    calls = [dict(call) for call in message.tool_calls]
    # Calls the provider couldn't parse (broken JSON) are dropped from tool_calls; take them back
    for bad in getattr(message, "invalid_tool_calls", None) or []:
        calls.append({"name": bad.get("name") or "", "args": bad.get("args"), "id": bad.get("id") or f"call-{len(calls)}",
                      "type": "tool_call", "unparsed": True})
    if not calls:
        return message

    offered = {tool.name: tool for tool in tools}
    fixes = {"repaired": 0, "constrained": 0, "failed": 0, "notes": {}}
    fixed_calls = []
    for call in calls:
        notes = []
        name = call["name"]
        args = call.get("args")
        if call.pop("unparsed", False):
            args = parse_args(args)
            if args is not None:
                notes.append("arguments parsed")
        resolved = resolve_name(name, offered)
        if resolved is None and constrain is not None and offered:
            picked = constrain("name", list(offered))
            if isinstance(picked, str) and picked in offered:
                resolved = picked
                fixes["constrained"] += 1
                notes.append(f"name {name or '(none)'} → {picked} (constrained)")
        elif resolved != name and resolved is not None:
            notes.append(f"{name} → {resolved}")
        if resolved is None:
            # Unknown: tool_node says so, as before
            fixes["failed"] += 1
            fixed_calls.append({**call, "args": args if isinstance(args, dict) else {}})
            continue

        tool = offered[resolved]
        if isinstance(args, dict):
            args, arg_notes = repair_args(tool, args)
            notes += arg_notes
        problem = check(tool, args) if isinstance(args, dict) else "the arguments aren't JSON"
        if problem and constrain is not None:
            regenerated = constrain("args", resolved, schema_of(tool))
            if isinstance(regenerated, dict):
                regenerated, _ = repair_args(tool, regenerated)
                if check(tool, regenerated) is None:
                    args, problem = regenerated, None
                    fixes["constrained"] += 1
                    notes.append("arguments regenerated (constrained)")
        if problem:
            fixes["failed"] += 1
        elif notes and not any("(constrained)" in note for note in notes):
            fixes["repaired"] += 1
        if notes:
            fixes["notes"][call["id"]] = notes[:MAX_NOTES]
        fixed_calls.append({**call, "name": resolved, "args": args if isinstance(args, dict) else {}})

    if not fixes["notes"] and not fixes["failed"] and not getattr(message, "invalid_tool_calls", None):
        return message
    metadata = {**message.response_metadata, "tool_fixes": fixes}
    return message.model_copy(update={"tool_calls": fixed_calls, "invalid_tool_calls": [], "response_metadata": metadata})


# --- Strict modes --------------------------------------------------------------------------

# Keywords strict modes don't take (OpenAI's and Anthropic's); the tool checks its own limits
UNSUPPORTED = {"default", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
               "minLength", "maxLength", "pattern", "minItems", "maxItems", "uniqueItems", "format",
               "minProperties", "maxProperties", "title", "examples"}


def strict_schema(schema: dict) -> dict | None:
    """The schema as strict modes want it: every object closed, every property listed as
    required, the optional ones nullable. None if it can't be made strict (open objects,
    references), so that tool goes without."""

    def convert(node, depth=0):
        if depth > 8 or not isinstance(node, dict) or "$ref" in node or "$defs" in node:
            raise ValueError("unsupported schema")
        node = {k: v for k, v in node.items() if k not in UNSUPPORTED}
        for key in ("anyOf", "allOf"):
            if key in node:
                node[key] = [convert(n, depth + 1) for n in node[key]]
        if "items" in node:
            node["items"] = convert(node["items"], depth + 1)
        if node.get("type") == "object" or "properties" in node:
            if node.get("additionalProperties") not in (None, False):
                raise ValueError("open object")
            properties = node.get("properties", {})
            required = set(node.get("required", []))
            node["properties"] = {key: convert(value, depth + 1) if key in required else nullable(convert(value, depth + 1))
                                  for key, value in properties.items()}
            node["required"] = list(properties)
            node["additionalProperties"] = False
            node["type"] = "object"
        return node

    def nullable(node):
        # anyOf rather than a list of types: both OpenAI's and Anthropic's strict modes take it
        if "anyOf" in node:
            return node if any(n.get("type") == "null" for n in node["anyOf"]) else {**node, "anyOf": [*node["anyOf"], {"type": "null"}]}
        if node.get("type") == "null":
            return node
        description = node.pop("description", None)
        wrapped = {"anyOf": [node, {"type": "null"}]}
        return {**wrapped, "description": description} if description else wrapped

    try:
        return convert(copy.deepcopy(schema))
    except ValueError:
        return None


def strict_tools(tools: list, kind: str) -> list:
    """The tools for a provider with a strict mode: as dicts with `strict` where the schema
    allows it (the others as they are)"""

    converted = []
    for tool in tools:
        definition = convert_to_openai_tool(tool)["function"]
        strict = strict_schema(definition.get("parameters") or {"type": "object", "properties": {}})
        if strict is None:
            converted.append(tool)
        elif kind == "anthropic":
            converted.append({"name": definition["name"], "description": definition.get("description", ""),
                              "input_schema": strict, "strict": True})
        else:
            converted.append({"type": "function", "function": {
                "name": definition["name"], "description": definition.get("description", ""),
                "parameters": strict, "strict": True}})
    return converted


def is_strict_error(error: Exception) -> bool:
    """A provider refusing strict mode or a strict schema (an older model, a server without it)"""
    text = str(error).lower()
    return "strict" in text or ("schema" in text and ("invalid" in text or "not supported" in text or "unsupported" in text))
