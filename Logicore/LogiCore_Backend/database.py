"""SQLite database: schema, seed data, and helpers."""
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('admin', 'staff'))
);
CREATE TABLE IF NOT EXISTS rules (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    code     TEXT NOT NULL UNIQUE,
    name     TEXT NOT NULL,
    priority INTEGER NOT NULL,
    result   TEXT NOT NULL,
    reason   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rule_conditions (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id  INTEGER NOT NULL REFERENCES rules(id) ON DELETE CASCADE,
    field    TEXT NOT NULL CHECK (field IN ('avg', 'income', 'failed', 'record')),
    operator TEXT NOT NULL CHECK (operator IN ('>=', '<=', '>', '<', '==')),
    value    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS applicants (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    avg        REAL NOT NULL CHECK (avg BETWEEN 0 AND 100),
    income     REAL NOT NULL CHECK (income >= 0),
    failed     INTEGER NOT NULL CHECK (failed IN (0, 1)),
    record     INTEGER NOT NULL CHECK (record IN (0, 1)),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    applicant_id INTEGER NOT NULL REFERENCES applicants(id) ON DELETE CASCADE,
    rule_id      INTEGER REFERENCES rules(id) ON DELETE SET NULL,
    rule_code    TEXT NOT NULL,
    result       TEXT NOT NULL,
    reason       TEXT NOT NULL,
    decided_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    decided_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decision_trace (
    decision_id INTEGER NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
    rule_code   TEXT NOT NULL,
    rule_name   TEXT NOT NULL,
    fired       INTEGER NOT NULL CHECK (fired IN (0, 1)),
    PRIMARY KEY (decision_id, rule_code)
);
"""

# code, name, priority, result, reason, conditions (field, operator, value)
DEFAULT_RULES = [
    ("R1", "Disciplinary record", 100, "Not eligible", "Applicants with a disciplinary record are disqualified.", [("record", "==", 1)]),
    ("R2", "Failing grade", 90, "Not eligible", "Applicants with a failing grade are disqualified.", [("failed", "==", 1)]),
    ("R3", "Below minimum average", 80, "Not eligible", "General average is below 85.", [("avg", "<", 85)]),
    ("R4", "Excellence + need", 60, "Full scholarship (100%)", "Average 95+ and income of PHP 15,000 or less.", [("avg", ">=", 95), ("income", "<=", 15000)]),
    ("R5", "High average", 50, "Partial scholarship (50%)", "General average is 90 or higher.", [("avg", ">=", 90)]),
    ("R6", "Financial need", 40, "Financial assistance", "Average 85+ and income of PHP 10,000 or less.", [("avg", ">=", 85), ("income", "<=", 10000)]),
    ("R7", "Waitlist", 10, "Waitlisted", "Meets the minimum average but not the other criteria.", [("avg", ">=", 85)]),
]


def connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def init_db(path):
    db = connect(path)
    db.executescript(SCHEMA)
    if db.execute("SELECT COUNT(*) FROM rules").fetchone()[0] == 0:
        seed_rules(db)
    db.commit()
    db.close()


def seed_rules(db):
    for code, name, priority, result, reason, conds in DEFAULT_RULES:
        cur = db.execute(
            "INSERT INTO rules (code, name, priority, result, reason) VALUES (?, ?, ?, ?, ?)",
            (code, name, priority, result, reason))
        for field, op, value in conds:
            db.execute(
                "INSERT INTO rule_conditions (rule_id, field, operator, value) VALUES (?, ?, ?, ?)",
                (cur.lastrowid, field, op, value))


def reset_rules(db):
    db.execute("DELETE FROM rules")
    seed_rules(db)


def load_rules(db):
    """All rules with their conditions, highest priority first."""
    rules = db.execute(
        "SELECT id, code, name, priority, result, reason FROM rules ORDER BY priority DESC, id").fetchall()
    conds = {}
    for c in db.execute("SELECT rule_id, field, operator, value FROM rule_conditions ORDER BY id"):
        conds.setdefault(c["rule_id"], []).append(
            {"field": c["field"], "operator": c["operator"], "value": c["value"]})
    return [dict(r, conditions=conds.get(r["id"], [])) for r in rules]


def next_code(db):
    row = db.execute("SELECT MAX(CAST(SUBSTR(code, 2) AS INTEGER)) AS n FROM rules").fetchone()
    return "R%d" % ((row["n"] or 0) + 1)


def save_conditions(db, rule_id, conditions):
    db.execute("DELETE FROM rule_conditions WHERE rule_id = ?", (rule_id,))
    for c in conditions:
        db.execute(
            "INSERT INTO rule_conditions (rule_id, field, operator, value) VALUES (?, ?, ?, ?)",
            (rule_id, c["field"], c["operator"], c["value"]))
