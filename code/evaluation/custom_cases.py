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


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


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
        ("user_906", "Test Foxtrot", "foxtrot@example.org", "+44 7700 900906", "2025-01-01T00:00:00Z"),
        ("user_907", "Test Golf", "golf@example.org", "+44 7700 900907", "2025-01-01T00:00:00Z"),
        ("user_908", "Test Hotel", "hotel@example.org", "+44 7700 900908", "2025-01-01T00:00:00Z"),
        ("user_909", "Test India", "india@example.org", "+44 7700 900909", "2025-01-01T00:00:00Z"),
        ("user_910", "Test Juliet", "juliet@example.org", "+44 7700 900910", "2025-01-01T00:00:00Z"),
        ("user_911", "Test Kilo", "kilo@example.org", "+44 7700 900911", "2025-01-01T00:00:00Z"),
    ]
    with open(os.path.join(tmp, "user_identities.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["user_id", "name", "email", "phone", "email_hash", "updated_at"])
        for u, n, e, p, t in users:
            w.writerow([u, n, e, p, h(e), t])
    with open(os.path.join(tmp, "user_audit_log.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["audit_id", "user_id", "event_time", "field_name", "old_value", "new_value"])
        w.writerow(["audit_9001", "user_901", "2026-05-01T00:00:00Z", "email", "alpha.old@example.org", "alpha.new@example.org"])
    _write(os.path.join(tmp, "deletion_rules.csv"), RULES)

    reqs = [
        # C1: request made with a HISTORICAL e-mail; the search entry still holds that old address
        #     while a regional replica still holds the (deleted) profile under the CURRENT address:
        #     only the audit-log history connects the two.
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
        # --- second batch: one scenario per attribution technique (audited 2026-09-30) ---
        # C6: a search copy with NO identifiers, re-created by the subject's active connector
        #     (lineage is the only link); primary profile found by workflow timing only.
        ("case_906", "foxtrot@example.org", "2026-09-20T10:00:00Z", "COMPLETE", "2026-09-21T10:00:00Z",
         "2026-09-30T10:00:00Z", "Please delete my account and all my personal data."),
        # C7/C8: two requests reported together; each has an anonymised analytics profile without
        #     identifiers -> each must get the one whose policy action matches its timing phase.
        ("case_907", "golf@example.org", "2026-09-20T10:00:00Z", "COMPLETE", "2026-09-22T10:00:00Z",
         "2026-09-30T10:00:00Z", "Bitte löschen Sie mein Konto und meine Daten."),
        ("case_908", "hotel@example.org", "2026-09-20T10:00:00Z", "COMPLETE", "2026-09-22T10:00:00Z",
         "2026-09-30T10:00:00Z", "Quiero la eliminación de mi cuenta y de mis datos personales."),
        # C9: search entry seen after the request, then a complete snapshot of the search service
        #     (from the case's own batch) no longer lists it -> absent by omission -> COMPLETE.
        ("case_909", "india@example.org", "2026-09-20T10:00:00Z", "COMPLETE", "2026-09-23T10:00:00Z",
         "2026-09-30T10:00:00Z", "Please erase my profile."),
        # C10: support item that the deletion worker tried to DELETE (an attachment, not a message)
        #     -> remaining record to delete, not PII to redact.
        ("case_910", "juliet@example.org", "2026-09-20T10:00:00Z", "PARTIALLY_COMPLETE", "2026-09-24T10:00:00Z",
         "2026-09-30T10:00:00Z", "Delete my account please, including the files I uploaded to support."),
        # C11: marketing profile visible only in a lookup ledger without identifiers; the activity
        #     catalogue links it to the subject's e-mail.  Reported COMPLETE, actually incomplete.
        ("case_911", "kilo@example.org", "2026-09-20T10:00:00Z", "COMPLETE", "2026-09-25T10:00:00Z",
         "2026-09-30T10:00:00Z", "I want my account deleted and to be forgotten."),
    ]
    with open(os.path.join(tmp, "deletion_requests.csv"), "w", newline="", encoding="utf-8") as f:
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
        # second batch (each primary profile submitted 50 min before its status report)
        ("activity_10", "2026-09-21T09:10:00Z", "DELETE_RECORD", "deletion_worker", "account_service", "account_9906", "SUCCESS",
         "Deletion worker submitted primary profile account_9906 to account_service."),
        ("activity_11", "2026-09-21T12:00:00Z", "LINK_RESOURCE_IDENTITY", "catalog_service", "marketing_platform", "marketing_9911",
         "SUCCESS", "Catalogue linked marketing_9911 (newsletter_audience_eu) to kilo@example.org."),
        ("activity_12", "2026-09-22T09:10:00Z", "DELETE_RECORD", "deletion_worker", "account_service", "account_9907", "SUCCESS",
         "Deletion worker submitted primary profile account_9907 to account_service."),
        ("activity_13", "2026-09-22T09:10:20Z", "DELETE_RECORD", "deletion_worker", "account_service", "account_9908", "SUCCESS",
         "Deletion worker submitted primary profile account_9908 to account_service."),
        ("activity_14", "2026-09-22T09:40:41Z", "ANONYMIZE_RECORD", "privacy_orchestrator", "analytics_platform", "analytics_9970",
         "SUCCESS", "Policy action completed for analytics_9970."),
        ("activity_15", "2026-09-22T09:41:00Z", "ANONYMIZE_RECORD", "privacy_orchestrator", "analytics_platform", "analytics_9980",
         "SUCCESS", "Policy action completed for analytics_9980."),
        ("activity_16", "2026-09-23T09:10:00Z", "DELETE_RECORD", "deletion_worker", "account_service", "account_9909", "SUCCESS",
         "Deletion worker submitted primary profile account_9909 to account_service."),
        ("activity_17", "2026-09-24T09:10:00Z", "DELETE_RECORD", "deletion_worker", "account_service", "account_9910", "PARTIAL",
         "Deletion worker submitted primary profile account_9910 to account_service."),
        ("activity_18", "2026-09-24T09:20:00Z", "DELETE_RECORD", "deletion_worker", "support_platform", "support_9910", "FAILED",
         "Deletion worker submitted support_9910 to support_platform."),
        ("activity_19", "2026-09-25T08:00:00Z", "SYNC_RECORD", "integration_service", "search_service", "data_flow_9906", "SUCCESS",
         "Active connector repopulated downstream resource search_9966."),
        ("activity_20", "2026-09-25T09:10:00Z", "DELETE_RECORD", "deletion_worker", "account_service", "account_9911", "SUCCESS",
         "Deletion worker submitted primary profile account_9911 to account_service."),
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
    _write(os.path.join(tmp, "evidence", "logs", "readback.log"), log)
    sql = """-- Captured at: 2026-09-09T09:00:00Z
-- Database topology: regional_replicas
BEGIN TRANSACTION;
CREATE TABLE customer_data_primary (resource_id TEXT PRIMARY KEY, origin_system TEXT NOT NULL, collection_name TEXT NOT NULL, record_json TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE customer_data_replica_eu_west (resource_id TEXT PRIMARY KEY, origin_system TEXT NOT NULL, collection_name TEXT NOT NULL, record_json TEXT NOT NULL, updated_at TEXT NOT NULL);
INSERT INTO "customer_data_replica_eu_west" VALUES('account_9901','account_service','customer_profiles','{"resource_id":"account_9901","customer_reference":null,"full_name":"Test Alpha","email":"alpha.new@example.org","phone":null,"account_status":"ACTIVE"}','2026-09-09T09:00:00Z');
INSERT INTO "customer_data_primary" VALUES('tombstone_9905','identity_service','deleted_customer_references','{"resource_id":"tombstone_9905","subject_reference":"user_905","name":null,"email":"echo@example.org","phone":null,"email_hash":null,"account_status":"DELETED"}','2026-09-09T09:00:00Z');
INSERT INTO "customer_data_primary" VALUES('audit_record_9905','security_service','security_audit_events','{"resource_id":"audit_record_9905","subject_reference":"user_905","event_name":"account_deletion_reviewed"}','2026-09-09T09:00:00Z');
COMMIT;
"""
    _write(os.path.join(tmp, "evidence", "databases", "replicas.sql"), sql)

    # --- second batch -------------------------------------------------------------------
    log2 = """2026-09-21T11:00:00Z level=DEBUG service=search_service trace_id=rb-27 event=index_query_started resource_id=search_9990 lookup_key=india@example.org
2026-09-21T11:00:00Z level=INFO service=search_service trace_id=rb-27 event=index_query_completed resource_id=search_9990 result=hit total_hits=1 index=customer_directory_v3 email=india@example.org customer_reference=null
2026-09-28T08:00:00Z level=INFO service=readback_collector event=export_started target_count=12 mode=point_in_time
2026-09-28T08:00:01Z level=DEBUG service=integration_service trace_id=rb-21 event=connector_config_read_started resource_id=data_flow_9906 lookup_key=foxtrot@example.org
2026-09-28T08:00:01Z level=INFO service=integration_service trace_id=rb-21 event=connector_config_read_completed resource_id=data_flow_9906 enabled=true connector_name=profile_search_sync customer_email=foxtrot@example.org customer_reference=null
2026-09-28T08:00:02Z level=DEBUG service=search_service trace_id=rb-22 event=index_query_started resource_id=search_9966 lookup_key=shard-7f3a
2026-09-28T08:00:02Z level=INFO service=search_service trace_id=rb-22 event=index_query_completed resource_id=search_9966 result=hit total_hits=1 index=customer_directory_v3 email=null customer_reference=null
2026-09-28T08:00:03Z level=DEBUG service=account_service trace_id=rb-23 event=profile_lookup_started resource_id=account_9907 lookup_key=golf@example.org
2026-09-28T08:00:03Z level=INFO service=account_service trace_id=rb-23 event=profile_lookup_completed resource_id=account_9907 rows=0 http_status=404
2026-09-28T08:00:04Z level=DEBUG service=account_service trace_id=rb-24 event=profile_lookup_started resource_id=account_9908 lookup_key=hotel@example.org
2026-09-28T08:00:04Z level=INFO service=account_service trace_id=rb-24 event=profile_lookup_completed resource_id=account_9908 rows=0 http_status=404
2026-09-28T08:00:05Z level=DEBUG service=analytics_platform trace_id=rb-25 event=analytics_profile_read_started resource_id=analytics_9970 lookup_key=segment-0412
2026-09-28T08:00:05Z level=INFO service=analytics_platform trace_id=rb-25 event=analytics_profile_read_completed resource_id=analytics_9970 email=null phone=null email_hash=null customer_reference=null
2026-09-28T08:00:06Z level=DEBUG service=analytics_platform trace_id=rb-26 event=analytics_profile_read_started resource_id=analytics_9980 lookup_key=segment-0413
2026-09-28T08:00:06Z level=INFO service=analytics_platform trace_id=rb-26 event=analytics_profile_read_completed resource_id=analytics_9980 email=null phone=null email_hash=null customer_reference=null
2026-09-28T08:00:07Z level=DEBUG service=account_service trace_id=rb-28 event=profile_lookup_started resource_id=account_9909 lookup_key=india@example.org
2026-09-28T08:00:07Z level=INFO service=account_service trace_id=rb-28 event=profile_lookup_completed resource_id=account_9909 rows=0 http_status=404
2026-09-28T08:00:08Z level=DEBUG service=account_service trace_id=rb-29 event=profile_lookup_started resource_id=account_9910 lookup_key=juliet@example.org
2026-09-28T08:00:08Z level=INFO service=account_service trace_id=rb-29 event=profile_lookup_completed resource_id=account_9910 rows=0 http_status=404
2026-09-28T08:00:09Z level=DEBUG service=support_platform trace_id=rb-30 event=support_ticket_read_started resource_id=support_9910 lookup_key=juliet@example.org
2026-09-28T08:00:09Z level=INFO service=support_platform trace_id=rb-30 event=support_ticket_read_completed resource_id=support_9910 customer_reference=null requester_name=null requester_email=juliet@example.org requester_phone=null
2026-09-28T08:00:10Z level=DEBUG service=account_service trace_id=rb-31 event=profile_lookup_started resource_id=account_9911 lookup_key=kilo@example.org
2026-09-28T08:00:10Z level=INFO service=account_service trace_id=rb-31 event=profile_lookup_completed resource_id=account_9911 rows=0 http_status=404
2026-09-28T08:00:11Z level=INFO service=readback_collector event=export_completed queries=12 dropped=0
"""
    _write(os.path.join(tmp, "evidence", "logs", "readback_batch2.log"), log2)
    sql2 = """-- Captured at: 2026-09-27T09:00:00Z
-- Database topology: primary_snapshot
BEGIN TRANSACTION;
CREATE TABLE snapshot_scope (origin_system TEXT NOT NULL, captured_at TEXT NOT NULL);
INSERT INTO "snapshot_scope" VALUES('search_service','2026-09-27T09:00:00Z');
INSERT INTO "snapshot_scope" VALUES('identity_service','2026-09-27T09:00:00Z');
CREATE TABLE customer_data_primary (resource_id TEXT PRIMARY KEY, origin_system TEXT NOT NULL, collection_name TEXT NOT NULL, record_json TEXT NOT NULL, updated_at TEXT NOT NULL);
INSERT INTO "customer_data_primary" VALUES('search_9999','search_service','search_documents','{"resource_id":"search_9999","document":{"email":"zulu.unrelated@example.net"}}','2026-09-27T09:00:00Z');
INSERT INTO "customer_data_primary" VALUES('tombstone_9909','identity_service','deleted_customer_references','{"resource_id":"tombstone_9909","subject_reference":"user_909","name":null,"email":null,"phone":null,"email_hash":null,"account_status":"DELETED"}','2026-09-27T09:00:00Z');
COMMIT;
"""
    _write(os.path.join(tmp, "evidence", "databases", "primary_snapshot.sql"), sql2)
    ledger = {"origin": "marketing_lookup_ledger", "records": [
        {"recorded_at": "2026-09-27T10:00:00Z", "system": "marketing_platform", "resource_id": "marketing_9911",
         "record_type": "MARKETING_PROFILE", "result": "FOUND", "subject_reference": None}]}
    with open(os.path.join(tmp, "evidence", "records", "marketing_lookup.json"), "w") as fh:
        json.dump(ledger, fh)

    with open(os.path.join(tmp, "evidence_files.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["file_id", "file_path", "origin", "captured_at"])
        w.writerow(["evidence_901", "evidence/logs/readback.log", "service_readback_logs", "2026-09-09T08:00:09Z"])
        w.writerow(["evidence_902", "evidence/databases/replicas.sql", "customer_data_regional_replicas_capture", "2026-09-09T09:00:00Z"])
        w.writerow(["evidence_903", "evidence/logs/readback_batch2.log", "service_readback_logs", "2026-09-28T08:00:30Z"])
        w.writerow(["evidence_904", "evidence/databases/primary_snapshot.sql", "customer_data_primary_capture", "2026-09-27T09:00:00Z"])
        w.writerow(["evidence_905", "evidence/records/marketing_lookup.json", "marketing_lookup_ledger", "2026-09-27T10:00:05Z"])

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
        "case_906": ("INCOMPLETE", [
            step(1, "FINDING", "search_service", "search_9966", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_903"),
            step(2, "FINDING", "integration_service", "data_flow_9906", "REPORT_ACTIVE_DATA_FLOW", "ACTIVE", "evidence_903"),
            step(3, "REMEDIATION", "integration_service", "data_flow_9906", "DISABLE_DATA_FLOW", "DISABLED"),
            step(4, "REMEDIATION", "search_service", "search_9966", "DELETE_RECORD", "ABSENT"),
            step(5, "VERIFICATION", S, "account_9906", "VERIFY_RECORD_ABSENT", "ABSENT"),
            step(6, "VERIFICATION", "search_service", "search_9966", "VERIFY_RECORD_ABSENT", "ABSENT"),
            step(7, "VERIFICATION", "integration_service", "data_flow_9906", "VERIFY_DATA_FLOW_DISABLED", "DISABLED"),
        ]),
        "case_907": ("COMPLETE", [
            step(1, "VERIFICATION", S, "account_9907", "VERIFY_RECORD_ABSENT", "ABSENT", "evidence_903"),
            step(2, "VERIFICATION", "analytics_platform", "analytics_9970", "VERIFY_RECORD_ANONYMIZED", "ANONYMIZED", "evidence_903"),
        ]),
        "case_908": ("COMPLETE", [
            step(1, "VERIFICATION", S, "account_9908", "VERIFY_RECORD_ABSENT", "ABSENT", "evidence_903"),
            step(2, "VERIFICATION", "analytics_platform", "analytics_9980", "VERIFY_RECORD_ANONYMIZED", "ANONYMIZED", "evidence_903"),
        ]),
        "case_909": ("COMPLETE", [
            step(1, "VERIFICATION", S, "account_9909", "VERIFY_RECORD_ABSENT", "ABSENT", "evidence_903"),
            step(2, "VERIFICATION", "search_service", "search_9990", "VERIFY_RECORD_ABSENT", "ABSENT", "evidence_904"),
            step(3, "VERIFICATION", "identity_service", "tombstone_9909", "VERIFY_RECORD_REDACTED", "REDACTED", "evidence_904"),
        ]),
        "case_910": ("INCOMPLETE", [
            step(1, "FINDING", "support_platform", "support_9910", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_903"),
            step(2, "REMEDIATION", "support_platform", "support_9910", "DELETE_RECORD", "ABSENT"),
            step(3, "VERIFICATION", S, "account_9910", "VERIFY_RECORD_ABSENT", "ABSENT"),
            step(4, "VERIFICATION", "support_platform", "support_9910", "VERIFY_RECORD_ABSENT", "ABSENT"),
        ]),
        "case_911": ("INCOMPLETE", [
            step(1, "FINDING", "marketing_platform", "marketing_9911", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_905"),
            step(2, "REMEDIATION", "marketing_platform", "marketing_9911", "DELETE_RECORD", "ABSENT"),
            step(3, "VERIFICATION", S, "account_9911", "VERIFY_RECORD_ABSENT", "ABSENT"),
            step(4, "VERIFICATION", "marketing_platform", "marketing_9911", "VERIFY_RECORD_ABSENT", "ABSENT"),
        ]),
    }
    return exp


DESCRIPTIONS = {
    "case_901": "Request sent from a historical e-mail; the search entry is keyed by that old address while a "
                "regional replica still holds the deleted profile under the current address (replication drift); "
                "only the identity history links both.",
    "case_902": "Access/portability request that explicitly refuses deletion -> NOT_APPLICABLE with [].",
    "case_903": "Compliant at audit_at; a connector re-creates the search entry two days AFTER audit_at (must be "
                "ignored) and a look-alike decoy e-mail (gamma.jr@...) must not be attributed.",
    "case_904": "Active connector re-populating search + restorable backup: disable/block must precede the "
                "downstream delete; lineage from the connector links the copy.",
    "case_905": "Tombstone still exposing the e-mail (REDACT, keep stable reference) and a support message that "
                "must be redacted rather than deleted; Japanese request text.",
    "case_906": "Search copy with no identifiers at all, re-created by the subject's active connector: only lineage "
                "links it; the primary profile is found by workflow timing alone.",
    "case_907": "Two requests reported together, each with an identifier-free anonymised analytics profile: the "
                "policy-action timing phase must give each case its own profile (pair with case_908).",
    "case_908": "Second half of the joint policy-phase scenario (German / Spanish request texts).",
    "case_909": "Search entry seen after the request; a later complete snapshot of the search service from the "
                "case's own batch no longer lists it -> absent by omission -> COMPLETE; redacted tombstone kept.",
    "case_910": "Support item the deletion worker tried to DELETE (an attachment): must be reported as a remaining "
                "record and deleted, not redacted like a message.",
    "case_911": "Marketing profile visible only in an identifier-free lookup ledger; the activity catalogue links "
                "it to the subject. Reported COMPLETE but actually incomplete.",
}


def run(engine_factory):
    """Build the synthetic dataset, run the engine, return (expected, predicted)."""
    with tempfile.TemporaryDirectory() as tmp:
        expected = build(tmp)
        auditor = engine_factory(tmp)
        with open(os.path.join(tmp, "deletion_requests.csv"), encoding="utf-8") as fh:
            reqs = list(csv.DictReader(fh))
        auditor.prepare(reqs)
        predicted = {}
        for r in reqs:
            st, steps, _ = auditor.audit(r)
            predicted[r["case_id"]] = (st, steps)
    return expected, predicted
