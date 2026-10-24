"""Deletion policy: data category -> required treatment, plus step vocabulary/ordering."""
from __future__ import annotations

import csv
import os

# Fallback mapping (used if deletion_rules.csv lacks a category); the CSV is authoritative.
DEFAULT_ACTIONS = {
    "USER_PROFILE": "DELETE", "SECONDARY_IDENTIFIER": "DELETE", "SEARCH_INDEX_ENTRY": "DELETE",
    "CACHE_ENTRY": "DELETE", "SUPPORT_MESSAGE": "REDACT", "SUPPORT_ATTACHMENT": "DELETE",
    "ANALYTICS_PROFILE": "ANONYMIZE", "PRODUCT_TELEMETRY": "ANONYMIZE", "USER_EXPORT": "DELETE",
    "MARKETING_PROFILE": "DELETE", "EMAIL_DELIVERY_LOG": "REDACT", "SECURITY_AUDIT_LOG": "RETAIN",
    "LEGAL_HOLD_RECORD": "RETAIN", "BACKUP_ARCHIVE": "RETAIN", "ACCOUNT_TOMBSTONE": "REDACT",
}


def load_rules(dataset_dir: str) -> dict[str, dict]:
    rules = {}
    path = os.path.join(dataset_dir, "deletion_rules.csv")
    for r in csv.DictReader(open(path, encoding="utf-8")):
        rules[r["data_category"]] = r
    return rules


def required_action(rules: dict, category: str) -> str:
    r = rules.get(category)
    if r:
        return r["required_action"].strip().upper()
    return DEFAULT_ACTIONS.get(category, "DELETE")


# Operation order inside each phase (as listed in the problem statement table)
FINDING_ORDER = ["REPORT_REMAINING_RECORD", "REPORT_EXPOSED_PII", "REPORT_ACTIVE_DATA_FLOW", "REPORT_RESTORE_RISK"]
REMEDIATION_ORDER = ["DISABLE_DATA_FLOW", "BLOCK_RESTORE", "DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD"]
VERIFICATION_ORDER = ["VERIFY_RECORD_ABSENT", "VERIFY_RECORD_PRESENT", "VERIFY_RECORD_REDACTED",
                      "VERIFY_RECORD_ANONYMIZED", "VERIFY_DATA_FLOW_DISABLED", "VERIFY_RESTORE_BLOCKED"]
PHASE_ORDER = {"FINDING": 0, "REMEDIATION": 1, "VERIFICATION": 2}
OP_RANK = {op: i for i, op in enumerate(FINDING_ORDER + REMEDIATION_ORDER + VERIFICATION_ORDER)}

OP_STATE = {
    "REPORT_REMAINING_RECORD": "PRESENT", "REPORT_EXPOSED_PII": "PRESENT", "REPORT_ACTIVE_DATA_FLOW": "ACTIVE",
    "REPORT_RESTORE_RISK": "ACTIVE", "DISABLE_DATA_FLOW": "DISABLED", "BLOCK_RESTORE": "BLOCKED",
    "DELETE_RECORD": "ABSENT", "REDACT_RECORD": "REDACTED", "ANONYMIZE_RECORD": "ANONYMIZED",
    "VERIFY_RECORD_ABSENT": "ABSENT", "VERIFY_RECORD_PRESENT": "PRESENT", "VERIFY_RECORD_REDACTED": "REDACTED",
    "VERIFY_RECORD_ANONYMIZED": "ANONYMIZED", "VERIFY_DATA_FLOW_DISABLED": "DISABLED",
    "VERIFY_RESTORE_BLOCKED": "BLOCKED",
}
OP_PHASE = {op: ("FINDING" if op.startswith("REPORT") else "VERIFICATION" if op.startswith("VERIFY") else "REMEDIATION")
            for op in OP_STATE}


def _id_key(resource_id: str):
    return resource_id


def order_steps(steps: list[dict]) -> list[dict]:
    steps = sorted(steps, key=lambda s: (PHASE_ORDER[s["phase"]], OP_RANK[s["operation"]], s["system_id"],
                                         _id_key(s["resource_id"])))
    out = []
    for i, s in enumerate(steps, 1):
        out.append({"step_no": i, "phase": s["phase"], "system_id": s["system_id"], "resource_id": s["resource_id"],
                    "operation": s["operation"], "state": OP_STATE[s["operation"]], "evidence_id": s.get("evidence_id", "")})
    return out
