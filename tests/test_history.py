"""Looking back at what the model no longer sees: cleared results by number, and searching"""

import threading
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from labb import context, runner
from labb.agent import chat_agent
from labb.tools import history
from conftest import ai


def look(messages: list, **args) -> str:
    return history.look_back.invoke(args, {"configurable": {"history": messages}})


def test_cleared_results_and_the_summary_say_how_to_get_them_back():
    messages = [ToolMessage("r" * 2000, tool_call_id=str(i), name="run_command") for i in range(4)]
    cleared = context.clear_old_tool_results(messages, start=10)
    assert "#10. look_back(number=10)" in cleared[0].content
    sent = context.model_messages({"messages": [HumanMessage("old")] * 4 + [HumanMessage("new")], "summary": "S",
                                   "summarized": 4}, "You help.")
    assert "messages #0 to #3; look_back searches them" in sent[0].content


def test_a_message_is_read_again_in_pages():
    output = "".join(f"line {i}\n" for i in range(1000))
    messages = [HumanMessage("run the tests"), AIMessage("", tool_calls=[{"name": "run_command", "args": {"command": "pytest"}, "id": "1"}]),
                ToolMessage(output, tool_call_id="1", name="run_command")]
    first = look(messages, number=2)
    assert first.startswith("Message #2, result of run_command (page 1 of 2") and "line 0\n" in first
    assert "line 999" in look(messages, number=2, page=2)
    assert "no message #7" in look(messages, number=7) and "has 2 pages" in look(messages, number=2, page=3)
    assert "Error" in look(messages) and "isn't available" in history.look_back.invoke({"number": 0}, {})


def test_search_finds_lines_and_tool_calls_but_not_earlier_lookups():
    messages = [AIMessage("", tool_calls=[{"name": "read_file", "args": {"path": "src/auth.py"}, "id": "1"}]),
                ToolMessage("ok\nTypeError: bad operand\nmore", tool_call_id="1", name="read_file"),
                ToolMessage("#1 (result of read_file): TypeError: bad operand", tool_call_id="2", name="look_back")]
    found = look(messages, query="TypeError")
    assert "#1 (result of read_file): TypeError: bad operand" in found and "#2" not in found
    assert "#0 (you): Called read_file" in look(messages, query="auth.py")
    # Every word on a line counts too, and nothing found says so
    assert "#1" in look(messages, query="operand TypeError")
    assert "Nothing in this conversation matches" in look(messages, query="KeyError")


def test_the_agent_reads_a_cleared_result_in_a_run(fake_model, config):
    thread = f"cli-{uuid4()}"
    chat_agent.update_state({"configurable": {"thread_id": thread}}, {"messages": [
        HumanMessage("run it"), AIMessage("", tool_calls=[{"name": "run_command", "args": {"command": "make"}, "id": "x"}]),
        ToolMessage("make: *** [build] Error 2", tool_call_id="x", name="run_command"), AIMessage("It failed.")]})
    fake_model(ai(tool_calls=[("look_back", {"number": 2})]), ai("It was a build error."))
    runner.run_reply(thread, "What was the error?", threading.Event(), lambda event: None,
                     configurable=config["configurable"], name=False)
    saved = chat_agent.get_state({"configurable": {"thread_id": thread}}).values["messages"]
    assert "make: *** [build] Error 2" in saved[-2].content and saved[-2].name == "look_back"
