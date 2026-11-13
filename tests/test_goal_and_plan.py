"""The terminal's goals (/goal) and approved plans (/plan show), and what outlives a summary"""

import threading
from uuid import uuid4

from conftest import ai

from labb import engine, runner, work
from labb.agent import chat_agent
from labb.cli import app
from labb.tools import goal


def session_in(tmp_path, thread_id: str | None = None) -> app.Session:
    return app.Session(workdir=str(tmp_path), thread_id=thread_id)


def test_a_goal_is_set_shown_paused_resumed_and_cleared(tmp_path):
    session = session_in(tmp_path)
    assert app.goal_command(session, "--calls 7 Make every test pass") == "Make every test pass"
    kept = goal.get(app.work_config(session))
    assert session.thread_id and kept["budget"] == 7 and kept["status"] == "active"
    assert (tmp_path / ".labb" / "goals" / f"{session.thread_id}.json").exists()
    assert app.goal_status(session) == "◎ goal active, 0 of 7 model calls used"
    assert app.goal_command(session, "pause") is None and goal.get(app.work_config(session))["status"] == "paused"
    assert app.goal_command(session, "resume") == "Keep working toward the goal."
    # Out of budget: resuming gives it another budget like the first
    goal.save(app.work_config(session), {**goal.get(app.work_config(session)), "status": "budget_limited", "used": 7})
    app.goal_command(session, "resume")
    assert goal.get(app.work_config(session))["budget"] == 14
    app.goal_command(session, "clear")
    assert goal.get(app.work_config(session)) is None and app.goal_status(session) is None


def test_the_agent_goes_on_by_itself_while_the_goal_is_active(tmp_path, fake_model):
    session = session_in(tmp_path, f"cli-{uuid4()}")
    goal.start(app.work_config(session), "Ship it")
    fake_model(ai("Shipped half."))
    # No rounds within the reply, so it ends with the goal still active
    no_rounds = engine.resolve({"continue": {"goal": 0}}, "x")
    runner.run_reply(session.thread_id, "Go", threading.Event(), lambda e: None,
                     configurable={"workdir": str(tmp_path), "engine": no_rounds}, name=False)
    assert app.keeps_going(session, {"type": "done"}, looping=False)
    assert not app.keeps_going(session, {"type": "stopped"}, looping=False)
    session.mode = "plan"
    assert not app.keeps_going(session, {"type": "done"}, looping=False)


def test_an_approved_plan_is_saved_and_named_to_the_model(tmp_path, fake_model):
    from labb import model

    thread = f"cli-{uuid4()}"
    fake_model(ai("1. Add the flag in cli.py\n2. Test it"))
    runner.run_reply(thread, "Add a --quiet flag", threading.Event(), lambda e: None,
                     configurable={"workdir": str(tmp_path), "mode": "plan"}, name=False)
    session = session_in(tmp_path, thread)
    saved = app.save_plan(session)
    assert saved == f".labb/plans/{thread}.md"
    text = (tmp_path / saved).read_text()
    assert "Request: Add a --quiet flag" in text and "2. Test it" in text and "Approved plan" in text
    # While there's work open, every call says where the plan is
    config = {"configurable": {"workdir": str(tmp_path), "thread_id": thread}}
    work.write_json(config, "todos", [{"id": 1, "task": "Add the flag", "status": "pending"}])
    assert f"`{saved}`" in model.work_reminder(config)
    assert f"Approved plan: {saved}" in model.work_facts(config, {"update_todos"})


def test_taking_a_message_back_moves_the_clearing_back(fake_model):
    thread = f"web-{uuid4()}"
    fake_model(ai("one"), ai("two"))
    for text in ("first", "second"):
        runner.run_reply(thread, text, threading.Event(), lambda e: None, name=False)
    config = {"configurable": {"thread_id": thread}}
    chat_agent.update_state(config, {"cleared_before": 3}, as_node="llm_call")
    runner.take_back(thread, 1)
    assert chat_agent.get_state(config).values["cleared_before"] == 2
