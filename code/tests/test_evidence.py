"""Evidence adapters turn each file family into normalised observations."""
import json
import os
import tempfile
import unittest

import _paths
_paths.ensure()
from ghost_audit.evidence import (Observation, parse_json, parse_log, parse_sql, parse_txt, scan_document,
                                  snapshot_obs, system_for)


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path


class ResourceConventions(unittest.TestCase):
    def test_prefix_mapping(self):
        self.assertEqual(system_for("data_flow_12"), ("integration_service", "DATA_FLOW"))
        self.assertEqual(system_for("audit_record_3"), ("security_service", "SECURITY_AUDIT_LOG"))
        self.assertEqual(system_for("account_5"), ("account_service", "USER_PROFILE"))
        self.assertEqual(system_for("tombstone_5"), ("identity_service", "ACCOUNT_TOMBSTONE"))
        self.assertEqual(system_for("weird_1"), (None, None))


class Snapshots(Tmp):
    def test_snapshot_bundle_records_and_coverage(self):
        bundle = {"snapshots": [
            {"data_store": "search_service", "collection": "search_documents", "captured_at": "2026-09-01T00:00:00Z",
             "records": [{"resource_id": "search_1", "hits": {"total": 0, "documents": []}, "query": "a@example.org"},
                         {"resource_id": "search_2", "hits": {"total": 1, "documents": [{"email": "b@example.org"}]}}]},
            {"data_store": "integration_service", "collection": "connectors", "captured_at": "2026-09-01T00:00:00Z",
             "records": [{"resource_id": "data_flow_1", "enabled": False, "subject_selector": "user_9"}]},
        ]}
        cov = []
        obs = parse_json("evidence_1", "2026-09-02T00:00:00Z", self.write("b.json", json.dumps(bundle)), cov)
        by = {o.resource_id: o for o in obs}
        self.assertEqual(by["search_1"].state, "ABSENT")
        self.assertIn("a@example.org", by["search_1"].keys)
        self.assertEqual(by["search_2"].state, "PRESENT")
        self.assertEqual(by["search_2"].pii["email"], ["b@example.org"])
        self.assertEqual(by["data_flow_1"].state, "DISABLED")
        self.assertEqual(by["data_flow_1"].time, "2026-09-01T00:00:00Z")      # record time, not file time
        self.assertEqual({c.system for c in cov}, {"search_service", "integration_service"})
        self.assertEqual(next(c for c in cov if c.system == "search_service").resource_ids, {"search_1", "search_2"})

    def test_free_text_leaks_identifiers(self):
        o = snapshot_obs("e", "t", "t", {"resource_id": "support_4", "requester_email": None,
                                         "message_text": "Please call me on +44 7700 900123 or mail z@example.org"})
        self.assertEqual(o.pii["email"], ["z@example.org"])
        self.assertEqual(o.pii["phone"], ["+44 7700 900123"])

    def test_backup_and_tombstone_states(self):
        b = snapshot_obs("e", "t", "t", {"resource_id": "backup_1", "restore_allowed": "false", "subject_reference": "user_1"})
        self.assertEqual((b.state, b.category), ("BLOCKED", "BACKUP_ARCHIVE"))
        t = snapshot_obs("e", "t", "t", {"resource_id": "tombstone_1", "subject_reference": "user_1", "email": None})
        self.assertEqual((t.state, t.category), ("PRESENT", "ACCOUNT_TOMBSTONE"))
        self.assertEqual(t.pii, {"user_ref": ["user_1"]})


class Ledgers(Tmp):
    def test_lookup_records(self):
        d = {"origin": "ledger", "records": [{"recorded_at": "2026-09-01T00:00:00Z", "system": "marketing_platform",
                                              "resource_id": "marketing_1", "record_type": "MARKETING_PROFILE",
                                              "result": "NOT_FOUND", "subject_reference": "a@example.org"}]}
        [o] = parse_json("evidence_2", "2026-09-02T00:00:00Z", self.write("l.json", json.dumps(d)))
        self.assertEqual((o.state, o.category, o.kind), ("ABSENT", "MARKETING_PROFILE", "lookup"))
        self.assertEqual(o.keys, {"a@example.org"})

    def test_handover_note(self):
        text = ("Shift hand-over\n"
                "- 2026-09-01T10:00:00Z | export_service | export_7 | USER_EXPORT | FOUND | a@example.org\n"
                "- 2026-09-01T10:05:00Z | cache_service | cache_3 | CACHE_ENTRY | NOT_FOUND | no direct reference\n")
        obs = parse_txt("evidence_3", "2026-09-02T00:00:00Z", self.write("h.txt", text))
        self.assertEqual([(o.resource_id, o.state) for o in obs], [("export_7", "PRESENT"), ("cache_3", "ABSENT")])
        self.assertEqual(obs[1].keys, set())


class Logs(Tmp):
    def test_trace_join_and_states(self):
        text = "\n".join([
            "2026-09-01T10:00:00Z level=DEBUG service=account_service trace_id=t1 event=profile_lookup_started resource_id=account_1 lookup_key=a@example.org",
            "2026-09-01T10:00:01Z level=INFO service=account_service trace_id=t1 event=profile_lookup_completed resource_id=account_1 rows=0 http_status=404",
            "2026-09-01T10:00:02Z level=INFO service=support_platform trace_id=t2 event=support_ticket_read_completed resource_id=support_1 requester_email=null requester_phone=null requester_name=null customer_reference=user_1",
            "2026-09-01T10:00:03Z level=INFO service=backup_service trace_id=t3 event=restore_policy_read_completed resource_id=backup_1 restore_allowed=true",
            "2026-09-01T10:00:04Z level=DEBUG service=x trace_id=t4 event=connection_acquired resource_id=account_1",
        ])
        obs = parse_log("evidence_4", "2026-09-02T00:00:00Z", self.write("a.log", text))
        by = {o.resource_id: o for o in obs}
        self.assertEqual(len(obs), 3)                                  # start/noise lines are skipped
        self.assertEqual(by["account_1"].state, "ABSENT")
        self.assertIn("a@example.org", by["account_1"].keys)           # key joined from the start line
        self.assertEqual(by["account_1"].time, "2026-09-01T10:00:01Z")  # line time, not export time
        self.assertEqual(by["support_1"].state, "REDACTED")
        self.assertEqual(by["backup_1"].state, "ACTIVE")


class Sql(Tmp):
    SQL = """-- Captured at: 2026-09-05T00:00:00Z
-- Database topology: primary_with_read_replica
CREATE TABLE snapshot_scope (origin_system TEXT, captured_at TEXT);
INSERT INTO snapshot_scope VALUES('account_service','2026-09-04T00:00:00Z');
CREATE TABLE customer_data_primary (resource_id TEXT, origin_system TEXT, collection_name TEXT, record_json TEXT, updated_at TEXT);
CREATE TABLE customer_data_read_replica (resource_id TEXT, origin_system TEXT, collection_name TEXT, record_json TEXT, updated_at TEXT);
INSERT INTO customer_data_read_replica VALUES('account_2','account_service','customer_profiles','{"email":"a@example.org","full_name":"A"}','2026-09-01T00:00:00Z');
CREATE TABLE connector_subscriptions (resource_id TEXT, connector_name TEXT, enabled INTEGER, subject_selector TEXT);
INSERT INTO connector_subscriptions VALUES('data_flow_9','crm_sync',1,'a@example.org');
"""

    def test_replica_rows_and_scope(self):
        cov = []
        obs = parse_sql("evidence_5", "2026-09-05T00:00:00Z", self.write("s.sql", self.SQL), cov)
        by = {o.resource_id: o for o in obs}
        self.assertEqual(by["account_2"].table, "customer_data_read_replica")
        self.assertEqual(by["account_2"].state, "PRESENT")
        self.assertEqual(by["account_2"].time, "2026-09-04T00:00:00Z")    # snapshot time of its system
        self.assertEqual(by["data_flow_9"].state, "ACTIVE")
        self.assertIn("a@example.org", by["data_flow_9"].keys)
        acct_cov = [c for c in cov if c.system == "account_service"]
        self.assertEqual(len(acct_cov), 1)
        self.assertEqual(acct_cov[0].resource_ids, {"account_2"})


class Documents(Tmp):
    def test_ocr_spacing_repairs_ids(self):
        cache = os.path.join(self.tmp.name, "cache")
        os.makedirs(cache)
        # a cached OCR result stands in for Tesseract
        self.write("cache/console.png.ocr.txt", "Search 1874 owner A@Example.org\ndata_flow.1121 user_42 support 77")
        doc = scan_document("evidence_6", "2026-09-01T00:00:00Z", os.path.join(self.tmp.name, "console.png"), "image", cache)
        self.assertIn("data_flow_1121", doc.resources)
        self.assertIn("support_77", doc.resources)
        self.assertEqual(doc.emails, ["a@example.org"])
        self.assertEqual(doc.users, ["user_42"])

    def test_observation_finish_fills_defaults(self):
        o = Observation("e", "t", "t", "export_3").finish()
        self.assertEqual((o.system, o.category), ("export_service", "USER_EXPORT"))


if __name__ == "__main__":
    unittest.main()
