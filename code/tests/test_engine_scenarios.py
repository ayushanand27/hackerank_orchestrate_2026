"""End-to-end runs of the real pipeline on the hand-written synthetic scenarios
(``code/evaluation/custom_cases.py``), plus checks that each technique is what solves its
scenario (switching it off must break exactly that scenario)."""
import csv
import os
import tempfile
import unittest

import _paths
_paths.ensure()
import custom_cases
from ghost_audit.config import Features
from ghost_audit.engine import Auditor
from metrics import compare


def failing(features=None):
    exp, pred = custom_cases.run(lambda d: Auditor(d, None, features=features))
    res = compare(exp, pred)
    return sorted(c for c, pc in res["per_case"].items() if not pc["exact"]), exp, pred


class Scenarios(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fails, cls.exp, cls.pred = failing()

    def test_all_scenarios_exact(self):
        self.assertEqual(self.fails, [])
        self.assertGreaterEqual(len(self.exp), 11)

    def test_not_applicable_has_no_steps(self):
        self.assertEqual(self.pred["case_902"], ("NOT_APPLICABLE", []))

    def test_each_technique_is_load_bearing(self):
        expectations = {
            "historical_identity": "case_901",
            "lineage": "case_906",
            "timing_attribution": "case_906",
            "policy_phase": "case_907",
            "omission": "case_909",
            "worker_hint": "case_910",
            "catalog_links": "case_911",
        }
        for feature, case in expectations.items():
            with self.subTest(feature=feature):
                fails, _, _ = failing(Features().without(feature))
                self.assertIn(case, fails)

    def test_trail_explains_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            custom_cases.build(tmp)
            auditor = Auditor(tmp, None)
            with open(os.path.join(tmp, "deletion_requests.csv"), encoding="utf-8") as fh:
                reqs = list(csv.DictReader(fh))
            auditor.prepare(reqs)
            r = next(r for r in reqs if r["case_id"] == "case_906")
            status, steps, trail = auditor.audit(r)
        self.assertEqual(trail["status"], status)
        self.assertTrue(any(w.startswith("lineage:") for w in trail["links"]["search_9966"]))
        self.assertTrue(any(w.startswith(("timing:", "cadence:")) for w in trail["links"]["account_9906"]))
        self.assertEqual(trail["views"]["search_9966"]["state"], "PRESENT")


if __name__ == "__main__":
    unittest.main()
