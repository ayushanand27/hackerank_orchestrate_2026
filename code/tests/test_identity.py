"""Identity resolution: current and historical identifiers, exact matching, decoys."""
import csv
import os
import tempfile
import unittest

import _paths
_paths.ensure()
from ghost_audit.identity import IdentityIndex, email_hash


def write_identities(tmp):
    with open(os.path.join(tmp, "user_identities.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["user_id", "name", "email", "phone", "email_hash", "updated_at"])
        w.writerow(["user_1", "Ana Test", "ana.new@example.org", "+44 7700 900111", email_hash("ana.new@example.org"), "2026-01-01T00:00:00Z"])
        w.writerow(["user_2", "Ana Test Jr", "ana.new.jr@example.org", "+44 7700 900222", "", "2026-01-01T00:00:00Z"])
        w.writerow(["user_3", "Reuse Owner", "shared@example.org", "", "", "2026-01-01T00:00:00Z"])
    with open(os.path.join(tmp, "user_audit_log.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["audit_id", "user_id", "event_time", "field_name", "old_value", "new_value"])
        w.writerow(["audit_1", "user_1", "2025-06-01T00:00:00Z", "email", "ana.old@example.org", "ana.new@example.org"])
        w.writerow(["audit_2", "user_1", "2025-07-01T00:00:00Z", "phone", "+1 (555) 010-9999", "+44 7700 900111"])
        # user_2 used to own an address that user_3 owns now
        w.writerow(["audit_3", "user_2", "2025-08-01T00:00:00Z", "email", "shared@example.org", "ana.new.jr@example.org"])


class Identity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        write_identities(cls.tmp.name)
        cls.idx = IdentityIndex(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_resolve_current_and_historical_email(self):
        self.assertEqual(self.idx.resolve("ana.new@example.org").user_id, "user_1")
        self.assertEqual(self.idx.resolve("ANA.OLD@example.org ").user_id, "user_1")
        self.assertIsNone(self.idx.resolve("nobody@example.org"))

    def test_current_owner_wins_for_reused_address(self):
        self.assertEqual(self.idx.resolve("shared@example.org").user_id, "user_3")

    def test_matches_every_identifier_form(self):
        s = self.idx.subjects["user_1"]
        self.assertTrue(s.matches("user_1"))
        self.assertTrue(s.matches("Ana.Old@Example.org"))
        self.assertTrue(s.matches(email_hash("ana.old@example.org")))      # hash of a historical e-mail
        self.assertTrue(s.matches("+1 555 010 9999"))                       # historical phone, other format
        self.assertTrue(s.matches("+44 7700 900111"))

    def test_decoys_do_not_match(self):
        s = self.idx.subjects["user_1"]
        self.assertFalse(s.matches("ana.new.jr@example.org"))
        self.assertFalse(s.matches("user_11"))
        self.assertFalse(s.matches(None))
        self.assertFalse(s.matches(""))
        self.assertFalse(s.matches("12"))                                   # too short for a phone

    def test_history_can_be_switched_off(self):
        idx = IdentityIndex(self.tmp.name, historical=False)
        self.assertIsNone(idx.resolve("ana.old@example.org"))
        self.assertEqual(idx.resolve("ana.new@example.org").user_id, "user_1")


if __name__ == "__main__":
    unittest.main()
