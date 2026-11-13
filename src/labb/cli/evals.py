"""`labb eval tools`: how well a model calls labb's tools.

Each prompt of `tool_eval_cases.CASES` should make the model call one tool. One model call per
prompt (no tool runs), scored three ways, like the K2VV ToolCall benchmark: as the model wrote
it, after labb's repairs, and after a constrained retry (Ollama), the steps every reply
goes through (see tool_calls.py). Run it before and after a change to see what it did.
"""

import argparse
import json
import tempfile
import time
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from rich.table import Table

from labb import engine, llm, model, tool_calls
from labb.cli.render import console
from labb.cli.tool_eval_cases import CASES, PROJECT
from labb.tools import offered_for, rules_for

# The columns of the report: (key, heading)
COLUMNS = [
    ("called", "Called a tool"),
    ("valid", "Valid as written"),
    ("right_tool", "Right tool"),
    ("right_args", "Right arguments"),
    ("repaired", "Valid after repair"),
    ("constrained", "Valid after retry"),
]


def make_project(root: Path):
    for path, text in PROJECT.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text)


def pick(calls: list[dict], expected: str) -> dict | None:
    """The call to score: the expected tool's if the model made it, else the first"""
    return next((c for c in calls if c["name"] == expected), calls[0] if calls else None)


def args_match(args: dict, expected: dict) -> bool:
    """The expected arguments are there, and contain the expected text"""

    for key, value in expected.items():
        if key not in args:
            return False
        if value is not None and value.lower() not in json.dumps(args[key], ensure_ascii=False).lower():
            return False
    return True


def score(case: dict, raw, tools: list, constrain=None) -> dict:
    """One reply's marks (see COLUMNS); `constrain` as for tool_calls.fix"""

    offered = {tool.name: tool for tool in tools}
    expected = case["tool"]
    called = bool(raw.tool_calls or getattr(raw, "invalid_tool_calls", None))
    call = pick(raw.tool_calls, expected)
    valid = bool(call) and not getattr(raw, "invalid_tool_calls", None) and call["name"] in offered \
        and tool_calls.check(offered[call["name"]], call["args"]) is None

    def good(message) -> bool:
        fixed = pick(message.tool_calls, expected)
        return bool(fixed) and fixed["name"] == expected and expected in offered \
            and tool_calls.check(offered[expected], fixed["args"]) is None and args_match(fixed["args"], case["args"])

    repaired = tool_calls.fix(raw, tools)
    marks = {
        "called": called,
        "valid": valid,
        "right_tool": bool(call) and call["name"] == expected,
        "right_args": valid and call["name"] == expected and args_match(call["args"], case["args"]),
        "repaired": good(repaired),
    }
    marks["constrained"] = marks["repaired"] or (constrain is not None and good(tool_calls.fix(raw, tools, constrain)))
    # Prompts that ask for several things: did it call them all in this one reply, so they run together
    if "together" in case:
        marks["together"] = sum(call["name"] == expected for call in raw.tool_calls) >= case["together"]
    return marks


def run_model(spec: str, cases: list[dict], repeat: int, workdir: str, verbose: bool, used: engine.Engine = engine.DEFAULT) -> dict:
    """One model's totals over the cases (each `repeat` times), with an engine's prompts and tools"""

    models = llm.load(spec)
    totals = {key: 0 for key, _ in COLUMNS} | {"runs": 0, "errors": 0, "seconds": 0.0, "together": 0, "together_runs": 0}
    with console.status(f"{spec}…") as status:
        for round_ in range(repeat):
            for i, case in enumerate(cases, 1):
                status.update(f"{spec}: {i + round_ * len(cases)}/{len(cases) * repeat}")
                config = {"configurable": ({} if case.get("web") else {"workdir": workdir}) | {"engine": used}}
                offered = offered_for(config)
                has_workdir = not case.get("web")
                tools = model.tools_of(config)
                messages = [SystemMessage(model.system_prompt(config)), HumanMessage(case["prompt"])]
                started = time.monotonic()
                try:
                    raw = model.call_model(models, messages, has_workdir, offered, rules_for(config), "auto", used.tool_shape())
                except Exception as e:
                    totals["errors"] += 1
                    if verbose:
                        console.print(f"[red]✗ {case['prompt'][:60]}: {e}[/]")
                    continue
                marks = score(case, raw, tools, model.constrainer(models, messages, raw))
                totals["seconds"] += time.monotonic() - started
                totals["runs"] += 1
                totals["together_runs"] += "together" in marks
                for key in marks:
                    totals[key] += marks[key]
                if verbose and not marks["right_args"]:
                    calls = [f"{c['name']}({json.dumps(c['args'], ensure_ascii=False)[:120]})" for c in raw.tool_calls]
                    calls += [f"(broken) {c.get('name')}: {str(c.get('args'))[:120]}" for c in raw.invalid_tool_calls or []]
                    after = "fixed" if marks["repaired"] else "retried" if marks["constrained"] else "still wrong"
                    console.print(f"[yellow]•[/] {case['prompt'][:70]}\n  [dim]wanted {case['tool']}, got "
                                  f"{'; '.join(calls) or 'no call: ' + raw.text[:100]!s} → {after}[/]")
    return totals


def report(results: dict[str, dict], output: str):
    if output == "json":
        print(json.dumps(results, indent=2))
        return
    table = Table(title="Tool calls", title_justify="left")
    table.add_column("Model")
    for _, heading in COLUMNS:
        table.add_column(heading, justify="right")
    table.add_column("Called together", justify="right")
    table.add_column("Per call", justify="right")
    for spec, totals in results.items():
        runs = totals["runs"] or 1
        together = f"{totals['together'] / totals['together_runs']:.0%}" if totals["together_runs"] else "-"
        row = [spec] + [f"{totals[key] / runs:.0%}" for key, _ in COLUMNS] + [together, f"{totals['seconds'] / runs:.1f}s"]
        table.add_row(*row)
    console.print(table)
    for spec, totals in results.items():
        if totals["errors"]:
            console.print(f"[red]{spec}: {totals['errors']} calls failed (see --verbose)[/]")
    console.print("[dim]Valid: a known tool, with arguments that fit its schema. Repair and retry are what labb "
                  "does to every reply; the retry is constrained decoding (Ollama only). Called together: asked for "
                  "several things, it called them all in one reply, so they run at the same time.[/]")


def main(argv: list[str]) -> int:
    """`labb eval tools …`, or `labb eval long …` (cli/long_evals.py)"""

    if argv[:1] == ["long"]:
        from labb.cli import long_evals
        return long_evals.main(argv)

    parser = argparse.ArgumentParser(prog="labb eval tools",
                                     description="Score how well models call labb's tools (one model call per prompt; no tool runs).")
    parser.add_argument("what", choices=["tools"], help="what to evaluate")
    parser.add_argument("-m", "--model", action="append", metavar="MODEL",
                        help="a model to score; again for more (default: the current model)")
    parser.add_argument("--engine", action="append", metavar="NAME",
                        help="score with this engine's prompts and tools; again to compare them (default: the default)")
    parser.add_argument("-n", "--repeat", type=int, default=1, help=f"run the {len(CASES)} prompts this many times (default 1)")
    parser.add_argument("-k", "--only", metavar="TEXT", help="only prompts whose tool or text contains this")
    parser.add_argument("-v", "--verbose", action="store_true", help="show each call that wasn't right")
    parser.add_argument("--output-format", choices=["text", "json"], default="text")
    args = parser.parse_args(argv)

    cases = [c for c in CASES if not args.only or args.only.lower() in (c["tool"] + " " + c["prompt"]).lower()]
    if not cases:
        console.print("[red]No prompts match.[/]")
        return 1
    specs = args.model or [llm.default.spec]
    used_engines = []
    for name in args.engine or ["default"]:
        entry = engine.find(name, str(Path.cwd()))
        if entry is None or entry.engine is None:
            console.print(f"[red]The engine {name} can't be used{': ' + '; '.join(entry.problems) if entry else ''}.[/]")
            return 1
        used_engines.append(entry.engine)
    results = {}
    with tempfile.TemporaryDirectory(prefix="labb-eval-") as workdir:
        make_project(Path(workdir))
        for spec in specs:
            for used in used_engines:
                try:
                    label = spec if used is engine.DEFAULT and not args.engine else f"{spec} · {used.name}"
                    results[label] = run_model(spec, cases, max(1, args.repeat), workdir, args.verbose, used)
                except ValueError as e:
                    console.print(f"[red]{e}[/]")
                    return 1
    report(results, args.output_format)
    return 0
