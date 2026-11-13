"""Loops: the terminal sends the same prompt again and again in one
conversation, every so often (`/loop 5m check the deploy`) or when the model says (`/loop
check the deploy`: self-paced).

A loop's runs have the `Loop` in `configurable["loop"]`; only they are offered these tools
(tools.offered_for) and get its section of the system prompt (model.system_prompt_parts).
The model calls `schedule_next` (self-paced) and `end_loop` (either kind); the terminal reads
what they set once the run ends. A run that fails is tried again a little later, and one that
forgets to schedule the next is reminded (continuation.py), then run again after its last wait.
"""

import re
from dataclasses import dataclass

from langchain.tools import tool
from langchain_core.runnables import RunnableConfig

NEEDS_WORKDIR = True
# Not in "all the tools": tools.offered_for adds them to a loop's runs
ONLY_IF_ALLOWED = True

# How soon and how late a run can be: a fixed interval, or one the model picks
MIN_SECONDS = 10
MAX_SECONDS = 24 * 3600
# Self-paced: the model's shortest wait (it can't poll faster than this)
MIN_SCHEDULED_SECONDS = 60
# A run that fails (the model or the connection) is tried again after these waits; failing
# once more than that many times in a row ends the loop
FAILURE_WAITS = (30, 120)
# Self-paced: a run that didn't say when to run again is run again after the last wait it chose
# (else this one); the second such run in a row ends the loop
UNSCHEDULED_SECONDS = 600
MAX_UNSCHEDULED = 2

UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
INTERVAL = re.compile(r"^(\d+)([smhd])$")


def parse_interval(text: str) -> int | None:
    """Seconds for "30s", "5m", "2h" or "1d"; None if it isn't one"""
    match = INTERVAL.match(text.strip().lower())
    return int(match[1]) * UNITS[match[2]] if match else None


def duration(seconds: float) -> str:
    """5m, 1h 30m, 45s"""
    seconds = round(seconds)
    if seconds < 60:
        return f"{seconds}s"
    hours, minutes = divmod(seconds // 60, 60)
    return f"{hours}h {minutes}m" if hours and minutes else f"{hours}h" if hours else f"{minutes}m"


@dataclass
class Loop:
    """A prompt run again and again: every `every` seconds, or when the model asks (None)"""

    prompt: str
    every: int | None = None
    runs: int = 0
    # time.time() of the next run
    next_at: float = 0.0
    # Set during a run by the tools: the model's wait and why, or why it ended the loop
    scheduled: int | None = None
    reason: str = ""
    ended: str | None = None
    # Runs in a row that failed, or (self-paced) said neither when to run again nor to stop
    failures: int = 0
    unscheduled: int = 0
    # The last wait the model chose
    last_wait: int | None = None

    def kind(self) -> str:
        return f"every {duration(self.every)}" if self.every else "self-paced"

    def prompt_section(self) -> str:
        if self.every:
            when = (f"The user set this message to run every {duration(self.every)} in this conversation. "
                    "Do what it asks and report briefly, saying what changed since the last run.")
        else:
            when = ("The user set this message to run again in this conversation whenever you say. Do what it "
                    "asks and report briefly, saying what changed since the last run. Then call `schedule_next` "
                    "with how long to wait: short while something is changing, longer while nothing is. If you "
                    "don't, the loop ends.")
        return (f"{when} Call `end_loop` when the job is done, or when what you're watching for has happened "
                "or can't happen any more.")


def loop_of(config: RunnableConfig | None) -> Loop | None:
    return ((config or {}).get("configurable") or {}).get("loop")


@tool
def schedule_next(minutes: float, reason: str, config: RunnableConfig = None) -> str:
    """Self-paced loop: when to run this message again.

    Args:
        minutes: How long to wait, from 1 to 1440 (a day), e.g. 5 while a deploy is running
        reason: Why that long, in a few words, e.g. "the build takes about 5 minutes"
    """
    loop = loop_of(config)
    if loop is None:
        return "Error: this isn't a loop run."
    if loop.every:
        return f"Not changed: this loop runs every {duration(loop.every)}, as the user set it. Call end_loop to stop it."
    seconds = max(MIN_SCHEDULED_SECONDS, min(MAX_SECONDS, round(minutes * 60)))
    loop.scheduled, loop.reason = seconds, reason.strip()
    return f"The next run is in {duration(seconds)}."


@tool
def end_loop(reason: str, config: RunnableConfig = None) -> str:
    """Stop the loop: no more runs of this message.

    Args:
        reason: Why, for the user, e.g. "the deploy finished"
    """
    loop = loop_of(config)
    if loop is None:
        return "Error: this isn't a loop run."
    loop.ended = reason.strip() or "the job is done"
    return "The loop ends after this run."


TOOLS = [schedule_next, end_loop]
