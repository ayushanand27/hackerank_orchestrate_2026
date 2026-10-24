"""Hand-written evaluation cases that stress specific failure modes.

Each scenario builds a tiny, fully synthetic dataset (identities, activity, evidence) in a
temporary directory, runs the real agent on it and compares with the expected answer that is
written here by hand.  The scenarios are designed from the rules in problem_statement.md, not
from the hidden answers.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile

RULES = """rule_id,data_category,condition,required_action
rule_01,USER_PROFILE,Always,DELETE
rule_02,SECONDARY_IDENTIFIER,Always,DELETE
rule_03,SEARCH_INDEX_ENTRY,Always,DELETE
rule_04,CACHE_ENTRY,Always,DELETE
rule_05,SUPPORT_MESSAGE,"Conversation contains shared customer, agent, or order context that must be retained",REDACT
rule_06,SUPPORT_ATTACHMENT,Attachment belongs only to the deletion subject,DELETE
rule_07,ANALYTICS_PROFILE,Record is still attributable to a user,ANONYMIZE
rule_08,PRODUCT_TELEMETRY,Record is still attributable to a user,ANONYMIZE
rule_09,USER_EXPORT,Always,DELETE
rule_10,MARKETING_PROFILE,Always,DELETE
rule_11,EMAIL_DELIVERY_LOG,Log contains a user email address,REDACT
rule_12,SECURITY_AUDIT_LOG,Record is within the supplied retention period,RETAIN
rule_13,LEGAL_HOLD_RECORD,A supplied legal hold is active,RETAIN
rule_14,BACKUP_ARCHIVE,Restore access can be blocked,RETAIN
rule_15,ACCOUNT_TOMBSTONE,Account is deleted and only a stable internal user_id is needed to preserve references to retained records,REDACT
"""


def h(e):
    return hashlib.sha256(e.lower().encode()).hexdigest()


def step(n, phase, system, rid, op, state, ev=""):
    return {"step_no": n, "phase": phase, "system_id": system, "resource_id": rid, "operation": op,
            "state": state, "evidence_id": ev}


def build(tmp: str):
    """Write the synthetic dataset; return expected answers {case_id: (status, steps)}."""
    os.makedirs(os.path.join(tmp, "evidence", "logs"), exist_ok=True)
    os.makedirs(os.path.join(tmp, "evidence", "databases"), exist_ok=True)
    os.makedirs(os.path.join(tmp, "evidence", "records"), exist_ok=True)
    users = [
        ("user_901", "Test Alpha", "alpha.new@example.org", "+44 7700 900901", "2026-05-01T00:00:00Z"),
        ("user_902", "Test Beta", "beta@example.org", "+44 7700 900902", "2025-01-01T00:00:00Z"),
        ("user_903", "Test Gamma", "gamma@example.org", "+44 7700 900903", "2025-01-01T00:00:00Z"),
        ("user_904", "Test Delta", "delta@example.org", "+44 7700 900904", "2025-01-01T00:00:00Z"),
        ("user_905", "Test Echo", "echo@example.org", "+44 7700 900905", "2025-01-01T00:00:00Z"),
    ]
    with open(os.path.join(tmp, "user_identities.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["user_id", "name", "email", "phone", "email_hash", "updated_at"])
        for u, n, e, p, t in users:
            w.writerow([u, n, e, p, h(e), t])
    with open(os.path.join(tmp, "user_audit_log.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["audit_id", "user_id", "event_time", "field_name", "old_value", "new_value"])
        w.writerow(["audit_9001", "user_901", "2026-05-01T00:00:00Z", "email", "alpha.old@example.org", "alpha.new@example.org"])
    open(os.path.join(tmp, "deletion_rules.csv"), "w").write(RULES)

    reqs = [
        # C1: request made with a HISTORICAL e-mail; search entry still holds the old address;
        #     a regional replica still holds the profile that the primary already deleted.
        ("case_901", "alpha.old@example.org", "2026-09-01T10:00:00Z", "COMPLETE", "2026-09-02T10:00:00Z",
         "2026-09-10T10:00:00Z", "Please delete my account and everything tied to my old address."),
        # C2: non-deletion (access) request that mentions deletion only to refuse it.
        ("case_902", "beta@example.org", "2026-09-01T10:00:00Z", "COMPLETE", "2026-09-02T10:00:00Z",
         "2026-09-10T10:00:00Z", "Could you send me a copy of my data? Please don't delete anything."),
        # C3: everything compliant at audit_at; a sync recreates the search entry AFTER audit_at
        #     (must be ignored) and a decoy look-alike e-mail must not be attributed.
        ("case_903", "gamma@example.org", "2026-09-01T10:00:00Z", "COMPLETE", "2026-09-02T10:00:00Z",
         "2026-09-10T10:00:00Z", "Je demande la suppression définitive de mon compte."),
        # C4: active connector and restorable backup -> disable/block BEFORE deleting downstream copy.
        ("case_904", "delta@example.org", "2026-09-01T10:00:00Z", "COMPLETE", "2026-09-02T10:00:00Z",
         "2026-09-10T10:00:00Z", "Please erase my account and stop anything that copies my data."),
        # C5: tombstone still exposing the e-mail; support message must be redacted, not deleted.
        ("case_905", "echo@example.org", "2026-09-01T10:00:00Z", "PARTIALLY_COMPLETE", "2026-09-02T10:00:00Z",
         "2026-09-10T10:00:00Z", "このメールアドレスに関連するアカウントの完全な削除を依頼します。"),
    ]
    with open(os.path.join(tmp, "deletion_requests.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["case_id", "email", "requested_at", "reported_status", "reported_at", "audit_at", "request_text"])
        for r in reqs:
            w.writerow(r)

    acts = [
        ("activity_1", "2026-09-02T09:05:00Z", "DELETE_RECORD", "deletion_worker", "account_service", "account_9901", "SUCCESS",
         "Deletion worker submitted primary profile account_9901 to account_service."),
        ("activity_2", "2026-09-02T09:05:00Z", "DELETE_RECORD", "deletion_worker", "account_service", "account_9903", "SUCCESS",
         "Deletion worker submitted primary profile account_9903 to account_service."),
        ("activity_3", "2026-09-12T08:00:00Z", "SYNC_RECORD", "integration_service", "search_service", "data_flow_9903", "SUCCESS",
         "Active connector repopulated downstream resource search_9933."),
        ("activity_4", "2026-09-02T09:05:00Z", "DELETE_RECORD", "deletion_worker", "account_service", "account_9904", "SUCCESS",
         "Deletion worker submitted primary profile account_9904 to account_service."),
        ("activity_5", "2026-09-05T08:00:00Z", "SYNC_RECORD", "integration_service", "search_service", "data_flow_9904", "SUCCESS",
         "Active connector repopulated downstream resource search_9944."),
    ]
    with open(os.path.join(tmp, "system_activity.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["activity_id", "event_time", "event_type", "source_system", "target_system", "resource_id",
                    "reported_result", "details"])
        for a in acts:
            w.writerow(a)

    log = """2026-09-09T08:00:00Z level=INFO service=readback_collector event=export_started target_count=9 mode=point_in_time
2026-09-09T08:00:01Z level=DEBUG service=search_service trace_id=rb-1 event=index_query_started resource_id=search_9911 lookup_key=alpha.old@example.org
2026-09-09T08:00:01Z level=INFO service=search_service trace_id=rb-1 event=index_query_completed resource_id=search_9911 result=hit total_hits=1 index=customer_directory_v3 email=alpha.old@example.org customer_reference=null
2026-09-09T08:00:02Z level=DEBUG service=account_service trace_id=rb-2 event=profile_lookup_started resource_id=account_9903 lookup_key=gamma@example.org
2026-09-09T08:00:02Z level=INFO service=account_service trace_id=rb-2 event=profile_lookup_completed resource_id=account_9903 rows=0 http_status=404
2026-09-09T08:00:03Z level=DEBUG service=search_service trace_id=rb-3 event=index_query_started resource_id=search_9934 lookup_key=gamma.jr@example.org
2026-09-09T08:00:03Z level=INFO service=search_service trace_id=rb-3 event=index_query_completed resource_id=search_9934 result=hit total_hits=1 index=customer_directory_v3 email=gamma.jr@example.org customer_reference=null
2026-09-09T08:00:04Z level=DEBUG service=security_service trace_id=rb-4 event=audit_record_read_started resource_id=audit_record_9903 lookup_key=user_903
2026-09-09T08:00:04Z level=INFO service=security_service trace_id=rb-4 event=audit_record_read_completed resource_id=audit_record_9903 rows=1 subject_reference=user_903 event_code=ACCOUNT_DELETION_RECORDED retention_until=2031-12-31T23:59:59Z
2026-09-09T08:00:05Z level=DEBUG service=integration_service trace_id=rb-5 event=connector_config_read_started resource_id=data_flow_9904 lookup_key=delta@example.org
2026-09-09T08:00:05Z level=INFO service=integration_service trace_id=rb-5 event=connector_config_read_completed resource_id=data_flow_9904 enabled=true last_delivery_status=delivered connector_name=search_profile_indexer customer_email=delta@example.org customer_reference=null
2026-09-09T08:00:06Z level=DEBUG service=backup_service trace_id=rb-6 event=restore_policy_read_started resource_id=backup_9904 lookup_key=user_904
2026-09-09T08:00:06Z level=INFO service=backup_service trace_id=rb-6 event=restore_policy_read_completed resource_id=backup_9904 archive_present=true restore_allowed=true archive_key=archive/customer-data/backup_9904.tar.zst customer_email=null customer_reference=user_904
2026-09-09T08:00:07Z level=DEBUG service=search_service trace_id=rb-7 event=index_query_started resource_id=search_9944 lookup_key=delta@example.org
2026-09-09T08:00:07Z level=INFO service=search_service trace_id=rb-7 event=index_query_completed resource_id=search_9944 result=hit total_hits=1 index=customer_directory_v3 email=delta@example.org customer_reference=null
2026-09-09T08:00:08Z level=DEBUG service=support_platform trace_id=rb-8 event=support_ticket_read_started resource_id=support_9905 lookup_key=echo@example.org
2026-09-09T08:00:08Z level=INFO service=support_platform trace_id=rb-8 event=support_ticket_read_completed resource_id=support_9905 customer_reference=null requester_name=null requester_email=echo@example.org requester_phone=null
2026-09-12T09:00:00Z level=DEBUG service=search_service trace_id=rb-9 event=index_query_started resource_id=search_9933 lookup_key=gamma@example.org
2026-09-12T09:00:00Z level=INFO service=search_service trace_id=rb-9 event=index_query_completed resource_id=search_9933 result=hit total_hits=1 index=customer_directory_v3 email=gamma@example.org customer_reference=null
2026-09-12T09:00:01Z level=INFO service=readback_collector event=export_completed queries=9 dropped=0
"""
    open(os.path.join(tmp, "evidence", "logs", "readback.log"), "w").write(log)
    sql = """-- Captured at: 2026-09-09T09:00:00Z
-- Database topology: regional_replicas
BEGIN TRANSACTION;
CREATE TABLE customer_data_primary (resource_id TEXT PRIMARY KEY, origin_system TEXT NOT NULL, collection_name TEXT NOT NULL, record_json TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE customer_data_replica_eu_west (resource_id TEXT PRIMARY KEY, origin_system TEXT NOT NULL, collection_name TEXT NOT NULL, record_json TEXT NOT NULL, updated_at TEXT NOT NULL);
INSERT INTO "customer_data_replica_eu_west" VALUES('account_9901','account_service','customer_profiles','{"resource_id":"account_9901","customer_reference":"user_901","full_name":"Test Alpha","email":"alpha.old@example.org","phone":null,"account_status":"ACTIVE"}','2026-09-09T09:00:00Z');
INSERT INTO "customer_data_primary" VALUES('tombstone_9905','identity_service','deleted_customer_references','{"resource_id":"tombstone_9905","subject_reference":"user_905","name":null,"email":"echo@example.org","phone":null,"email_hash":null,"account_status":"DELETED"}','2026-09-09T09:00:00Z');
INSERT INTO "customer_data_primary" VALUES('audit_record_9905','security_service','security_audit_events','{"resource_id":"audit_record_9905","subject_reference":"user_905","event_name":"account_deletion_reviewed"}','2026-09-09T09:00:00Z');
COMMIT;
"""
    open(os.path.join(tmp, "evidence", "databases", "replicas.sql"), "w").write(sql)
    with open(os.path.join(tmp, "evidence_files.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["file_id", "file_path", "origin", "captured_at"])
        w.writerow(["evidence_901", "evidence/logs/readback.log", "service_readback_logs", "2026-09-09T08:00:09Z"])
        w.writerow(["evidence_902", "evidence/databases/replicas.sql", "customer_data_regional_replicas_capture", "2026-09-09T09:00:00Z"])

    S = "account_service"
    exp = {
        "case_901": ("INCOMPLETE", [
            step(1, "FINDING", S, "account_9901", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_902"),
            step(2, "FINDING", "search_service", "search_9911", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_901"),
            step(3, "REMEDIATION", S, "account_9901", "DELETE_RECORD", "ABSENT"),
            step(4, "REMEDIATION", "search_service", "search_9911", "DELETE_RECORD", "ABSENT"),
            step(5, "VERIFICATION", S, "account_9901", "VERIFY_RECORD_ABSENT", "ABSENT"),
            step(6, "VERIFICATION", "search_service", "search_9911", "VERIFY_RECORD_ABSENT", "ABSENT"),
        ]),
        "case_902": ("NOT_APPLICABLE", []),
        "case_903": ("COMPLETE", [
            step(1, "VERIFICATION", S, "account_9903", "VERIFY_RECORD_ABSENT", "ABSENT", "evidence_901"),
            step(2, "VERIFICATION", "security_service", "audit_record_9903", "VERIFY_RECORD_PRESENT", "PRESENT", "evidence_901"),
        ]),
        "case_904": ("INCOMPLETE", [
            step(1, "FINDING", "search_service", "search_9944", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_901"),
            step(2, "FINDING", "integration_service", "data_flow_9904", "REPORT_ACTIVE_DATA_FLOW", "ACTIVE", "evidence_901"),
            step(3, "FINDING", "backup_service", "backup_9904", "REPORT_RESTORE_RISK", "ACTIVE", "evidence_901"),
            step(4, "REMEDIATION", "integration_service", "data_flow_9904", "DISABLE_DATA_FLOW", "DISABLED"),
            step(5, "REMEDIATION", "backup_service", "backup_9904", "BLOCK_RESTORE", "BLOCKED"),
            step(6, "REMEDIATION", "search_service", "search_9944", "DELETE_RECORD", "ABSENT"),
            step(7, "VERIFICATION", S, "account_9904", "VERIFY_RECORD_ABSENT", "ABSENT"),
            step(8, "VERIFICATION", "search_service", "search_9944", "VERIFY_RECORD_ABSENT", "ABSENT"),
            step(9, "VERIFICATION", "integration_service", "data_flow_9904", "VERIFY_DATA_FLOW_DISABLED", "DISABLED"),
            step(10, "VERIFICATION", "backup_service", "backup_9904", "VERIFY_RESTORE_BLOCKED", "BLOCKED"),
        ]),
        "case_905": ("INCOMPLETE", [
            step(1, "FINDING", "identity_service", "tombstone_9905", "REPORT_EXPOSED_PII", "PRESENT", "evidence_902"),
            step(2, "FINDING", "support_platform", "support_9905", "REPORT_EXPOSED_PII", "PRESENT", "evidence_901"),
            step(3, "REMEDIATION", "identity_service", "tombstone_9905", "REDACT_RECORD", "REDACTED"),
            step(4, "REMEDIATION", "support_platform", "support_9905", "REDACT_RECORD", "REDACTED"),
            step(5, "VERIFICATION", "security_service", "audit_record_9905", "VERIFY_RECORD_PRESENT", "PRESENT"),
            step(6, "VERIFICATION", "identity_service", "tombstone_9905", "VERIFY_RECORD_REDACTED", "REDACTED"),
            step(7, "VERIFICATION", "support_platform", "support_9905", "VERIFY_RECORD_REDACTED", "REDACTED"),
        ]),
    }
    return exp


DESCRIPTIONS = {
    "case_901": "Request sent from a historical e-mail; search entry keyed by the old address and a regional "
                "replica still holding a profile the primary already deleted (replication drift).",
    "case_902": "Access/portability request that explicitly refuses deletion -> NOT_APPLICABLE with [].",
    "case_903": "Compliant at audit_at; a connector re-creates the search entry two days AFTER audit_at (must be "
                "ignored) and a look-alike decoy e-mail (gamma.jr@...) must not be attributed.",
    "case_904": "Active connector re-populating search + restorable backup: disable/block must precede the "
                "downstream delete; lineage from the connector links the copy.",
    "case_905": "Tombstone still exposing the e-mail (REDACT, keep stable reference) and a support message that "
                "must be redacted rather than deleted; Japanese request text.",
}


def run(engine_factory):
    """Build the synthetic dataset, run the engine, return (expected, predicted)."""
    with tempfile.TemporaryDirectory() as tmp:
        expected = build(tmp)
        auditor = engine_factory(tmp)
        reqs = list(csv.DictReader(open(os.path.join(tmp, "deletion_requests.csv"))))
        auditor.prepare(reqs)
        predicted = {}
        for r in reqs:
            st, steps, _ = auditor.audit(r)
            predicted[r["case_id"]] = (st, steps)
    return expected, predicted
