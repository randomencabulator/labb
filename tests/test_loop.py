"""/loop: a prompt run again and again, every so often or when the model says"""

import time

from prompt_toolkit import PromptSession
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from labb import model
from labb.cli import app
from labb.cli.input import LOOP_DUE, prompt_until
from labb.tools import offered_for, tools_for
from labb.tools.loop import Loop, duration, end_loop, parse_interval, schedule_next


def run(tool, loop, **args):
    return tool.invoke(args, {"configurable": {"loop": loop}})


def test_intervals_and_durations():
    assert [parse_interval(t) for t in ("30s", "5m", "2h", "1d", "5", "m5", "five m")] == [30, 300, 7200, 86400, None, None, None]
    assert [duration(s) for s in (45, 300, 3600, 5400)] == ["45s", "5m", "1h", "1h 30m"]


def test_the_model_paces_and_ends_a_loop():
    loop = Loop("check the deploy")
    assert run(schedule_next, loop, minutes=0.1, reason="soon") == "The next run is in 1m."
    assert (loop.scheduled, loop.reason) == (60, "soon")
    run(schedule_next, loop, minutes=10_000, reason="later")
    assert loop.scheduled == 86400
    fixed = Loop("check", every=300)
    assert "every 5m" in run(schedule_next, fixed, minutes=1, reason="x") and fixed.scheduled is None
    run(end_loop, fixed, reason="the deploy finished")
    assert fixed.ended == "the deploy finished"
    assert "isn't a loop run" in schedule_next.invoke({"minutes": 1, "reason": "x"}, {"configurable": {}})


def test_only_loop_runs_get_the_tools_and_the_prompt(tmp_path):
    plain = {"configurable": {"workdir": str(tmp_path)}}
    looping = {"configurable": {"workdir": str(tmp_path), "loop": Loop("check")}}
    names = lambda config: {tool.name for tool in tools_for(True, offered_for(config))}
    assert not {"schedule_next", "end_loop"} & names(plain)
    assert {"schedule_next", "end_loop"} <= names(looping)
    assert "Loop" not in dict(model.system_prompt_parts(plain))
    assert "schedule_next" in dict(model.system_prompt_parts(looping))["Loop"]


def test_starting_a_loop(tmp_path):
    session = app.Session(workdir=str(tmp_path))
    app.loop_command(session, "5m check the deploy", True)
    assert (session.loop.prompt, session.loop.every, session.loop.next_at) == ("check the deploy", 300, 0.0)
    app.loop_command(session, "watch the tests", True)
    assert (session.loop.prompt, session.loop.every) == ("watch the tests", None)
    for refused in ("5s check", "2d check", "5m", "/compact", "/loop 5m x"):
        app.loop_command(session, refused, True)
        assert session.loop.prompt == "watch the tests"
    app.loop_command(session, "stop", True)
    assert session.loop is None
    app.loop_command(session, "5m check", False)
    assert session.loop is None


def test_after_each_run_the_loop_goes_on_or_ends(tmp_path):
    session = app.Session(workdir=str(tmp_path))
    done = {"type": "done"}
    session.loop = Loop("check", every=300)
    app.after_loop_run(session, done)
    assert session.loop.next_at > time.time() + 290
    session.loop = Loop("check")
    session.loop.scheduled = 120
    app.after_loop_run(session, done)
    assert time.time() + 110 < session.loop.next_at and session.loop.scheduled is None
    # Stopped or ended by the model: over
    for final, setup in [({"type": "stopped"}, {"scheduled": 60}), (done, {"scheduled": 60, "ended": "finished"})]:
        session.loop = Loop("check", **setup)
        app.after_loop_run(session, final)
        assert session.loop is None


def test_a_failed_run_is_tried_again_a_few_times(tmp_path):
    session = app.Session(workdir=str(tmp_path))
    failed = {"type": "done", "failed": True}
    session.loop = Loop("check", every=10)
    app.after_loop_run(session, failed)
    assert session.loop.failures == 1 and session.loop.next_at > time.time() + 25
    # A run that works counts afresh
    app.after_loop_run(session, {"type": "done"})
    assert session.loop.failures == 0
    for _ in range(3):
        app.after_loop_run(session, failed)
    assert session.loop is None


def test_a_run_that_didnt_schedule_the_next_gets_one_more(tmp_path):
    session = app.Session(workdir=str(tmp_path))
    done = {"type": "done"}
    session.loop = Loop("check")
    session.loop.scheduled = 120
    app.after_loop_run(session, done)
    app.after_loop_run(session, done)
    # The last wait the model chose
    assert session.loop.unscheduled == 1 and time.time() + 110 < session.loop.next_at < time.time() + 130
    app.after_loop_run(session, done)
    assert session.loop is None


def test_the_prompt_gives_way_to_a_due_run_unless_something_is_typed():
    with create_pipe_input() as pipe:
        session = PromptSession(input=pipe, output=DummyOutput())
        assert prompt_until(session, time.time() + 0.2) is LOOP_DUE
        pipe.send_text("hi\r")
        assert prompt_until(session, time.time() + 0.2) == "hi"
