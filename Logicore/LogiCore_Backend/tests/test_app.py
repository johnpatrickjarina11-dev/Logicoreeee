import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import create_app  # noqa: E402


class AppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        self.app = create_app({"DATABASE": self.db_path, "TESTING": True})
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def login(self, username="admin", password="admin123"):
        res = self.client.post("/api/login", json={"username": username, "password": password})
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        return {"X-CSRF-Token": res.get_json()["csrf"]}

    def good_rule(self, **kw):
        rule = {"name": "Good standing", "result": "Partial scholarship (25%)", "priority": 45,
                "reason": "Average is 88 or higher.", "conditions": [{"field": "avg", "operator": ">=", "value": 88}]}
        rule.update(kw)
        return rule

    # ---- authentication
    def test_pages_need_login(self):
        for path in ("/api/rules", "/api/history", "/api/report", "/api/tests", "/api/samples"):
            self.assertEqual(self.client.get(path).status_code, 401, path)

    def test_wrong_password_rejected(self):
        res = self.client.post("/api/login", json={"username": "admin", "password": "wrong"})
        self.assertEqual(res.status_code, 401)

    def test_login_is_rate_limited(self):
        for _ in range(5):
            self.client.post("/api/login", json={"username": "admin", "password": "wrong"})
        res = self.client.post("/api/login", json={"username": "admin", "password": "admin123"})
        self.assertEqual(res.status_code, 429)

    def test_passwords_are_hashed(self):
        row = sqlite3.connect(self.db_path).execute("SELECT password_hash FROM users WHERE username='admin'").fetchone()
        self.assertNotEqual(row[0], "admin123")
        self.assertNotIn("admin123", row[0])

    def test_logout_ends_session(self):
        h = self.login()
        self.client.post("/api/logout", headers=h)
        self.assertEqual(self.client.get("/api/rules").status_code, 401)

    # ---- CSRF and roles
    def test_csrf_token_required(self):
        self.login()
        res = self.client.post("/api/evaluate", json={"avg": 90, "income": 1000, "failed": 0, "record": 0})
        self.assertEqual(res.status_code, 403)

    def test_staff_cannot_manage_rules(self):
        h = self.login("staff", "staff123")
        self.assertEqual(self.client.get("/api/rules").status_code, 403)
        self.assertEqual(self.client.post("/api/rules", json=self.good_rule(), headers=h).status_code, 403)
        self.assertEqual(self.client.delete("/api/history", headers=h).status_code, 403)

    def test_staff_can_evaluate(self):
        h = self.login("staff", "staff123")
        res = self.client.post("/api/evaluate", json={"avg": 96, "income": 12000, "failed": 0, "record": 0}, headers=h)
        self.assertEqual(res.status_code, 200)

    # ---- evaluation and validation
    def test_evaluate_saves_decision_and_trace(self):
        h = self.login()
        res = self.client.post("/api/evaluate", json={"name": "Papat", "avg": 96, "income": 12000, "failed": 0, "record": 0}, headers=h)
        body = res.get_json()
        self.assertEqual(body["decision"]["result"], "Full scholarship (100%)")
        self.assertEqual(body["decision"]["rule_code"], "R4")
        self.assertEqual(len(body["trace"]), 7)
        self.assertEqual(sum(1 for t in body["trace"] if t["won"]), 1)
        hist = self.client.get("/api/history").get_json()["history"]
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]["name"], "Papat")
        self.assertEqual(hist[0]["decided_by"], "admin")

    def test_invalid_applicant_input_rejected(self):
        h = self.login()
        bad = [
            {"avg": 150, "income": 1000, "failed": 0, "record": 0},
            {"avg": -1, "income": 1000, "failed": 0, "record": 0},
            {"avg": "abc", "income": 1000, "failed": 0, "record": 0},
            {"avg": 90, "income": -5, "failed": 0, "record": 0},
            {"avg": 90, "income": "", "failed": 0, "record": 0},
            {"avg": 90, "income": 1000, "failed": 2, "record": 0},
            {"avg": 90, "income": 1000, "failed": 0},
            {"avg": "nan", "income": 1000, "failed": 0, "record": 0},
            {"avg": True, "income": 1000, "failed": 0, "record": 0},
        ]
        for payload in bad:
            res = self.client.post("/api/evaluate", json=payload, headers=h)
            self.assertEqual(res.status_code, 400, payload)
        self.assertEqual(len(self.client.get("/api/history").get_json()["history"]), 0)

    def test_no_matching_rule_means_manual_review(self):
        h = self.login()
        for r in self.client.get("/api/rules").get_json()["rules"]:
            self.client.delete("/api/rules/%d" % r["id"], headers=h)
        res = self.client.post("/api/evaluate", json={"avg": 90, "income": 1000, "failed": 0, "record": 0}, headers=h)
        self.assertEqual(res.get_json()["decision"]["result"], "Manual review needed")

    # ---- rule management
    def test_rule_create_edit_delete(self):
        h = self.login()
        self.assertEqual(self.client.post("/api/rules", json=self.good_rule(), headers=h).status_code, 201)
        rules = self.client.get("/api/rules").get_json()["rules"]
        self.assertEqual(len(rules), 8)
        new = [r for r in rules if r["name"] == "Good standing"][0]
        self.assertEqual(new["code"], "R8")
        res = self.client.put("/api/rules/%d" % new["id"], json=self.good_rule(priority=47), headers=h)
        self.assertEqual(res.status_code, 200)
        changed = [r for r in self.client.get("/api/rules").get_json()["rules"] if r["id"] == new["id"]][0]
        self.assertEqual(changed["priority"], 47)
        self.assertEqual(self.client.delete("/api/rules/%d" % new["id"], headers=h).status_code, 200)
        self.assertEqual(len(self.client.get("/api/rules").get_json()["rules"]), 7)

    def test_new_rule_changes_decision(self):
        h = self.login()
        payload = {"avg": 88, "income": 30000, "failed": 0, "record": 0}
        before = self.client.post("/api/evaluate", json=payload, headers=h).get_json()
        self.assertEqual(before["decision"]["result"], "Waitlisted")
        self.client.post("/api/rules", json=self.good_rule(), headers=h)
        after = self.client.post("/api/evaluate", json=payload, headers=h).get_json()
        self.assertEqual(after["decision"]["result"], "Partial scholarship (25%)")

    def test_invalid_rules_rejected(self):
        h = self.login()
        bad = [
            self.good_rule(name=""),
            self.good_rule(result=""),
            self.good_rule(name="x" * 61),
            self.good_rule(priority="abc"),
            self.good_rule(priority=5000),
            self.good_rule(conditions=[]),
            self.good_rule(conditions=[{"field": "salary", "operator": ">=", "value": 1}]),
            self.good_rule(conditions=[{"field": "avg", "operator": "LIKE", "value": 1}]),
            self.good_rule(conditions=[{"field": "avg", "operator": ">=", "value": "x"}]),
            self.good_rule(conditions="avg>=88"),
        ]
        for payload in bad:
            self.assertEqual(self.client.post("/api/rules", json=payload, headers=h).status_code, 400, payload)

    def test_reset_restores_default_rules(self):
        h = self.login()
        self.client.post("/api/rules", json=self.good_rule(), headers=h)
        self.client.post("/api/rules/reset", headers=h)
        self.assertEqual(len(self.client.get("/api/rules").get_json()["rules"]), 7)

    def test_sql_injection_text_is_stored_as_plain_text(self):
        h = self.login()
        evil = "x'); DROP TABLE rules;--"
        self.assertEqual(self.client.post("/api/rules", json=self.good_rule(name=evil), headers=h).status_code, 201)
        self.assertEqual(len(self.client.get("/api/rules").get_json()["rules"]), 8)
        res = self.client.post("/api/evaluate", json={"name": evil, "avg": 90, "income": 1, "failed": 0, "record": 0}, headers=h)
        self.assertEqual(res.status_code, 200)

    # ---- report, history, csv, tests page
    def test_report_numbers(self):
        h = self.login()
        for avg in (96, 91, 80):
            self.client.post("/api/evaluate", json={"avg": avg, "income": 12000, "failed": 0, "record": 0}, headers=h)
        rep = self.client.get("/api/report").get_json()
        self.assertEqual(rep["total"], 3)
        self.assertEqual(rep["granted"], 2)
        self.assertEqual(rep["approval_rate"], 67)
        self.assertEqual(rep["avg_grade"], 89.0)

    def test_clear_history(self):
        h = self.login()
        self.client.post("/api/evaluate", json={"avg": 96, "income": 12000, "failed": 0, "record": 0}, headers=h)
        self.client.delete("/api/history", headers=h)
        self.assertEqual(self.client.get("/api/history").get_json()["history"], [])

    def test_csv_blocks_formula_injection(self):
        h = self.login()
        self.client.post("/api/evaluate", json={"name": "=HYPERLINK(\"http://x\")", "avg": 90, "income": 1, "failed": 0, "record": 0}, headers=h)
        res = self.client.get("/api/history.csv")
        self.assertEqual(res.status_code, 200)
        self.assertIn("'=HYPERLINK", res.get_data(as_text=True))
        self.assertIn("text/csv", res.headers["Content-Type"])

    def test_tests_endpoint_passes_on_default_rules(self):
        self.login()
        body = self.client.get("/api/tests").get_json()
        self.assertEqual((body["passed"], body["total"]), (6, 6))

    def test_security_headers(self):
        res = self.client.get("/")
        self.assertEqual(res.headers["X-Frame-Options"], "DENY")
        self.assertEqual(res.headers["X-Content-Type-Options"], "nosniff")


if __name__ == "__main__":
    unittest.main()
