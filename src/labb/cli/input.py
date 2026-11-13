"""labb's input: the prompt (multi-line editing, history, completion of /commands and
@paths, key bindings), Esc to stop a reply, and copying to the clipboard."""

import asyncio
import base64
import os
import re
import select
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from prompt_toolkit import PromptSession
from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style

from labb.tools.files import SKIPPED_DIRS

try:
    import termios
    import tty
except ImportError:  # Windows
    termios = tty = None

MAX_PATH_COMPLETIONS = 50
# The word being typed after an @, e.g. "@tools/fi"
MENTION_AT_CURSOR = re.compile(r"(?:^|\s)@(\S*)$")

STYLE = Style.from_dict({
    "bottom-toolbar": "noreverse fg:ansibrightblack",
    "prompt": "bold fg:ansiyellow",
})


def load_history(path: Path) -> FileHistory:
    """The prompt's history, converting the file readline wrote before (one entry per line)"""

    try:
        text = path.read_text()
    except OSError:
        text = None
    if text and not any(line.startswith("+") for line in text.splitlines()):
        entries = [line for line in text.splitlines() if line and line != "_HiStOrY_V2_"]
        path.write_text("".join(f"\n# from readline\n+{entry}\n" for entry in entries))
    return FileHistory(str(path))


class LabbCompleter(Completer):
    """/commands at the start of the input, and paths of the working directory after an @"""

    def __init__(self, commands: dict[str, str], workdir: str):
        self.commands = commands
        self.root = Path(workdir).resolve()

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if text.startswith("/") and not any(c.isspace() for c in text):
            for name, description in self.commands.items():
                if name.startswith(text):
                    yield Completion(name, start_position=-len(text), display_meta=description)
            return
        if match := MENTION_AT_CURSOR.search(text):
            yield from self.paths(match.group(1))

    def paths(self, fragment: str):
        folder, _, prefix = fragment.rpartition("/")
        base = (self.root / folder).resolve()
        if not base.is_relative_to(self.root) or not base.is_dir():
            return
        try:
            entries = sorted(base.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        except OSError:
            return
        shown = 0
        for entry in entries:
            name = entry.name
            if not name.startswith(prefix) or name in SKIPPED_DIRS:
                continue
            # Hidden files only when asked for, like a shell
            if name.startswith(".") and not prefix.startswith("."):
                continue
            is_dir = entry.is_dir()
            completion = f"{folder}/{name}" if folder else name
            yield Completion(
                completion + ("/" if is_dir else ""),
                start_position=-len(fragment),
                display=name + ("/" if is_dir else ""),
            )
            shown += 1
            if shown >= MAX_PATH_COMPLETIONS:
                return


def make_prompt(
    *,
    history_file: Path,
    commands: dict[str, str],
    workdir: str,
    toolbar: Callable[[], str],
    on_edit_last: Callable[[], str | None],
    on_expand: Callable[[], None],
    on_toggle_mode: Callable[[], None],
    on_toggle_thinking: Callable[[], None],
) -> PromptSession:
    """The chat's prompt.

    Enter sends; Alt-Enter, Ctrl-J or a `\\` at the end of the line starts a new line.
    Esc Esc puts `on_edit_last()` (the last message) back in the input to edit,
    Ctrl-O runs `on_expand` (showing the last reply's thinking and tool outputs in full),
    Ctrl-T `on_toggle_thinking`, and Shift-Tab `on_toggle_mode` (plan mode on or off).
    """
    keys = KeyBindings()

    @keys.add("enter")
    def _(event):
        buffer = event.current_buffer
        if buffer.complete_state and buffer.complete_state.current_completion:
            buffer.apply_completion(buffer.complete_state.current_completion)
        elif buffer.document.text_before_cursor.endswith("\\"):
            buffer.delete_before_cursor()
            buffer.insert_text("\n")
        else:
            buffer.validate_and_handle()

    @keys.add("escape", "enter")
    @keys.add("c-j")
    def _(event):
        event.current_buffer.insert_text("\n")

    @keys.add("escape", "escape")
    def _(event):
        if (text := on_edit_last()) is not None:
            event.current_buffer.text = text
            event.current_buffer.cursor_position = len(text)

    @keys.add("c-o")
    def _(event):
        run_in_terminal(on_expand)

    @keys.add("c-t")
    def _(event):
        run_in_terminal(on_toggle_thinking)

    @keys.add("s-tab")
    def _(event):
        on_toggle_mode()
        event.app.invalidate()

    return PromptSession(
        history=load_history(history_file),
        completer=LabbCompleter(commands, workdir),
        complete_while_typing=True,
        key_bindings=keys,
        multiline=True,
        prompt_continuation="  ",
        bottom_toolbar=toolbar,
        style=STYLE,
    )


PROMPT = HTML("<prompt>&gt;</prompt> ")

# What prompt_until returns when the loop's next run is due
LOOP_DUE = object()


def prompt_until(session: PromptSession, due: float | None, **kwargs):
    """`session.prompt(PROMPT)`, which gives up with LOOP_DUE at `due` (a time.time()) for a
    loop's run. While something is typed in the box, the run waits for it to be sent or cleared."""

    if due is None:
        return session.prompt(PROMPT, **kwargs)
    timer = None

    def pre_run():
        nonlocal timer
        app, event_loop = session.app, asyncio.get_running_loop()

        def check():
            nonlocal timer
            if not app.is_running:
                return
            if app.current_buffer.text.strip():
                timer = event_loop.call_later(1, check)
            else:
                app.exit(result=LOOP_DUE)

        timer = event_loop.call_later(max(0.0, due - time.time()), check)

    try:
        # Refreshed every second for the status line's countdown
        return session.prompt(PROMPT, pre_run=pre_run, refresh_interval=1, **kwargs)
    finally:
        if timer:
            timer.cancel()


class ReplyKeys:
    """Reads single keys while a reply streams: Esc stops it, Ctrl-O asks for the reply in
    full (thinking and tool outputs), Ctrl-T shows or hides the thinking.

    The terminal is put in cbreak mode (keys arrive one by one, unechoed; Ctrl-C still
    works) between start() and stop(). Other keys typed meanwhile are dropped, so they
    can't end up in the next message or answer a question. Does nothing when stdin
    isn't a terminal.
    """

    def __init__(self):
        self.fd = sys.stdin.fileno() if termios and sys.stdin.isatty() else None
        self.saved = None

    def start(self):
        if self.fd is not None and self.saved is None:
            self.saved = termios.tcgetattr(self.fd)
            tty.setcbreak(self.fd)

    def stop(self):
        if self.saved is not None:
            termios.tcflush(self.fd, termios.TCIFLUSH)
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)
            self.saved = None

    def pressed(self) -> set[str]:
        """The keys pressed since the last call that mean something ("esc", "ctrl-o", "ctrl-t")"""

        if self.saved is None or not select.select([self.fd], [], [], 0)[0]:
            return set()
        data = os.read(self.fd, 64)
        keys = set()
        # Arrow keys and the like also start with Esc, but arrive as one longer sequence
        if data == b"\x1b":
            keys.add("esc")
        if b"\x0f" in data:
            keys.add("ctrl-o")
        if b"\x14" in data:
            keys.add("ctrl-t")
        return keys


def copy_to_clipboard(text: str) -> str:
    """Copy with the system's clipboard tool, or ask the terminal to (OSC 52).
    Returns how it was copied."""

    for command in (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"], ["pbcopy"]):
        if shutil.which(command[0]):
            try:
                subprocess.run(command, input=text.encode(), check=True, timeout=5)
                return command[0]
            except (subprocess.SubprocessError, OSError):
                continue
    # Most terminals copy what's sent this way, even over ssh
    sys.stdout.write(f"\x1b]52;c;{base64.b64encode(text.encode()).decode()}\a")
    sys.stdout.flush()
    return "the terminal"
