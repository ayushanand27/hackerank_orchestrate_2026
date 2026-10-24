"""Every log layout found in the evidence normalises to the same flat record."""
import unittest

import _paths
_paths.ensure()
from ghost_audit.logparse import parse_kv, parse_line


class LogLayouts(unittest.TestCase):
    def check(self, line, **expected):
        rec = parse_line(line)
        self.assertIsNotNone(rec, line)
        for k, v in expected.items():
            self.assertEqual(rec.get(k), v, f"{k} in {line}")
        return rec

    def test_bracket(self):
        self.check('2026-07-28T05:51:18Z [DEBUG] [account_service] [tr-1] profile_lookup_started: '
                   'instance="account-5" lookup_key="ana.test@example.org" attempt="1"',
                   ts="2026-07-28T05:51:18Z", service="account_service", trace="tr-1",
                   event="profile_lookup_started", lookup_key="ana.test@example.org")

    def test_pipe_with_leading_separator(self):
        self.check('2026-07-03T07:51:32Z | DEBUG | security_service | tr-2 | audit_record_read_completed | '
                   'resource_id="audit_record_1" rows="1" correlation_key="user_7"',
                   service="security_service", trace="tr-2", event="audit_record_read_completed",
                   resource_id="audit_record_1", correlation_key="user_7")

    def test_logfmt(self):
        self.check("2026-02-18T05:47:12Z level=INFO service=integration_service trace_id=rb-1 "
                   "event=connector_config_read_completed resource_id=data_flow_1 enabled=true",
                   service="integration_service", trace="rb-1", event="connector_config_read_completed",
                   enabled="true")

    def test_json(self):
        self.check('2026-04-19T06:51:13Z {"severity":"DEBUG","logger":"search_service","trace_id":"tr-3",'
                   '"event":"index_query_started","match_key":"bo.test@example.org"}',
                   service="search_service", trace="tr-3", event="index_query_started",
                   match_key="bo.test@example.org")

    def test_syslog(self):
        self.check('2026-03-25T08:51:15Z <15>1 node-1 support_platform tr-4 - - level=DEBUG '
                   'event="message_scan_started" subject_key="cy.test@example.org"',
                   service="support_platform", trace="tr-4", event="message_scan_started",
                   subject_key="cy.test@example.org")

    def test_nested_cloud_message(self):
        self.check('2026-09-30T11:51:03Z requestId=tr-5 level=DEBUG function=profile_service '
                   'message="instance=\\"profile-3\\" resource_id=\\"account_9\\" event=\\"profile_lookup_completed\\" '
                   'rows=\\"0\\" http_status=\\"404\\""',
                   service="profile_service", trace="tr-5", event="profile_lookup_completed",
                   resource_id="account_9", rows="0", http_status="404")

    def test_non_log_line_is_ignored(self):
        self.assertIsNone(parse_line("# exported by collector v2"))
        self.assertIsNone(parse_line(""))

    def test_quoted_values_keep_spaces(self):
        self.assertEqual(parse_kv('a="x y" b=z c="say \\"hi\\""'), {"a": "x y", "b": "z", "c": 'say "hi"'})


if __name__ == "__main__":
    unittest.main()
