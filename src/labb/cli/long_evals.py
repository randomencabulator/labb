"""`labb eval long`: how a model and an engine do on longer jobs, run for real in a throwaway
project (cases in long_eval_cases.py).

It measures what the context and staying-on-track work is meant to change: whether every
step asked for gets done (checked in the files), whether a reply ended with todo steps still
open, steps the model tried to drop, how often it was sent back to work and why, and what
the context cost: model calls, tokens, the share of input tokens the provider had cached, and
summaries. The results go to .labb/evals/ as JSON, to compare before and after a change.
"""

import argparse
import json
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from rich.table import Table

from labb import chats, engine, llm, runner
from labb.agent import chat_agent
from labb.cli.long_eval_cases import CASES
from labb.cli.render import console
from labb.tools import todo

COLUMNS = [
    ("checks", "Checks passed"),
    ("open_at_end", "Open steps at end"),
    ("dropped", "Steps dropped"),
    ("went_on", "Sent back"),
    ("model_calls", "Model calls"),
    ("tokens", "Tokens"),
    ("cached", "Cached"),
    ("summaries", "Summaries"),
    ("seconds", "Time"),
]


def make_project(root: Path, files: dict[str, str]):
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text)


def run_case(case: dict, spec: str, used: engine.Engine, root: Path, stop: threading.Event | None = None) -> dict:
    """Run one case in `root` and measure it"""

    make_project(root, case["files"])
    overlay = case.get("engine")
    run_engine = engine.resolve({**(used.overlay or {}), **overlay}, used.name) if overlay else used
    thread = f"eval-{uuid4()}"
    configurable = {"workdir": str(root), "model": spec, "engine": run_engine, "allow_all": True, "mode": "acceptEdits"}
    went_on: dict[str, int] = {}
    dropped = 0
    errors = []
    started = time.monotonic()
    for message in case["messages"]:
        events = []
        runner.run_reply(thread, message, stop or threading.Event(), events.append, configurable=configurable, name=False, source="eval")
        for event in events:
            if event["type"] == "continue":
                went_on[event["reason"]] = went_on.get(event["reason"], 0) + 1
            elif event["type"] == "error":
                errors.append(event["message"])
    seconds = time.monotonic() - started

    state = chat_agent.get_state({"configurable": {"thread_id": thread}}).values
    messages = state.get("messages", [])
    for message in messages:
        if message.type == "tool" and message.name == "update_todos" and "You left out" in message.text:
            dropped += 1
    replies = [m for m in messages if m.type == "ai"]
    usage = [m.usage_metadata or {} for m in replies]
    input_tokens = sum(u.get("input_tokens", 0) for u in usage)
    cached = sum((u.get("input_token_details") or {}).get("cache_read", 0) or 0 for u in usage)
    passed = [name for name, check in case["checks"] if check(root)]
    open_steps = todo.open_items(todo.load(str(root), thread))
    result = {
        "case": case["name"],
        "checks": len(passed) / len(case["checks"]),
        "failed_checks": [name for name, _ in case["checks"] if name not in passed],
        "open_at_end": len(open_steps),
        "dropped": dropped,
        "went_on": went_on,
        "model_calls": len(replies),
        "tokens": input_tokens + sum(u.get("output_tokens", 0) for u in usage),
        "cached": cached / input_tokens if input_tokens else 0.0,
        "summaries": 1 if state.get("summary") else 0,
        "summarized": state.get("summarized", 0),
        "seconds": round(seconds, 1),
        "errors": errors,
    }
    chats.delete_chat(thread)
    return result


def run_model(spec: str, cases: list[dict], used: engine.Engine, verbose: bool) -> list[dict]:
    results = []
    with console.status(f"{spec}…") as status:
        for i, case in enumerate(cases, 1):
            status.update(f"{spec}: {case['name']} ({i}/{len(cases)})")
            with tempfile.TemporaryDirectory(prefix="labb-eval-long-") as folder:
                result = run_case(case, spec, used, Path(folder))
            results.append(result)
            if verbose:
                failed = ", ".join(result["failed_checks"]) or "none"
                console.print(f"[dim]{spec} · {case['name']}: failed checks: {failed}; sent back: {result['went_on'] or 'never'}"
                              + (f"; errors: {'; '.join(result['errors'])}" if result["errors"] else "") + "[/]")
    return results


def totals(results: list[dict]) -> dict:
    count = len(results) or 1
    went_on = sum(sum(r["went_on"].values()) for r in results)
    return {
        "checks": sum(r["checks"] for r in results) / count, "open_at_end": sum(r["open_at_end"] for r in results),
        "dropped": sum(r["dropped"] for r in results), "went_on": went_on,
        "model_calls": sum(r["model_calls"] for r in results), "tokens": sum(r["tokens"] for r in results),
        "cached": sum(r["cached"] for r in results) / count, "summaries": sum(r["summaries"] for r in results),
        "seconds": sum(r["seconds"] for r in results),
    }


def report(results: dict[str, list[dict]]):
    table = Table(title="Longer jobs", title_justify="left")
    table.add_column("Model")
    for _, heading in COLUMNS:
        table.add_column(heading, justify="right")
    for label, runs in results.items():
        t = totals(runs)
        table.add_row(label, f"{t['checks']:.0%}", str(t["open_at_end"]), str(t["dropped"]), str(t["went_on"]), str(t["model_calls"]),
                      f"{t['tokens']:,}", f"{t['cached']:.0%}", str(t["summaries"]), f"{t['seconds']:.0f}s")
    console.print(table)
    console.print("[dim]Checks: what each job asked for, checked in the files. Open steps at end: todo steps still open "
                  "after the last reply. Steps dropped: times the model left open steps out of its list. Sent back: "
                  "replies that went round again (open steps, goal, …). Cached: the share of input tokens the provider "
                  "had cached (0 where it doesn't say).[/]")


def save(results: dict[str, list[dict]], folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"long-{datetime.now():%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps({"results": results, "totals": {k: totals(v) for k, v in results.items()}}, indent=2) + "\n")
    return path


def main(argv: list[str]) -> int:
    """`labb eval long …`"""

    parser = argparse.ArgumentParser(prog="labb eval long",
                                     description="Run longer jobs for real in a throwaway project and measure them.")
    parser.add_argument("what", choices=["long"], help="what to evaluate")
    parser.add_argument("-m", "--model", action="append", metavar="MODEL", help="a model to run; again for more (default: the current model)")
    parser.add_argument("--engine", action="append", metavar="NAME", help="run with this engine; again to compare them (default: the default)")
    parser.add_argument("-k", "--only", metavar="NAME", help="only the case with this name (" + ", ".join(c["name"] for c in CASES) + ")")
    parser.add_argument("-v", "--verbose", action="store_true", help="say what failed in each case")
    parser.add_argument("--output-format", choices=["text", "json"], default="text")
    args = parser.parse_args(argv)

    cases = [c for c in CASES if not args.only or c["name"] == args.only]
    if not cases:
        console.print("[red]No case has that name.[/]")
        return 1
    used_engines = []
    for name in args.engine or ["default"]:
        entry = engine.find(name, str(Path.cwd()))
        if entry is None or entry.engine is None:
            console.print(f"[red]The engine {name} can't be used{': ' + '; '.join(entry.problems) if entry else ''}.[/]")
            return 1
        used_engines.append(entry.engine)
    results = {}
    for spec in args.model or [llm.default.spec]:
        for used in used_engines:
            label = spec if used is engine.DEFAULT and not args.engine else f"{spec} · {used.name}"
            try:
                results[label] = run_model(spec, cases, used, args.verbose)
            except ValueError as e:
                console.print(f"[red]{e}[/]")
                return 1
    path = save(results, Path.cwd() / ".labb" / "evals")
    if args.output_format == "json":
        print(json.dumps(results, indent=2))
    else:
        report(results)
        console.print(f"[dim]Saved in {path}.[/]")
    return 0
