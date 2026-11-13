"""An agent working with others: asking the agents it was given (`ask_agent`) and using the
Studio actions it was given (`call_action`), both chosen in the agent's editor.

Only offered to an agent that has some (tools.offered_for); the agent's system prompt lists them
(model.agent_section). The server runs them (server/studio/team.py sets `ask` and `act`), as
whoever is chatting; this module stays free of the server's code."""

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

LABEL = "Other agents and actions"
# Offered only to an agent that was given agents or actions (tools.allowed_for names it then)
ONLY_IF_ALLOWED = True

# Set by the server: (config, agent name, message) -> reply, and (config, action name, inputs) -> result
ask = None
act = None


@tool
def ask_agent(agent: str, message: str, config: RunnableConfig = None) -> str:
    """Ask one of the agents you can work with (listed in your instructions) and get its answer.
    Give it everything it needs in the message: it doesn't see this conversation.

    Args:
        agent: the agent's name, as listed
        message: what to ask or have it do
    """
    if ask is None:
        return "Asking other agents only works on the server."
    return ask(config, agent, message)


@tool
def call_action(action: str, inputs: dict | None = None, config: RunnableConfig = None) -> str:
    """Use one of the actions you were given (listed in your instructions), e.g. to create a task in
    another app. Returns what it answered.

    Args:
        action: the action's name, as listed
        inputs: its inputs by name, e.g. {"title": "Buy milk"}
    """
    if act is None:
        return "Actions only work on the server."
    return act(config, action, inputs or {})


TOOLS = [ask_agent, call_action]
