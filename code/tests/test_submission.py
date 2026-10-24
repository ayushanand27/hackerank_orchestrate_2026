"""Checks on the real dataset and the submitted output.csv (skipped when dataset/ is absent,
e.g. when running from the code.zip alone)."""
import csv
import os
import tempfile
import unittest

from _paths import DATASET, REPO, ensure
ensure()
from contract import validate_output
from ghost_audit.output import write_output

HAVE_DATA = os.path.exists(os.path.join(DATASET, "deletion_requests.csv"))
OUTPUT = os.path.join(REPO, "output.csv")


def read(name):
    with open(os.path.join(DATASET, name), encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@unittest.skipUnless(HAVE_DATA, "dataset/ not available")
class Submission(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ghost_audit.engine import Auditor
        cls.auditor = Auditor(DATASET, os.path.join(REPO, "code", ".cache"))
        cls.sample = read("sample_deletion_requests.csv")
        cls.test = read("deletion_requests.csv")
        cls.auditor.prepare(cls.test + cls.sample)
        cls.rows, cls.trails = [], {}
        for r in sorted(cls.test, key=lambda r: int(r["case_id"].split("_")[1])):
            st, steps, trail = cls.auditor.audit(r)
            cls.rows.append((r["case_id"], st, steps))
            cls.trails[r["case_id"]] = trail

    def test_fresh_run_satisfies_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "output.csv")
            write_output(path, self.rows)
            self.assertEqual(validate_output(path, DATASET), [])

    def test_fresh_run_reproduces_submitted_file(self):
        if not os.path.exists(OUTPUT):
            self.skipTest("output.csv not generated yet")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "output.csv")
            write_output(path, self.rows)
            with open(path, "rb") as a, open(OUTPUT, "rb") as b:
                self.assertEqual(a.read(), b.read(), "output.csv is stale: run python3 code/main.py")

    def test_primary_profile_always_covered(self):
        """Every case with an attributed primary profile verifies (or reports) it; cases without one
        are the few hand-crafted single-artifact requests whose evidence names no account."""
        without, deletion_cases = [], 0
        for cid, st, steps in self.rows:
            if st == "NOT_APPLICABLE":
                continue
            deletion_cases += 1
            acct = self.trails[cid].get("primary_account")
            if acct is None:
                without.append(cid)
                continue
            self.assertTrue(any(s["resource_id"] == acct for s in steps), cid)
        self.assertLess(len(without) / deletion_cases, 0.03, without)

    def test_sample_status_accuracy(self):
        from metrics import compare, load_output_csv, summary
        exp = load_output_csv(os.path.join(DATASET, "sample_output.csv"))
        pred = {r["case_id"]: self.auditor.audit(r)[:2] for r in self.sample}
        s = summary(compare(exp, pred))
        self.assertEqual(s["status_accuracy"], 1.0)
        self.assertGreaterEqual(s["step_f1"], 0.99)

    def test_no_evidence_after_audit_at_is_ever_cited(self):
        manifest = {r["file_id"]: r["captured_at"] for r in read("evidence_files.csv")}
        audit_at = {r["case_id"]: r["audit_at"] for r in self.test}
        for cid, _st, steps in self.rows:
            for s in steps:
                if s["evidence_id"] in manifest:
                    self.assertLessEqual(manifest[s["evidence_id"]], audit_at[cid], (cid, s))

    def test_deterministic(self):
        from ghost_audit.engine import Auditor
        again = Auditor(DATASET, os.path.join(REPO, "code", ".cache"))
        again.prepare(self.test + self.sample)
        for cid, st, steps in self.rows[:120]:
            r = next(r for r in self.test if r["case_id"] == cid)
            self.assertEqual(again.audit(r)[:2], (st, steps))


if __name__ == "__main__":
    unittest.main()
