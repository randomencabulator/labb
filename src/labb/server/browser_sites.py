"""The sites each person lets the browser click and type on without asking first, in every chat.
What sends, pays or deletes still asks (see tools/browser.py)."""

from labb.chats import conn, lock, now

with lock, conn:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS browser_sites ("
        "user_id TEXT NOT NULL, site TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (user_id, site))"
    )


def listed(user_id: str) -> list[str]:
    with lock:
        return [row[0] for row in conn.execute("SELECT site FROM browser_sites WHERE user_id = ? ORDER BY site", (user_id,)).fetchall()]


def add(user_id: str, site: str):
    with lock, conn:
        conn.execute("INSERT INTO browser_sites (user_id, site, created_at) VALUES (?, ?, ?) ON CONFLICT (user_id, site) DO NOTHING",
                     (user_id, site, now()))


def remove(user_id: str, site: str):
    with lock, conn:
        conn.execute("DELETE FROM browser_sites WHERE user_id = ? AND site = ?", (user_id, site))
