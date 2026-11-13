import os

from labb.tools.files import glob, grep, list_files, read_file


def run(tool, config, **args):
    return tool.invoke(args, config)


def test_paths_outside_the_working_directory_are_refused(tmp_path, config):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret")
    (tmp_path / "link.txt").symlink_to(outside)
    for path in ["../outside.txt", str(outside), "link.txt"]:
        assert "outside the working directory" in run(read_file, config, path=path)
    assert "outside the working directory" in run(list_files, config, path="..")


def test_file_tools_need_a_working_directory():
    assert "only work in the terminal" in read_file.invoke({"path": "a.txt"}, {"configurable": {}})


def test_read_file_numbers_lines_and_reads_in_parts(tmp_path, config):
    (tmp_path / "long.txt").write_text("".join(f"line {i}\n" for i in range(1, 11)))
    first = run(read_file, config, path="long.txt", limit=3)
    assert first.splitlines()[0].split() == ["1", "line", "1"]
    assert "read again with offset=4" in first
    rest = run(read_file, config, path="long.txt", offset=9)
    assert "line 10" in rest and "more lines" not in rest
    assert "has only 10 lines" in run(read_file, config, path="long.txt", offset=50)


def test_read_file_refuses_binary_files_and_folders(tmp_path, config):
    (tmp_path / "image.png").write_bytes(b"\x89PNG\x00\x00data")
    (tmp_path / "src").mkdir()
    assert "binary" in run(read_file, config, path="image.png")
    assert "is a folder" in run(read_file, config, path="src")


def test_glob_matches_folders_and_skips_ignored_ones(tmp_path, config):
    for path in ["a.py", "src/b.py", "src/deep/c.py", "node_modules/x/d.py", "notes.md"]:
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text("")
    assert run(glob, config, pattern="**/*.py").splitlines() == ["a.py", "src/b.py", "src/deep/c.py"]
    assert run(glob, config, pattern="*.py") == "a.py"
    assert run(glob, config, pattern="*.py", path="src") == "src/b.py"
    assert run(glob, config, pattern="**/*.rs").startswith("No files match")


def test_grep_finds_lines_with_their_files(tmp_path, config):
    (tmp_path / "app.py").write_text("def add(a, b):\n    return a + b\n")
    (tmp_path / "notes.md").write_text("add more tests\n")
    assert run(grep, config, pattern=r"def \w+") == "app.py:1: def add(a, b):"
    assert run(grep, config, pattern="ADD", ignore_case=True, glob="*.md") == "notes.md:1: add more tests"
    assert run(grep, config, pattern="missing").startswith("No matches")
    assert "invalid regular expression" in run(grep, config, pattern="(")


def test_list_files_skips_symlinks_and_ignored_folders(tmp_path, config):
    (tmp_path / ".venv" / "lib").mkdir(parents=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x")
    os.symlink(tmp_path / "src", tmp_path / "linked")
    listing = run(list_files, config, recursive=True)
    assert "src/a.py" in listing and "linked  (link)" in listing
    assert ".venv/lib" not in listing and "(not listed: .venv/)" in listing


def test_secret_files_are_read_only_with_approval(tmp_path):
    from labb.tools.files import grep, read_file
    (tmp_path / ".env").write_text("API_KEY=hunter2\n")
    (tmp_path / "app.py").write_text("API_KEY = load()\n")
    asked = []

    def approve(command, needed, always_ask, title="Run this command?"):
        asked.append(always_ask)
        return ("no", None)

    config = {"configurable": {"workdir": str(tmp_path), "approve": approve}}
    assert "didn't allow" in read_file.invoke({"path": ".env"}, config=config)
    assert "may hold secrets" in asked[0]
    found = grep.invoke({"pattern": "API_KEY"}, config=config)
    assert "app.py:1" in found and "hunter2" not in found and "Not searched" in found
    # Without a way to ask (the -p mode), it's refused
    assert "approval" in read_file.invoke({"path": ".env"}, config={"configurable": {"workdir": str(tmp_path)}})
    config["configurable"]["approve"] = lambda *args: "yes"
    assert "hunter2" in read_file.invoke({"path": ".env"}, config=config)
