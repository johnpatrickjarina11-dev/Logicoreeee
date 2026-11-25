import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import database  # noqa: E402
import engine  # noqa: E402


def default_rules():
    rules = []
    for i, (code, name, priority, result, reason, conds) in enumerate(database.DEFAULT_RULES, 1):
        rules.append({
            "id": i, "code": code, "name": name, "priority": priority, "result": result, "reason": reason,
            "conditions": [{"field": f, "operator": o, "value": v} for f, o, v in conds],
        })
    return rules


def applicant(avg, income, failed=0, record=0):
    return {"avg": avg, "income": income, "failed": failed, "record": record}


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.rules = default_rules()

    def decide(self, **kw):
        _, winner = engine.evaluate(self.rules, applicant(**kw))
        return winner["result"] if winner else None

    def test_all_sample_applicants_pass(self):
        rows = engine.run_tests(self.rules)
        self.assertEqual(len(rows), 6)
        for r in rows:
            self.assertTrue(r["pass"], "%s got %s" % (r["name"], r["got"]))

    def test_full_scholarship_boundary(self):
        self.assertEqual(self.decide(avg=95, income=15000), "Full scholarship (100%)")

    def test_just_over_income_limit_is_partial(self):
        self.assertEqual(self.decide(avg=95, income=15001), "Partial scholarship (50%)")

    def test_financial_assistance_beats_waitlist(self):
        self.assertEqual(self.decide(avg=85, income=10000), "Financial assistance")

    def test_below_minimum_average(self):
        self.assertEqual(self.decide(avg=84.99, income=5000), "Not eligible")

    def test_failing_grade_outranks_scholarship(self):
        self.assertEqual(self.decide(avg=97, income=5000, failed=1), "Not eligible")

    def test_disciplinary_record_has_top_priority(self):
        _, winner = engine.evaluate(self.rules, applicant(92, 9000, record=1))
        self.assertEqual(winner["code"], "R1")

    def test_no_rule_matches(self):
        self.assertIsNone(engine.evaluate([], applicant(90, 1000))[1])

    def test_tie_goes_to_lowest_id(self):
        rules = [
            {"id": 2, "code": "R2", "name": "B", "priority": 10, "result": "B", "reason": "", "conditions": [{"field": "avg", "operator": ">=", "value": 0}]},
            {"id": 1, "code": "R1", "name": "A", "priority": 10, "result": "A", "reason": "", "conditions": [{"field": "avg", "operator": ">=", "value": 0}]},
        ]
        self.assertEqual(engine.evaluate(rules, applicant(50, 0))[1]["result"], "A")

    def test_trace_lists_every_rule_by_priority(self):
        trace, _ = engine.evaluate(self.rules, applicant(92, 9000))
        self.assertEqual(len(trace), 7)
        priorities = [t["rule"]["priority"] for t in trace]
        self.assertEqual(priorities, sorted(priorities, reverse=True))

    def test_tone(self):
        self.assertEqual(engine.tone("Not eligible"), "bad")
        self.assertEqual(engine.tone("Full scholarship (100%)"), "ok")
        self.assertEqual(engine.tone("Waitlisted"), "mid")


if __name__ == "__main__":
    unittest.main()
