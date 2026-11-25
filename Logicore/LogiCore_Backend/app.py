"""LogiCore: A Rule-Based Scholarship Decision System (Flask + SQLite).

Run:  python app.py        then open http://127.0.0.1:5000
"""
import csv
import io
import math
import os
import secrets
import time
from datetime import datetime, timezone
from functools import wraps

import click
from flask import Flask, Response, g, jsonify, render_template, request, session
from werkzeug.security import check_password_hash, generate_password_hash

import database
import engine

DUMMY_HASH = generate_password_hash("not-a-real-password")  # keeps login timing similar for unknown users


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_or_create_secret(instance_path):
    path = os.path.join(instance_path, "secret.key")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
    with open(path) as f:
        return f.read().strip()


# ----------------------------------------------------------------- validation
def to_number(value, low, high):
    """Parse a finite number inside [low, high], or return None."""
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(n) or not (low <= n <= high):
        return None
    return n


def parse_applicant(data):
    """Validate applicant input on the server. Return (clean, error)."""
    name = str(data.get("name", "")).strip()[:80] or "Unnamed"
    avg = to_number(data.get("avg"), 0, 100)
    if avg is None:
        return None, "General average must be a number from 0 to 100."
    income = to_number(data.get("income"), 0, 1e9)
    if income is None:
        return None, "Monthly family income must be a number of 0 or more."
    flags = {}
    for key, label in (("failed", "Failing grade"), ("record", "Disciplinary record")):
        v = to_number(data.get(key), 0, 1)
        if v not in (0.0, 1.0):
            return None, label + " must be 0 (no) or 1 (yes)."
        flags[key] = int(v)
    return {"name": name, "avg": avg, "income": income, **flags}, None


def parse_rule(data):
    """Validate a rule from the admin page. Return (clean, error)."""
    name = str(data.get("name", "")).strip()
    result = str(data.get("result", "")).strip()
    reason = str(data.get("reason", "")).strip() or "Matched rule conditions."
    if not name or len(name) > 60:
        return None, "Rule name is required (60 characters at most)."
    if not result or len(result) > 60:
        return None, "Result is required (60 characters at most)."
    if len(reason) > 200:
        return None, "Reason must be 200 characters or fewer."
    priority = data.get("priority")
    if isinstance(priority, bool) or not isinstance(priority, (int, float, str)):
        return None, "Priority must be a whole number."
    try:
        priority = int(float(priority))
    except (TypeError, ValueError, OverflowError):
        return None, "Priority must be a whole number."
    if not (-1000 <= priority <= 1000):
        return None, "Priority must be between -1000 and 1000."
    conditions = data.get("conditions")
    if not isinstance(conditions, list) or not (1 <= len(conditions) <= 8):
        return None, "Add between 1 and 8 conditions."
    clean = []
    for c in conditions:
        if not isinstance(c, dict):
            return None, "Each condition must have a field, operator, and value."
        field, op = c.get("field"), c.get("operator")
        value = to_number(c.get("value"), -1e9, 1e9)
        if field not in engine.FIELDS or op not in engine.OPERATORS or value is None:
            return None, "Each condition needs a valid field, operator, and number."
        clean.append({"field": field, "operator": op, "value": value})
    return {"name": name, "result": result, "reason": reason, "priority": priority, "conditions": clean}, None


def csv_safe(value):
    """Stop spreadsheet formulas from running when a CSV is opened."""
    text = str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


# -------------------------------------------------------------------- factory
def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    os.makedirs(app.instance_path, exist_ok=True)
    app.config.update(
        DATABASE=os.path.join(app.instance_path, "logicore.db"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("LOGICORE_HTTPS") == "1",
        MAX_CONTENT_LENGTH=64 * 1024,
        LOGIN_MAX_FAILS=5,
        LOGIN_WINDOW=300,
        SEED_DEMO_USERS=True,
    )
    if test_config:
        app.config.update(test_config)
    app.secret_key = os.environ.get("LOGICORE_SECRET") or load_or_create_secret(app.instance_path)

    database.init_db(app.config["DATABASE"])
    if app.config["SEED_DEMO_USERS"]:
        db = database.connect(app.config["DATABASE"])
        if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            for username, role, password in (("admin", "admin", "admin123"), ("staff", "staff", "staff123")):
                db.execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                           (username, generate_password_hash(password), role))
            db.commit()
        db.close()

    failed_logins = {}  # ip -> list of timestamps

    # ---------------------------------------------------------------- helpers
    def get_db():
        if "db" not in g:
            g.db = database.connect(app.config["DATABASE"])
        return g.db

    @app.teardown_appcontext
    def close_db(_exc):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    def current_user():
        if "user" not in g:
            uid = session.get("user_id")
            row = None
            if uid is not None:
                row = get_db().execute("SELECT id, username, role FROM users WHERE id = ?", (uid,)).fetchone()
            g.user = row
        return g.user

    def login_required(fn):
        @wraps(fn)
        def wrapper(*a, **k):
            if not current_user():
                return jsonify(error="Please log in."), 401
            return fn(*a, **k)
        return wrapper

    def admin_required(fn):
        @wraps(fn)
        def wrapper(*a, **k):
            user = current_user()
            if not user:
                return jsonify(error="Please log in."), 401
            if user["role"] != "admin":
                return jsonify(error="Only an administrator can do this."), 403
            return fn(*a, **k)
        return wrapper

    def rule_json(r):
        return {**r, "text": " and ".join(engine.condition_text(c) for c in r["conditions"])}

    # --------------------------------------------------------------- security
    @app.before_request
    def csrf_protect():
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.path.startswith("/api/") \
                and request.path != "/api/login" and "user_id" in session:
            token = request.headers.get("X-CSRF-Token", "")
            if not session.get("csrf") or not secrets.compare_digest(token, session["csrf"]):
                return jsonify(error="Invalid or missing security token. Please refresh the page."), 403

    @app.after_request
    def security_headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    # ------------------------------------------------------------------ pages
    @app.get("/")
    def index():
        return render_template("index.html")

    # ------------------------------------------------------------------- auth
    @app.post("/api/login")
    def login():
        ip = request.remote_addr or "unknown"
        now = time.time()
        recent = [t for t in failed_logins.get(ip, []) if now - t < app.config["LOGIN_WINDOW"]]
        if len(recent) >= app.config["LOGIN_MAX_FAILS"]:
            return jsonify(error="Too many failed attempts. Try again in a few minutes."), 429
        data = request.get_json(silent=True) or {}
        username = str(data.get("username", "")).strip()[:50]
        password = str(data.get("password", ""))[:200]
        user = get_db().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        ok = check_password_hash(user["password_hash"] if user else DUMMY_HASH, password)
        if not (user and ok):
            recent.append(now)
            failed_logins[ip] = recent
            return jsonify(error="Wrong username or password."), 401
        failed_logins.pop(ip, None)
        session.clear()
        session["user_id"] = user["id"]
        session["csrf"] = secrets.token_urlsafe(32)
        return jsonify(user={"username": user["username"], "role": user["role"]}, csrf=session["csrf"])

    @app.post("/api/logout")
    def logout():
        session.clear()
        return jsonify(ok=True)

    @app.get("/api/me")
    def me():
        user = current_user()
        if not user:
            return jsonify(user=None)
        return jsonify(user={"username": user["username"], "role": user["role"]}, csrf=session.get("csrf"))

    # ------------------------------------------------------------------ rules
    @app.get("/api/rules")
    @admin_required
    def list_rules():
        return jsonify(rules=[rule_json(r) for r in database.load_rules(get_db())])

    @app.post("/api/rules")
    @admin_required
    def create_rule():
        clean, error = parse_rule(request.get_json(silent=True) or {})
        if error:
            return jsonify(error=error), 400
        db = get_db()
        cur = db.execute(
            "INSERT INTO rules (code, name, priority, result, reason) VALUES (?, ?, ?, ?, ?)",
            (database.next_code(db), clean["name"], clean["priority"], clean["result"], clean["reason"]))
        database.save_conditions(db, cur.lastrowid, clean["conditions"])
        db.commit()
        return jsonify(ok=True, id=cur.lastrowid), 201

    @app.put("/api/rules/<int:rule_id>")
    @admin_required
    def update_rule(rule_id):
        clean, error = parse_rule(request.get_json(silent=True) or {})
        if error:
            return jsonify(error=error), 400
        db = get_db()
        if not db.execute("SELECT 1 FROM rules WHERE id = ?", (rule_id,)).fetchone():
            return jsonify(error="Rule not found."), 404
        db.execute("UPDATE rules SET name = ?, priority = ?, result = ?, reason = ? WHERE id = ?",
                   (clean["name"], clean["priority"], clean["result"], clean["reason"], rule_id))
        database.save_conditions(db, rule_id, clean["conditions"])
        db.commit()
        return jsonify(ok=True)

    @app.delete("/api/rules/<int:rule_id>")
    @admin_required
    def delete_rule(rule_id):
        db = get_db()
        cur = db.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
        db.commit()
        if cur.rowcount == 0:
            return jsonify(error="Rule not found."), 404
        return jsonify(ok=True)

    @app.post("/api/rules/reset")
    @admin_required
    def reset_rules():
        db = get_db()
        database.reset_rules(db)
        db.commit()
        return jsonify(ok=True)

    # --------------------------------------------------------------- evaluate
    @app.get("/api/samples")
    @login_required
    def samples():
        return jsonify(samples=engine.SAMPLES)

    @app.post("/api/evaluate")
    @login_required
    def evaluate():
        applicant, error = parse_applicant(request.get_json(silent=True) or {})
        if error:
            return jsonify(error=error), 400
        db = get_db()
        trace, winner = engine.evaluate(database.load_rules(db), applicant)
        stamp = now_iso()
        a = db.execute(
            "INSERT INTO applicants (name, avg, income, failed, record, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (applicant["name"], applicant["avg"], applicant["income"], applicant["failed"], applicant["record"], stamp))
        if winner:
            result, reason, code, rid, rname = winner["result"], winner["reason"], winner["code"], winner["id"], winner["name"]
        else:
            result, reason, code, rid, rname = "Manual review needed", "No rule matched this applicant.", "-", None, "None"
        d = db.execute(
            "INSERT INTO decisions (applicant_id, rule_id, rule_code, result, reason, decided_by, decided_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (a.lastrowid, rid, code, result, reason, current_user()["id"], stamp))
        for t in trace:
            db.execute("INSERT INTO decision_trace (decision_id, rule_code, rule_name, fired) VALUES (?, ?, ?, ?)",
                       (d.lastrowid, t["rule"]["code"], t["rule"]["name"], 1 if t["fired"] else 0))
        db.commit()
        return jsonify(
            decision_id=d.lastrowid,
            applicant=applicant,
            decision={"result": result, "reason": reason, "rule_code": code, "rule_name": rname, "tone": engine.tone(result)},
            trace=[{
                "code": t["rule"]["code"], "name": t["rule"]["name"], "priority": t["rule"]["priority"],
                "conditions": " and ".join(engine.condition_text(c) for c in t["rule"]["conditions"]),
                "fired": t["fired"], "won": bool(winner and winner["id"] == t["rule"]["id"]),
            } for t in trace])

    # ---------------------------------------------------------------- history
    def history_rows(limit=None):
        sql = ("SELECT d.id, d.decided_at, a.name, a.avg, a.income, d.result, d.rule_code, u.username AS decided_by "
               "FROM decisions d JOIN applicants a ON a.id = d.applicant_id "
               "LEFT JOIN users u ON u.id = d.decided_by ORDER BY d.id DESC")
        if limit:
            sql += " LIMIT %d" % limit
        return [dict(r) for r in get_db().execute(sql).fetchall()]

    @app.get("/api/history")
    @login_required
    def history():
        return jsonify(history=history_rows(200))

    @app.delete("/api/history")
    @admin_required
    def clear_history():
        db = get_db()
        db.execute("DELETE FROM decisions")
        db.execute("DELETE FROM applicants")
        db.commit()
        return jsonify(ok=True)

    @app.get("/api/history.csv")
    @login_required
    def history_csv():
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["Date (UTC)", "Applicant", "Average", "Income", "Decision", "Rule", "Evaluated by"])
        for r in history_rows():
            w.writerow([csv_safe(x) for x in (r["decided_at"], r["name"], r["avg"], r["income"],
                                              r["result"], r["rule_code"], r["decided_by"] or "")])
        return Response(out.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=logicore-history.csv"})

    @app.get("/api/report")
    @login_required
    def report():
        db = get_db()
        rows = db.execute("SELECT result, COUNT(*) AS n FROM decisions GROUP BY result ORDER BY n DESC, result").fetchall()
        total = sum(r["n"] for r in rows)
        granted = sum(r["n"] for r in rows if engine.tone(r["result"]) == "ok")
        avg = db.execute("SELECT AVG(a.avg) FROM decisions d JOIN applicants a ON a.id = d.applicant_id").fetchone()[0]
        return jsonify(
            total=total, granted=granted,
            approval_rate=round(granted / total * 100) if total else None,
            avg_grade=round(avg, 1) if avg is not None else None,
            by_result=[{"result": r["result"], "count": r["n"]} for r in rows])

    # ------------------------------------------------------------------ tests
    @app.get("/api/tests")
    @login_required
    def tests():
        rows = engine.run_tests(database.load_rules(get_db()))
        return jsonify(rows=rows, passed=sum(1 for r in rows if r["pass"]), total=len(rows))

    # -------------------------------------------------------------------- cli
    @app.cli.command("create-user")
    @click.argument("username")
    @click.argument("role", type=click.Choice(["admin", "staff"]))
    @click.password_option()
    def create_user(username, role, password):
        """Create a user, or reset a user's password."""
        db = database.connect(app.config["DATABASE"])
        hashed = generate_password_hash(password)
        if db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            db.execute("UPDATE users SET password_hash = ?, role = ? WHERE username = ?", (hashed, role, username))
        else:
            db.execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)", (username, hashed, role))
        db.commit()
        db.close()
        click.echo("Saved user %s (%s)." % (username, role))

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
