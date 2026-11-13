"""The agent building an app (tools/apps.py, server/app_tools.py, /chat's app_chat): only in the
app's own chat, only the user's own app, and the tools against a stand-in spawner."""

import json

import httpx
import pytest
from fastapi import HTTPException

from labb.server import app_tools, apps
from labb.tools import apps as app_toolset
from labb.tools import offered_for

APP = "0123456789ab"
ME, SOMEONE = "user-1", "user-2"


@pytest.fixture
def spawner(monkeypatch):
    """A stand-in spawner with one app of ME's, and a stand-in app server: what each was asked"""
    files = {"Dockerfile": "FROM busybox\n", "site/index.html": "<h1>Old</h1>\n<p>Old</p>\n"}
    state = {"build": 1, "status": "running", "asked": [], "fail": False}

    def handle(request: httpx.Request) -> httpx.Response:
        state["asked"].append(request)
        if request.url.host == "labb-app-x":
            if request.url.path in state.get("broken", ()):
                return httpx.Response(502, json={"detail": "The app's server isn't answering"})
            return httpx.Response(200, text="<h1>Hello</h1>", headers={"content-type": "text/html"})
        parts = request.url.path.strip("/").split("/")
        if request.url.params.get("owner") != ME or parts[1] != APP:
            return httpx.Response(404, json={"detail": "No such app"})
        app = {"id": APP, "name": "Board", "build": state["build"], "status": state["status"], "detail": "",
               "legacy": False, "upstream": "http://labb-app-x:8080", "server": state.get("server", True)}
        if len(parts) == 2:
            return httpx.Response(200, json=app)
        if parts[2] == "files":
            path = "/".join(parts[3:])
            if not path:
                return httpx.Response(200, json=[{"path": p, "size": len(c)} for p, c in sorted(files.items())])
            if request.method == "PUT":
                files[path] = json.loads(request.content)["content"]
                return httpx.Response(200, json={"path": path, "size": len(files[path])})
            if request.method == "DELETE":
                del files[path]
                return httpx.Response(204)
            if path not in files:
                return httpx.Response(404, json={"detail": f"No file {path!r}"})
            return httpx.Response(200, json={"path": path, "content": files[path]})
        if parts[2] == "build":
            if request.method == "GET":
                return httpx.Response(200, json=state.get("last_build") or {"build": state["build"], "ok": True, "error": None})
            if state.get("crash"):
                return httpx.Response(200, json={"ok": False, "error": state["crash"], "log": "Step 1\nSuccessfully built\n", "app": app,
                                                 "before_crashing": state.get("before_crashing", False)})
            if state["fail"]:
                return httpx.Response(200, json={"ok": False, "error": "exit 1", "log": "Step 1\nboom\nThe build failed: exit 1\n", "app": app})
            state["build"] += 1
            page = state.get("built_page")
            return httpx.Response(200, json={"ok": page is None or page["ok"], "error": None if page is None or page["ok"] else "The page didn't build",
                                             "log": "Step 1\nSuccessfully built\n", "app": {**app, "build": state["build"]},
                                             "server": {"ok": True, "error": None}, "page": page})
        if parts[2] == "page":
            state.setdefault("page_asks", []).append(request.url.params.get("wait"))
            return httpx.Response(200, json=state.get("page") or {"page": True, "build": 1, "ok": True, "error": None,
                                                                  "pending": False, "changed": False, "log": ""})
        if parts[2] == "settings":
            return httpx.Response(200, json={"variables": [{"name": "API_KEY", "secret": True, "value": None},
                                                           {"name": "CITY", "secret": False, "value": "Prishtina"}],
                                             "data": {"path": "/data", "size": 2048, "limit": 1073741824}})
        if parts[2] == "database":
            if parts[3:] == ["query"]:
                state["queried"] = json.loads(request.content)
                return httpx.Response(200, json={"columns": ["name", "n"], "rows": [["milk", 2], [None, 1]], "truncated": False,
                                                 "rowcount": 2, "written": False})
            if not state.get("database"):
                return httpx.Response(200, json={"available": True, "enabled": False})
            return httpx.Response(200, json={"available": True, "enabled": True, "name": f"app_{APP}", "size": 9000,
                                             "api": state.get("api") or {"enabled": False},
                                             "limit": 536870912, "pending": ["0002_more.sql"],
                                             "migrations": [{"name": "0001_items.sql", "checksum": "x", "applied_at": "t"}],
                                             "tables": [{"schema": "public", "name": "items", "kind": "table", "rows": 2,
                                                         "columns": [{"name": "id", "type": "integer", "nullable": False, "default": None},
                                                                     {"name": "name", "type": "text", "nullable": True, "default": None}]}]})
        if parts[2] == "users":
            return httpx.Response(200, json={"users": [], "total": 3, "offset": 0})
        if parts[2] == "versions":
            state["asked_versions"] = state.get("asked_versions", 0) + 1
            if len(parts) == 3:
                return httpx.Response(200, json={"unsaved": ["app.py"], "versions": [
                    {"id": "aaaaaaabcccccccccccccccccccccccccccccccc", "message": "Build 2, by labb in its chat", "at": "2026-11-08T17:00:00Z", "files": ["app.py"]},
                    {"id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "message": "Made from the static starter", "at": "2026-11-08T16:00:00Z", "files": ["Dockerfile", "app.py"]}]})
            if parts[3] == "unsaved":
                return httpx.Response(200, json={"diff": "+print(2)"})
            if parts[4] == "diff":
                return httpx.Response(200, json={"diff": f"diff of {parts[3]}"})
            state["restored"] = parts[3]
            return httpx.Response(200, json={"version": "d" * 40, "unsaved": [], "versions": []})
        if parts[2] == "labb-helper":
            name = {"python": "labb.py", "node": "labb.js"}[request.url.params["language"]]
            files[name] = "def ask(message): ..."
            return httpx.Response(200, json={"path": name, "size": len(files[name])})
        if parts[2] == "logs":
            if "logs" in state:
                return httpx.Response(200, json={"lines": "\n".join(state["logs"][-int(request.url.params["tail"]):]) + "\n"})
            return httpx.Response(200, json={"lines": f"last {request.url.params['tail']} lines\n"})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(apps, "SPAWNER_URL", "http://spawner:4100")
    monkeypatch.setattr(apps, "SPAWNER_TOKEN", "secret")
    monkeypatch.setattr(app_tools.httpx, "request", client.request)
    monkeypatch.setattr(app_tools.httpx, "get", client.get)
    return {"files": files, "state": state}


def config(user=ME, app=APP):
    return {"configurable": {"user_id": user, "app": {"id": app, "name": "Board"}}}


def use(tool, cfg=None, **args):
    return tool.invoke(args, cfg if cfg is not None else config())


def test_only_an_apps_chat_has_the_tools():
    assert "apps" in offered_for(config())
    # Elsewhere: None is "all of them", which leaves out ONLY_IF_ALLOWED toolsets like this one
    elsewhere = offered_for({"configurable": {"user_id": ME}})
    assert elsewhere is None or "apps" not in elsewhere
    # Not an agent's, even in an app's chat
    with_agent = {"configurable": {**config()["configurable"], "agent": {"toolsets": ["web"]}}}
    assert "apps" not in offered_for(with_agent)
    # And a tool called anywhere else says so
    assert app_toolset.list_app_files.invoke({}, {"configurable": {"user_id": ME}}).startswith("Error: this only works")


def test_reading_and_changing_files(spawner):
    listed = use(app_toolset.list_app_files)
    assert listed.startswith("App 'Board': build 1 runs, running.") and "site/index.html  (24 bytes)" in listed
    # The variables' names, never their values; and its data
    assert "API_KEY (secret), CITY." in listed and "Prishtina" not in listed and "/data: 2,048 of 1,073,741,824 bytes" in listed
    # Whether it may use labb (it may not, until the user turns it on)
    assert "It may not use labb" in listed
    assert use(app_toolset.read_app_file, path="site/index.html") == "site/index.html (2 lines):\n1| <h1>Old</h1>\n2| <p>Old</p>"
    assert use(app_toolset.write_app_file, path="api.py", content="print(1)").startswith("Wrote api.py (8 bytes)")
    assert spawner["files"]["api.py"] == "print(1)"
    assert use(app_toolset.delete_app_file, path="api.py") == "Deleted api.py."
    assert use(app_toolset.read_app_file, path="nope.txt") == "Error: No file 'nope.txt'"


def test_edits_change_text_found_exactly_once(spawner):
    edited = use(app_toolset.edit_app_file, path="site/index.html", old_text="<h1>Old</h1>", new_text="<h1>New</h1>")
    assert edited.startswith("Wrote site/index.html") and spawner["files"]["site/index.html"] == "<h1>New</h1>\n<p>Old</p>\n"
    assert "isn't in" in use(app_toolset.edit_app_file, path="site/index.html", old_text="<h2>", new_text="x")
    spawner["files"]["site/index.html"] = "a a"
    assert "2 times" in use(app_toolset.edit_app_file, path="site/index.html", old_text="a", new_text="b")
    use(app_toolset.edit_app_file, path="site/index.html", old_text="a", new_text="b", replace_all=True)
    assert spawner["files"]["site/index.html"] == "b b"
    assert "can't be empty" in use(app_toolset.edit_app_file, path="site/index.html", old_text="", new_text="x")
    # An edit that changes nothing is said to, so a model notices
    assert "nothing would change" in use(app_toolset.edit_app_file, path="site/index.html", old_text="b b", new_text="b b")


def test_building_and_checking(spawner):
    built = use(app_toolset.build_app)
    assert built.startswith("Build 2 worked: it started and runs now") and "Successfully built" in built
    spawner["state"]["fail"] = True
    failed = use(app_toolset.build_app)
    assert failed.startswith("The build failed: exit 1") and "build 2) still runs" in failed and "boom" in failed
    assert use(app_toolset.app_logs, lines=9999) == "last 500 lines"
    looked = use(app_toolset.look_at_app, path="/")
    assert looked.startswith("200 OK · text/html") and "<h1>Hello</h1>" in looked
    assert "has to start with" in use(app_toolset.look_at_app, path="http://elsewhere/")
    spawner["state"]["status"] = "exited"
    assert "isn't running" in use(app_toolset.look_at_app, path="/")


NODE_CRASH = ["2026-11-10T21:31:41Z /app/server.js:133", "2026-11-10T21:31:41Z })", "2026-11-10T21:31:41Z ^", "2026-11-10T21:31:41Z ",
              "2026-11-10T21:31:41Z SyntaxError: missing ) after argument list"] + ["2026-11-10T21:31:41Z     at wrapSafe (node:internal)"] * 8


def test_a_build_that_crashes_says_what_it_wrote(spawner):
    spawner["files"]["backend/server.js"] = "".join(f"line {n}\n" for n in range(1, 21))
    spawner["state"]["crash"] = "It built, but crashed as it started (exit code 1). What it wrote:\n/app/server.js:12\n})\n^\nSyntaxError: missing )"
    failed = use(app_toolset.build_app)
    assert failed.startswith("The build failed. It built, but crashed") and "SyntaxError: missing )" in failed
    assert "last build that worked (build 1) runs again" in failed and "Successfully built" not in failed
    # The lines it names, from the app's own file (backend/ is /app in the container)
    assert "backend/server.js around line 12:\n 8| line 8" in failed and "12| line 12\n13| line 13\n14| line 14" in failed
    # When the build that went back in crashes too, its logs aren't this build's
    spawner["state"]["before_crashing"] = True
    assert "it was crashing too, so app_logs shows build 1's errors" in use(app_toolset.build_app)


def test_logs_and_looking_show_the_error_a_stack_hides(spawner):
    spawner["state"]["logs"] = NODE_CRASH * 3
    # The last 5 lines are only the stack: the error before them comes first
    logs = use(app_toolset.app_logs, lines=5)
    assert logs.startswith("The latest error, from before these lines:") and "SyntaxError: missing )" in logs
    assert "/app/server.js:133" in logs and logs.endswith("at wrapSafe (node:internal)")
    # With the error in them, as they are
    assert use(app_toolset.app_logs, lines=9).startswith("2026-11-10T21:31:41Z SyntaxError")
    spawner["state"]["status"] = "restarting"
    looked = use(app_toolset.look_at_app, path="/")
    assert "isn't running" in looked and "Its latest error:" in looked and "SyntaxError: missing )" in looked


def test_edits_forgive_spaces_at_the_ends_of_lines(spawner):
    spawner["files"]["server.py"] = "if x:\n    y()\n\n# Handle hello\nz = 1\n"
    # As a small model wrote it, with a space after a line's end
    edited = use(app_toolset.edit_app_file, path="server.py", old_text="    y() \n  \n# Handle hello", new_text="    y()\n# Hello")
    assert "spaces at the ends of lines" in edited
    assert spawner["files"]["server.py"] == "if x:\n    y()\n# Hello\nz = 1\n"
    # Not found at all: the closest lines, to copy from
    missed = use(app_toolset.edit_app_file, path="server.py", old_text="    y()\n# Helo there", new_text="x")
    assert "isn't in server.py" in missed and "The closest part of it is:\n2|     y()\n3| # Hello" in missed
    assert "closest" not in use(app_toolset.edit_app_file, path="server.py", old_text="nothing like it at all", new_text="x")


def test_writes_say_when_a_file_wont_parse(spawner):
    broken = use(app_toolset.write_app_file, path="backend/app.py", content="def go(:\n    pass\n")
    assert "syntax error" in broken and "backend/app.py, line 1" in broken and spawner["files"]["backend/app.py"]
    assert "syntax error" not in use(app_toolset.write_app_file, path="backend/app.py", content="def go():\n    pass\n")
    assert "not valid JSON" in use(app_toolset.write_app_file, path="backend/package.json", content='{"dependencies": {"pg": "8"},}')
    # An edit that breaks it says so too
    assert "syntax error" in use(app_toolset.edit_app_file, path="backend/app.py", old_text="():", new_text="(:")


def test_reading_some_lines_and_writing_what_was_copied_with_its_numbers(spawner):
    spawner["files"]["app.py"] = "".join(f"x{n} = {n}\n" for n in range(1, 13))
    some = use(app_toolset.read_app_file, path="app.py", start_line=9, end_line=20)
    assert some == "app.py, lines 9-12 of 12:\n 9| x9 = 9\n10| x10 = 10\n11| x11 = 11\n12| x12 = 12"
    assert "has 12 lines, so there's no line 30" in use(app_toolset.read_app_file, path="app.py", start_line=30)
    # Lines copied with their numbers lose them, in edits and in writes
    use(app_toolset.edit_app_file, path="app.py", old_text="10| x10 = 10\n11| x11 = 11", new_text="10| x10 = 100\n11| x11 = 11")
    assert "x10 = 100\nx11 = 11\n" in spawner["files"]["app.py"]
    use(app_toolset.write_app_file, path="other.py", content="1| def go():\n2|     return 1\n3|")
    assert spawner["files"]["other.py"] == "def go():\n    return 1\n"
    # Code that only looks a little like it keeps its text
    assert app_toolset.unnumbered("a = 1| 2\nb = 3") == "a = 1| 2\nb = 3"
    # A syntax error shows the lines around it
    broken = use(app_toolset.write_app_file, path="bad.py", content="a = 1\nb = 2\nif a\n    pass\n")
    assert "bad.py, line 3" in broken and "The file around line 3:\n1| a = 1\n2| b = 2\n3| if a\n4|     pass" in broken
    # Past the last line (a file that ends too soon): its end
    assert app_toolset.around("a\nb\nc\n", 4) == "1| a\n2| b\n3| c" and app_toolset.around("a\n", 9) == ""


@pytest.mark.skipif(not app_toolset.shutil.which("node"), reason="needs node")
def test_javascript_is_checked_with_node(spawner):
    broken = use(app_toolset.write_app_file, path="backend/server.js", content='http.createServer((req, res) => {\n  res.end("hi");\n\n.listen(8080);\n')
    assert "syntax error" in broken and "backend/server.js:" in broken and "SyntaxError" in broken and "    at " not in broken
    assert "syntax error" not in use(app_toolset.write_app_file, path="backend/server.js", content='import x from "y";\nexport default x;\n')
    assert "syntax error" not in use(app_toolset.write_app_file, path="backend/server.js", content='require("http").createServer(() => {}).listen(1);\n')


def test_someone_elses_app_is_out_of_reach(spawner):
    # The spawner is asked as the chat's user: someone else's app is "no such app"
    assert use(app_toolset.list_app_files, config(user=SOMEONE)) == "Error: No such app"
    assert use(app_toolset.write_app_file, config(user=SOMEONE), path="x", content="y") == "Error: No such app"
    assert "x" not in spawner["files"]
    assert all(r.url.params.get("owner") in (ME, SOMEONE) for r in spawner["state"]["asked"] if r.url.host == "spawner")


def test_a_chat_belongs_to_an_app_by_its_thread(spawner):
    from labb.server.app import ChatRequest, app_chat
    me = {"id": ME, "role": "member"}
    named = ChatRequest(message="hi", app_id=APP)
    assert app_chat(named, me) == {"id": APP, "name": "Board"} and named.thread_id == f"app-{APP}"
    # Sent into that thread from anywhere, it's still the app's chat
    assert app_chat(ChatRequest(message="hi", thread_id=f"app-{APP}"), me)["id"] == APP
    assert app_chat(ChatRequest(message="hi", thread_id="some-chat"), me) is None
    for refused, status in [
        (ChatRequest(message="hi", thread_id=f"app-{APP}", app_id="ba9876543210"), 400),
        (ChatRequest(message="hi", app_id=APP, agent_id="a1"), 400),
        (ChatRequest(message="hi", app_id=APP, pc_id="pc1"), 400),
        (ChatRequest(message="hi", app_id="nothex"), 404),
    ]:
        with pytest.raises(HTTPException) as e:
            app_chat(refused, me)
        assert e.value.status_code == status
    # Someone else's app, or its thread, isn't theirs to chat in
    with pytest.raises(HTTPException) as e:
        app_chat(ChatRequest(message="hi", thread_id=f"app-{APP}"), {"id": SOMEONE, "role": "member"})
    assert e.value.status_code == 404


def test_an_app_has_sessions_each_starting_afresh(spawner):
    from labb.server.app import ChatRequest, app_chat, app_of_thread
    me = {"id": ME, "role": "member"}
    # A new session keeps its own thread, and is the app's chat all the same
    fresh = ChatRequest(message="hi", thread_id=f"app-{APP}--k3x9q2m7ab", app_id=APP)
    assert app_chat(fresh, me) == {"id": APP, "name": "Board"} and fresh.thread_id == f"app-{APP}--k3x9q2m7ab"
    assert app_chat(ChatRequest(message="hi", thread_id=f"app-{APP}--k3x9q2m7ab"), me)["id"] == APP
    assert app_of_thread(f"app-{APP}") == APP and app_of_thread(f"app-{APP}--k3x9q2m7ab") == APP
    for bad in (f"app-{APP}--", f"app-{APP}--UPPER123", f"app-{APP}x", "app-nothex", f"app-{APP}--a/b"):
        assert app_of_thread(bad) is None, bad
        with pytest.raises(HTTPException) as e:
            app_chat(ChatRequest(message="hi", thread_id=bad), me)
        assert e.value.status_code == 400
    # Another app's session isn't this app's
    with pytest.raises(HTTPException):
        app_chat(ChatRequest(message="hi", thread_id=f"app-{APP}--k3x9q2m7ab", app_id="ba9876543210"), me)
    with pytest.raises(HTTPException) as e:
        app_chat(ChatRequest(message="hi", thread_id=f"app-{APP}--k3x9q2m7ab"), {"id": SOMEONE, "role": "member"})
    assert e.value.status_code == 404


def test_a_stuck_session_starts_fresh_with_what_was_asked_and_where_the_app_is(spawner):
    from langchain_core.messages import AIMessage, HumanMessage

    from labb import chats
    from labb.agent import chat_agent
    from labb.server import apps_routes
    from labb.tools import todo

    stuck = f"app-{APP}--stuck12345"
    chats.touch(stuck, "I want to build a todo app", user_id=ME)
    chat_agent.update_state({"configurable": {"thread_id": stuck}}, {"messages": [
        HumanMessage("I want to build a todo app"), AIMessage("The [labb] text is in server.js!"),
        HumanMessage("Continue building the todo app"), AIMessage("Still stuck")]})
    todo.save_for({"configurable": {"thread_id": stuck, "user_id": ME}},
                     [{"id": 1, "task": "Build the API", "status": "done"}, {"id": 2, "task": "Test the app", "status": "pending"}])
    spawner["files"]["backend/server.js"] = "".join(f"line {n}\n" for n in range(1, 21))
    spawner["state"].update(status="restarting", last_build={"build": 3, "ok": False, "error":
                            "It built, but crashed: /app/server.js:12\n\n\n\nSyntaxError: x\n    at wrapSafe (node:internal)\nNode.js v22"})
    me = {"id": ME, "role": "member"}
    message = apps_routes.fresh_start(APP, stuck, me)["message"]
    assert message.startswith("Let's start fresh on this app.") and "don't go by what it concluded" in message
    assert "- I want to build a todo app\n- Continue building the todo app" in message
    # The steps left, but not what the model said
    assert "- Test the app" in message and "Build the API" not in message and "[labb]" not in message
    assert "build 1 runs, restarting" in message and "Its last build (build 3) failed: It built, but crashed: /app/server.js:12\n\nSyntaxError: x" in message
    # Without Node's stack, with the file's lines
    assert "wrapSafe" not in message and "Node.js v22" not in message and "backend/server.js around line 12:" in message
    # Someone else's session, another app's, or a thread that isn't the app's, is out of reach
    for thread, user in [(stuck, {"id": SOMEONE, "role": "member"}), ("some-chat", me), (f"app-{APP}--nobody0000", me)]:
        with pytest.raises(HTTPException) as e:
            apps_routes.fresh_start(APP, thread, user)
        assert e.value.status_code == 404


def test_the_labb_helper_and_whether_the_app_may_use_labb(spawner, monkeypatch):
    from labb.server import app_keys
    assert use(app_toolset.add_labb_helper, language="python").startswith("Wrote labb.py")
    assert "labb.py" in spawner["files"]
    monkeypatch.setattr(app_keys, "current", lambda app_id, user_id: {"scopes": ["ask", "memories"]})
    assert "It may use labb, as the user (ask, memories)" in use(app_toolset.list_app_files)


def test_history_diffs_and_going_back(spawner):
    history = use(app_toolset.app_history)
    assert history.startswith("Changed since the last version (not built): app.py")
    assert "aaaaaaabcccc  2026-11-08T17:00:00Z  Build 2, by labb in its chat  (app.py)" in history
    assert use(app_toolset.app_version_diff, version="unsaved") == "+print(2)"
    assert use(app_toolset.app_version_diff, version="aaaaaaabc") == "diff of aaaaaaabcccccccccccccccccccccccccccccccc"
    # A start that's two versions, or too short, or none
    assert "no single version" in use(app_toolset.app_version_diff, version="a" * 7)
    assert "at least the first 7" in use(app_toolset.restore_app_version, version="ab")
    assert use(app_toolset.restore_app_version, version="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa").startswith("The files are as they were in aaaaaaaaaaaa")
    assert spawner["state"]["restored"] == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def test_a_build_from_the_chat_says_so(spawner):
    use(app_toolset.build_app)
    build = next(r for r in spawner["state"]["asked"] if r.url.path.endswith("/build"))
    assert build.url.params["note"] == "by labb in its chat"


def test_the_apps_database_for_the_agent(spawner):
    assert "It has no database" in use(app_toolset.db_schema)
    spawner["state"]["database"] = True
    schema = use(app_toolset.db_schema)
    assert schema.startswith(f"Database app_{APP}: 9,000 of 536,870,912 bytes.")
    assert "Migrations applied: 0001_items.sql; not yet (build to apply): 0002_more.sql" in schema
    assert "table public.items (~2 rows): id integer not null, name text" in schema
    assert "Its API is off" in schema and "auth.uid()" in schema
    spawner["state"]["api"] = {"enabled": True, "port": 9200, "status": "running", "signups": False}
    schema = use(app_toolset.db_schema)
    assert "/auth/ sign-in for its own users (3 so far; new ones can not sign up)" in schema
    assert use(app_toolset.db_query, sql="select name, count(*) n from items group by 1") == "name | n\nmilk | 2\nNULL | 1"
    # Always read-only, whatever it asks
    assert spawner["state"]["queried"]["write"] is False


PAGE_ERROR = "`vite build` failed (paths are in frontend/):\n[builtin:vite-transform] Expected `}` but found `EOF`\n src/App.jsx:3:5"


def test_a_write_says_whether_it_goes_live_by_itself(spawner):
    assert use(app_toolset.write_app_file, path="frontend/src/App.jsx", content="x").endswith("goes live by itself in a few seconds: check it with look_at_app.")
    assert use(app_toolset.write_app_file, path="backend/notes.py", content="x = 1").endswith("Build the app (build_app) to run it.")
    assert use(app_toolset.write_app_file, path="db/migrations/0002_notes.sql", content="").endswith("build_app) to run it.")
    assert use(app_toolset.write_app_file, path="README.md", content="hi") == "Wrote README.md (2 bytes)."
    assert use(app_toolset.delete_app_file, path="frontend/src/App.jsx").startswith("Deleted frontend/src/App.jsx. The page builds")


def test_the_files_say_what_the_app_is(spawner):
    listed = use(app_toolset.list_app_files)
    assert "Its page (frontend/) builds and goes live by itself after each change: it's built and live." in listed
    assert "Its server is backend/ (only its /api/), which build_app builds and runs." in listed
    spawner["state"]["server"] = False
    spawner["state"]["page"] = {"page": True, "build": 2, "ok": False, "error": "boom", "pending": False, "changed": False, "log": ""}
    listed = use(app_toolset.list_app_files)
    assert "its latest build failed (look_at_app says why)" in listed and "It has no server (no backend/)" in listed


def test_looking_at_the_page_waits_for_it_and_says_when_it_failed(spawner):
    spawner["files"]["frontend/src/App.jsx"] = "export default function App() {\n  return (\n    <div>\n"
    assert use(app_toolset.look_at_app, path="/").startswith("200 OK")
    assert spawner["state"]["page_asks"] == ["true"]
    spawner["state"]["page"] = {"page": True, "build": 2, "ok": False, "error": PAGE_ERROR, "pending": False, "changed": False, "log": ""}
    looked = use(app_toolset.look_at_app, path="/")
    assert looked.startswith("The page didn't build, so the last good one is still served") and "Expected `}`" in looked
    # The lines of the file it names, from frontend/
    assert "frontend/src/App.jsx around line 3:" in looked and "3|     <div>" in looked
    assert "\n\nWhat's served now:\n200 OK" in looked
    # The server's paths don't wait for the page
    use(app_toolset.look_at_app, path="/api/items")
    assert spawner["state"]["page_asks"] == ["true", "true"]


def test_a_build_says_how_the_page_went_and_whether_the_app_answers(spawner, monkeypatch):
    monkeypatch.setattr(app_tools, "ANSWER_WAIT", 0)
    spawner["files"]["frontend/src/App.jsx"] = "a\nb\nc\n"
    spawner["state"]["built_page"] = {"build": 2, "ok": False, "error": PAGE_ERROR, "log": "…"}
    built = use(app_toolset.build_app)
    assert built.startswith("Build 2 worked") and "The page didn't build, so the last good one is still served" in built
    assert "frontend/src/App.jsx around line 3:" in built
    # One that runs but doesn't answer isn't said to work
    spawner["state"]["built_page"] = {"build": 3, "ok": True, "error": None, "log": ""}
    spawner["state"]["broken"] = {"/api/health"}
    spawner["state"]["logs"] = ["TypeError: not all arguments converted during string formatting"]
    built = use(app_toolset.build_app)
    assert built.startswith("Build 3 runs, but doesn't answer as it should: /api/health answered 502")
    assert "Its latest error:\nTypeError: not all arguments" in built and built.endswith("Fix that, then build again.")
    # A page only: it's the page that's checked
    spawner["state"].update(server=False, broken=set())
    assert use(app_toolset.build_app).startswith("Build 4 worked: the page is built and live")
    assert [r.url.path for r in spawner["state"]["asked"] if r.url.host == "labb-app-x"][-1] == "/"


def test_an_apps_chat_has_no_skills():
    # Its notes are the app's own (tests/test_notes.py), so those stay
    offered = offered_for(config())
    assert "apps" in offered and not offered & {"skills", "skill_writing"}


def test_a_python_crash_shows_the_apps_own_frame(spawner):
    spawner["files"]["backend/main.py"] = "".join(f"line {n}\n" for n in range(1, 15))
    spawner["state"]["crash"] = ("It built, but crashed as it started (exit code 1). What it wrote:\nTraceback (most recent call last):\n"
                                 '  File "/usr/local/bin/uvicorn", line 6, in <module>\n'
                                 '  File "/usr/local/lib/python3.14/importlib/__init__.py", line 88, in import_module\n'
                                 '  File "/app/main.py", line 9, in <module>\n    import itemz\n'
                                 "ModuleNotFoundError: No module named 'itemz'")
    failed = use(app_toolset.build_app)
    assert "No module named 'itemz'" in failed and "backend/main.py around line 9:" in failed and " 9| line 9" in failed
