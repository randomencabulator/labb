"""Answer modes (fast, auto, thinking): what each asks of the providers, and how a run gets one"""

from langchain_core.messages import AIMessage

from labb import llm, model


def models(kind: str, levels: list[str] | None = None) -> llm.Models:
    chat = type("Chat", (), {"profile": {"reasoning_effort_levels": levels} if levels else {}})()
    return llm.Models(f"{kind}:m", kind, "m", chat, chat, 1000, kind)


def test_each_provider_gets_its_own_knob():
    assert llm.mode_options(models("ollama"), "fast") == {"reasoning": False}
    assert llm.mode_options(models("ollama"), "thinking") == {}
    gpt = models("openai", ["none", "low", "medium", "high", "xhigh"])
    assert llm.mode_options(gpt, "fast") == {"reasoning_effort": "none"}
    assert llm.mode_options(gpt, "thinking") == {"reasoning_effort": "xhigh"}
    claude = models("anthropic", ["low", "medium", "high"])
    assert llm.mode_options(claude, "fast") == {"reasoning_effort": "low"}
    assert llm.mode_options(claude, "thinking") == {"reasoning_effort": "high"}
    # No effort levels (gpt-4o) or an unknown server: only the prompt changes
    assert llm.mode_options(models("openai"), "thinking") == {}
    assert llm.mode_options(models("openai_compatible", ["low", "high"]), "fast") == {}
    assert all(llm.mode_options(gpt, "auto") == {} for gpt in (gpt, claude, models("ollama")))


def test_the_mode_adds_its_prompt_and_unknown_ones_are_auto():
    def labels(mode):
        return [label for label, _ in model.system_prompt_parts({"configurable": {"answer_mode": mode}})]

    assert "Answer mode" in labels("fast") and "Answer mode" in labels("thinking")
    assert "Answer mode" not in labels("auto") and "Answer mode" not in labels("bogus")
    assert llm.answer_mode({"configurable": {"answer_mode": "bogus"}}) == "auto"


def test_the_web_chat_sends_its_mode_to_the_model(client, monkeypatch):
    seen = []

    class Bound:
        def invoke(self, messages, **options):
            seen.append((messages[0].content, options))
            return AIMessage(content="ok")

    monkeypatch.setattr(model, "model_with_tools", lambda *args: Bound())
    monkeypatch.setattr(llm, "mode_options", lambda models, mode: {"mode": mode})
    client.post("/chat", json={"message": "hi", "answer_mode": "thinking"})
    prompt, options = seen[-1]
    assert options == {"mode": "thinking"} and model.MODE_PROMPTS["thinking"] in prompt
    assert client.post("/chat", json={"message": "hi", "answer_mode": "slow"}).status_code == 422


def test_anthropic_thinking_shows_like_ollama_reasoning():
    from labb.runner import thinking_of

    assert thinking_of(AIMessage(content="hi", additional_kwargs={"reasoning_content": "hmm"})) == "hmm"
    claude = AIMessage(content=[{"type": "thinking", "thinking": "let me see"}, {"type": "text", "text": "4"}])
    assert thinking_of(claude) == "let me see" and claude.text == "4"
    assert thinking_of(AIMessage(content="plain")) == ""
