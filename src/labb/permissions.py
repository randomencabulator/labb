"""Which shell commands labb may run without asking.

Read-only commands run right away. Others need the user's approval, which can be
remembered per project in <workdir>/.labb/permissions.json as command prefixes
("mkdir", "npm install"). Commands that can't be judged by their first words
(command substitution, writing redirections, subshells, sudo) are asked every time.
"""

import fnmatch
import json
import os
import re
import shlex
import subprocess
from pathlib import Path

from labb import settings
from typing import NamedTuple

# Commands that only read; they run without asking
READ_ONLY = {
    "ls", "pwd", "cd", "cat", "head", "tail", "wc", "echo", "printf", "which", "type", "file",
    "stat", "du", "df", "tree", "date", "uname", "whoami", "basename", "dirname", "realpath",
    "sort", "uniq", "cut", "tr", "diff", "grep", "rg", "true", "false", "test", "[",
}
READ_ONLY_GIT = {"status", "diff", "log", "show", "branch", "rev-parse", "ls-files", "remote"}
# `git branch` and `git remote` only read when they list: with no arguments, or with these
GIT_BRANCH_LISTING = {"-a", "--all", "-r", "--remotes", "-v", "-vv", "--verbose", "--list", "-l", "--show-current",
                      "--contains", "--no-contains", "--merged", "--no-merged", "--points-at", "--color", "--no-color",
                      "--sort", "--format", "--column", "--no-column", "--ignore-case", "-i"}
# …and these take a value (a commit or a pattern) as the next word
GIT_BRANCH_VALUES = {"--contains", "--no-contains", "--merged", "--no-merged", "--points-at", "--sort", "--format"}
# Options of read-only commands that write a file or run a program, so the command is asked every time
# (remembering "rg" or "git diff" would allow these too); per command: long options, and the letters
# of short ones, which can be grouped like `sort -uo out`
WRITING_OPTIONS = {
    "rg": ({"--pre", "--pre-glob", "--search-zip"}, "z", "it runs other programs on the files (rg --pre/--search-zip)"),
    "sort": ({"--output"}, "o", "it writes to a file (sort -o)"),
    "tree": (set(), "o", "it writes to a file (tree -o)"),
    "git": ({"--output"}, "", "it writes to a file (git --output)"),
}
# find options that run, delete or write something
FIND_WRITES = re.compile(r"^-(exec|execdir|ok|okdir|delete|fprint\w*|fls)$")
# Tools whose permission is per subcommand: "npm install", not all of "npm"
SUBCOMMAND_TOOLS = {"git", "npm", "npx", "pnpm", "yarn", "uv", "pip", "cargo", "go", "docker", "kubectl", "make"}
SEPARATORS = {"&&", "||", ";", "|", "&", "|&"}
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# Environment variables that make a command load or run something else (GIT_EXTERNAL_DIFF=… git
# diff runs a program; LD_PRELOAD loads one): set in front of a command, it's asked every time
RUNS_PROGRAMS = re.compile(
    r"^(LD_\w*|DYLD_\w*|GIT_\w*|\w*PAGER|EDITOR|VISUAL|BASH_ENV|ENV|PATH|IFS|PS4|SHELLOPTS|BASHOPTS|PROMPT_COMMAND"
    r"|PYTHON\w*|NODE_OPTIONS|NODE_PATH|PERL5\w*|PERLLIB|RUBY\w*|LESSOPEN|LESSCLOSE|MANPAGER|BROWSER)$"
)
# A repository's own git config can make reading commands run programs: these, in .git/config
GIT_RUNS_PROGRAMS = re.compile(r"^(core\.fsmonitor|diff\.external|diff\..+\.(textconv|command)|core\.(hookspath|sshcommand))$")


# Files that may hold secrets: reading them always asks, in commands and the file tools.
# Folders under the home folder, and file names anywhere (an .env.example is only an example).
SECRET_FOLDERS = (".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".config/gcloud", ".config/gh", ".labb")
SECRET_NAMES = (".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*", "id_ecdsa*", "id_dsa*",
                ".netrc", ".pgpass", ".git-credentials", ".npmrc", ".pypirc", "credentials.json", "secret.key")
NOT_SECRET = ("*.example", "*.sample", "*.template", "*.dist", "*.pub")
# How path_risk's reasons start: these commands only read, but ask because of what they read
READS = "it reads "
# Commands whose arguments are text, not paths
NO_PATHS = {"echo", "printf", "true", "false", "test", "["}


def is_secret(path: Path) -> bool:
    """Whether a file may hold secrets, by its name or the folder it's in (see SECRET_NAMES)"""

    name = path.name
    if any(fnmatch.fnmatchcase(name, pattern) for pattern in NOT_SECRET):
        return False
    if any(fnmatch.fnmatchcase(name, pattern) for pattern in SECRET_NAMES):
        return True
    home = Path.home()
    return any(path == home / folder or path.is_relative_to(home / folder) for folder in SECRET_FOLDERS)


def path_risk(words: list[str], workdir: str | None, read_only: bool) -> str | None:
    """Why a command's paths need asking every time: a file that may hold secrets, or (for a
    command that would run without asking) one outside the working directory"""

    if words[0] in NO_PATHS:
        return None
    root = Path(workdir).resolve() if workdir else None
    for word in words[1:]:
        if word.startswith("-") and "=" in word:
            word = word.partition("=")[2]
        elif word.startswith("-"):
            continue
        if not word or word in ("<", "<<", "<<<"):
            continue
        path = Path(os.path.expanduser(word))
        if not path.is_absolute():
            if root is None:
                continue
            path = root / path
        try:
            path = path.resolve()
        except (OSError, RuntimeError):
            continue
        if is_secret(path):
            return f"{READS if read_only else 'it uses '}a file that may hold secrets ({word})"
        # Only paths that exist: a pattern like "/api/" that isn't a path doesn't count
        if (read_only and root is not None and not path.is_relative_to(root) and path != Path("/dev/null")
                and os.path.lexists(path)):
            return f"{READS}outside the working directory ({word})"
    return None


class Check(NamedTuple):
    # Prefixes of the commands that need approval (empty: none do)
    needed: list[str]
    # Why the command needs approval every time, if it does
    always_ask: str | None = None

    @property
    def ok(self) -> bool:
        return not self.needed and not self.always_ask


def split(command: str) -> list[list[str]]:
    """The simple commands in a command line, as word lists. Raises ValueError if it can't be parsed."""

    lexer = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    commands, current = [], []
    for token in lexer:
        if token in SEPARATORS:
            if current:
                commands.append(current)
            current = []
        else:
            current.append(token)
    if current:
        commands.append(current)
    return commands


def prefix(words: list[str]) -> str:
    """"mkdir -p a" → "mkdir", "npm install x" → "npm install" """
    if words[0] in SUBCOMMAND_TOOLS and len(words) > 1 and not words[1].startswith("-"):
        return f"{words[0]} {words[1]}"
    return words[0]


def is_read_only(words: list[str]) -> bool:
    name = words[0]
    if name == "find":
        return True  # find with -delete, -exec etc. is caught by risk() first
    if name == "git":
        if len(words) < 2 or words[1] not in READ_ONLY_GIT:
            return False
        if words[1] == "branch":
            return git_branch_lists(words[2:])
        if words[1] == "remote":
            rest = [word for word in words[2:] if word not in ("-v", "--verbose")]
            return not rest or rest[0] in ("show", "get-url")
        return True
    return name in READ_ONLY


def git_runs_programs(workdir: str) -> str | None:
    """A setting of the repository's own git config (not the user's) that makes git status, diff, log
    or show run a program, if there is one: a repository unpacked from somewhere, or a config the
    agent wrote, shouldn't get to run things through a "read-only" command"""

    try:
        found = subprocess.run(
            ["git", "config", "--show-scope", "--get-regexp", r"^(core\.fsmonitor|core\.hookspath|core\.sshcommand|diff\..*)$"],
            cwd=workdir, capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in found.stdout.splitlines():
        scope, _, rest = line.partition("\t")
        key, _, value = rest.partition(" ")
        if scope not in ("local", "worktree", "command") or not GIT_RUNS_PROGRAMS.match(key.lower()):
            continue
        # fsmonitor = true/false is git's own; anything else is a program
        if key.lower() == "core.fsmonitor" and value.strip().lower() in ("true", "false", "yes", "no", "on", "off", "1", "0", ""):
            continue
        return key
    return None


def git_branch_lists(arguments: list[str]) -> bool:
    """Whether `git branch <arguments>` only lists branches; a bare name creates one"""

    patterns = False  # after --list, bare words are patterns of the branches to list
    for i, word in enumerate(arguments):
        if word.startswith("-"):
            if word.partition("=")[0] not in GIT_BRANCH_LISTING:
                return False
            patterns = patterns or word in ("--list", "-l")
        elif not patterns and (i == 0 or arguments[i - 1] not in GIT_BRANCH_VALUES):
            return False
    return True


def writing_option(words: list[str]) -> str | None:
    """Why a read-only command writes or runs something through one of its options, if it does"""

    if words[0] not in WRITING_OPTIONS:
        return None
    long, letters, reason = WRITING_OPTIONS[words[0]]
    for word in words[1:]:
        if word == "--":
            break
        if word.startswith("--"):
            if word.partition("=")[0] in long:
                return reason
        elif word.startswith("-") and len(word) > 1 and any(letter in word[1:] for letter in letters):
            return reason
    return None


def risk(command: str, words: list[str]) -> str | None:
    """Why a simple command can't be judged by its prefix, if so"""

    for i, word in enumerate(words):
        if word in ("(", ")", "{", "}"):
            return "it runs a subshell or group"
        if word in (">", ">>", ">|", "&>", "&>>"):
            target = words[i + 1] if i + 1 < len(words) else ""
            if target != "/dev/null":
                return "it writes to a file"
        if word == ">&" and not (i + 1 < len(words) and words[i + 1].isdigit()):
            return "it writes to a file"
    if words[0] == "sudo":
        return "it runs as another user (sudo)"
    if words[0] == "find" and any(FIND_WRITES.match(word) for word in words[1:]):
        # Remembering "find" would allow every find, including these
        return "it deletes files or runs commands (find -delete/-exec)"
    return writing_option(words)


def check(command: str, allowed: set[str], workdir: str | None = None) -> Check:
    """What running `command` in `workdir` needs, given the remembered prefixes. Without a
    workdir, paths outside it aren't checked (secrets are)."""

    if "$(" in command or "`" in command or "<(" in command or ">(" in command:
        return Check([], "it runs a command substitution")
    try:
        commands = split(command)
    except ValueError:
        return Check([], "it can't be parsed")
    if not commands:
        return Check([], "it's empty")

    needed = []
    # Asked every time for what it reads; kept looking, so plan mode knows if it's only that
    path_reason = None
    for words in commands:
        # Leading VAR=value assignments don't change what runs, unless they're one that runs programs
        while words and ASSIGNMENT.match(words[0]):
            if RUNS_PROGRAMS.match(words[0].partition("=")[0]):
                return Check([], f"it sets {words[0].partition('=')[0]}, which can make it run other programs")
            words = words[1:]
        if not words:
            continue
        if reason := risk(command, words):
            return Check([], reason)
        read_only = is_read_only(words)
        if read_only and words[0] == "git" and workdir and (setting := git_runs_programs(workdir)):
            return Check([], f"this repository's git config runs programs ({setting})")
        path_reason = path_reason or path_risk(words, workdir, read_only)
        if read_only:
            continue
        name = prefix(words)
        if name not in allowed and name not in needed:
            needed.append(name)
    return Check(needed, path_reason)


def permissions_file(workdir: str) -> Path:
    return settings.permissions_file(workdir)


def load_allowed(workdir: str) -> list[str]:
    """The remembered prefixes of a project; a missing or broken file means none. A file labb
    didn't write itself (one that came with the folder) counts once the user agrees to it."""

    rules = settings.remembered_commands(workdir)
    return rules if settings.is_trusted(workdir, "permissions", rules) else []


def allowed(workdir: str) -> list[str]:
    """Everything allowed without asking in a directory: the remembered rules, and
    `permissions.allow` of the settings (which /permissions can't remove)"""

    rules = load_allowed(workdir)
    from_settings = settings.load(workdir)[0]["permissions"]["allow"]
    return rules + [rule for rule in from_settings if isinstance(rule, str) and rule not in rules]


def tool_allowed(name: str, rules: list[str]) -> bool:
    """Whether a tool (an MCP tool, e.g. mcp__github__create_issue) is allowed by name,
    or by a rule with wildcards like mcp__github__*"""
    return any(fnmatch.fnmatchcase(name, rule) for rule in rules if rule.startswith("mcp__"))


def save_allowed(workdir: str, rules: list[str]):
    path = permissions_file(workdir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"allow": rules}, indent=2) + "\n")
    # What labb wrote itself, as the user said
    settings.trust(workdir, "permissions", rules)


def allow(workdir: str, prefixes: list[str]):
    rules = load_allowed(workdir)
    save_allowed(workdir, rules + [p for p in prefixes if p not in rules])
