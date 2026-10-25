"""Read-only investigation tools the review agent may call, and their executor.

The tools expose what the deterministic engine knows about ONE case: its plan, the linked
resources with their observations, related workflow activity and evidence metadata. They return
identifier KINDS (e.g. "email"), never the identifier values, so the model sees no personal data
it does not need. ``submit_review`` is the only way to finish.
"""
from __future__ import annotations

import csv
import json
import os

from ..evidence import load_manifest
from ..policy import required_action

MAX_RESULT_CHARS = 4000      # keep every tool result small (context budget)
MAX_OBSERVATIONS = 12
MAX_ACTIVITIES = 10

VERDICTS = ("AGREE", "DISAGREE", "UNCERTAIN")

TOOL_SPECS = [
    {"name": "get_case_plan",
     "description": "The engine's status and ordered steps for this case, the primary profile, and every "
                    "linked resource with the reasons it was linked. Call this first.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "get_resource",
     "description": "State, category, policy action and cited evidence of one linked resource, plus its "
                    "observations up to audit_at (newest first, at most 12).",
     "input_schema": {"type": "object", "properties": {"resource_id": {"type": "string"}},
                      "required": ["resource_id"], "additionalProperties": False}},
    {"name": "get_activity",
     "description": "Workflow activity events for one resource up to audit_at (newest first, at most 10). "
                    "Results in these events are claims, not proof.",
     "input_schema": {"type": "object", "properties": {"resource_id": {"type": "string"}},
                      "required": ["resource_id"], "additionalProperties": False}},
    {"name": "check_evidence",
     "description": "Metadata of an evidence id (file, activity or audit-log id): what it is and when it "
                    "was captured, and whether that is at or before audit_at.",
     "input_schema": {"type": "object", "properties": {"evidence_id": {"type": "string"}},
                      "required": ["evidence_id"], "additionalProperties": False}},
    {"name": "submit_review",
     "description": "Finish the review. verdict: AGREE, DISAGREE or UNCERTAIN. concerns: specific problems, "
                    "each citing an evidence id returned by the tools. summary: 1-2 sentences.",
     "input_schema": {"type": "object", "properties": {
         "verdict": {"type": "string", "enum": list(VERDICTS)},
         "concerns": {"type": "array", "items": {"type": "object", "properties": {
             "resource_id": {"type": "string"}, "issue": {"type": "string"}, "evidence_id": {"type": "string"}},
             "required": ["resource_id", "issue", "evidence_id"], "additionalProperties": False}},
         "summary": {"type": "string"}},
         "required": ["verdict", "concerns", "summary"], "additionalProperties": False}},
]


class EvidenceIndex:
    """Capture times of every citable id: evidence files, activity events, audit-log rows."""

    def __init__(self, dataset_dir: str, activities: list):
        self.files = {m["file_id"]: m for m in load_manifest(dataset_dir)}
        self.activities = {a["activity_id"]: a for a in activities}
        self.audits = {}
        path = os.path.join(dataset_dir, "user_audit_log.csv")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                self.audits = {r["audit_id"]: r for r in csv.DictReader(fh)}

    def describe(self, evidence_id: str) -> dict | None:
        """{kind, time, ...} of a citable id, or None if it does not exist."""
        if evidence_id in self.files:
            m = self.files[evidence_id]
            return {"kind": "file", "time": m["captured_at"], "path": m["file_path"], "origin": m.get("origin", "")}
        if evidence_id in self.activities:
            a = self.activities[evidence_id]
            return {"kind": "activity", "time": a["event_time"], "event_type": a["event_type"],
                    "resource_id": a["resource_id"]}
        if evidence_id in self.audits:
            return {"kind": "audit_log", "time": self.audits[evidence_id]["event_time"]}
        return None


class CaseTools:
    """Executes the read-only tools for one case (``trail`` = the engine's audit trail of it)."""

    def __init__(self, auditor, req: dict, status: str, steps: list, trail: dict, index: EvidenceIndex):
        self.data = auditor.data
        self.req, self.status, self.steps, self.trail, self.index = req, status, steps, trail, index
        self.audit_at = req["audit_at"]

    def linked(self) -> set:
        """Resource ids the case is linked to or plans for."""
        return set(self.trail.get("links", {})) | {s["resource_id"] for s in self.steps}

    def run(self, name: str, args: dict) -> tuple[str, bool]:
        """(JSON result, is_error) for one tool call."""
        try:
            if name == "get_case_plan":
                out = {"case_id": self.req["case_id"], "audit_at": self.audit_at, "status": self.status,
                       "intent": self.trail.get("intent"), "primary_account": self.trail.get("primary_account"),
                       "steps": self.steps, "linked_resources": self.trail.get("links", {})}
            elif name == "get_resource":
                out = self._resource(str(args.get("resource_id", "")))
            elif name == "get_activity":
                out = self._activity(str(args.get("resource_id", "")))
            elif name == "check_evidence":
                ev = str(args.get("evidence_id", ""))
                d = self.index.describe(ev)
                out = {"error": f"unknown evidence id {ev}"} if d is None else dict(d, evidence_id=ev,
                                                                                    before_audit=d["time"] <= self.audit_at)
            else:
                return json.dumps({"error": f"unknown tool {name}"}), True
        except Exception as exc:  # a tool must never crash the run
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"}), True
        text = json.dumps(out, ensure_ascii=False, default=str)
        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS] + "... [truncated]"
        return text, "error" in out

    def _resource(self, rid: str) -> dict:
        """View + observations of one linked resource."""
        if rid not in self.linked():
            return {"error": f"{rid} is not linked to this case"}
        view = self.trail.get("views", {}).get(rid, {})
        obs = sorted((o for o in self.data.by_resource.get(rid, []) if o.time <= self.audit_at),
                     key=lambda o: o.time, reverse=True)[:MAX_OBSERVATIONS]
        return {"resource_id": rid, "view": view,
                "policy_action": required_action(self.data.rules, view.get("category")) if view.get("category") else None,
                "observations": [{"time": o.time, "evidence_id": o.evidence_id, "state": o.state, "source": o.kind,
                                  "table": o.table, "identifier_kinds": sorted(o.pii)} for o in obs]}

    def _activity(self, rid: str) -> dict:
        """Workflow events of one linked resource (claims)."""
        if rid not in self.linked():
            return {"error": f"{rid} is not linked to this case"}
        acts = sorted((a for a in self.data.activities if a["resource_id"] == rid and a["event_time"] <= self.audit_at),
                      key=lambda a: a["event_time"], reverse=True)[:MAX_ACTIVITIES]
        return {"resource_id": rid, "events": [{"activity_id": a["activity_id"], "time": a["event_time"],
                                                "event_type": a["event_type"], "reported_result": a["reported_result"],
                                                "details": a["details"][:200]} for a in acts]}
