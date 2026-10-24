"""Point-in-time state rules and the plan builder."""
import unittest

import _paths
_paths.ensure()
from ghost_audit.evidence import Observation
from ghost_audit.planner import plan
from ghost_audit.state import effective_state

RULES = {c: {"required_action": a} for c, a in {
    "USER_PROFILE": "DELETE", "SEARCH_INDEX_ENTRY": "DELETE", "SUPPORT_MESSAGE": "REDACT",
    "ANALYTICS_PROFILE": "ANONYMIZE", "SECURITY_AUDIT_LOG": "RETAIN", "ACCOUNT_TOMBSTONE": "REDACT",
    "BACKUP_ARCHIVE": "RETAIN", "SUPPORT_ATTACHMENT": "DELETE"}.items()}


def obs(state=None, kind="log", **pii):
    o = Observation("e", "t", "t", "x_1", state=state, kind=kind)
    o.pii = {k: [v] for k, v in pii.items()}
    return o


def view(system, category, state, evidence="evidence_1"):
    return dict(system=system, category=category, state=state, evidence=evidence)


class EffectiveState(unittest.TestCase):
    def test_delete_category_any_copy_is_present(self):
        self.assertEqual(effective_state("SEARCH_INDEX_ENTRY", obs("IDENTIFIABLE")), "PRESENT")
        self.assertEqual(effective_state("SEARCH_INDEX_ENTRY", obs("ABSENT")), "ABSENT")

    def test_redact_category_from_snapshot_fields(self):
        self.assertEqual(effective_state("SUPPORT_MESSAGE", obs("PRESENT", "sql", email="a@example.org")), "IDENTIFIABLE")
        self.assertEqual(effective_state("ACCOUNT_TOMBSTONE", obs("PRESENT", "sql", user_ref="user_1")), "REDACTED")
        self.assertEqual(effective_state("SUPPORT_MESSAGE", obs("ANONYMIZED")), "REDACTED")

    def test_anonymize_category_stable_reference_still_links(self):
        self.assertEqual(effective_state("ANALYTICS_PROFILE", obs("PRESENT", "snapshot", user_ref="user_1")), "IDENTIFIABLE")
        self.assertEqual(effective_state("ANALYTICS_PROFILE", obs("PRESENT", "snapshot")), "ANONYMIZED")

    def test_process_states_pass_through(self):
        for st in ("ACTIVE", "DISABLED", "BLOCKED"):
            self.assertEqual(effective_state("DATA_FLOW", obs(st)), st)


class Planner(unittest.TestCase):
    def test_complete_case_cites_evidence(self):
        views = {
            "account_1": view("account_service", "USER_PROFILE", "ABSENT", "evidence_3"),
            "audit_record_1": view("security_service", "SECURITY_AUDIT_LOG", "PRESENT", "evidence_2"),
            "backup_1": view("backup_service", "BACKUP_ARCHIVE", "BLOCKED", "evidence_2"),
            "analytics_1": view("analytics_platform", "ANALYTICS_PROFILE", "ANONYMIZED", "activity_9"),
        }
        status, steps = plan(views, "account_1", RULES)
        self.assertEqual(status, "COMPLETE")
        self.assertTrue(all(s["phase"] == "VERIFICATION" and s["evidence_id"] for s in steps))
        self.assertEqual([s["operation"] for s in steps], [
            "VERIFY_RECORD_ABSENT", "VERIFY_RECORD_PRESENT", "VERIFY_RECORD_ANONYMIZED", "VERIFY_RESTORE_BLOCKED"])

    def test_incomplete_case_full_plan(self):
        views = {
            "search_1": view("search_service", "SEARCH_INDEX_ENTRY", "PRESENT", "evidence_1"),
            "support_1": view("support_platform", "SUPPORT_MESSAGE", "IDENTIFIABLE", "evidence_4"),
            "data_flow_1": view("integration_service", "DATA_FLOW", "ACTIVE", "evidence_1"),
            "analytics_1": view("analytics_platform", "ANALYTICS_PROFILE", "ANONYMIZED", "evidence_1"),
            "audit_record_1": view("security_service", "SECURITY_AUDIT_LOG", "PRESENT", "evidence_1"),
        }
        status, steps = plan(views, "account_1", RULES)
        self.assertEqual(status, "INCOMPLETE")
        got = [(s["phase"], s["operation"], s["resource_id"], s["evidence_id"]) for s in steps]
        self.assertEqual(got, [
            ("FINDING", "REPORT_REMAINING_RECORD", "search_1", "evidence_1"),
            ("FINDING", "REPORT_EXPOSED_PII", "support_1", "evidence_4"),
            ("FINDING", "REPORT_ACTIVE_DATA_FLOW", "data_flow_1", "evidence_1"),
            ("REMEDIATION", "DISABLE_DATA_FLOW", "data_flow_1", ""),
            ("REMEDIATION", "DELETE_RECORD", "search_1", ""),
            ("REMEDIATION", "REDACT_RECORD", "support_1", ""),
            ("VERIFICATION", "VERIFY_RECORD_ABSENT", "account_1", ""),     # primary profile always verified
            ("VERIFICATION", "VERIFY_RECORD_ABSENT", "search_1", ""),
            ("VERIFICATION", "VERIFY_RECORD_PRESENT", "audit_record_1", ""),
            ("VERIFICATION", "VERIFY_RECORD_REDACTED", "support_1", ""),
            ("VERIFICATION", "VERIFY_DATA_FLOW_DISABLED", "data_flow_1", ""),
        ])
        # already-compliant analytics is not re-verified in an incomplete plan
        self.assertNotIn("analytics_1", {s["resource_id"] for s in steps})

    def test_surviving_primary_profile_is_a_finding(self):
        status, steps = plan({"account_1": view("account_service", "USER_PROFILE", "PRESENT")}, "account_1", RULES)
        self.assertEqual(status, "INCOMPLETE")
        self.assertEqual([s["operation"] for s in steps], ["REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT"])

    def test_unknown_state_produces_no_step(self):
        status, steps = plan({"search_1": view("search_service", "SEARCH_INDEX_ENTRY", None)}, None, RULES)
        self.assertEqual((status, steps), ("COMPLETE", []))


if __name__ == "__main__":
    unittest.main()
