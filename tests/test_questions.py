"""The terminal's coding prompt and the ask_user tool."""

import threading
from uuid import uuid4

from labb import model, runner
from labb.agent import chat_agent
from labb.tools import tools_for
from labb.tools.questions import ask_user
from conftest import ai


def ask(config, question="Memory or SQLite?", options=("Memory", "SQLite")):
    return ask_user.invoke({"question": question, "options": list(options)}, config)


def test_the_answer_goes_back_to_the_model(config):
    asked = []
    config["configurable"]["ask_user"] = lambda question, options: asked.append((question, options)) or "SQLite"
    assert ask(config) == "The user answered: SQLite"
    assert asked == [("Memory or SQLite?", ["Memory", "SQLite"])]


def test_without_anyone_to_answer_the_recommended_option_is_used(config):
    result = ask(config)
    assert "Nobody can answer" in result and "'Memory'" in result


def test_a_question_needs_options_and_an_answer(config):
    config["configurable"]["ask_user"] = lambda question, options: None
    assert "2 to 4 options" in ask(config, options=["Only one", " "])
    assert "didn't answer" in ask(config)


def test_asking_is_only_offered_in_the_terminal():
    assert "ask_user" in [t.name for t in tools_for(True)]
    assert "ask_user" not in [t.name for t in tools_for(False)]


def test_the_coding_prompt_is_only_for_the_terminal(config):
    terminal = model.system_prompt(config)
    assert "You're labb, a coding agent" in terminal and "How to work on the user's requests" in terminal
    assert "`ask_user`" in terminal
    web = model.system_prompt()
    assert "coding agent" not in web and "ask_user" not in web


def test_the_graph_asks_and_carries_on(fake_model, config):
    fake_model(ai(tool_calls=[("ask_user", {"question": "Which?", "options": ["A", "B"]})]), ai("Going with B."))
    config["configurable"]["ask_user"] = lambda question, options: "B"
    thread = f"cli-{uuid4()}"
    runner.run_reply(thread, "Do it", threading.Event(), lambda event: None, configurable=config["configurable"], name=False)
    messages = chat_agent.get_state({"configurable": {"thread_id": thread}}).values["messages"]
    assert messages[2].content == "The user answered: B" and messages[-1].content == "Going with B."
