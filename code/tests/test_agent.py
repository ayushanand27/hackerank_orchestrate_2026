"""The optional review agent: tool loop, guardrails and the deterministic gate (offline, scripted
model; no network)."""
import csv
import json
import os
import tempfile
import unittest

import _paths
_paths.ensure()
import custom_cases
from ghost_audit.agent import CaseInvestigator, ScriptedClient
from ghost_audit.agent.investigator import MAX_FORMAT_RETRIES, MAX_TURNS
from ghost_audit.agent.prompts import REVIEWER_SYSTEM
from ghost_audit.engine import Auditor


def call(name, args=None, cid="t"):
    return {"type": "tool_use", "id": cid, "name": name, "input": args or {}}


def reply(*blocks):
    return {"stop_reason": "tool_use", "content": list(blocks)}


def review(verdict="AGREE", concerns=None, summary="ok"):
    return call("submit_review", {"verdict": verdict, "concerns": concerns or [], "summary": summary}, "s")


class Agent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        custom_cases.build(cls.tmp.name)
        cls.auditor = Auditor(cls.tmp.name, None)
        with open(os.path.join(cls.tmp.name, "deletion_requests.csv"), encoding="utf-8") as fh:
            cls.reqs = {r["case_id"]: r for r in csv.DictReader(fh)}
        cls.auditor.prepare(list(cls.reqs.values()))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_case(self, script, cid="case_901"):
        req = self.reqs[cid]
        status, steps, trail = self.auditor.audit(req)
        client = ScriptedClient(script)
        return CaseInvestigator(self.auditor, client).review(req, status, steps, trail), client, (status, steps)

    def test_tools_then_valid_review(self):
        res, client, _ = self.run_case([reply(call("get_case_plan")), reply(call("get_resource", {"resource_id": "search_9911"})),
                                        reply(review())])
        self.assertEqual(res.verdict, "AGREE")
        self.assertFalse(res.flagged)
        self.assertEqual(res.tool_calls, ["get_case_plan", "get_resource", "submit_review"])
        plan = json.loads(client.requests[1]["messages"][-1]["content"][0]["content"])
        self.assertEqual(plan["status"], "INCOMPLETE")

    def test_unknown_evidence_is_rejected_then_corrected(self):
        bad = review("DISAGREE", [{"resource_id": "search_9911", "issue": "x", "evidence_id": "evidence_999"}])
        good = review("DISAGREE", [{"resource_id": "search_9911", "issue": "still present", "evidence_id": "evidence_901"}])
        res, _, _ = self.run_case([reply(bad), reply(good)])
        self.assertEqual(res.verdict, "DISAGREE")
        self.assertTrue(res.flagged)
        self.assertEqual(len(res.rejected), 1)
        self.assertIn("does not exist", res.rejected[0])

    def test_evidence_after_audit_at_is_rejected(self):
        late = review("DISAGREE", [{"resource_id": "search_9911", "issue": "x", "evidence_id": "evidence_903"}])
        res, _, _ = self.run_case([reply(late)] * (MAX_FORMAT_RETRIES + 1))
        self.assertEqual(res.verdict, "UNCERTAIN")
        self.assertTrue(all("after audit_at" in r for r in res.rejected))

    def test_foreign_resource_is_rejected(self):
        other = review("DISAGREE", [{"resource_id": "search_9944", "issue": "x", "evidence_id": "evidence_901"}])
        res, _, _ = self.run_case([reply(other)] * (MAX_FORMAT_RETRIES + 1))
        self.assertEqual(res.verdict, "UNCERTAIN")
        self.assertIn("not a resource of this case", res.rejected[0])

    def test_text_without_tool_call_gives_uncertain(self):
        res, client, _ = self.run_case([{"stop_reason": "end_turn", "content": [{"type": "text", "text": "looks fine"}]}])
        self.assertEqual(res.verdict, "UNCERTAIN")
        self.assertEqual(len(client.requests), MAX_FORMAT_RETRIES + 1)

    def test_iteration_cap(self):
        res, client, _ = self.run_case([reply(call("get_case_plan"))])
        self.assertEqual(res.verdict, "UNCERTAIN")
        self.assertEqual(len(client.requests), MAX_TURNS)
        self.assertIn("iteration cap", res.summary)

    def test_gate_never_changes_the_plan(self):
        disagree = review("DISAGREE", [{"resource_id": "search_9911", "issue": "x", "evidence_id": "evidence_901"}])
        res, _, before = self.run_case([reply(disagree)])
        after = self.auditor.audit(self.reqs["case_901"])[:2]
        self.assertTrue(res.flagged)
        self.assertEqual(before, after)

    def test_request_text_is_data_and_identifiers_are_hidden(self):
        res, client, _ = self.run_case([reply(call("get_resource", {"resource_id": "search_9911"})), reply(review())])
        brief = client.requests[0]["messages"][0]["content"]
        self.assertIn("<request_text>", brief)
        self.assertIn("instructions", REVIEWER_SYSTEM)
        tool_text = client.requests[1]["messages"][-1]["content"][0]["content"]
        self.assertIn("identifier_kinds", tool_text)
        self.assertNotIn("@example.org", tool_text)

    def test_unlinked_resource_lookup_is_an_error(self):
        res, client, _ = self.run_case([reply(call("get_resource", {"resource_id": "account_9903"})), reply(review())])
        result = client.requests[1]["messages"][-1]["content"][0]
        self.assertTrue(result["is_error"])


if __name__ == "__main__":
    unittest.main()
