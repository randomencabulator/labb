"""The browser itself, on Playwright. Each person has a browser context of their own with their
sign-ins, kept in a file between uses; each chat has its tabs in it, so chats share sign-ins but
not what they have open.

`act` does one thing and answers {"text": what happened and the page as it is now}, with
{"error": …} for what went wrong, said for the model. Nothing here decides what needs asking:
labb's tools do (tools/browser.py), with `inspect`. What's here regardless of who's calling: no
private addresses unless allowed, and sensitive fields aren't filled in by the model."""

import asyncio
import base64
import hashlib
import json
import mimetypes
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from labb_browser import snapshot, urls

WIDTH, HEIGHT = 1280, 800
SETTLE_SECONDS = 6
QUIET_MS = 500
NAVIGATE_TIMEOUT = 30_000
ACT_TIMEOUT = 8_000
# Characters of a page in one snapshot
SNAPSHOT_CHARS = int(os.getenv("BROWSER_SNAPSHOT_CHARS", 10_000))
MAX_ITEMS = 4_000
MAX_TABS = 6
TYPE_CHARS = 5_000
PASTE_CHARS = 1_000_000
MAX_UPLOAD = 25_000_000
MAX_DOWNLOAD = 100_000_000
DOWNLOAD_CHARS = 20_000
# A chat's tabs close after this long without use, and the browser context when no chat is left
IDLE_SECONDS = float(os.getenv("BROWSER_IDLE_MINUTES", 30)) * 60
SWEEP_SECONDS = 60
# People with a browser context at once: the one unused the longest makes way
MAX_PROFILES = int(os.getenv("BROWSER_PROFILES", 4))


class Problem(Exception):
    """What went wrong, said for the model"""


def first_line(error: Exception) -> str:
    return str(error).splitlines()[0] if str(error) else type(error).__name__


@dataclass
class TabState:
    """What a tab's snapshots remember: a frame's Map, and which frame each number is in"""

    states: dict = field(default_factory=dict)
    frame_of: dict = field(default_factory=dict)
    next_ref: int = 1


@dataclass
class Session:
    """One chat's tabs"""

    thread: str
    profile: "Profile"
    tabs: list = field(default_factory=list)
    current: object = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    used: float = field(default_factory=time.monotonic)
    notes: list = field(default_factory=list)
    # How the next question from the page is answered: "accept" or None (no)
    dialog: str | None = None
    dialog_text: str | None = None
    chooser: object = None
    downloads: dict = field(default_factory=dict)
    folder: str | None = None
    state: dict = field(default_factory=dict)


@dataclass
class Profile:
    user: str
    context: object
    path: Path
    sessions: dict = field(default_factory=dict)
    used: float = field(default_factory=time.monotonic)
    changed: bool = False


class Driver:
    def __init__(self, data_dir: str | Path, allow_private: bool = False, headless: bool = True):
        self.data_dir = Path(data_dir)
        self.allow_private = allow_private
        self.headless = headless
        self.playwright = None
        self.browser = None
        self.profiles: dict[str, Profile] = {}
        self.sweeper = None
        self._lock = None
        # The clipboard is the whole browser's, so only one paste at a time, and it's emptied after
        self._paste_lock = None

    # --- The browser, people's contexts, chats' tabs -----------------------------------------

    async def _browser(self):
        if self._lock is None:
            self._lock = asyncio.Lock()
            self._paste_lock = asyncio.Lock()
        async with self._lock:
            if self.browser is None or not self.browser.is_connected():
                from playwright.async_api import async_playwright
                if self.playwright is None:
                    self.playwright = await async_playwright().start()
                self.browser = await self.playwright.chromium.launch(headless=self.headless, channel="chromium" if self.headless else None)
                # Whatever a browser that went away had open is gone with it
                self.profiles.clear()
            if self.sweeper is None:
                self.sweeper = asyncio.create_task(self._sweep())
            return self.browser

    def _path(self, user: str) -> Path:
        return self.data_dir / "profiles" / f"{hashlib.sha256(user.encode()).hexdigest()[:32]}.json"

    async def _profile(self, user: str) -> Profile:
        browser = await self._browser()
        async with self._lock:
            profile = self.profiles.get(user)
            if profile is None:
                if len(self.profiles) >= MAX_PROFILES:
                    await self._close_profile(min(self.profiles.values(), key=lambda p: p.used))
                path = self._path(user)
                context = await browser.new_context(
                    viewport={"width": WIDTH, "height": HEIGHT}, accept_downloads=True,
                    storage_state=str(path) if path.is_file() else None,
                )
                context.set_default_timeout(ACT_TIMEOUT)
                context.set_default_navigation_timeout(NAVIGATE_TIMEOUT)
                profile = self.profiles[user] = Profile(user, context, path)
                context.on("page", lambda page: asyncio.create_task(self._new_page(profile, page)))
                if not self.allow_private:
                    await context.route("**/*", self._route)
                    await context.route_web_socket(re.compile(r".*"), self._route_socket)
            profile.used = time.monotonic()
            return profile

    async def _route(self, route):
        """Every request is checked: the page's own, and those of what it loads"""
        address = route.request.url
        if address.startswith(("http://", "https://")) and await asyncio.to_thread(urls.is_private, address):
            await route.abort("blockedbyclient")
        else:
            await route.fallback()

    async def _route_socket(self, socket):
        if await asyncio.to_thread(urls.is_private, socket.url.replace("ws", "http", 1)):
            await socket.close()
        else:
            socket.connect_to_server()

    async def session(self, user: str, thread: str) -> Session:
        profile = await self._profile(user)
        session = profile.sessions.get(thread)
        if session is None:
            session = profile.sessions[thread] = Session(thread, profile)
        session.used = profile.used = time.monotonic()
        return session

    def existing(self, user: str, thread: str) -> Session | None:
        profile = self.profiles.get(user)
        return profile.sessions.get(thread) if profile else None

    async def _new_page(self, profile: Profile, page):
        """A tab a page opened goes with the chat of the tab that opened it, and becomes the current one"""
        try:
            opener = await page.opener()
        except Exception:
            opener = None
        for session in profile.sessions.values():
            if opener in session.tabs:
                self._watch(session, page)
                session.tabs.append(page)
                session.current = page
                session.notes.append("A new tab opened, and is the current one now.")
                await self._limit_tabs(session)
                return

    def _watch(self, session: Session, page):
        session.state[page] = TabState()
        page.on("dialog", lambda dialog: asyncio.create_task(self._on_dialog(session, dialog)))
        page.on("download", lambda download: asyncio.create_task(self._on_download(session, download)))
        page.on("filechooser", lambda chooser: self._on_chooser(session, chooser))
        page.on("close", lambda page: self._on_close(session, page))

    def _on_close(self, session: Session, page):
        if page in session.tabs:
            session.tabs.remove(page)
        session.state.pop(page, None)
        if session.current is page:
            session.current = session.tabs[-1] if session.tabs else None

    async def _limit_tabs(self, session: Session):
        while len(session.tabs) > MAX_TABS:
            oldest = next(tab for tab in session.tabs if tab is not session.current)
            session.notes.append(f"An old tab was closed: at most {MAX_TABS} stay open.")
            await oldest.close()

    async def _on_dialog(self, session: Session, dialog):
        said = dialog.message[:300]
        try:
            if dialog.type == "alert":
                await dialog.accept()
                session.notes.append(f"The page said: {said!r}")
            elif dialog.type == "beforeunload":
                await dialog.accept()
            elif session.dialog == "accept":
                await dialog.accept(session.dialog_text or dialog.default_value)
                session.notes.append(f"The page asked {said!r}, and got a yes.")
            else:
                await dialog.dismiss()
                session.notes.append(f"The page asked {said!r}, and got a no. To answer yes, do it again with dialog=\"accept\".")
        except Exception:
            pass

    async def _on_download(self, session: Session, download):
        name = Path(download.suggested_filename).name or "download"
        try:
            session.folder = session.folder or tempfile.mkdtemp(prefix="labb-downloads-")
            target = Path(session.folder) / name
            for i in range(1, 100):
                if not target.exists():
                    break
                target = Path(session.folder) / f"{Path(name).stem}-{i}{Path(name).suffix}"
            await download.save_as(target)
            if target.stat().st_size > MAX_DOWNLOAD:
                target.unlink()
                session.notes.append(f"{name} wasn't kept: it's over {MAX_DOWNLOAD // 1_000_000} MB.")
                return
            session.downloads[target.name] = target
            session.notes.append(f"Downloaded {target.name} ({target.stat().st_size:,} bytes); browser_files reads it.")
        except Exception as e:
            session.notes.append(f"A download of {name} failed ({first_line(e)}).")

    def _on_chooser(self, session: Session, chooser):
        session.chooser = chooser
        session.notes.append("The page opened a file picker: browser_upload gives it a file.")

    async def _open_tab(self, session: Session):
        page = await session.profile.context.new_page()
        self._watch(session, page)
        session.tabs.append(page)
        session.current = page
        await self._limit_tabs(session)
        return page

    def _page(self, session: Session):
        page = session.current
        if page is None or page.is_closed():
            raise Problem("No page is open: use browser_open first.")
        return page

    async def _save(self, profile: Profile):
        profile.path.parent.mkdir(parents=True, exist_ok=True)
        partial = profile.path.with_suffix(".partial")
        state = await profile.context.storage_state()
        descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as file:
            json.dump(state, file)
        os.replace(partial, profile.path)
        profile.changed = False

    async def _close_session(self, session: Session):
        session.profile.sessions.pop(session.thread, None)
        for tab in list(session.tabs):
            try:
                await tab.close()
            except Exception:
                pass
        if session.folder:
            shutil.rmtree(session.folder, ignore_errors=True)

    async def _close_profile(self, profile: Profile):
        self.profiles.pop(profile.user, None)
        try:
            await self._save(profile)
        except Exception:
            pass
        for session in list(profile.sessions.values()):
            await self._close_session(session)
        try:
            await profile.context.close()
        except Exception:
            pass

    async def _sweep(self):
        """Closes what's been left alone, and keeps sign-ins on disk in case the service stops"""
        while True:
            await asyncio.sleep(SWEEP_SECONDS)
            await self.sweep_once(time.monotonic())

    async def sweep_once(self, now: float):
        for profile in list(self.profiles.values()):
            try:
                for session in list(profile.sessions.values()):
                    if now - session.used > IDLE_SECONDS and not session.lock.locked():
                        await self._close_session(session)
                if profile.changed:
                    await self._save(profile)
                if not profile.sessions and now - profile.used > IDLE_SECONDS:
                    await self._close_profile(profile)
            except Exception:
                # A browser that's gone is started again on next use
                pass

    async def close(self):
        if self.sweeper:
            self.sweeper.cancel()
            self.sweeper = None
        for profile in list(self.profiles.values()):
            await self._close_profile(profile)
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        self.browser = self.playwright = None
        self._lock = self._paste_lock = None

    async def forget(self, user: str):
        """Sign out of everything: the person's context and file are gone"""
        if profile := self.profiles.get(user):
            await self._close_profile(profile)
        self._path(user).unlink(missing_ok=True)

    async def close_chat(self, user: str, thread: str):
        if session := self.existing(user, thread):
            await self._close_session(session)

    # --- Reading the page ---------------------------------------------------------------------

    async def _read_frame(self, tab: TabState, frame) -> list:
        state = tab.states.get(frame)
        for attempt in range(2):
            try:
                if state is None:
                    state = tab.states[frame] = await frame.evaluate_handle(snapshot.MAKE_STATE)
                found = await state.evaluate(snapshot.READ, {"next": tab.next_ref, "maxItems": MAX_ITEMS})
                break
            except Exception:
                # The frame went elsewhere, and its Map with it
                tab.states.pop(frame, None)
                state = None
                if attempt:
                    return []
        for number in range(tab.next_ref, found["next"]):
            tab.frame_of[number] = frame
        tab.next_ref = found["next"]
        return found["items"]

    async def _read_page(self, session: Session) -> list:
        page = self._page(session)
        tab = session.state[page]
        items = await self._read_frame(tab, page.main_frame)
        for frame in page.frames:
            if frame is page.main_frame or frame.is_detached() or frame.url in ("", "about:blank"):
                continue
            inner = await self._read_frame(tab, frame)
            # Not the empty ones and the tracking pixels
            if any(line[1] for line in inner) or sum(len(line[0]) for line in inner) > 80:
                items += [[f"--- frame {frame.url[:90]} ---", False, False], *inner, ["--- end of frame ---", False, False]]
        return items

    async def _snapshot(self, session: Session, start: int = 0, find: str = "") -> str:
        page = self._page(session)
        for attempt in range(2):
            try:
                items = await self._read_page(session)
                title = await page.title()
                break
            except Exception as e:
                # The page went elsewhere while it was read
                if attempt:
                    raise Problem(f"Couldn't read the page ({first_line(e)})")
                await self._settle(page)
        tabs = f"\nTabs: {len(session.tabs)} open, this is {session.tabs.index(page) + 1}" if len(session.tabs) > 1 else ""
        return snapshot.render(title, page.url + tabs, items, start, SNAPSHOT_CHARS, find)

    async def _settle(self, page):
        # A page that's going elsewhere does so right after the click
        await asyncio.sleep(0.1)
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=SETTLE_SECONDS * 1000)
        except Exception:
            pass
        for _ in range(2):
            try:
                await page.evaluate(snapshot.SETTLE, {"maxMs": SETTLE_SECONDS * 1000, "quietMs": QUIET_MS})
                return
            except Exception:
                # Sent elsewhere while it waited
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=SETTLE_SECONDS * 1000)
                except Exception:
                    return

    def _drain(self, session: Session) -> list[str]:
        notes, session.notes = session.notes, []
        return notes

    async def _after(self, session: Session, did: str) -> dict:
        """What was done and how the page is now"""
        page = self._page(session)
        await self._settle(page)
        session.profile.changed = True
        session.dialog = session.dialog_text = None
        view = await self._snapshot(session)
        return {"text": "\n".join([did, *self._drain(session), "", view])}

    async def _element(self, session: Session, ref):
        """The element a number stands for"""
        page = self._page(session)
        tab = session.state[page]
        try:
            ref = int(str(ref).strip("[] "))
        except ValueError:
            raise Problem(f"{ref!r} isn't a number from the page: browser_look shows them.")
        frame = tab.frame_of.get(ref)
        state = tab.states.get(frame) if frame else None
        gone = Problem(f"[{ref}] isn't on the page any more: browser_look shows what's there now.")
        if state is None:
            raise gone
        try:
            handle = await state.evaluate_handle("(state, ref) => { const el = state.els.get(ref); return el && el.isConnected ? el : null; }", ref)
        except Exception:
            raise gone
        element = handle.as_element()
        if element is None:
            raise gone
        return element

    # --- Doing things --------------------------------------------------------------------------

    async def act(self, user: str, thread: str, action: str, args: dict) -> dict:
        method = getattr(self, f"do_{action}", None)
        if method is None:
            return {"error": f"The browser has no action {action!r}"}
        try:
            session = await self.session(user, thread)
            async with session.lock:
                return await method(session, **args)
        except Problem as e:
            return {"error": str(e)}
        except urls.Refused as e:
            return {"error": str(e)}
        except TypeError as e:
            return {"error": f"Bad arguments for {action}: {e}"}
        except Exception as e:
            return {"error": f"The browser couldn't do that ({first_line(e)})"}

    def _allowed(self, url: str):
        if not self.allow_private and url != "about:blank" and urls.is_private(url):
            raise Problem(f"{urls.site(url)} is a private or local address, which this browser doesn't open.")

    async def do_open(self, session: Session, url: str, new_tab: bool = False) -> dict:
        word = url.strip().lower()
        if word in ("back", "forward", "reload"):
            page = self._page(session)
            await {"back": page.go_back, "forward": page.go_forward, "reload": page.reload}[word]()
            return await self._after(session, f"Went {word}." if word != "reload" else "Reloaded.")
        target = urls.normalize(url)
        await asyncio.to_thread(self._allowed, target)
        page = await self._open_tab(session) if new_tab or session.current is None else self._page(session)
        status = None
        try:
            response = await page.goto(target, wait_until="domcontentloaded")
            status = response.status if response else None
        except Exception as e:
            problem = first_line(e)
            if "ERR_BLOCKED_BY_CLIENT" in problem:
                raise Problem("That address leads to a private or local one, which this browser doesn't open.")
            if "interrupted by another navigation" not in problem and "Download is starting" not in problem:
                raise Problem(f"Couldn't open {target} ({problem})")
        did = f"Opened {page.url}" + (f" (the site answered {status})" if status and status >= 400 else "")
        return await self._after(session, did)

    async def do_look(self, session: Session, start: int = 0, find: str = "", screenshot: bool = False) -> dict:
        page = self._page(session)
        view = await self._snapshot(session, start, find)
        notes = self._drain(session)
        result = {"text": "\n".join([*notes, *([""] if notes else []), view])}
        if screenshot:
            image = await page.screenshot(type="jpeg", quality=70)
            result["image"] = base64.b64encode(image).decode()
        return result

    async def do_inspect(self, session: Session, ref=None) -> dict:
        """What's about to be used, for asking the user; without `ref`, what has the focus"""
        page = self._page(session)
        info = {"site": urls.site(page.url), "url": page.url}
        if ref is not None:
            element = await self._element(session, ref)
            return {**info, **await element.evaluate(snapshot.INSPECT)}
        frame = await self._focused_frame(page)
        focused = await frame.evaluate_handle("() => document.activeElement")
        element = focused.as_element()
        return {**info, **await element.evaluate(snapshot.INSPECT)} if element else info

    async def _focused_frame(self, page):
        """The frame typing goes to: an iframe that has the focus, else the page"""
        for frame in page.frames:
            if frame is page.main_frame:
                continue
            try:
                if await frame.evaluate("() => document.hasFocus() && document.activeElement && document.activeElement !== document.body"):
                    return frame
            except Exception:
                continue
        return page.main_frame

    def _expect_dialog(self, session: Session, dialog: str | None, dialog_text: str | None = None):
        session.dialog = "accept" if dialog == "accept" else None
        session.dialog_text = dialog_text

    async def do_click(self, session: Session, ref=None, x: float | None = None, y: float | None = None, double: bool = False,
                       right: bool = False, dialog: str | None = None, dialog_text: str | None = None) -> dict:
        page = self._page(session)
        self._expect_dialog(session, dialog, dialog_text)
        button, count = "right" if right else "left", 2 if double else 1
        if ref is None:
            if x is None or y is None:
                raise Problem("Give the number of what to click, or x and y from a screenshot.")
            await page.mouse.click(x, y, button=button, click_count=count)
            return await self._after(session, f"Clicked at {x:g}, {y:g}.")
        element = await self._element(session, ref)
        name = (await element.evaluate(snapshot.INSPECT))["name"]
        try:
            await element.click(button=button, click_count=count)
        except Exception as e:
            text = str(e)
            over = re.search(r"<[^>]+>[^\n]*intercepts pointer events", text)
            if over:
                raise Problem(f"[{ref}] is covered by something else on the page ({over.group(0)[:120]}). Close it first; browser_look shows it.")
            if "not enabled" in text or "disabled" in text:
                raise Problem(f"[{ref}] is disabled. The page may be waiting for something, like a required field.")
            if "Timeout" in text:
                raise Problem(f"[{ref}] can't be clicked ({first_line(e)}). It may be hidden: browser_look with screenshot=true shows the page.")
            raise Problem(f"[{ref}] can't be clicked ({first_line(e)})")
        return await self._after(session, f"Clicked [{ref}] {name!r}.")

    async def _fill_one(self, session: Session, ref, value, allow_sensitive: bool) -> str:
        element = await self._element(session, ref)
        info = await element.evaluate(snapshot.INSPECT)
        name = info["name"] or f"[{ref}]"
        if info["sensitive"] and not allow_sensitive:
            return f"[{ref}] {name!r}: not filled in. It's a sensitive field: use browser_handover, and the user fills it in themselves."
        kind = info["type"]
        if info["tag"] == "input" and kind == "file":
            return f"[{ref}] {name!r}: it takes a file: use browser_upload."
        if info["tag"] == "select":
            wanted = [str(v) for v in value] if isinstance(value, list) else [str(value)]
            control = await element.evaluate_handle("el => el.tagName === 'LABEL' ? el.control : el")
            try:
                await control.as_element().select_option(label=wanted)
            except Exception:
                try:
                    await control.as_element().select_option(value=wanted)
                except Exception:
                    shown = ", ".join(info["options"][:30])
                    return f"[{ref}] {name!r}: no option {value!r}. The options: {shown}"
            return f"[{ref}] {name!r}: chose {', '.join(wanted)}"
        if kind in ("checkbox", "radio") or info["role"] in ("checkbox", "radio", "switch", "menuitemcheckbox"):
            on = value.strip().lower() not in ("false", "no", "off", "0", "") if isinstance(value, str) else bool(value)
            if on != info["checked"]:
                await element.click()
            return f"[{ref}] {name!r}: {'checked' if on else 'not checked'}"
        if not info["editable"]:
            return f"[{ref}] {name!r}: not something to type in. Click it instead, and pick from what opens."
        text = "" if value is None else str(value)
        try:
            await element.fill(text)
        except Exception:
            # An editor that takes keys, not values
            await element.click()
            await element.page.keyboard.press("Control+A")
            await element.page.keyboard.insert_text(text)
        shown = await element.evaluate("el => 'value' in el ? String(el.value) : (el.innerText || '')")
        if shown.strip() != text.strip():
            return f"[{ref}] {name!r}: filled in, but it now shows {shown[:100]!r}"
        return f"[{ref}] {name!r}: filled in"

    async def do_fill(self, session: Session, fields: dict, allow_sensitive: bool = False) -> dict:
        if not fields:
            raise Problem("Give the fields to fill in, by number.")
        done = []
        for ref, value in fields.items():
            try:
                done.append(await self._fill_one(session, ref, value, allow_sensitive))
            except Problem as e:
                done.append(str(e))
            except Exception as e:
                done.append(f"[{ref}]: couldn't fill it in ({first_line(e)})")
        return await self._after(session, "\n".join(done))

    async def _check_typing(self, session: Session, allow_sensitive: bool):
        if allow_sensitive:
            return
        info = await self.do_inspect(session)
        if info.get("sensitive"):
            raise Problem("What has the focus is a sensitive field: use browser_handover, and the user fills it in themselves.")

    async def do_press(self, session: Session, keys: str, ref=None, dialog: str | None = None, dialog_text: str | None = None, allow_sensitive: bool = False) -> dict:
        page = self._page(session)
        self._expect_dialog(session, dialog, dialog_text)
        if ref is not None:
            await (await self._element(session, ref)).focus()
        names = [key for key in re.split(r"[\s,]+", keys.strip()) if key]
        if not names:
            raise Problem("Give the keys to press, like \"Enter\" or \"Control+a\".")
        # Letter by letter is still typing
        if any(len(key) == 1 for key in names):
            await self._check_typing(session, allow_sensitive)
        for key in names[:50]:
            # Named like Playwright's, with the usual spellings accepted too
            try:
                await page.keyboard.press(_key(key))
            except Exception as e:
                raise Problem(f"{key!r} isn't a key ({first_line(e)})")
        return await self._after(session, f"Pressed {' '.join(names[:50])}.")

    async def do_type(self, session: Session, text: str, ref=None, allow_sensitive: bool = False) -> dict:
        """Types into what has the focus, as keys, so it works where there's no field to fill (a grid drawn on a canvas)"""
        page = self._page(session)
        if len(text) > TYPE_CHARS:
            raise Problem(f"That's over {TYPE_CHARS:,} characters to type: use browser_type with paste=true.")
        if ref is not None:
            await (await self._element(session, ref)).click()
        await self._check_typing(session, allow_sensitive)
        # Tabs and new lines are the keys, which is how a grid moves between cells
        for piece in re.split(r"(\t|\n)", text):
            if piece == "\t":
                await page.keyboard.press("Tab")
            elif piece == "\n":
                await page.keyboard.press("Enter")
            elif piece:
                await page.keyboard.type(piece)
        return await self._after(session, f"Typed {len(text):,} characters.")

    async def do_paste(self, session: Session, text: str, ref=None, allow_sensitive: bool = False) -> dict:
        """Pastes through the real clipboard, as tab-separated rows if it's for a grid"""
        page = self._page(session)
        if len(text) > PASTE_CHARS:
            raise Problem(f"That's over {PASTE_CHARS:,} characters.")
        if ref is not None:
            await (await self._element(session, ref)).click()
        await self._check_typing(session, allow_sensitive)
        frame = await self._focused_frame(page)
        origin = page.url.split("/", 3)
        origin = "/".join(origin[:3])
        async with self._paste_lock:
            context = session.profile.context
            await context.grant_permissions(["clipboard-read", "clipboard-write"], origin=origin)
            try:
                wrote = await frame.evaluate("async text => { try { await navigator.clipboard.writeText(text); return true; } catch { return false; } }", text)
                if wrote:
                    await page.keyboard.press("Control+V")
                    await asyncio.sleep(0.2)
                    await frame.evaluate("async () => { try { await navigator.clipboard.writeText(''); } catch {} }")
                else:
                    # No clipboard on a plain http page: the event pages listen to, with the text in it
                    await frame.evaluate("""text => {
                        const el = document.activeElement || document.body;
                        const data = new DataTransfer();
                        data.setData("text/plain", text);
                        const handled = !el.dispatchEvent(new ClipboardEvent("paste", {clipboardData: data, bubbles: true, cancelable: true}));
                        if (!handled) return;
                        if (!document.execCommand("insertText", false, text) && "value" in el) {
                            el.value = text;
                            el.dispatchEvent(new Event("input", {bubbles: true}));
                        }
                    }""", text)
            finally:
                await context.clear_permissions()
        return await self._after(session, f"Pasted {len(text):,} characters.")

    async def do_hover(self, session: Session, ref) -> dict:
        element = await self._element(session, ref)
        await element.hover()
        return await self._after(session, f"The mouse is over [{ref}].")

    async def do_scroll(self, session: Session, direction: str = "down", ref=None, amount: float = 1) -> dict:
        page = self._page(session)
        if ref is not None:
            await (await self._element(session, ref)).scroll_into_view_if_needed()
            return await self._after(session, f"Scrolled to [{ref}].")
        pages = max(0.1, min(float(amount), 20))
        if direction in ("top", "bottom"):
            await page.evaluate("to => window.scrollTo(0, to === 'top' ? 0 : document.documentElement.scrollHeight)", direction)
        elif direction in ("up", "down", "left", "right"):
            steps = {"up": (0, -HEIGHT), "down": (0, HEIGHT), "left": (-WIDTH, 0), "right": (WIDTH, 0)}[direction]
            await page.mouse.move(WIDTH / 2, HEIGHT / 2)
            await page.mouse.wheel(steps[0] * pages, steps[1] * pages)
            # The page takes a moment to get there
            await asyncio.sleep(0.25)
        else:
            raise Problem("The direction is up, down, left, right, top or bottom.")
        position = await page.evaluate("() => [Math.round(scrollY), document.documentElement.scrollHeight, innerHeight]")
        return await self._after(session, f"Scrolled {direction}; now at {position[0]:,} of {max(position[1] - position[2], 0):,}.")

    async def do_drag(self, session: Session, source, target) -> dict:
        page = self._page(session)
        start, end = await self._element(session, source), await self._element(session, target)
        await start.scroll_into_view_if_needed()
        a, b = await start.bounding_box(), await end.bounding_box()
        if not a or not b:
            raise Problem("One of them isn't showing on the page.")
        await page.mouse.move(a["x"] + a["width"] / 2, a["y"] + a["height"] / 2)
        await page.mouse.down()
        await page.mouse.move(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2, steps=12)
        await page.mouse.up()
        return await self._after(session, f"Dragged [{source}] onto [{target}].")

    async def do_tabs(self, session: Session, action: str = "list", tab: int | None = None) -> dict:
        if action == "switch" or action == "close":
            if tab is None or not 1 <= int(tab) <= len(session.tabs):
                raise Problem(f"There's no tab {tab}: {len(session.tabs)} are open.")
            page = session.tabs[int(tab) - 1]
            if action == "switch":
                session.current = page
                await page.bring_to_front()
                return await self._after(session, f"Tab {tab} is the current one now.")
            await page.close()
            if not session.current:
                return {"text": f"Closed tab {tab}. No tab is open now."}
            return await self._after(session, f"Closed tab {tab}.")
        if action != "list":
            raise Problem("The action is list, switch or close.")
        lines = [f"{i}. {await tab_.title() or '(no title)'} {tab_.url}{' (current)' if tab_ is session.current else ''}"
                 for i, tab_ in enumerate(session.tabs, 1)]
        return {"text": "\n".join(lines) or "No tab is open."}

    async def do_upload(self, session: Session, name: str, data: str, ref=None) -> dict:
        """A file for a file field, or for the picker the page opened"""
        page = self._page(session)
        content = base64.b64decode(data)
        if len(content) > MAX_UPLOAD:
            raise Problem(f"That file is over {MAX_UPLOAD // 1_000_000} MB.")
        file = {"name": Path(name).name, "mimeType": mimetypes.guess_type(name)[0] or "application/octet-stream", "buffer": content}
        if ref is None:
            chooser, session.chooser = session.chooser, None
            if chooser is None:
                raise Problem("Give the file field's number, or click what opens the file picker first.")
            await chooser.set_files(file)
        else:
            element = await self._element(session, ref)
            info = await element.evaluate(snapshot.INSPECT)
            if info["tag"] == "input" and info["type"] == "file":
                await element.set_input_files(file)
            else:
                async with page.expect_file_chooser(timeout=ACT_TIMEOUT) as opened:
                    await element.click()
                await (await opened.value).set_files(file)
                session.chooser = None
                self._drain_chooser_note(session)
        return await self._after(session, f"Gave the page {file['name']} ({len(content):,} bytes).")

    def _drain_chooser_note(self, session: Session):
        session.notes = [note for note in session.notes if "file picker" not in note]

    async def do_wait(self, session: Session, text: str = "", gone: bool = False, seconds: float = 10) -> dict:
        page = self._page(session)
        seconds = max(0.1, min(float(seconds), 60))
        if not text:
            await asyncio.sleep(seconds)
            return await self._after(session, f"Waited {seconds:g} seconds.")
        try:
            await page.get_by_text(text).first.wait_for(state="hidden" if gone else "visible", timeout=seconds * 1000)
            did = f"{text!r} is {'gone' if gone else 'there'}."
        except Exception:
            did = f"After {seconds:g} seconds, {text!r} is {'still there' if gone else 'not there'}."
        return await self._after(session, did)

    async def do_files(self, session: Session, name: str = "", start: int = 0, raw: bool = False) -> dict:
        """What's been downloaded; with `name`, that file's text, or `raw` its bytes in base64"""
        if not name:
            found = [f"{key} ({path.stat().st_size:,} bytes)" for key, path in session.downloads.items()]
            return {"text": "Downloaded so far:\n" + "\n".join(found) if found else "Nothing has been downloaded in this chat."}
        path = session.downloads.get(Path(name).name)
        if path is None:
            raise Problem(f"Nothing was downloaded as {name}.")
        content = path.read_bytes()
        if raw:
            return {"text": f"{path.name} ({len(content):,} bytes)", "name": path.name, "data": base64.b64encode(content).decode()}
        if b"\0" in content[:8192]:
            return {"text": f"{path.name} is a binary file of {len(content):,} bytes, not something to read here."}
        text = content.decode("utf-8", "replace")
        part = text[start:start + DOWNLOAD_CHARS]
        more = start + DOWNLOAD_CHARS < len(text)
        return {"text": part + (f"\n\n[… read on with start={start + DOWNLOAD_CHARS}]" if more else "")}

    # --- The live view: what the user sees and does -------------------------------------------

    async def view_state(self, user: str, thread: str) -> dict | None:
        session = self.existing(user, thread)
        if session is None or session.current is None:
            return None
        tabs = []
        for i, tab in enumerate(session.tabs, 1):
            try:
                tabs.append({"tab": i, "title": await tab.title(), "url": tab.url, "current": tab is session.current})
            except Exception:
                continue
        return {"tabs": tabs, "width": WIDTH, "height": HEIGHT}

    async def view_frame(self, user: str, thread: str, quality: int = 60) -> bytes | None:
        session = self.existing(user, thread)
        if session is None or session.current is None or session.current.is_closed():
            return None
        # Not use: a panel left open mustn't keep the tabs from closing when idle
        return await session.current.screenshot(type="jpeg", quality=quality, timeout=5_000)

    async def view_input(self, user: str, thread: str, event: dict) -> str | None:
        """Something the user did in the live view; what went wrong, if something did. What they type
        goes straight to the page: it isn't kept, and the model doesn't see it, unless the page shows it."""

        session = self.existing(user, thread)
        kind = event.get("type")
        if kind == "open" and (session is None or session.current is None):
            # The address bar works with nothing open: the user can start without the model
            try:
                await asyncio.to_thread(self._allowed, urls.normalize(str(event["url"])))
                session = await self.session(user, thread)
                await self._open_tab(session)
            except (Problem, urls.Refused) as e:
                return str(e)
            except Exception as e:
                return first_line(e)
        if session is None or session.current is None:
            return "No page is open"
        page = session.current
        session.used = session.profile.used = time.monotonic()
        session.profile.changed = True
        try:
            if kind == "click":
                await page.mouse.click(float(event["x"]), float(event["y"]), button=event.get("button", "left"), click_count=int(event.get("clicks", 1)))
            elif kind == "move":
                await page.mouse.move(float(event["x"]), float(event["y"]))
            elif kind == "wheel":
                await page.mouse.move(float(event["x"]), float(event["y"]))
                await page.mouse.wheel(float(event.get("dx", 0)), float(event.get("dy", 0)))
            elif kind == "key":
                await page.keyboard.press(str(event["key"]))
            elif kind == "text":
                await page.keyboard.insert_text(str(event["text"]))
            elif kind == "open":
                target = urls.normalize(str(event["url"]))
                await asyncio.to_thread(self._allowed, target)
                await page.goto(target, wait_until="commit")
            elif kind in ("back", "forward", "reload"):
                await {"back": page.go_back, "forward": page.go_forward, "reload": page.reload}[kind](wait_until="commit")
            elif kind == "tab":
                session.current = session.tabs[int(event["tab"]) - 1]
            else:
                return f"Not something the view does: {kind}"
        except (Problem, urls.Refused) as e:
            return str(e)
        except Exception as e:
            return first_line(e)
        return None

    async def save_signins(self, user: str):
        """After the user took over: what they signed in to is kept right away"""
        if (profile := self.profiles.get(user)) and profile.changed:
            await self._save(profile)


def _key(name: str) -> str:
    """A key as Playwright names it: "ctrl+a" is "Control+a", "enter" is "Enter" """

    aliases = {"ctrl": "Control", "control": "Control", "cmd": "Meta", "meta": "Meta", "alt": "Alt", "shift": "Shift", "esc": "Escape",
               "escape": "Escape", "enter": "Enter", "return": "Enter", "tab": "Tab", "space": "Space", "del": "Delete", "delete": "Delete",
               "backspace": "Backspace", "up": "ArrowUp", "down": "ArrowDown", "left": "ArrowLeft", "right": "ArrowRight",
               "pageup": "PageUp", "pagedown": "PageDown", "home": "Home", "end": "End"}
    return "+".join(aliases.get(part.lower(), part) for part in name.split("+"))
