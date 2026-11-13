import pytest

from labb import permissions


@pytest.mark.parametrize("command", [
    "ls -la",
    "cat README.md | grep labb | wc -l",
    "git status && git log --oneline -5",
    "find . -name '*.py'",
    "FOO=1 echo hi",
    "ls > /dev/null 2>&1",
])
def test_read_only_commands_run_right_away(command):
    assert permissions.check(command, set()).ok


@pytest.mark.parametrize("command, needed", [
    ("mkdir -p src", ["mkdir"]),
    ("npm install left-pad", ["npm install"]),
    ("git push origin main", ["git push"]),
    ("ls && rm -rf build && npm test", ["rm", "npm test"]),
])
def test_other_commands_need_their_prefix(command, needed):
    check = permissions.check(command, set())
    assert check.needed == needed and check.always_ask is None


def test_remembered_prefixes_are_allowed():
    assert permissions.check("npm install left-pad", {"npm install"}).ok
    # Only that subcommand
    assert permissions.check("npm publish", {"npm install"}).needed == ["npm publish"]


@pytest.mark.parametrize("command, reason", [
    ("echo $(whoami)", "command substitution"),
    ("echo hi > notes.txt", "writes to a file"),
    ("(cd build && make)", "subshell"),
    ("sudo ls", "sudo"),
    ("find . -name '*.tmp' -delete", "find -delete"),
])
def test_some_commands_are_asked_every_time(command, reason):
    check = permissions.check(command, {"echo", "find", "ls", "make"})
    assert not check.ok and reason in check.always_ask


@pytest.mark.parametrize("command", [
    "git branch", "git branch -a", "git branch -vv --merged main", "git branch --list 'feat*' 'fix*'",
    "git branch --show-current", "git remote", "git remote -v", "git remote show origin", "git remote get-url origin",
    "rg -n pattern src", "sort -u names.txt", "tree -L 2", "git diff --stat",
])
def test_listing_and_reading_options_run_right_away(command):
    assert permissions.check(command, set()).ok


@pytest.mark.parametrize("command, needed", [
    ("git branch -D main", ["git branch"]),
    ("git branch --delete old", ["git branch"]),
    ("git branch -m new-name", ["git branch"]),
    ("git branch feature", ["git branch"]),
    ("git branch -u origin/main", ["git branch"]),
    ("git remote add fork https://example.com/x.git", ["git remote"]),
    ("git remote set-url origin https://example.com/x.git", ["git remote"]),
    ("git remote remove origin", ["git remote"]),
])
def test_git_branch_and_remote_changes_need_approval(command, needed):
    check = permissions.check(command, set())
    assert check.needed == needed and check.always_ask is None


@pytest.mark.parametrize("command, reason", [
    ("rg --pre ./script pattern", "runs other programs"),
    ("rg --pre=./script pattern", "runs other programs"),
    ("rg --pre-glob '*.gz' --pre zcat x", "runs other programs"),
    ("rg -nz pattern", "runs other programs"),
    ("git diff --output=patch.txt", "writes to a file"),
    ("git log --output out.txt", "writes to a file"),
    ("sort -o out.txt names.txt", "writes to a file"),
    ("sort -uo out.txt names.txt", "writes to a file"),
    ("sort --output=out.txt names.txt", "writes to a file"),
    ("tree -o out.txt", "writes to a file"),
])
def test_options_that_write_or_run_are_asked_every_time(command, reason):
    # Even when the command was allowed before: these options aren't covered by its prefix
    check = permissions.check(command, {"git diff", "git log"})
    assert not check.ok and reason in check.always_ask


def test_tools_are_allowed_by_name_or_wildcard():
    assert permissions.tool_allowed("mcp__github__list_issues", ["mcp__github__*"])
    assert permissions.tool_allowed("mcp__notes__add", ["mcp__notes__add"])
    assert not permissions.tool_allowed("mcp__notes__add", ["mcp__github__*", "npm test"])


def test_remembered_rules_are_saved_per_project(tmp_path):
    workdir = str(tmp_path)
    permissions.allow(workdir, ["mkdir", "npm install"])
    permissions.allow(workdir, ["mkdir"])
    assert permissions.load_allowed(workdir) == ["mkdir", "npm install"]
    assert permissions.allowed(workdir) == ["mkdir", "npm install"]


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A home folder with an SSH key, and a project in it"""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".ssh" / "id_ed25519").write_text("key")
    (tmp_path / "project").mkdir()
    (tmp_path / "elsewhere.txt").write_text("hi")
    return tmp_path


@pytest.mark.parametrize("command, reason", [
    ("cat ~/.ssh/id_ed25519", "may hold secrets"),
    ("cat .env", "may hold secrets"),
    ("grep KEY config/.env.local", "may hold secrets"),
    ("cat < .env", "may hold secrets"),
    ("head -c 100 server.pem", "may hold secrets"),
    ("cat ../elsewhere.txt", "outside the working directory"),
    ("ls /", "outside the working directory"),
    ("cat ~/elsewhere.txt", "outside the working directory"),
])
def test_reading_secrets_or_outside_the_folder_is_asked_every_time(home, command, reason):
    check = permissions.check(command, set(), str(home / "project"))
    assert not check.ok and reason in check.always_ask and check.always_ask.startswith(permissions.READS)


@pytest.mark.parametrize("command", [
    "cat .env.example", "ls src", "grep -r '/api/' src", "echo ~/.ssh/id_ed25519", "cat id_ed25519.pub",
])
def test_ordinary_paths_still_run_right_away(home, command):
    assert permissions.check(command, set(), str(home / "project")).ok


def test_a_command_that_changes_a_secret_isnt_just_a_read(home):
    check = permissions.check("rm .env", set(), str(home / "project"))
    assert check.needed == ["rm"] and not check.always_ask.startswith(permissions.READS)
    # A read and then a change: the change still needs approval
    assert permissions.check("cat .env && rm x", set(), str(home / "project")).needed == ["rm"]


def test_variables_that_run_programs_are_asked_every_time():
    from labb import permissions as p

    for command in ("GIT_EXTERNAL_DIFF='rm -rf ~' git diff", "LD_PRELOAD=./x.so ls", "PAGER=evil git log",
                    "PATH=.:$PATH ls", "NODE_OPTIONS=--require=./x.js cat a"):
        result = p.check(command, set())
        assert result.always_ask and not result.ok, command
    # Others don't change what runs
    assert p.check("LANG=C ls", set()).ok and p.check("NODE_ENV=test npm test", {"npm test"}).ok


def test_git_reads_ask_when_the_repository_config_runs_programs(tmp_path):
    import subprocess
    from labb import permissions as p

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert p.check("git status", set(), str(tmp_path)).ok
    subprocess.run(["git", "-C", str(tmp_path), "config", "core.fsmonitor", "true"], check=True)
    assert p.check("git status", set(), str(tmp_path)).ok
    for key, value in (("core.fsmonitor", "./hook.sh"), ("diff.external", "./x"), ("diff.pdf.textconv", "./conv")):
        subprocess.run(["git", "-C", str(tmp_path), "config", key, value], check=True)
        result = p.check("git diff", set(), str(tmp_path))
        assert not result.ok and "git config runs programs" in result.always_ask, key
        subprocess.run(["git", "-C", str(tmp_path), "config", "--unset", key], check=True)
