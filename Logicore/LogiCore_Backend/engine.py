"""LogiCore rule engine.

Pure Python: no database and no web code, so it is easy to test.

A rule has an id, a priority (its score), a result, a reason, and a list of
conditions. A rule fires only when ALL of its conditions are true. When
several rules fire, the one with the highest priority decides. If two rules
have the same priority, the one created first (lowest id) decides.
"""

FIELDS = {
    "avg": "General average",
    "income": "Monthly income",
    "failed": "Failing grade (1=yes, 0=no)",
    "record": "Disciplinary record (1=yes, 0=no)",
}
OPERATORS = (">=", "<=", ">", "<", "==")


def check(cond, applicant):
    """Return True if one condition is true for the applicant."""
    x = applicant[cond["field"]]
    v = cond["value"]
    op = cond["operator"]
    if op == ">=":
        return x >= v
    if op == "<=":
        return x <= v
    if op == ">":
        return x > v
    if op == "<":
        return x < v
    return x == v


def rule_matches(rule, applicant):
    return all(check(c, applicant) for c in rule["conditions"])


def number_text(v):
    return str(int(v)) if float(v).is_integer() else str(v)


def condition_text(cond):
    label = FIELDS[cond["field"]].split(" (")[0]
    return "%s %s %s" % (label, cond["operator"], number_text(cond["value"]))


def evaluate(rules, applicant):
    """Check every rule. Return (trace, winner).

    trace  : list of {"rule": rule, "fired": bool}, highest priority first
    winner : the fired rule with the highest priority, or None
    """
    ordered = sorted(rules, key=lambda r: (-r["priority"], r["id"]))
    trace = [{"rule": r, "fired": rule_matches(r, applicant)} for r in ordered]
    fired = [t["rule"] for t in trace if t["fired"]]
    winner = fired[0] if fired else None
    return trace, winner


def tone(result):
    """Color class for a result: ok, mid, or bad."""
    r = result.lower()
    if r.startswith("not"):
        return "bad"
    if "scholarship" in r:
        return "ok"
    return "mid"


SAMPLES = [
    {"label": "Top student, low income", "name": "Papat", "avg": 96, "income": 12000, "failed": 0, "record": 0, "expect": "Full scholarship (100%)"},
    {"label": "High average", "name": "Joshua", "avg": 91, "income": 30000, "failed": 0, "record": 0, "expect": "Partial scholarship (50%)"},
    {"label": "Needs support", "name": "Renz", "avg": 87, "income": 8000, "failed": 0, "record": 0, "expect": "Financial assistance"},
    {"label": "Average only", "name": "Sarah", "avg": 86, "income": 40000, "failed": 0, "record": 0, "expect": "Waitlisted"},
    {"label": "Disciplinary record", "name": "Allison", "avg": 92, "income": 9000, "failed": 0, "record": 1, "expect": "Not eligible"},
    {"label": "Low average", "name": "Jobert", "avg": 80, "income": 5000, "failed": 0, "record": 0, "expect": "Not eligible"},
]


def run_tests(rules):
    """Run the sample applicants against the rules and compare with expected."""
    rows = []
    for s in SAMPLES:
        _, winner = evaluate(rules, s)
        got = winner["result"] if winner else "Manual review needed"
        rows.append({**s, "got": got, "pass": got == s["expect"]})
    return rows
