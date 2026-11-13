"""The model's thinking (Ollama's): folded in the terminal, Ctrl-O and Ctrl-T."""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from labb import settings
from labb.cli import app
from labb.cli.render import ReplyView, ThoughtBlock, console


def test_a_hidden_thought_says_how_to_expand_it():
    assert "ctrl+o to expand" in ThoughtBlock(2.0, "Let me look.", False).render().plain
    assert "ctrl+o" not in ThoughtBlock(2.0, "", False).render().plain
    with console.capture() as shown:
        console.print(ThoughtBlock(2.0, "Let me look.", True).render())
    assert "Let me look." in shown.get()


def test_ctrl_t_shows_the_thinking_while_it_streams():
    view = ReplyView(show_thinking=False)
    view.handle({"type": "thinking", "content": "Checking the tests first."})
    with console.capture() as hidden:
        console.print(view.render())
    assert "Checking the tests" not in hidden.get() and "ctrl+t: show thinking" in hidden.get()
    view.show_thinking = True
    with console.capture() as shown:
        console.print(view.render())
    assert "Checking the tests" in shown.get()


def test_ctrl_o_shows_the_last_replys_thinking_and_tool_outputs_in_order():
    messages = [
        HumanMessage("old"), AIMessage(content="", additional_kwargs={"reasoning_content": "Old thought."}),
        HumanMessage("Read a.py"),
        AIMessage(content="", additional_kwargs={"reasoning_content": "I should read it."},
                  tool_calls=[{"name": "read_file", "args": {"path": "a.py"}, "id": "c1"}]),
        ToolMessage("print('hi')", tool_call_id="c1", name="read_file"),
        AIMessage(content="It prints hi."),
    ]
    with console.capture() as shown:
        app.show_last_reply(messages)
    text = shown.get()
    assert "Old thought" not in text
    assert text.index("I should read it.") < text.index("print('hi')")
    with console.capture() as nothing:
        app.show_last_reply([HumanMessage("hi"), AIMessage(content="Hello.")])
    assert "didn't think out loud or use any tools" in nothing.get()


def test_show_thinking_is_a_setting(tmp_path):
    good, bad = tmp_path / "good.json", tmp_path / "bad.json"
    good.write_text('{"showThinking": true}')
    bad.write_text('{"showThinking": "yes"}')
    problems = []
    assert settings.read(good, problems) == {"showThinking": True}
    assert settings.read(bad, problems) == {} and "showThinking should be true or false" in problems[0]
