"""SQLite state: which items each agent has already triaged and posted, so one finding
is reported once per renotify window however many runs see it."""
import json
import os
import sqlite3
import time

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    agent      TEXT NOT NULL,
    key        TEXT NOT NULL,
    title      TEXT NOT NULL,
    first_seen REAL NOT NULL,
    last_seen  REAL NOT NULL,
    times_seen INTEGER NOT NULL DEFAULT 1,
    triaged    REAL,          -- when the agent last triaged it
    triage     TEXT,          -- the agent's structured output (JSON)
    notified   REAL,          -- when it was last posted to Slack
    failures   INTEGER NOT NULL DEFAULT 0,  -- triage attempts that failed in a row
    PRIMARY KEY (agent, key)
);
CREATE TABLE IF NOT EXISTS runs (
    agent     TEXT NOT NULL,
    started   REAL NOT NULL,
    finished  REAL,
    status    TEXT,
    summary   TEXT
);
"""


class State:
    def __init__(self, agent):
        self.agent = agent
        self.db = sqlite3.connect(os.path.join(config.STATE_DIR, "state.db"))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        if "failures" not in [r["name"] for r in self.db.execute("PRAGMA table_info(items)")]:
            self.db.execute("ALTER TABLE items ADD COLUMN failures INTEGER NOT NULL DEFAULT 0")
        self.renotify = config.AGENTS[agent]["renotify_hours"] * 3600

    def classify(self, items, now=None):
        """Split collected items into (to_triage, to_repost, quiet).
        to_triage: never seen, or last posted longer ago than the renotify window.
        to_repost: triaged inside the window but never posted (Slack was down or unset).
        quiet: already posted inside the window."""
        now = now or time.time()
        to_triage, to_repost, quiet = [], [], []
        for item in items:
            row = self.db.execute("SELECT * FROM items WHERE agent=? AND key=?", (self.agent, item["key"])).fetchone()
            if row is None:
                self.db.execute("INSERT INTO items (agent, key, title, first_seen, last_seen) VALUES (?,?,?,?,?)",
                                (self.agent, item["key"], item["title"], now, now))
                to_triage.append(item)
                continue
            self.db.execute("UPDATE items SET last_seen=?, times_seen=times_seen+1 WHERE agent=? AND key=?",
                            (now, self.agent, item["key"]))
            if row["notified"] and now - row["notified"] < self.renotify:
                quiet.append(item)
            elif row["triaged"] and now - row["triaged"] < self.renotify:
                item["triage"] = json.loads(row["triage"]) if row["triage"] else None
                to_repost.append(item)
            else:
                to_triage.append(item)
        self.db.commit()
        return to_triage, to_repost, quiet

    def save_triage(self, key, triage):
        self.db.execute("UPDATE items SET triaged=?, triage=?, failures=0 WHERE agent=? AND key=?",
                        (time.time(), json.dumps(triage) if triage is not None else None, self.agent, key))
        self.db.commit()

    def triage_failed(self, key):
        """Count a failed triage and return how many failed in a row. The item stays
        untriaged, so the next run tries it again."""
        self.db.execute("UPDATE items SET failures=failures+1 WHERE agent=? AND key=?", (self.agent, key))
        self.db.commit()
        return self.db.execute("SELECT failures FROM items WHERE agent=? AND key=?",
                               (self.agent, key)).fetchone()["failures"]

    def mark_notified(self, keys):
        now = time.time()
        self.db.executemany("UPDATE items SET notified=? WHERE agent=? AND key=?",
                            [(now, self.agent, k) for k in keys])
        self.db.commit()

    def record_run(self, started, status, summary):
        self.db.execute("INSERT INTO runs (agent, started, finished, status, summary) VALUES (?,?,?,?,?)",
                        (self.agent, started, time.time(), status, json.dumps(summary)))
        # Keep 30 days of history.
        cutoff = time.time() - 30 * 86400
        self.db.execute("DELETE FROM runs WHERE started < ?", (cutoff,))
        self.db.execute("DELETE FROM items WHERE last_seen < ?", (cutoff,))
        self.db.commit()
