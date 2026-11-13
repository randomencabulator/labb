"""The jobs of `labb eval long` (cli/long_evals.py): longer work in a small project, checked by
what's in the files afterwards rather than by what the agent says.

Each case has `files` (the project it starts from), `messages` (sent one after another, as a
user would), and `checks`: (what's checked, a function of the project's folder that says whether
it's so). `engine` is an overlay for the run, e.g. to make summaries come sooner.
"""

import re
from pathlib import Path


def text(root: Path, path: str) -> str:
    try:
        return (root / path).read_text()
    except OSError:
        return ""


def has(path: str, pattern: str):
    return lambda root: re.search(pattern, text(root, path), re.MULTILINE) is not None


def unchanged(path: str, original: str):
    return lambda root: text(root, path) == original


CALC = '''def plus(a, b):
    return a + b


def minus(a, b):
    return a - b
'''

MAIN = '''from calc import plus, minus

print(plus(2, 3), minus(5, 1))
'''

LEGACY = "# Old code: don't change this file.\nVALUE = 1\n"


def filler(n: int) -> str:
    """A long file to read, so the conversation fills up and gets summarized"""
    return "".join(f"record {n}-{i}: status ok, value {i * 7 % 13}, note nothing to see here\n" for i in range(400))


CASES = [
    {
        "name": "steps",
        "about": "Seven explicit changes in one request: are they all done, and does the reply end only then?",
        "files": {"calc.py": CALC, "main.py": MAIN},
        "messages": [
            "Make these changes, all of them:\n"
            "1. In calc.py, add square(x) that returns x * x.\n"
            "2. Add cube(x) that returns x ** 3.\n"
            "3. Rename plus to add, and update main.py to match.\n"
            "4. Add a one-line docstring to every function in calc.py.\n"
            "5. Add test_calc.py with a test for square and a test for cube.\n"
            "6. Add a CHANGES.md that lists what you changed.\n"
            "7. Make main.py also print square(4)."
        ],
        "checks": [
            ("square", has("calc.py", r"def square\(x\)")),
            ("cube", has("calc.py", r"def cube\(x\)")),
            ("renamed", lambda root: re.search(r"def add\(", text(root, "calc.py")) and not re.search(r"def plus\(", text(root, "calc.py"))),
            ("main.py uses add", has("main.py", r"\badd\b")),
            ("docstrings", lambda root: text(root, "calc.py").count('"""') >= 8),
            ("tests", lambda root: re.search(r"square", text(root, "test_calc.py")) and re.search(r"cube", text(root, "test_calc.py"))),
            ("CHANGES.md", lambda root: len(text(root, "CHANGES.md").strip()) > 20),
            ("prints square(4)", has("main.py", r"square\(4\)")),
        ],
    },
    {
        "name": "recall",
        "about": "Facts said early, then enough reading to get them summarized: are they still known?",
        "files": {**{f"logs/day{n}.txt": filler(n) for n in range(1, 5)}, "config.py": "# Settings\n", "legacy/old.py": LEGACY},
        # Summaries come at a third of the window, so they happen on a small model too
        "engine": {"context": {"compact_at": 0.3, "keep_recent": 0.1}},
        "messages": [
            "Two things to keep in mind for this whole session: we deploy on port 7314, and nothing in legacy/ may be changed. "
            "Just say OK for now.",
            "Read logs/day1.txt and logs/day2.txt in full and tell me how many records each has.",
            "Now read logs/day3.txt and logs/day4.txt in full and tell me the value of their last record.",
            "Add the deploy port to config.py as PORT = <the port>, and tell me which folder you must not change.",
        ],
        "checks": [
            ("port", has("config.py", r"PORT\s*=\s*7314")),
            ("legacy untouched", unchanged("legacy/old.py", LEGACY)),
        ],
    },
]
