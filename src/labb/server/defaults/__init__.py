"""The agents and skills every account starts with: ready-made ones for common web jobs
(news, research, fact-checking, shopping, trips), so a new account has something to try.

- They're given once per account, the first time it's seen (`ensure`: when it's made, and
  at server start for accounts made before). `defaults_given` remembers that, so ones
  deleted stay deleted.
- They're the account's own after that, to change or delete like any other.
- `restore` puts back the ones that are missing (Settings → Account): a skill by its name,
  an agent by its `default_key`. Ones still there are left as they are, edits and all.
- The skills are folders next to this file (`skills/<name>/`); the agents are `AGENTS`.

`enabled` is set by the server's lifespan, so tests get accounts without them unless they
turn it on.
"""

from pathlib import Path

from labb import db
from labb.chats import conn, lock, now

enabled = False

SKILLS_DIR = Path(__file__).parent / "skills"

# Each agent's `key` stays the same across versions: it's how restore knows it's there
AGENTS = [
    {
        "key": "news-desk",
        "name": "News Desk",
        "emoji": "📰",
        "description": "Recent news from good sources, with dates and links, and checks on what's true.",
        "instructions": (
            "You find and explain the news. Use the news-briefing skill for updates on a topic, "
            "fact-check when asked whether something is true, and page-summary for a shared link.\n"
            "- Search before you answer: never give news from memory.\n"
            "- Every fact gets its source and date. Say when something is reported by one outlet only.\n"
            "- Keep news and opinion apart, and stay neutral."
        ),
        "toolsets": ["web", "notes"],
        "skills": ["news-briefing", "fact-check", "page-summary"],
        "starters": [
            "What's the most important news today?",
            "Give me a briefing on the latest in AI this week",
            "Is this claim true? ",
        ],
    },
    {
        "key": "researcher",
        "name": "Researcher",
        "emoji": "🔎",
        "description": "Digs into a question across many sources and writes a cited report.",
        "instructions": (
            "You research questions on the web and report what you find. Use the web-research skill for open "
            "questions, fact-check for single claims, and page-summary for links.\n"
            "- Read the sources, don't just skim search snippets.\n"
            "- Cite every fact inline, and say how sure you are.\n"
            "- Keep notes of what took effort to find, so longer research can go on later."
        ),
        "toolsets": ["web", "notes"],
        "skills": ["web-research", "fact-check", "page-summary"],
        "starters": [
            "Research the pros and cons of heat pumps for an older house",
            "What does the evidence say about intermittent fasting?",
            "Summarize this article: ",
        ],
    },
    {
        "key": "shopping-scout",
        "name": "Shopping Scout",
        "emoji": "🛒",
        "description": "Compares products with independent reviews and current prices, and picks one.",
        "instructions": (
            "You help people decide what to buy. Use the product-comparison skill.\n"
            "- Trust independent tests and owner reviews over shops and makers.\n"
            "- Give prices with the shop and the date.\n"
            "- End with one clear recommendation for their need."
        ),
        "toolsets": ["web", "browser"],
        "skills": ["product-comparison", "page-summary"],
        "starters": [
            "Best noise-cancelling headphones under 200 euros?",
            "Which robot vacuum is best for pet hair?",
            "Is this worth buying? ",
        ],
    },
    {
        "key": "trip-planner",
        "name": "Trip Planner",
        "emoji": "🧳",
        "description": "Plans trips and days out with checked opening hours, transport and costs.",
        "instructions": (
            "You plan trips. Use the trip-planner skill.\n"
            "- Check opening hours, closing days and prices on official sites before putting them in a plan.\n"
            "- Keep days realistic: places close together, time to eat and get around.\n"
            "- Remember what the person likes (pace, budget, food) for next time."
        ),
        "toolsets": ["web"],
        "skills": ["trip-planner", "page-summary"],
        "starters": [
            "Plan 3 days in Lisbon in spring",
            "What can we do this weekend with kids nearby?",
            "A one-day food tour of Naples",
        ],
    },
    {
        "key": "writing-editor",
        "name": "Writing Editor",
        "emoji": "✍️",
        "description": "Edits your writing to be clear and correct, keeping your voice.",
        "instructions": (
            "You edit what people write: emails, posts, essays, documents.\n"
            "- Keep their voice and meaning. Fix grammar, spelling and unclear sentences; cut what isn't needed.\n"
            "- Give the edited text first, then a short list of the main changes and why.\n"
            "- If they ask for a different tone (more formal, friendlier, shorter), do that and nothing else.\n"
            "- Don't add facts or claims of your own."
        ),
        "toolsets": [],
        "skills": "none",
        "starters": [
            "Make this email clearer and friendlier: ",
            "Proofread this: ",
            "Shorten this to under 100 words: ",
        ],
    },
]

# agents.py makes the agents table, and skills.py the skills one
from labb.server import agents as _agents  # noqa: E402, F401
from labb.server import skills as _skills  # noqa: E402, F401

with lock, conn:
    conn.execute("CREATE TABLE IF NOT EXISTS defaults_given (user_id TEXT PRIMARY KEY, given_at TEXT NOT NULL)")
    # Which default an agent was made from, for restore
    db.add_column(conn, "agents", "default_key", "TEXT")


def skill_files() -> dict[str, dict[str, str]]:
    """The default skills' files, by skill name, then path in the skill"""

    found = {}
    for folder in sorted(SKILLS_DIR.iterdir()):
        if (folder / "SKILL.md").is_file():
            found[folder.name] = {str(p.relative_to(folder)): p.read_text()
                                  for p in sorted(folder.rglob("*")) if p.is_file()}
    return found


def missing(user_id: str) -> dict[str, list[str]]:
    """The default agents (keys) and skills (names) the account doesn't have"""

    with lock:
        skill_names = {r[0] for r in conn.execute("SELECT name FROM skills WHERE user_id = ?", (user_id,)).fetchall()}
        agent_keys = {r[0] for r in conn.execute("SELECT default_key FROM agents WHERE user_id = ? AND default_key IS NOT NULL",
                                                  (user_id,)).fetchall()}
    return {"agents": [a["key"] for a in AGENTS if a["key"] not in agent_keys],
            "skills": [name for name in skill_files() if name not in skill_names]}


def restore(user_id: str) -> dict[str, list[str]]:
    """Add the default agents and skills the account doesn't have; what was added"""

    from labb.server import agents, skills

    gone = missing(user_id)
    added = {"agents": [], "skills": []}
    files = skill_files()
    for name in gone["skills"]:
        try:
            skills.save(user_id, files[name], shared=False, replace=False)
            added["skills"].append(name)
        except Exception:  # noqa: BLE001 (full, or a name taken since): the rest still go in
            continue
    usable_skills = {s["name"] for s in skills.list_for(user_id)}
    usable_tools = {t["name"] for t in agents.tool_choices()}
    # Newest first in the list, so the first one here is made last
    for spec in reversed(AGENTS):
        if spec["key"] not in gone["agents"]:
            continue
        fields = {k: v for k, v in spec.items() if k != "key"}
        # Only what this server has: a toolset it doesn't offer, or a skill that couldn't be added
        fields["toolsets"] = [t for t in spec["toolsets"] if t in usable_tools]
        if isinstance(spec["skills"], list):
            fields["skills"] = [s for s in spec["skills"] if s in usable_skills]
        try:
            agent = agents.create(user_id, fields)
        except agents.AgentError:
            continue
        with lock, conn:
            conn.execute("UPDATE agents SET default_key = ? WHERE id = ?", (spec["key"], agent["id"]))
        added["agents"].append(spec["key"])
    return added


def ensure(user_id: str):
    """Give the account the defaults, the first time only"""

    if not enabled:
        return
    with lock, conn:
        first = conn.execute("INSERT INTO defaults_given (user_id, given_at) VALUES (?, ?) ON CONFLICT (user_id) DO NOTHING",
                             (user_id, now())).rowcount
    if first:
        restore(user_id)


def ensure_all():
    """At server start: accounts made before there were defaults get them too"""

    with lock:
        user_ids = [r[0] for r in conn.execute(
            "SELECT id FROM users WHERE id NOT IN (SELECT user_id FROM defaults_given)").fetchall()]
    for user_id in user_ids:
        ensure(user_id)
