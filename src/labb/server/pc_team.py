"""What tools/pcs.py does on the server: the web chat's agent starting and following work on the
chatter's PCs. Each is a session of its own, which they can open and go on with. It asks the person in
the chat first, and its approvals are passed to them in the same chat. Questions aren't: it goes on
with what it recommends, and says so."""

import time

from labb.server import pcs

# How long a tool call waits for a run; it goes on after that, and check_pc_run follows it
WAIT_SECONDS = 30 * 60
POLL_SECONDS = 0.5
MAX_REPLY = 20_000


def _user_id(config) -> str:
    return config["configurable"]["user_id"]


def _report(run: dict) -> str:
    where = f"{run['pc']}, {run['folder']}"
    match run["status"]:
        case "done":
            head = f"Finished on {where}."
        case "failed":
            head = f"Failed on {where}: {run['error']}"
        case "stopped":
            head = f"Stopped on {where}."
        case "lost":
            head = f"Lost on {where}: {run['error']}"
        case status:
            head = f"Still going on {where} ({status}), run id {run['id']}; check_pc_run looks again."
    said = run["result"][-MAX_REPLY:]
    opened = f"\n\nThe user can open it and go on: /chat/{run['thread_id']}"
    return (f"{head}\n\nIt said:\n{said}" if said else head) + opened


def _approved(answer) -> tuple[bool, str | None]:
    if answer in ("yes", "always"):
        return True, None
    return False, answer[1] if isinstance(answer, tuple) and len(answer) > 1 else None


class Backend:
    def list(self, config) -> str:
        found = pcs.list_for(_user_id(config))
        if not found:
            return "No PCs yet. The user connects one by running labb connect on it."
        lines = []
        for pc in found:
            state = "connected" if pc["online"] else "not connected"
            lines.append(f"- {pc['name']} ({state}): " + ", ".join(pc["folders"]))
        return "\n".join(lines)

    def run(self, config, pc_name: str, folder: str, request: str) -> str:
        configurable = config["configurable"]
        user_id, approve = configurable["user_id"], configurable.get("approve")
        if approve is None:
            return "Error: nobody is here to approve starting that."
        pc = next((p for p in pcs.list_for(user_id) if p["name"] == pc_name), None)
        if pc is None:
            return f"Error: there's no PC called {pc_name}; list_pcs shows them."
        if not pc["online"]:
            return f"Error: {pc_name} isn't connected."
        if folder not in pc["folders"]:
            return f"Error: {pc_name} doesn't serve {folder}; list_pcs shows its folders."
        warning = "Nothing there will ask first: it was connected with --allow-all.\n\n" if pc["allow_all"] else ""
        allowed, reason = _approved(approve(f"{warning}On {pc_name}, in {folder}:\n\n{request}", [], "it starts work on your PC", "Start this on your PC?"))
        if not allowed:
            return "Not started: the user said no." + (f" They said: {reason}" if reason else "")
        try:
            session = pcs.new_session(user_id, pc["id"], folder, request, source="agent")
            run = pcs.start(user_id, session["thread_id"], request, questions=False, source="agent")
        except (pcs.PcError, LookupError) as e:
            return f"Error: {e}"
        return self._follow(run["id"], user_id, approve, configurable.get("stop_requested"))

    def _follow(self, run_id: str, user_id: str, approve, stop_requested) -> str:
        deadline = time.monotonic() + WAIT_SECONDS
        asked: set[str] = set()
        while True:
            run = pcs.get_run(run_id, user_id)
            if run is None:
                return "Error: that run is gone."
            if run["status"] in pcs.FINISHED or time.monotonic() > deadline:
                return _report(run)
            if stop_requested is not None and stop_requested.is_set():
                pcs.stop(run_id, user_id)
                return f"Stopping it on {run['pc']}, since this reply was stopped."
            if new := [i for i in run["waiting"] if i not in asked]:
                events = {e["event"].get("id"): e["event"] for e in pcs.events_after(run_id, 0) if e["event"].get("type") == "approval"}
                for question_id in new:
                    asked.add(question_id)
                    event = events.get(question_id, {})
                    allowed, reason = _approved(approve(f"On {run['pc']}, in {run['folder']}:\n\n{event.get('detail', '')}", [], event.get("because") or "it changes something on your PC",
                                                        event.get("title", "Allow this on your PC?")))
                    pcs.answer(run_id, user_id, question_id, allow=allowed, reason=reason)
            time.sleep(POLL_SECONDS)

    def check(self, config, run_id: str) -> str:
        run = pcs.get_run(run_id, _user_id(config))
        return _report(run) if run else "Error: there's no such run."
