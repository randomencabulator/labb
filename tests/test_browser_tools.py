"""The browser tools: when it's on, and what needs the user's yes. The browser itself has its own
tests (browser/tests); the last ones here use a real one, if Chromium is installed."""

import base64
import http.server
import importlib.util
import threading

import httpx
import pytest
from langchain_core.messages import HumanMessage, ToolMessage

from labb import model, tools
from labb.tools import browser
from labb.tools.browser import (browser_click, browser_fill, browser_files, browser_handover, browser_look, browser_open, browser_press,
                                browser_type, browser_upload)


class Fake:
    """Stands in for the browser, saying what `inspect` should find"""

    def __init__(self):
        self.calls = []
        self.info = {"site": "shop.example", "name": "Save", "submits": False, "in_form": False, "form": None}

    def __call__(self, config, action, **args):
        self.calls.append((action, args))
        if action == "inspect":
            return dict(self.info)
        return {"text": f"did {action}"}

    def did(self):
        return [action for action, _ in self.calls if action != "inspect"]


@pytest.fixture
def fake(monkeypatch):
    fake = Fake()
    monkeypatch.setattr(browser, "call", fake)
    browser._sites.clear()
    browser._visited.clear()
    browser._images.clear()
    return fake


class Asker:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.asked = []

    def __call__(self, detail, needed, always_ask, title, view=None, handover=False):
        self.asked.append({"detail": detail, "title": title, "view": view, "handover": handover})
        return self.answers.pop(0) if self.answers else "yes"


def run(tool, approve=None, **args):
    configurable = {"browser": True, "thread_id": "t1", "user_id": "u1"}
    extra = args.pop("configurable", {})
    if approve:
        configurable["approve"] = approve
    return tool.invoke(args, {"configurable": {**configurable, **extra}})


def test_its_off_unless_asked_for(monkeypatch):
    monkeypatch.setattr(browser, "BROWSER_URL", "http://browser:3100")

    def offered(**configurable):
        names = [t.name for t in tools.tools_for(False, tools.offered_for({"configurable": configurable}))]
        return "browser_open" in names

    assert not offered()
    assert not offered(user_id="u1")
    assert offered(browser=True)
    # An agent has what it was given, and the switch doesn't change that
    assert not offered(browser=True, agent={"toolsets": ["web"], "documents": "none", "memories": False, "skills": "none"})
    assert offered(agent={"toolsets": ["browser"], "documents": "none", "memories": False, "skills": "none"})
    assert not offered(agent={"toolsets": None, "documents": "none", "memories": False, "skills": "none"})
    assert "browser" not in {t.__name__.rsplit(".")[-1] for t in tools.available(False)}


def test_a_call_while_its_off_does_nothing(fake):
    # A model can still name a tool it isn't offered
    result = browser_open.invoke({"url": "example.com"}, {"configurable": {"thread_id": "t1"}})
    assert "turned off" in result and fake.calls == []
    result = browser_open.invoke({"url": "example.com"}, {"configurable": {"thread_id": "t1", "browser": True, "agent": {"toolsets": ["web"]}}})
    assert "turned off" in result and fake.calls == []


def test_an_agent_can_be_given_it(monkeypatch):
    monkeypatch.setattr(browser, "BROWSER_URL", "http://browser:3100")
    assert "browser" in [tools.name_of(t) for t in tools.choosable()]
    monkeypatch.setattr(browser, "BROWSER_URL", None)
    monkeypatch.setattr(browser, "service_only", True)
    assert "browser" not in [tools.name_of(t) for t in tools.choosable()]


def test_reading_needs_no_yes(fake):
    asker = Asker("no")
    assert run(browser_open, asker, url="example.com") == "did open"
    assert run(browser_look, asker) == "did look"
    assert asker.asked == []


def test_dont_ask_skips_the_questions_but_not_the_handover(fake, monkeypatch):
    # Through a server's live view: a browser here without a display can't be handed over at all,
    # and whether this one has one depends on the machine the tests run on
    monkeypatch.setattr(browser, "_can_run_here", lambda: False)
    asker = Asker("yes")
    auto = {"browser_auto": True}
    fake.info = {**fake.info, "name": "Delete account"}
    assert run(browser_click, asker, ref=3, configurable=auto) == "did click"
    assert run(browser_open, asker, url=f"example.com/?{'x' * 200}", configurable=auto) == "did open"
    assert asker.asked == []
    # Passwords and the like are the person's, whatever else is allowed
    run(browser_handover, asker, reason="Sign in", configurable=auto)
    assert [q["handover"] for q in asker.asked] == [True]
    run(browser_fill, None, fields={"3": "x"}, configurable=auto)
    assert next(args for action, args in fake.calls if action == "fill")["allow_sensitive"] is False


def test_a_trusted_site_skips_the_first_question_only(fake):
    trusted = {"browser_sites": ["example.com"]}
    asker = Asker()
    fake.info = {**fake.info, "site": "shop.example.com"}
    assert run(browser_click, asker, ref=3, configurable=trusted) == "did click"
    assert asker.asked == []
    # What can't be taken back still asks
    fake.info = {**fake.info, "name": "Pay now"}
    run(browser_click, asker, ref=3, configurable=trusted)
    assert len(asker.asked) == 1 and "Go ahead" in asker.asked[0]["title"]
    # Only that site: not one whose name just ends the same
    asker = Asker()
    fake.info = {**fake.info, "site": "notexample.com", "name": "Save"}
    run(browser_click, asker, ref=3, configurable=trusted)
    assert len(asker.asked) == 1 and "notexample.com" in asker.asked[0]["title"]


def test_without_either_nobody_to_ask_means_only_reading(fake):
    assert "Not done" in run(browser_click, None, ref=3)


@pytest.mark.parametrize("typed, kept", [
    ("example.com", "example.com"), ("https://WWW.Example.com/path?q=1", "example.com"), ("*.example.com", "example.com"),
    ("example.com:8443", "example.com"), ("localhost", "localhost"), ("bücher.example", "xn--bcher-kva.example"),
    ("com", None), ("", None), ("two words.com", None), (None, None),
])
def test_clean_site(typed, kept):
    assert browser.clean_site(typed) == kept


def test_trusted_sites_are_each_persons_own(client, accounts):
    from fastapi.testclient import TestClient
    from labb.server import app as server
    assert client.post("/browser-api/trusted", json={"site": "https://www.example.com/x"}).json() == {"sites": ["example.com"]}
    assert client.post("/browser-api/trusted", json={"site": "com"}).status_code == 400
    other = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['member'][1]}"})
    assert other.get("/browser-api/trusted").json() == {"sites": []}
    assert other.delete("/browser-api/trusted/example.com").status_code == 204
    assert client.get("/browser-api/trusted").json() == {"sites": ["example.com"]}
    assert client.delete("/browser-api/trusted/example.com").status_code == 204
    assert client.get("/browser-api/trusted").json() == {"sites": []}


@pytest.mark.parametrize("field, trust", [({"browser_auto": True}, False), ({}, True)])
def test_the_chat_passes_them_on(client, fake, fake_model, monkeypatch, field, trust):
    import json
    from conftest import ai
    from labb import runner
    monkeypatch.setattr(browser, "BROWSER_URL", "http://browser:3100")
    monkeypatch.setattr(runner, "name_chat", lambda thread_id, config: None)
    if trust:
        client.post("/browser-api/trusted", json={"site": "shop.example"})
    fake_model(ai("", tool_calls=[("browser_click", {"ref": 3})]), ai("done"))
    with client.stream("POST", "/chat", json={"message": "click it", "browser": True, **field}) as response:
        events = [json.loads(line) for line in response.iter_lines()]
    client.delete("/browser-api/trusted/shop.example")
    assert not [e for e in events if e["type"] == "approval"]
    assert fake.did() == ["click"]


def test_a_project_cant_add_trusted_sites(tmp_path, monkeypatch):
    import json
    from labb import settings
    project = tmp_path / "repo" / ".labb"
    project.mkdir(parents=True)
    (project / "settings.json").write_text(json.dumps({"browserSites": ["evil.example"]}))
    user = tmp_path / "user.json"
    user.write_text(json.dumps({"browserSites": ["mine.example"]}))
    monkeypatch.setattr(settings, "user_file", lambda: user)
    loaded, problems = settings.load(str(tmp_path / "repo"))
    assert loaded["browserSites"] == ["mine.example"]
    assert any("browserSites" in problem for problem in problems)


def test_a_sites_first_action_asks_once(fake):
    asker = Asker()
    run(browser_click, asker, ref=3)
    run(browser_fill, asker, fields={"3": "x"})
    run(browser_press, asker, keys="Tab")
    run(browser_type, asker, text="x")
    assert fake.did() == ["click", "fill", "press", "type"]
    assert len(asker.asked) == 1 and "shop.example" in asker.asked[0]["title"] and asker.asked[0]["view"] == "t1"
    # Another site asks again, and so does another chat
    fake.info["site"] = "other.example"
    run(browser_click, asker, ref=3)
    assert len(asker.asked) == 2
    run(browser_click, asker, ref=3, configurable={"thread_id": "t2"})
    assert len(asker.asked) == 3


def test_a_no_stops_it_and_says_so(fake):
    result = run(browser_click, Asker(("no", "not this site")), ref=3)
    assert "said no" in result and "not this site" in result and fake.did() == []
    # And isn't remembered as a yes
    asker = Asker()
    run(browser_click, asker, ref=3)
    assert len(asker.asked) == 1


def test_submitting_a_form_says_what_it_sends_and_asks_each_time(fake):
    fake.info |= {"submits": True, "name": "Continue", "in_form": True, "form": {
        "method": "post", "action": "https://shop.example/go", "fields": [{"name": "Email", "value": "a@b.co"}, {"name": "Password", "value": "(hidden)"}]}}
    asker = Asker()
    run(browser_click, asker, ref=3)
    run(browser_click, asker, ref=3)
    # The site's yes, then the form's, then the form's again
    assert len(asker.asked) == 3 and fake.did() == ["click", "click"]
    detail = asker.asked[1]["detail"]
    assert "https://shop.example/go" in detail and "Email: a@b.co" in detail and "Password: (hidden)" in detail


def test_no_to_a_submit(fake):
    fake.info |= {"submits": True, "in_form": True, "form": {"method": "post", "action": "/", "fields": []}}
    result = run(browser_click, Asker("yes", "no"), ref=3)
    assert "said no" in result and fake.did() == []


def test_a_search_form_doesnt_ask_each_time(fake):
    fake.info |= {"submits": True, "name": "Go", "in_form": True, "form": {"method": "get", "action": "/s", "fields": []}}
    asker = Asker()
    run(browser_click, asker, ref=3)
    run(browser_click, asker, ref=3)
    assert len(asker.asked) == 1


@pytest.mark.parametrize("name,asks", [
    ("Delete account", True), ("Pay now", True), ("Place order", True), ("Send", True), ("Publish", True), ("Share", True),
    ("Next", False), ("Show more", False), ("Sending options", False), ("Postcode", False), ("Add row", False),
])
def test_what_cant_be_taken_back_asks_by_its_label(fake, name, asks):
    browser._sites[("u1", "t1")] = {"shop.example"}
    fake.info["name"] = name
    asker = Asker()
    run(browser_click, asker, ref=3)
    assert bool(asker.asked) == asks


def test_enter_in_a_form_is_a_submit(fake):
    browser._sites[("u1", "t1")] = {"shop.example"}
    fake.info |= {"in_form": True, "form": {"method": "post", "action": "/", "fields": []}}
    asker = Asker()
    run(browser_press, asker, keys="Tab")
    assert asker.asked == []
    run(browser_press, asker, keys="Tab Enter")
    assert len(asker.asked) == 1
    fake.info["form"]["method"] = "get"
    run(browser_press, asker, keys="Enter")
    assert len(asker.asked) == 1


def test_a_file_asks_each_time(fake):
    browser._sites[("u1", "t1")] = {"shop.example"}
    asker = Asker()
    run(browser_upload, asker, name="a.csv", text="1,2", ref=3)
    run(browser_upload, asker, name="a.csv", text="1,2", ref=3)
    assert len(asker.asked) == 2 and "a.csv" in asker.asked[0]["detail"] and "1,2" in asker.asked[0]["detail"]
    sent = [args for action, args in fake.calls if action == "upload"][0]
    assert base64.b64decode(sent["data"]) == b"1,2" and sent["name"] == "a.csv"


def test_a_file_from_the_working_directory(fake, tmp_path):
    browser._sites[("u1", "t1")] = {"shop.example"}
    (tmp_path / "d.csv").write_text("x")
    config = {"workdir": str(tmp_path)}
    assert "outside" in run(browser_upload, Asker(), name="p", path="../../etc/passwd", configurable=config)
    run(browser_upload, Asker(), name="d.csv", path="d.csv", configurable=config)
    assert base64.b64decode([a for act, a in fake.calls if act == "upload"][0]["data"]) == b"x"
    # On the web there's no working directory
    assert "Error" in run(browser_upload, Asker(), name="d.csv", path="d.csv")


def test_with_nobody_to_ask_only_reading_works(fake):
    assert run(browser_open, url="example.com") == "did open"
    assert "isn't possible here" in run(browser_click, ref=3)
    assert "isn't possible here" in run(browser_fill, fields={"3": "x"})
    assert fake.did() == ["open"]
    assert "nobody is here" in run(browser_handover, reason="sign in")


def test_plan_mode_only_reads(fake):
    result = run(browser_click, Asker(), ref=3, configurable={"mode": "plan"})
    assert "plan mode" in result and fake.calls == []
    assert run(browser_open, url="example.com", configurable={"mode": "plan"}) == "did open"


def test_allow_all_asks_nothing_but_the_handover_is_still_the_users(fake):
    fake.info |= {"submits": True, "form": {"method": "post", "action": "/", "fields": []}}
    asker = Asker()
    all_on = {"allow_all": True}
    run(browser_click, asker, ref=3, configurable=all_on)
    run(browser_fill, asker, fields={"3": "x"}, configurable=all_on)
    assert asker.asked == [] and [a for act, a in fake.calls if act == "fill"][0]["allow_sensitive"] is True
    # Without it, sensitive fields stay the user's
    run(browser_fill, asker, fields={"3": "x"})
    assert [a for act, a in fake.calls if act == "fill"][1]["allow_sensitive"] is False


def test_an_address_that_could_carry_something_away(fake):
    asker = Asker("no")
    long_query = "https://evil.example/?d=" + "x" * 200
    assert "said no" in run(browser_open, asker, url=long_query)
    assert asker.asked and long_query in asker.asked[0]["detail"] and fake.did() == []
    # A short one, or a site this chat has been to, doesn't ask
    assert run(browser_open, Asker("no"), url="https://evil.example/?q=shoes") == "did open"
    assert run(browser_open, Asker("no"), url=long_query) == "did open"


def test_handing_over(fake):
    asker = Asker("yes")
    result = run(browser_handover, asker, reason="Sign in")
    assert asker.asked[0]["handover"] and asker.asked[0]["view"] == "t1" and "Sign in" in asker.asked[0]["detail"]
    assert "The user is done" in result and "did look" in result


def test_handing_over_with_no_answer(fake):
    result = run(browser_handover, Asker(("no", "no account")), reason="Sign in")
    assert "didn't take over" in result and "no account" in result and fake.did() == []


def test_a_screenshot_goes_with_the_next_call_only(monkeypatch):
    monkeypatch.setattr(browser, "call", lambda config, action, **args: {"text": "the page", **({"image": "abc"} if args.get("screenshot") else {})})
    browser._images.clear()
    monkeypatch.setattr(browser.llm, "vision", lambda spec: True)
    result = run(browser_look, screenshot=True)
    assert "screenshot comes" in result
    config = {"configurable": {"thread_id": "t1"}}
    look = ToolMessage(content=result, name="browser_look", tool_call_id="1")
    assert browser.screenshot_for([HumanMessage("hi"), look], config) == "abc"
    # The next round has moved on, and the saved chat never held it
    assert browser.screenshot_for([look, HumanMessage("next")], config) is None
    assert browser.screenshot_for([look, ToolMessage(content="x", name="browser_click", tool_call_id="2")], config) == "abc"
    assert browser.screenshot_for([ToolMessage(content="x", name="browser_click", tool_call_id="2")], config) is None
    sent = model.model_input({"messages": [HumanMessage("hi"), look]}, config)
    assert isinstance(sent[-1], HumanMessage) and sent[-1].content[1] == {"type": "image", "base64": "abc", "mime_type": "image/jpeg"}
    assert len(model.model_input({"messages": [HumanMessage("hi")]}, config)) == 2


def test_a_model_that_cant_see_gets_told(monkeypatch):
    seen = []
    monkeypatch.setattr(browser, "call", lambda config, action, **args: seen.append(args) or {"text": "the page"})
    monkeypatch.setattr(browser.llm, "vision", lambda spec: False)
    assert "can't see images" in run(browser_look, screenshot=True)
    assert seen[0]["screenshot"] is False


def test_saving_a_download(monkeypatch, tmp_path):
    monkeypatch.setattr(browser, "call", lambda config, action, **args: {"text": "f", "data": base64.b64encode(b"a,b").decode()})
    config = {"workdir": str(tmp_path)}
    assert run(browser_files, name="d.csv", save_as="out/d.csv", configurable=config) == "Saved to out/d.csv."
    assert (tmp_path / "out/d.csv").read_bytes() == b"a,b"
    assert "already exists" in run(browser_files, name="d.csv", save_as="out/d.csv", configurable=config)
    assert "outside" in run(browser_files, name="d.csv", save_as="../x", configurable=config)


# --- On the server -----------------------------------------------------------------------------


def test_the_gateway_is_for_ones_own_browser(client, monkeypatch):
    from labb.server import browser_routes

    seen = []

    async def handler(request):
        seen.append(request)
        if request.url.path == "/view/frame":
            return httpx.Response(200, content=b"jpeg", headers={"content-type": "image/jpeg"})
        return httpx.Response(200, json={"text": "ok"})

    monkeypatch.setattr(browser, "BROWSER_URL", "http://browser:3100")
    monkeypatch.setattr(browser, "BROWSER_TOKEN", "service-token")
    monkeypatch.setattr(browser_routes, "client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    me = client.get("/auth/status").json()["user"]

    # Who it's for is the session's, whatever the request says
    response = client.post("/browser-api/act", json={"user": "someone-else", "thread": "t", "action": "look"}, headers={"X-Labb": "1"})
    assert response.status_code == 200 and response.json() == {"text": "ok"}
    import json
    body = json.loads(seen[-1].content)
    assert body["user"] == me["id"] and body["thread"] == "t" and seen[-1].headers["authorization"] == "Bearer service-token"

    frame = client.get("/browser-api/t/frame?user=someone-else")
    assert frame.content == b"jpeg" and frame.headers["content-type"] == "image/jpeg"
    assert dict(seen[-1].url.params)["user"] == me["id"]

    assert client.post("/browser-api/t/input", json={"event": {"type": "text", "text": "x"}}, headers={"X-Labb": "1"}).status_code == 200
    assert json.loads(seen[-1].content)["user"] == me["id"]
    assert client.delete("/browser-api/profile", headers={"X-Labb": "1"}).status_code == 204
    assert dict(seen[-1].url.params)["user"] == me["id"]


def test_the_gateway_needs_a_login(client):
    from fastapi.testclient import TestClient

    from labb.server.app import app

    assert TestClient(app).get("/browser-api/t/frame").status_code == 401


def test_the_server_never_runs_a_browser_itself(client, monkeypatch):
    # Importing the server's app set it; with no service there's simply no browser
    assert browser.service_only is True
    monkeypatch.setattr(browser, "BROWSER_URL", None)
    assert browser.enabled() is False
    assert client.get("/models").json()["browser"] is False
    assert "no browser" in browser.call(None, "look")["error"]


def test_the_chat_request_turns_it_on(client, fake_model, monkeypatch):
    seen = {}
    real = browser.is_on
    monkeypatch.setattr(browser, "BROWSER_URL", "http://browser:3100")

    def spy(config):
        seen["configurable"] = dict(config["configurable"])
        return real(config)

    monkeypatch.setattr(browser, "is_on", spy)
    monkeypatch.setattr(browser, "call", lambda config, action, **args: {"text": "the page"})
    from conftest import ai
    fake_model(ai("", [("browser_look", {})]), ai("done"))
    client.post("/chat", json={"message": "look", "browser": True}, headers={"X-Labb": "1"})
    assert seen["configurable"]["browser"] is True and seen["configurable"]["user_id"]


def test_the_chat_asks_with_the_live_view(client, fake_model, monkeypatch):
    import json

    from conftest import ai
    from labb.server import app as server

    monkeypatch.setattr(browser, "BROWSER_URL", "http://browser:3100")
    monkeypatch.setattr(browser, "call", lambda config, action, **args: {"site": "shop.example", "name": "Save"} if action == "inspect" else {"text": "the page"})
    browser._sites.clear()
    fake_model(ai("", [("browser_click", {"ref": 3})]), ai("done"))

    # Answer from outside, as the page does
    def answer():
        import time
        for _ in range(100):
            if server.approvals:
                server.approvals[next(iter(server.approvals))][1].put("yes")
                return
            time.sleep(0.05)

    threading.Thread(target=answer, daemon=True).start()
    lines = client.post("/chat", json={"message": "click", "browser": True}, headers={"X-Labb": "1"}).text.splitlines()
    events = [json.loads(line) for line in lines]
    asked = next(e for e in events if e["type"] == "approval")
    assert asked["view"] and asked["handover"] is False and "shop.example" in asked["title"]
    assert asked["view"] in {e.get("thread_id") for e in events}


# --- With a real browser ------------------------------------------------------------------------


def chromium() -> bool:
    if not importlib.util.find_spec("playwright"):
        return False
    from pathlib import Path
    return any(Path.home().glob(".cache/ms-playwright/chromium-*"))


real = pytest.mark.skipif(not chromium(), reason="Chromium isn't installed")


class Site(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        page = b"<title>Form</title><form method=post action=/done><label>Name <input name=n></label><button>Send it</button></form>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(page)

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<title>Thanks</title>You sent " + body)

    def log_message(self, *args):
        pass


@pytest.fixture
def local_browser(monkeypatch, tmp_path):
    """The browser in this process, as the terminal has it"""
    monkeypatch.setattr(browser, "service_only", False)
    monkeypatch.setattr(browser.settings, "USER_DIR", tmp_path)
    monkeypatch.setenv("LABB_BROWSER_HEADLESS", "1")
    browser._here.cache_clear()
    browser._sites.clear()
    browser._visited.clear()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    browser._here().close()
    browser._here.cache_clear()


@real
def test_a_form_from_start_to_end_as_the_terminal_does_it(local_browser):
    asker = Asker()
    # Its own machine: asks first
    text = run(browser_open, asker, url=local_browser)
    assert len(asker.asked) == 1 and "your own machine" in asker.asked[0]["detail"]
    assert '[1] textbox "Name"' in text and '[2] button "Send it" (submits the form)' in text
    # The site's yes, once
    filled = run(browser_fill, asker, fields={"1": "Ada"})
    assert 'value="Ada"' in filled and "127.0.0.1" in asker.asked[1]["title"]
    # And the form's, showing what goes
    done = run(browser_click, asker, ref=2)
    assert "Name: Ada" in asker.asked[2]["detail"] and "POST" in asker.asked[2]["detail"]
    assert "Page: Thanks" in done and "You sent n=Ada" in done
    assert len(asker.asked) == 3
