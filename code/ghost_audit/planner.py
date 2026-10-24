"""Turn per-resource states into the ordered FINDING -> REMEDIATION -> VERIFICATION plan.

For each linked resource the policy decides the required end state:

=================  ===================  =========================  ==========================
policy / kind      compliant state      violation -> finding        remediation / verification
=================  ===================  =========================  ==========================
DATA_FLOW          DISABLED             ACTIVE -> active data flow  disable / verify disabled
BACKUP_ARCHIVE     BLOCKED              ACTIVE -> restore risk      block / verify blocked
DELETE             ABSENT               PRESENT -> remaining record delete / verify absent
REDACT             REDACTED             IDENTIFIABLE -> exposed PII redact / verify redacted
ANONYMIZE          ANONYMIZED           IDENTIFIABLE -> exposed PII anonymise / verify anonymised
RETAIN             PRESENT              (never remediated)          verify present
=================  ===================  =========================  ==========================

A case is COMPLETE only if there is no finding; then every compliant record is verified with
the evidence that proves it.  An INCOMPLETE case lists findings (with evidence), the full
remediation plan and the post-remediation checks (both with an empty ``evidence_id``); there the
verification phase covers the primary profile, retained records and every remediated item.
"""
from __future__ import annotations

from .policy import order_steps, required_action

# category -> (finding, remediation, verification) for each policy action
TREATMENT = {
    "DELETE": ("REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT"),
    "REDACT": ("REPORT_EXPOSED_PII", "REDACT_RECORD", "VERIFY_RECORD_REDACTED"),
    "ANONYMIZE": ("REPORT_EXPOSED_PII", "ANONYMIZE_RECORD", "VERIFY_RECORD_ANONYMIZED"),
}


def triple(finding: str, remediation: str, verification: str, rid: str, system: str, ev: str) -> list[tuple]:
    return [("FINDING", finding, rid, system, ev),
            ("REMEDIATION", remediation, rid, system, ""),
            ("VERIFICATION", verification, rid, system, "")]


def plan(views: dict, primary_account: str | None, rules: dict) -> tuple[str, list[dict]]:
    """``views``: rid -> {system, category, state, evidence}.  Returns (status, ordered steps)."""
    steps: list[tuple] = []     # (phase, operation, resource_id, system_id, evidence_id)
    findings = 0
    verify_ok: list[tuple] = []  # verifications of already-compliant records (COMPLETE only)
    for rid, v in views.items():
        st, cat, ev, sysid = v["state"], v["category"], v["evidence"], v["system"]
        if st is None or cat is None:
            continue
        if cat == "DATA_FLOW":
            if st == "ACTIVE":
                steps += triple("REPORT_ACTIVE_DATA_FLOW", "DISABLE_DATA_FLOW", "VERIFY_DATA_FLOW_DISABLED", rid, sysid, ev)
                findings += 1
            elif st == "DISABLED":
                verify_ok.append(("VERIFICATION", "VERIFY_DATA_FLOW_DISABLED", rid, sysid, ev))
            continue
        if cat == "BACKUP_ARCHIVE":
            if st == "ACTIVE":
                steps += triple("REPORT_RESTORE_RISK", "BLOCK_RESTORE", "VERIFY_RESTORE_BLOCKED", rid, sysid, ev)
                findings += 1
            elif st == "BLOCKED":
                verify_ok.append(("VERIFICATION", "VERIFY_RESTORE_BLOCKED", rid, sysid, ev))
            continue
        action = required_action(rules, cat)
        if action == "DELETE":
            if st == "ABSENT":
                # the primary profile's absence is verified in every deletion audit
                target = steps if cat == "USER_PROFILE" else verify_ok
                target.append(("VERIFICATION", "VERIFY_RECORD_ABSENT", rid, sysid, ev))
            else:
                steps += triple(*TREATMENT["DELETE"], rid, sysid, ev)
                findings += 1
        elif action in ("REDACT", "ANONYMIZE"):
            finding, remediation, verification = TREATMENT[action]
            if st == "IDENTIFIABLE":
                steps += triple(finding, remediation, verification, rid, sysid, ev)
                findings += 1
            elif st in ("REDACTED", "ANONYMIZED"):
                verify_ok.append(("VERIFICATION", verification, rid, sysid, ev))
        elif action == "RETAIN":
            if st != "ABSENT":
                steps.append(("VERIFICATION", "VERIFY_RECORD_PRESENT", rid, sysid, ev))
    # every deletion audit verifies that the primary profile is gone
    if primary_account and not any(s[2] == primary_account for s in steps):
        v = views.get(primary_account, {})
        if v.get("state") in (None, "ABSENT"):
            steps.append(("VERIFICATION", "VERIFY_RECORD_ABSENT", primary_account, "account_service",
                          v.get("evidence", "")))
    status = "INCOMPLETE" if findings else "COMPLETE"
    if status == "COMPLETE":
        steps += verify_ok
    out = []
    for phase, op, rid, sysid, ev in steps:
        if status == "INCOMPLETE" and phase == "VERIFICATION":
            ev = ""   # post-remediation checks describe future work
        out.append({"phase": phase, "operation": op, "system_id": sysid, "resource_id": rid, "evidence_id": ev})
    return status, order_steps(out)
