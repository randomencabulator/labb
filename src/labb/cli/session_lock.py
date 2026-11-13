"""One process at a time writing in a session: a session started from the web page is also on
this PC's list for `labb -r`, and two processes writing would overwrite each other. The operating
system frees the lock if a process dies, so a crash never leaves a session locked."""

import contextlib
import getpass
import os
import re
import tempfile
from pathlib import Path

try:
    import fcntl
except ImportError:
    # Windows: no file locks this way, so nothing's checked
    fcntl = None


def folder() -> Path:
    path = Path(tempfile.gettempdir()) / f"labb-locks-{getpass.getuser()}"
    path.mkdir(mode=0o700, exist_ok=True)
    return path


@contextlib.contextmanager
def held(thread_id: str):
    """Hold the session's lock while the block runs; gives False if another process has it"""

    if fcntl is None:
        yield True
        return
    file = os.open(folder() / re.sub(r"[^\w-]", "_", thread_id), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        yield True
    finally:
        os.close(file)
