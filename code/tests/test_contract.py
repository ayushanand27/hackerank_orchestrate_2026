"""The output-contract validator catches every class of violation (mutation tests)."""
import csv
import json
import os
import tempfile
import unittest

import _paths
_paths.ensure()
from contract import validate_output
from ghost_audit.output import write_output


def step(n, phase, system, rid, op, state, ev=""):
    return {"step_no": n, "phase": phase, "system_id": system, "resource_id": rid, "operation": op,
            "state": state, "evidence_id": ev}


GOOD = {
    "case_1": ("INCOMPLETE", [
        step(1, "FINDING", "search_service", "search_1", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_1"),
        step(2, "REMEDIATION", "search_service", "search_1", "DELETE_RECORD", "ABSENT"),
        step(3, "VERIFICATION", "search_service", "search_1", "VERIFY_RECORD_ABSENT", "ABSENT")]),
    "case_2": ("COMPLETE", [
        step(1, "VERIFICATION", "account_service", "account_2", "VERIFY_RECORD_ABSENT", "ABSENT", "evidence_1"),
        step(2, "VERIFICATION", "security_service", "audit_record_2", "VERIFY_RECORD_PRESENT", "PRESENT", "activity_1")]),
    "case_3": ("NOT_APPLICABLE", []),
}


class Contract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name

        def w(name, header, rows):
            with open(os.path.join(d, name), "w", newline="", encoding="utf-8") as f:
                cw = csv.writer(f)
                cw.writerow(header)
                cw.writerows(rows)
        w("deletion_requests.csv", ["case_id", "email", "requested_at", "reported_status", "reported_at", "audit_at",
                                    "request_text"],
          [[c, f"{c}@example.org", "2026-09-01T00:00:00Z", "COMPLETE", "2026-09-02T00:00:00Z",
            "2026-09-10T00:00:00Z", "delete my account"] for c in GOOD])
        w("evidence_files.csv", ["file_id", "file_path", "origin", "captured_at"],
          [["evidence_1", "evidence/a.log", "x", "2026-09-05T00:00:00Z"],
           ["evidence_2", "evidence/b.log", "x", "2026-09-20T00:00:00Z"]])
        w("system_activity.csv", ["activity_id", "event_time", "event_type", "source_system", "target_system",
                                  "resource_id", "reported_result", "details"],
          [["activity_1", "2026-09-03T00:00:00Z", "RETAIN_RECORD", "a", "security_service", "audit_record_2", "SUCCESS", ""]])
        w("user_audit_log.csv", ["audit_id", "user_id", "event_time", "field_name", "old_value", "new_value"], [])

    def tearDown(self):
        self.tmp.cleanup()

    def errors(self, answers):
        path = os.path.join(self.tmp.name, "output.csv")
        write_output(path, [(c, st, steps) for c, (st, steps) in answers.items()])
        return validate_output(path, self.tmp.name)

    def mutate(self, case, fn):
        answers = json.loads(json.dumps(GOOD))
        fn(answers[case][1])
        return self.errors(answers)

    def test_valid_output_passes(self):
        self.assertEqual(self.errors(GOOD), [])

    def test_each_violation_is_reported(self):
        def set_field(i, k, v):
            return lambda steps: steps[i].__setitem__(k, v)
        cases = {
            "late evidence": ("case_2", set_field(0, "evidence_id", "evidence_2"), "captured after audit_at"),
            "unknown evidence": ("case_2", set_field(0, "evidence_id", "evidence_9"), "unknown evidence id"),
            "future step cites evidence": ("case_1", set_field(1, "evidence_id", "evidence_1"), "future step"),
            "finding without evidence": ("case_1", set_field(0, "evidence_id", ""), "finding without evidence"),
            "wrong state": ("case_1", set_field(1, "state", "REDACTED"), "bad op/state/phase"),
            "numbering": ("case_1", set_field(2, "step_no", 7), "step numbering"),
            "order": ("case_1", lambda s: s.reverse(), "out of order"),
            "extra key": ("case_1", set_field(0, "note", "x"), "bad keys"),
        }
        for label, (case, fn, needle) in cases.items():
            with self.subTest(label):
                errs = self.mutate(case, fn)
                self.assertTrue(any(needle in e for e in errs), errs)

    def test_status_rules(self):
        bad = dict(GOOD, case_3=("NOT_APPLICABLE", GOOD["case_2"][1]))
        self.assertTrue(any("NOT_APPLICABLE must have []" in e for e in self.errors(bad)))
        bad = dict(GOOD, case_2=("COMPLETE", GOOD["case_1"][1]))
        self.assertTrue(any("COMPLETE with non-verification" in e for e in self.errors(bad)))
        bad = dict(GOOD, case_1=("INCOMPLETE", GOOD["case_1"][1][1:]))
        self.assertTrue(any("INCOMPLETE without findings" in e for e in self.errors(bad)))

    def test_missing_row(self):
        answers = dict(GOOD)
        answers.pop("case_3")
        self.assertTrue(any("row count" in e for e in self.errors(answers)))


if __name__ == "__main__":
    unittest.main()
