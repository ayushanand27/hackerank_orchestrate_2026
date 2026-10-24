"""Policy vocabulary and step ordering."""
import unittest

import _paths
_paths.ensure()
from ghost_audit.policy import OP_PHASE, OP_STATE, order_steps, required_action


def s(phase, op, system, rid, ev=""):
    return {"phase": phase, "operation": op, "system_id": system, "resource_id": rid, "evidence_id": ev}


class Policy(unittest.TestCase):
    def test_required_action_prefers_rules_file(self):
        rules = {"SUPPORT_MESSAGE": {"required_action": " redact "}}
        self.assertEqual(required_action(rules, "SUPPORT_MESSAGE"), "REDACT")
        self.assertEqual(required_action({}, "LEGAL_HOLD_RECORD"), "RETAIN")     # fallback table
        self.assertEqual(required_action({}, "UNKNOWN_CATEGORY"), "DELETE")

    def test_every_operation_has_one_phase_and_state(self):
        self.assertEqual(len(OP_STATE), 15)
        for op, phase in OP_PHASE.items():
            self.assertIn(phase, ("FINDING", "REMEDIATION", "VERIFICATION"))
            self.assertIn(OP_STATE[op], ("PRESENT", "ACTIVE", "DISABLED", "BLOCKED", "ABSENT", "REDACTED", "ANONYMIZED"))

    def test_order_disable_and_block_before_delete(self):
        steps = [
            s("VERIFICATION", "VERIFY_RECORD_ABSENT", "search_service", "search_2"),
            s("REMEDIATION", "DELETE_RECORD", "search_service", "search_2"),
            s("REMEDIATION", "BLOCK_RESTORE", "backup_service", "backup_1"),
            s("FINDING", "REPORT_RESTORE_RISK", "backup_service", "backup_1", "evidence_1"),
            s("REMEDIATION", "DISABLE_DATA_FLOW", "integration_service", "data_flow_1"),
            s("FINDING", "REPORT_REMAINING_RECORD", "search_service", "search_2", "evidence_1"),
            s("VERIFICATION", "VERIFY_RECORD_ABSENT", "account_service", "account_1"),
        ]
        out = order_steps(steps)
        self.assertEqual([x["step_no"] for x in out], list(range(1, 8)))
        self.assertEqual([x["operation"] for x in out], [
            "REPORT_REMAINING_RECORD", "REPORT_RESTORE_RISK", "DISABLE_DATA_FLOW", "BLOCK_RESTORE",
            "DELETE_RECORD", "VERIFY_RECORD_ABSENT", "VERIFY_RECORD_ABSENT"])
        self.assertEqual(out[5]["system_id"], "account_service")       # system_id breaks ties
        self.assertEqual(out[2]["state"], "DISABLED")
        self.assertEqual(set(out[0]), {"step_no", "phase", "system_id", "resource_id", "operation", "state",
                                       "evidence_id"})


if __name__ == "__main__":
    unittest.main()
