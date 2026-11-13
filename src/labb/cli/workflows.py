"""Studio's workflows from the terminal: `labb workflows` lists them, `labb run <name>`
runs one on the server and shows its steps as they end, asking here when it waits for an
approval. Also /workflows and /run in the chat. Workflows run on the server (they use its
agents, documents and actions), so this needs LABB_SERVER and a login."""

import argparse
import json
import sys
import time

import httpx
from rich.markdown import Markdown
from rich.table import Table

from labb import remote
from labb.cli.render import console

POLL_SECONDS = 1
MARKS = {"done": "[green]✓[/]", "failed": "[red]✗[/]", "skipped": "[dim]–[/]", "stopped": "[yellow]■[/]", "waiting": "[yellow]…[/]"}
EXIT = {"done": 0, "failed": 1, "stopped": 2, "waiting": 3}


class WorkflowError(Exception):
    """Something to tell the person, then stop"""


def _client() -> httpx.Client:
    server = remote.server()
    if not server:
        raise WorkflowError("Workflows run on the server: set LABB_SERVER (install.sh does), then labb --login")
    return httpx.Client(base_url=server, headers=remote.headers(), timeout=30)


def _check(response: httpx.Response):
    if response.status_code == 401:
        raise WorkflowError("The server doesn't know this PC: run labb --login")
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = response.text[:200]
        raise WorkflowError(str(detail))
    return response.json()


def listing(client: httpx.Client) -> list[dict]:
    return _check(client.get("/studio/workflows"))


def find(client: httpx.Client, name: str) -> dict:
    """A workflow by id or name (whole, or the start of one); raises WorkflowError"""

    workflows = listing(client)
    wanted = name.strip().lower()
    for test in (lambda w: w["id"] == name, lambda w: w["name"].lower() == wanted, lambda w: w["name"].lower().startswith(wanted)):
        found = [w for w in workflows if test(w)]
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            names = ", ".join(f"{w['name']} ({w['id']})" for w in found)
            raise WorkflowError(f"More than one workflow is called that: {names}. Give its id.")
    raise WorkflowError(f"There's no workflow called {name}; labb workflows lists them")


def show_list(client: httpx.Client):
    workflows = listing(client)
    if not workflows:
        console.print("[dim]No workflows yet: make one in the web chat's Studio.[/]")
        return
    table = Table(box=None, pad_edge=False, show_header=True, header_style="dim")
    for column in ("Workflow", "Version", "Starts", "Id"):
        table.add_column(column)
    for w in workflows:
        starts = ", ".join(x for x in (
            f"next {w['next_run'][:16].replace('T', ' ')} UTC" if w["next_run"] and w["enabled"] else "",
            "webhook" if w["webhook"] else "") if x) or "by hand"
        version = f"v{w['published_version']}" if w["published_version"] else "[dim]draft[/]"
        owner = "" if w["mine"] else f" [dim](by {w['owner']})[/]"
        table.add_row(f"{w['emoji']} {w['name']}{owner}", version, starts, f"[dim]{w['id']}[/]")
    console.print(table)


def parse_inputs(pairs: list[str]) -> dict:
    """name=value pairs; a value that's JSON (a number, true, a list) is read as such"""

    inputs = {}
    for pair in pairs:
        name, sep, value = pair.partition("=")
        if not sep or not name.strip():
            raise WorkflowError(f"Inputs are name=value, not {pair!r}")
        try:
            inputs[name.strip()] = json.loads(value)
        except ValueError:
            inputs[name.strip()] = value
    return inputs


def _seconds(step: dict) -> str:
    if not step.get("started_at") or not step.get("finished_at"):
        return ""
    from datetime import datetime

    taken = (datetime.fromisoformat(step["finished_at"]) - datetime.fromisoformat(step["started_at"])).total_seconds()
    return f" [dim]{taken:.1f}s[/]"


def _ask(client: httpx.Client, run: dict, step: dict, label: str) -> bool:
    """Ask for an approval here; False if nobody can answer (not a terminal)"""

    message = (step.get("input") or {}).get("message", "")
    console.print(f"[yellow]? {label}[/]\n{message}")
    if not sys.stdin.isatty():
        return False
    answer = console.input("[bold]Approve?[/] [dim](y/n, or a note after it: y looks good)[/] ").strip()
    approved = answer[:1].lower() == "y"
    comment = answer[1:].strip(" :,") if len(answer) > 1 else ""
    _check(client.post(f"/studio/runs/{run['id']}/approve", json={"node_id": step["node_id"], "approved": approved, "comment": comment}))
    return True


def follow(client: httpx.Client, run: dict, quiet: bool = False) -> dict:
    """Show a run's steps as they end, asking for approvals, until it ends; the run then"""

    labels = {n["id"]: n.get("label") or n["key"] for n in (run.get("definition") or {}).get("nodes", [])}
    shown: dict[str, str] = {}
    asked: set[str] = set()
    while True:
        for step in run.get("steps") or []:
            status = step["status"]
            if status in MARKS and shown.get(step["node_id"]) != status and step["key"] != "trigger":
                shown[step["node_id"]] = status
                if not quiet and status != "waiting":
                    branch = f" [dim]→ {step['handle']}[/]" if step.get("handle") else ""
                    console.print(f"{MARKS[status]} {labels.get(step['node_id'], step['key'])}{branch}{_seconds(step)}")
                    if status == "failed" and step.get("error"):
                        console.print(f"  [red]{step['error']}[/]")
        if run["status"] in ("done", "failed", "stopped"):
            return run
        if run["status"] == "waiting":
            waiting = [s for s in run.get("steps") or [] if s["status"] == "waiting" and s["node_id"] not in asked]
            approvals = [s for s in waiting if isinstance(s.get("input"), dict) and "message" in s["input"]]
            if not approvals:
                return run
            for step in approvals:
                asked.add(step["node_id"])
                if not _ask(client, run, step, labels.get(step["node_id"], step["key"])):
                    return run
        time.sleep(POLL_SECONDS)
        run = _check(client.get(f"/studio/runs/{run['id']}"))


def run_workflow(name: str, inputs: dict, *, draft: bool = False, output: str = "text") -> int:
    """`labb run`: start a run and follow it; the exit code (0 done, 1 failed, 2 stopped, 3 waiting)"""

    with _client() as client:
        workflow = find(client, name)
        run = _check(client.post(f"/studio/workflows/{workflow['id']}/runs", json={"inputs": inputs, "draft": draft}))
        if output == "text":
            version = "the draft" if draft else f"v{workflow['published_version']}"
            console.print(f"[dim]Running {workflow['emoji']} {workflow['name']} ({version}) on the server…[/]")
        try:
            run = follow(client, run, quiet=output != "text")
        except KeyboardInterrupt:
            client.post(f"/studio/runs/{run['id']}/stop")
            console.print("[yellow]Stopped.[/]")
            return 130
    if output == "json":
        print(json.dumps({k: run.get(k) for k in ("id", "status", "result", "error", "input_tokens", "output_tokens")},
                         ensure_ascii=False, indent=2))
    elif run["status"] == "done":
        result = run.get("result")
        if isinstance(result, str):
            console.print(Markdown(result))
        elif result is not None:
            console.print_json(json.dumps(result, ensure_ascii=False))
    elif run["status"] == "failed":
        console.print(f"[red]It failed: {run.get('error')}[/]")
    elif run["status"] == "waiting":
        console.print("[yellow]It's waiting: answer its approval in the web chat's Studio, or it goes on after its wait.[/]")
    return EXIT.get(run["status"], 1)


def main(argv: list[str]) -> int:
    """`labb run …` and `labb workflows`"""

    if argv[0] == "workflows":
        try:
            with _client() as client:
                show_list(client)
            return 0
        except (WorkflowError, httpx.HTTPError) as e:
            console.print(f"[red]{e}[/]")
            return 1
    parser = argparse.ArgumentParser(prog="labb run", description="Run one of your Studio workflows on the server.")
    parser.add_argument("workflow", help="its name (or the start of it), or its id; labb workflows lists them")
    parser.add_argument("-i", "--input", action="append", default=[], metavar="NAME=VALUE", help="an input of the run; again for more")
    parser.add_argument("--draft", action="store_true", help="run the draft being edited instead of the published version")
    parser.add_argument("--output-format", choices=["text", "json"], default="text", help="the result as text, or the run as JSON")
    args = parser.parse_args(argv[1:])
    try:
        return run_workflow(args.workflow, parse_inputs(args.input), draft=args.draft, output=args.output_format)
    except (WorkflowError, httpx.HTTPError) as e:
        console.print(f"[red]{e}[/]")
        return 1


def command(text: str) -> None:
    """/workflows and /run <workflow> name=value… in the chat"""

    parts = text.split()
    try:
        if parts[0] == "/workflows":
            with _client() as client:
                show_list(client)
        else:
            # The name is everything before the first name=value
            words = parts[1:]
            first = next((i for i, w in enumerate(words) if "=" in w), len(words))
            if first == 0:
                console.print("[dim]/run <workflow> [name=value …]: run one of your Studio workflows; /workflows lists them[/]")
                return
            run_workflow(" ".join(words[:first]), parse_inputs(words[first:]))
    except (WorkflowError, httpx.HTTPError) as e:
        console.print(f"[red]{e}[/]")
