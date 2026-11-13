"""A real Chromium against pages served here: nothing is faked, since what matters is what a browser does."""

import asyncio
import base64
import http.server
import json
import re
import threading
from urllib.parse import parse_qs

import pytest

from labb_browser import urls
from labb_browser.driver import Driver

FORM = """<!doctype html><title>Sign up</title>
<h1>Create your account</h1>
<p>Fill this in.</p>
<form method="post" action="/done">
  <label>Email <input name="email" required type="email"></label>
  <label>Password <input name="pw" type="password"></label>
  <label for="c">Country</label><select id="c" name="country"><option>Albania</option><option value="xk">Kosovo</option></select>
  <label><input type="checkbox" name="terms"> I agree</label>
  <label style="display:block"><input type="radio" name="plan" value="free" checked> Free</label>
  <label style="display:block"><input type="radio" name="plan" value="pro"> Pro</label>
  <label class="custom"><input type="checkbox" name="news" style="display:none"> News</label>
  <textarea name="note" placeholder="A note"></textarea>
  <input type="file" name="doc">
  <button>Create account</button>
</form>
<a href="/other">Other page</a>"""

PAGES = {
    "/": FORM,
    "/pictures": """<title>Pictures</title>
<img alt="A red barn" width="200" height="100" src="data:image/gif;base64,R0lGODlhAQABAAAAACw=">
<img width="200" height="100" src="data:image/gif;base64,R0lGODlhAQABAAAAACw=">
<img width="16" height="16" src="data:image/gif;base64,R0lGODlhAQABAAAAACw=">""",
    "/other": """<title>Other</title><h2>Elsewhere</h2><a href="/" target="_blank">Open in a new tab</a>
        <button onclick="alert('hello')">Alert</button>
        <button onclick="document.title = confirm('Sure?') ? 'yes' : 'no'; document.body.append(document.title)">Ask</button>
        <div style="position:fixed;inset:0;display:none" id="over">cover</div>
        <button onclick="document.getElementById('over').style.display='block'">Cover</button>
        <button id="dis" disabled>Off</button>
        <a href="/file.csv" download>Get data</a>""",
    "/inlabel": """<title>In label</title><form method=post action=/to><label>Plan <select name=p><option>Free<option>Pro</select></label>
        <input type=hidden name=action value=x><button>Go</button></form>""",
    "/late": """<title>Late</title><div id=a>loading</div><script>setTimeout(() => a.innerHTML = '<button>Ready now</button>', 800)</script>""",
    "/grid": """<title>Grid</title><h1>Sheet</h1>
        <canvas id=g width=400 height=200 tabindex=0></canvas>
        <textarea id=hidden style="position:absolute;opacity:0;width:1px;height:1px" aria-label="cell input"></textarea>
        <pre id=out></pre>
        <script>
          const log = [];
          document.addEventListener('keydown', e => { log.push(e.key); out.textContent = JSON.stringify(log); });
          g.addEventListener('click', e => { window.at = [e.offsetX, e.offsetY]; document.title = 'clicked ' + e.offsetX + ',' + e.offsetY; document.getElementById('hidden').focus(); });
          document.addEventListener('paste', e => { document.title = 'pasted'; out.textContent = e.clipboardData.getData('text/plain'); e.preventDefault(); });
        </script>""",
    "/shadow": """<title>Shadow</title><my-el></my-el><iframe src="/inner"></iframe>
        <script>class E extends HTMLElement { constructor() { super(); this.attachShadow({mode:'open'}).innerHTML = '<button onclick="this.textContent=`pressed`">In shadow</button>'; } }
        customElements.define('my-el', E);</script>""",
    "/inner": """<title>Inner</title><p>Text inside the frame, long enough to count as content, surely it is.</p><button onclick="this.textContent='hit'">In frame</button>""",
    "/roles": """<title>Roles</title><div role="button" tabindex="0" onclick="this.textContent='done'">Fancy</div>
        <div role="checkbox" aria-checked="false" tabindex="0" onclick="this.setAttribute('aria-checked', this.getAttribute('aria-checked') !== 'true')">Toggle me</div>
        <div contenteditable="true" aria-label="Editor"></div>
        <div hidden><button>Never</button></div><button style="display:none">Nope</button>
        <dialog open><p>Hello there</p><button>Close</button></dialog>
        <select multiple name=m><option>A</option><option>B</option></select>""",
    "/chooser": """<title>Chooser</title><input type=file id=f style="display:none" onchange="document.title=this.files[0].name">
        <button onclick="f.click()">Import</button>""",
    "/drag": """<title>Drag</title><div id=a role=button tabindex=0 draggable=true style="width:80px;height:80px;background:red">Drag me</div>
        <div id=b role=button tabindex=0 style="width:80px;height:80px;margin-top:40px;background:blue">Drop here</div>
        <script>b.addEventListener('dragover', e => e.preventDefault());
          b.addEventListener('drop', e => { e.preventDefault(); document.title = 'dropped on ' + e.target.id; });</script>""",
    "/hover": """<title>Hover</title><button onmouseover="document.getElementById('m').hidden=false">Menu</button><div id=m hidden><a href=/other>Inside</a></div>""",
    "/tall": """<title>Tall</title><div style=height:5000px>Top</div><button>Bottom button</button>""",
    "/long": "<title>Long</title>" + "<p>" + "words " * 200 + "</p>" * 1 + "".join(f"<p>{'filler ' * 100}{i}</p>" for i in range(40)) + "<button>Save everything</button>",
}
posted = []


class Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, body: str, kind="text/html", extra=None):
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/file.csv":
            return self._send("a,b\n1,2\n", "text/csv", {"Content-Disposition": 'attachment; filename="data.csv"'})
        if path == "/login":
            return self._send("<title>Login</title><form method=post action=/session><input name=u><button>Go</button></form>")
        if path == "/account":
            signed = "sid=1" in self.headers.get("Cookie", "")
            return self._send(f"<title>Account</title><p>{'Signed in' if signed else 'Signed out'}</p>")
        self._send(PAGES.get(path, "<title>Nope</title>not found"))

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        kind = self.headers.get("Content-Type", "")
        posted.append(body)
        if self.path == "/session":
            return self._send("<title>In</title>ok", extra={"Set-Cookie": "sid=1; Max-Age=86400; Path=/"})
        fields = parse_qs(body.decode("latin-1")) if "urlencoded" in kind else {}
        self._send(f"<title>Done</title><h1>Thanks</h1><pre>{json.dumps(fields)}</pre>")

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def site():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


class Run:
    """A driver on an event loop of its own, used as the tools use it"""

    def __init__(self, tmp, allow_private=True):
        self.loop = asyncio.new_event_loop()
        self.driver = Driver(tmp, allow_private=allow_private)

    def __call__(self, verb, /, thread="t", user="u", **args):
        return self.loop.run_until_complete(self.driver.act(user, thread, verb, args))

    def ok(self, verb, /, **args):
        result = self(verb, **args)
        assert "error" not in result, result
        return result["text"]

    def run(self, coroutine):
        return self.loop.run_until_complete(coroutine)

    def close(self):
        self.run(self.driver.close())
        self.loop.close()


@pytest.fixture
def run(tmp_path):
    r = Run(tmp_path)
    yield r
    r.close()


def ref(text: str, pattern: str) -> int:
    """The number of the first control that matches"""
    found = re.search(r"\[(\d+)\] [^\n]*" + pattern, text)
    assert found, f"{pattern!r} not in:\n{text}"
    return int(found.group(1))


def title(run):
    return run.ok("look").split("\n")[0]


def test_a_form_reads_as_numbered_controls(run, site):
    text = run.ok("open", url=site)
    assert "Page: Sign up" in text
    assert "# Create your account" in text
    assert re.search(r'\[\d+\] textbox "Email" value="" type=email \(required\)', text), text
    assert re.search(r'\[\d+\] textbox "Password" \(sensitive', text), text
    assert re.search(r'combobox "Country" value="Albania" options: Albania \| Kosovo', text)
    assert re.search(r'checkbox "I agree" \(not checked\)', text)
    assert re.search(r'radio "Free" \(checked\)', text)
    assert re.search(r'checkbox "News" \(not checked\)', text), "a custom checkbox has its label as the control"
    assert re.search(r'button "Create account" \(submits the form\)', text)
    assert re.search(r'link "Other page" -> /other', text)
    assert "Fill this in." in text


def test_filling_in_and_submitting_a_form(run, site):
    text = run.ok("open", url=site)
    result = run.ok("fill", fields={
        str(ref(text, '"Email"')): "a@b.co",
        str(ref(text, '"Country"')): "Kosovo",
        str(ref(text, '"I agree"')): True,
        str(ref(text, '"Pro"')): True,
        str(ref(text, '"News"')): True,
        str(ref(text, '"A note"')): "hi there",
    })
    assert "filled in" in result and "chose Kosovo" in result and "'I agree': checked" in result
    assert re.search(r'"Email" value="a@b.co"', result)
    assert re.search(r'checkbox "News" \(checked\)', result), result
    assert re.search(r'radio "Pro" \(checked\)', result)
    done = run.ok("click", ref=ref(result, "Create account"))
    assert "Page: Done" in done
    sent = json.loads(re.search(r"\{.*\}", done, re.S).group(0).replace("&quot;", '"'))
    assert sent["email"] == ["a@b.co"] and sent["country"] == ["xk"] and sent["plan"] == ["pro"] and sent["note"] == ["hi there"]


def test_sensitive_fields_are_left_to_the_user(run, site):
    text = run.ok("open", url=site)
    pw = ref(text, '"Password"')
    result = run.ok("fill", fields={str(pw): "secret"})
    assert "not filled in" in result and "browser_handover" in result
    # Typing into it instead doesn't get around that
    assert "sensitive" in run("type", text="secret", ref=pw)["error"]
    # Nor does pressing the keys one by one

    assert "sensitive" in run("press", keys="s e c r e t", ref=pw)["error"]
    # What the user typed never shows
    page = run.driver.existing("u", "t").current
    run.run(page.fill("input[name=pw]", "hunter2"))
    assert "hunter2" not in run.ok("look")
    # Nor in what a form is said to send
    assert run("inspect", ref=ref(text, "Create account"))["form"]["fields"][1]["value"] == "(hidden)"


def test_inspect_says_what_a_button_would_send(run, site):
    text = run.ok("open", url=site)
    run.ok("fill", fields={str(ref(text, '"Email"')): "a@b.co"})
    info = run("inspect", ref=ref(text, "Create account"))
    assert info["submits"] and info["form"]["method"] == "post" and info["site"] == "127.0.0.1"
    assert {"name": "Email", "value": "a@b.co"} in info["form"]["fields"]
    assert info["form"]["action"] == f"{site}/done"
    assert run("inspect", ref=ref(text, "Other page"))["submits"] is False


def test_a_dropdown_inside_its_label(run, site):
    text = run.ok("open", url=f"{site}/inlabel")
    assert re.search(r'combobox "Plan" value="Free"', text), text
    assert "'Plan': chose Pro" in run.ok("fill", fields={str(ref(text, '"Plan"')): "Pro"})
    info = run("inspect", ref=ref(text, '"Go"'))
    assert {"name": "Plan", "value": "Pro"} in info["form"]["fields"]
    # A field named "action" doesn't hide where the form goes
    assert info["form"]["action"] == f"{site}/to"


def test_numbers_stay_the_same_across_snapshots(run, site):
    text = run.ok("open", url=site)
    email = ref(text, '"Email"')
    again = run.ok("look")
    assert ref(again, '"Email"') == email
    # So two calls from one reply both work
    run.ok("fill", fields={str(email): "x@y.z"})
    assert 'value="x@y.z"' in run.ok("fill", fields={str(ref(text, '"A note"')): "n"})


def test_a_number_from_a_page_thats_gone(run, site):
    text = run.ok("open", url=site)
    link, email = ref(text, "Other page"), ref(text, '"Email"')
    run.ok("click", ref=link)
    assert "isn't on the page any more" in run("click", ref=email)["error"]
    assert "isn't a number" in run("click", ref="the button")["error"]
    assert "[9999]" in run("click", ref=9999)["error"]


def test_going_back_and_forward(run, site):
    text = run.ok("open", url=site)
    assert "Page: Other" in run.ok("click", ref=ref(text, "Other page"))
    assert "Page: Sign up" in run.ok("open", url="back")
    assert "Page: Other" in run.ok("open", url="forward")
    assert "Page: Other" in run.ok("open", url="reload")
    assert "Page: Other" in run.ok("look")


def test_a_new_tab_becomes_the_current_one(run, site):
    text = run.ok("open", url=f"{site}/other")
    after = run.ok("click", ref=ref(text, "Open in a new tab"))
    assert "A new tab opened" in after and "Page: Sign up" in after and "Tabs: 2 open, this is 2" in after
    listed = run.ok("tabs")
    assert "1. Other" in listed and "(current)" in listed.split("\n")[1]
    assert "Page: Other" in run.ok("tabs", action="switch", tab=1)
    assert "Page: Sign up" in run.ok("tabs", action="close", tab=1)
    assert "no tab 5" in run("tabs", action="switch", tab=5)["error"]


def test_tabs_are_limited(run, site):
    run.ok("open", url=site)
    for _ in range(8):
        last = run.ok("open", url=f"{site}/other", new_tab=True)
    assert "Tabs: 6 open" in last


def test_the_pages_questions(run, site):
    text = run.ok("open", url=f"{site}/other")
    assert "The page said: 'hello'" in run.ok("click", ref=ref(text, '"Alert"'))
    no = run.ok("click", ref=ref(text, '"Ask"'))
    assert "got a no" in no and "dialog=\"accept\"" in no and "Page: no" in no
    yes = run.ok("click", ref=ref(text, '"Ask"'), dialog="accept")
    assert "got a yes" in yes and "Page: yes" in yes
    # The yes was for that click only
    assert "got a no" in run.ok("click", ref=ref(text, '"Ask"'))


def test_a_covered_button_says_so(run, site):
    text = run.ok("open", url=f"{site}/other")
    run.ok("click", ref=ref(text, '"Cover"'))
    error = run("click", ref=ref(text, '"Alert"'))["error"]
    assert "covered" in error and "cover" in error


def test_a_disabled_button_says_so(run, site):
    text = run.ok("open", url=f"{site}/other")
    assert "(disabled)" in text
    assert "disabled" in run("click", ref=ref(text, '"Off"'))["error"]


def test_downloads(run, site):
    text = run.ok("open", url=f"{site}/other")
    after = run.ok("click", ref=ref(text, "Get data"))
    # The download may finish just after the page settles
    listed = run.ok("files")
    assert "data.csv" in listed + after
    assert run.ok("files", name="data.csv") == "a,b\n1,2\n"
    raw = run("files", name="data.csv", raw=True)
    assert base64.b64decode(raw["data"]) == b"a,b\n1,2\n"
    assert "Nothing was downloaded" in run("files", name="nope.csv")["error"]


def test_uploads(run, site):
    text = run.ok("open", url=site)
    field = ref(text, "file input")
    data = base64.b64encode(b"a,b\n").decode()
    after = run.ok("upload", ref=field, name="d.csv", data=data)
    assert "d.csv" in after and "files: d.csv" in after
    assert "takes a file" in run.ok("fill", fields={str(field): "x"})


def test_a_button_that_opens_the_file_picker(run, site):
    text = run.ok("open", url=f"{site}/chooser")
    data = base64.b64encode(b"x").decode()
    assert "Page: d.csv" in run.ok("upload", ref=ref(text, "Import"), name="d.csv", data=data)
    text = run.ok("open", url=f"{site}/chooser")
    assert "file picker" in run.ok("click", ref=ref(text, "Import"))
    assert "Page: e.csv" in run.ok("upload", name="e.csv", data=data)
    assert "file picker" in run("upload", name="e.csv", data=data)["error"]


def test_images_without_words_are_still_mentioned(run, site):
    text = run.ok("open", url=f"{site}/pictures")
    assert "[image: A red barn]" in text
    assert text.count("[image, no description]") == 1
    assert run("look", screenshot=True)["image"]


def test_a_grid_drawn_on_a_canvas(run, site):
    """What a spreadsheet app comes down to: a click somewhere, then keys and a paste"""
    run.ok("open", url=f"{site}/grid")
    shot = run("look", screenshot=True)
    assert shot["image"] and base64.b64decode(shot["image"])[:2] == b"\xff\xd8"
    assert "clicked 50,20" in run.ok("click", x=58, y=20 + 8 + 37 + 21)
    typed = run.ok("type", text="ab\tc\nd")
    log = json.loads(re.search(r'^\["[^\n]*\]$', typed, re.M).group(0))
    assert log == ["a", "b", "Tab", "c", "Enter", "d"]
    run.ok("click", x=58, y=86)


def test_pasting_rows(run, site):
    # On localhost the real clipboard is there, and a page's paste event gets the text
    run.ok("open", url=f"{site}/grid")
    run.ok("click", x=58, y=86)
    pasted = run.ok("paste", text="a\tb\n1\t2")
    assert "Page: pasted" in pasted
    page = run.driver.existing("u", "t").current
    assert run.run(page.evaluate("() => document.getElementById('out').textContent")) == "a\tb\n1\t2"
    # And it doesn't stay on the clipboard for the next page
    run.run(page.context.grant_permissions(["clipboard-read"]))
    assert run.run(page.evaluate("() => navigator.clipboard.readText()")) == ""


def test_shadow_dom_and_frames(run, site):
    text = run.ok("open", url=f"{site}/shadow")
    assert "In shadow" in text and "Text inside the frame" in text and "--- frame" in text
    assert 'button "pressed"' in run.ok("click", ref=ref(text, "In shadow"))
    assert 'button "hit"' in run.ok("click", ref=ref(text, "In frame"))


def test_roles_hidden_elements_and_dialogs(run, site):
    text = run.ok("open", url=f"{site}/roles")
    assert "Never" not in text and "Nope" not in text
    assert re.search(r'button "Fancy"', text)
    assert re.search(r'checkbox "Toggle me" \(not checked\)', text)
    assert re.search(r'textbox "Editor"', text)
    assert '--- dialog' in text and "Hello there" in text and "--- end of dialog ---" in text
    assert 'button "done"' in run.ok("click", ref=ref(text, "Fancy"))
    assert re.search(r'"Toggle me" \(checked, focused\)', run.ok("fill", fields={str(ref(text, "Toggle me")): True}))
    edited = run.ok("fill", fields={str(ref(text, '"Editor"')): "words"})
    assert 'value="words"' in edited
    assert "chose A, B" in run.ok("fill", fields={str(ref(text, "listbox")): ["A", "B"]})


def test_waiting_for_what_a_page_loads_later(run, site):
    text = run.ok("open", url=f"{site}/late")
    # Reading settles until the text stops changing, so often it's there already
    done = run.ok("wait", text="Ready now", seconds=5)
    assert "is there" in done and 'button "Ready now"' in done
    assert "not there" in run.ok("wait", text="Nothing like this", seconds=0.2)
    assert "still there" in run.ok("wait", text="Ready now", gone=True, seconds=0.2)
    assert text


def test_hover_opens_what_it_opens(run, site):
    text = run.ok("open", url=f"{site}/hover")
    assert "Inside" not in text
    assert "Inside" in run.ok("hover", ref=ref(text, "Menu"))


def test_scrolling(run, site):
    text = run.ok("open", url=f"{site}/tall")
    assert "now at 0" not in run.ok("scroll", direction="down")
    assert "now at 0" in run.ok("scroll", direction="top")
    assert "now at 0" not in run.ok("scroll", direction="bottom")
    assert "Scrolled to" in run.ok("scroll", ref=ref(text, "Bottom button"))
    assert "direction" in run("scroll", direction="sideways")["error"]


def test_dragging(run, site):
    text = run.ok("open", url=f"{site}/drag")
    assert "Page: dropped on b" in run.ok("drag", source=ref(text, "Drag me"), target=ref(text, "Drop here"))


def test_keys_by_their_usual_names(run, site):
    text = run.ok("open", url=f"{site}/grid")
    typed = run.ok("press", keys="ctrl+a enter ESC tab")
    log = json.loads(re.search(r'^\["[^\n]*\]$', typed, re.M).group(0))
    assert log == ["Control", "a", "Enter", "Escape", "Tab"]
    assert "isn't a key" in run("press", keys="NoSuchKey")["error"]
    assert text


def test_a_long_page_comes_in_parts_with_its_controls_in_reach(run, site):
    text = run.ok("open", url=f"{site}/long")
    assert len(text) < 11_000
    assert "more characters" in text and "Controls further down" in text and "Save everything" in text
    start = int(re.search(r"start=(\d+)", text).group(1))
    assert "Save everything" in run.ok("look", start=start + 8000)


def test_finding_on_a_page(run, site):
    run.ok("open", url=site)
    found = run.ok("look", find="country")
    assert "Country" in found and "Email" not in found
    assert "Nothing on the page mentions" in run.ok("look", find="zebra")


def test_no_page_open(run):
    assert "browser_open" in run("look")["error"]
    assert "browser_open" in run("click", ref=1)["error"]
    assert "no action" in run("nonsense")["error"]
    assert "Bad arguments" in run("click", nope=1)["error"]


def test_only_web_addresses(run):
    for address in ("file:///etc/passwd", "chrome://settings", "javascript:alert(1)", "data:text/html,x"):
        assert "http" in run("open", url=address)["error"]


@pytest.mark.parametrize("typed, opened", [
    ("localhost:5173", "http://localhost:5173"),
    ("127.0.0.1:3000/a", "http://127.0.0.1:3000/a"),
    ("0.0.0.0:3000", "http://0.0.0.0:3000"),
    ("[::1]:5173", "http://[::1]:5173"),
    ("192.168.1.217:3000", "http://192.168.1.217:3000"),
    ("myapp.local:8080", "http://myapp.local:8080"),
    ("api.localhost:8000", "http://api.localhost:8000"),
    ("example.com:8443", "https://example.com:8443"),
    ("localhost.example.com", "https://localhost.example.com"),
    ("8.8.8.8", "https://8.8.8.8"),
])
def test_an_address_without_a_scheme_gets_the_one_it_speaks(typed, opened):
    assert urls.normalize(typed) == opened


def test_private_addresses_are_refused(tmp_path, site):
    run = Run(tmp_path, allow_private=False)
    try:
        assert "private or local" in run("open", url=site)["error"]
        assert "private or local" in run("open", url="http://169.254.169.254/latest")["error"]
        assert "private or local" in run("open", url="http://localhost:1/")["error"]
        # A public page can't reach in either: its requests go through the same check
        page = run.run(run.driver.session("u", "t"))
        tab = run.run(run.driver._open_tab(page))

        async def fetched():
            await tab.route("http://public.example/", lambda route: route.fulfill(body="<title>Public</title>"))
            await tab.goto("http://public.example/")
            return await tab.evaluate("address => fetch(address, {mode: 'no-cors'}).then(() => 'reached', () => 'blocked')", site)

        assert run.run(fetched()) == "blocked"
    finally:
        run.close()


def test_chats_have_their_own_tabs_and_people_their_own_signins(run, site):
    run.ok("open", url=f"{site}/login")
    text = run("look")["text"]
    run("click", ref=ref(text, '"Go"'))
    assert "Signed in" in run.ok("open", url=f"{site}/account")
    # Another chat of the same person: signed in, on a page of its own
    assert "No page is open" in run("look", thread="other")["error"]
    assert "Signed in" in run("open", thread="other", url=f"{site}/account")["text"]
    # Someone else: not
    assert "Signed out" in run("open", user="them", url=f"{site}/account")["text"]


def test_signins_are_kept_and_can_be_forgotten(tmp_path, site):
    first = Run(tmp_path)
    text = first.ok("open", url=f"{site}/login")
    first("click", ref=ref(text, '"Go"'))
    first.close()

    second = Run(tmp_path)
    assert "Signed in" in second.ok("open", url=f"{site}/account")
    second.run(second.driver.forget("u"))
    assert "Signed out" in second.ok("open", url=f"{site}/account")
    second.close()


def test_idle_chats_close(run, site):
    run.ok("open", url=site)
    import time
    run.run(run.driver.sweep_once(time.monotonic() + 10_000))
    assert run.driver.existing("u", "t") is None
    assert run.driver.profiles == {}


def test_watching_doesnt_keep_tabs_open(run, site):
    run.ok("open", url=site)
    import time
    used = run.driver.existing("u", "t").used
    time.sleep(0.01)
    assert run.run(run.driver.view_frame("u", "t"))
    assert run.driver.existing("u", "t").used == used


def test_the_address_bar_opens_the_first_page(run, site):
    assert run.run(run.driver.view_input("u", "fresh", {"type": "click", "x": 1, "y": 1})) == "No page is open"
    # A refused address doesn't start anything
    assert run.run(run.driver.view_input("u", "fresh", {"type": "open", "url": "file:///etc/passwd"}))
    assert run.driver.existing("u", "fresh") is None
    assert run.run(run.driver.view_input("u", "fresh", {"type": "open", "url": site})) is None
    state = run.run(run.driver.view_state("u", "fresh"))
    assert len(state["tabs"]) == 1 and state["tabs"][0]["url"].rstrip("/") == site


def test_the_live_view(run, site):
    assert run.run(run.driver.view_frame("u", "t")) is None
    assert run.run(run.driver.view_state("u", "t")) is None
    text = run.ok("open", url=site)
    frame = run.run(run.driver.view_frame("u", "t"))
    assert frame[:2] == b"\xff\xd8"
    state = run.run(run.driver.view_state("u", "t"))
    assert state["tabs"][0]["url"].rstrip("/") == site and state["tabs"][0]["current"] and state["width"] == 1280
    # What the user does happens on the page
    page = run.driver.existing("u", "t").current
    box = run.run(page.locator("input[name=email]").bounding_box())
    assert run.run(run.driver.view_input("u", "t", {"type": "click", "x": box["x"] + 5, "y": box["y"] + 5})) is None
    assert run.run(run.driver.view_input("u", "t", {"type": "text", "text": "me@x.y"})) is None
    assert 'value="me@x.y"' in run.ok("look")
    assert run.run(run.driver.view_input("u", "t", {"type": "key", "key": "Tab"})) is None
    assert run.run(run.driver.view_input("u", "t", {"type": "open", "url": f"{site}/other"})) is None
    assert "Page: Other" in run.ok("look")
    assert "http" in run.run(run.driver.view_input("u", "t", {"type": "open", "url": "file:///etc/passwd"}))
    assert run.run(run.driver.view_input("u", "t", {"type": "nonsense"}))
    assert run.run(run.driver.view_input("x", "t", {"type": "back"})) == "No page is open"
    # Someone else can't see it
    assert run.run(run.driver.view_frame("them", "t")) is None
    assert text
