"""`labb eval long`: a job run for real, measured by its files and its conversation"""

import json

from conftest import ai

from labb import engine
from labb.cli import long_evals
from labb.cli.long_eval_cases import CASES, has


def test_a_case_is_run_checked_and_measured(tmp_path, fake_model):
    case = {"name": "tiny", "files": {"a.py": "x = 1\n"}, "messages": ["Add y = 2 to a.py, with a todo list."],
            "checks": [("y", has("a.py", r"^y = 2")), ("z", has("a.py", r"^z = 3"))]}
    fake_model(
        ai("", [("update_todos", {"todos": [{"task": "Add y"}, {"task": "Check it"}]})]),
        ai("", [("edit_file", {"path": "a.py", "old_string": "x = 1\n", "new_string": "x = 1\ny = 2\n"})]),
        # Ends with a step open: sent back once, then it finishes the list
        ai("Added y."),
        ai("", [("update_todos", {"todos": [{"task": "Add y", "status": "done"}, {"task": "Check it", "status": "done"}]})]),
        ai("Done: y = 2 is in a.py."),
    )
    result = long_evals.run_case(case, "ollama:gemma4:e4b", engine.DEFAULT, tmp_path)
    assert result["checks"] == 0.5 and result["failed_checks"] == ["z"]
    assert result["went_on"] == {"open_steps": 1} and result["open_at_end"] == 0 and result["model_calls"] == 5
    saved = long_evals.save({"fake": [result]}, tmp_path / "evals")
    assert json.loads(saved.read_text())["totals"]["fake"]["checks"] == 0.5


def test_the_cases_check_what_they_say():
    steps = next(c for c in CASES if c["name"] == "steps")
    # Seven steps; the rename is checked in both files
    assert len(steps["checks"]) == 8 and all(callable(check) for _, check in steps["checks"])
    recall = next(c for c in CASES if c["name"] == "recall")
    assert engine.check(recall["engine"]) == []
