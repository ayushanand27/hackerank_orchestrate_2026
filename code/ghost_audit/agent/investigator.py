"""The review agent: a bounded tool-calling loop with validated, gated output.

Loop: the model gets a case brief, calls read-only tools (``tools.py``) as it chooses, and must
finish with ``submit_review``. Guardrails:

* iteration cap (MAX_TURNS) and a cap on malformed answers (MAX_FORMAT_RETRIES);
* every submitted review is validated by code: verdict from the fixed set, each concern on a
  resource of this case, each cited evidence id real and captured at or before ``audit_at``;
* deterministic gate: the review never changes the engine's status or steps. AGREE confirms the
  row; DISAGREE or UNCERTAIN only flags it for human review in the audit trail.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .prompts import CASE_BRIEF, LAST_TURN_NUDGE, PROMPT_VERSION, RETRY_NUDGE, REVIEWER_SYSTEM
from .tools import TOOL_SPECS, VERDICTS, CaseTools, EvidenceIndex

MAX_TURNS = 8
MAX_FORMAT_RETRIES = 2
MAX_SUMMARY_CHARS = 600


@dataclass
class ReviewResult:
    """Outcome of one review; ``flagged`` rows need a human look, nothing else changes."""
    case_id: str
    verdict: str
    summary: str
    concerns: list = field(default_factory=list)
    turns: int = 0
    tool_calls: list = field(default_factory=list)
    rejected: list = field(default_factory=list)     # why submitted reviews were refused
    prompt_version: str = PROMPT_VERSION

    @property
    def flagged(self) -> bool:
        """Anything but a validated AGREE goes to human review."""
        return self.verdict != "AGREE"

    def as_dict(self) -> dict:
        """JSON-friendly form for the audit trail."""
        return {"verdict": self.verdict, "flagged": self.flagged, "summary": self.summary, "concerns": self.concerns,
                "turns": self.turns, "tool_calls": self.tool_calls, "rejected": self.rejected,
                "prompt_version": self.prompt_version}


def validate_review(args: dict, tools: CaseTools) -> str | None:
    """Problem with a submitted review, or None when it is valid."""
    if not isinstance(args, dict):
        return "arguments must be an object"
    verdict = args.get("verdict")
    if verdict not in VERDICTS:
        return f"verdict must be one of {', '.join(VERDICTS)}"
    concerns = args.get("concerns")
    if not isinstance(concerns, list):
        return "concerns must be a list"
    summary = args.get("summary")
    if not isinstance(summary, str) or not summary.strip() or len(summary) > MAX_SUMMARY_CHARS:
        return f"summary must be 1-{MAX_SUMMARY_CHARS} characters"
    if verdict == "DISAGREE" and not concerns:
        return "DISAGREE needs at least one concern"
    linked = tools.linked()
    for c in concerns:
        if not isinstance(c, dict) or not all(isinstance(c.get(k), str) for k in ("resource_id", "issue", "evidence_id")):
            return "each concern needs resource_id, issue and evidence_id strings"
        if c["resource_id"] not in linked:
            return f"{c['resource_id']} is not a resource of this case"
        d = tools.index.describe(c["evidence_id"])
        if d is None:
            return f"evidence id {c['evidence_id']} does not exist"
        if d["time"] > tools.audit_at:
            return f"evidence id {c['evidence_id']} was captured after audit_at"
    return None


class CaseInvestigator:
    """Runs the review loop for cases audited by ``auditor`` with model ``client``."""

    def __init__(self, auditor, client):
        self.auditor = auditor
        self.client = client
        self.index = EvidenceIndex(auditor.data.dataset_dir, auditor.data.activities)

    def review(self, req: dict, status: str, steps: list, trail: dict) -> ReviewResult:
        """Investigate one case; always returns a result (UNCERTAIN when the loop gives up)."""
        tools = CaseTools(self.auditor, req, status, steps, trail, self.index)
        result = ReviewResult(case_id=req["case_id"], verdict="UNCERTAIN", summary="")
        messages = [{"role": "user", "content": CASE_BRIEF.format(
            case_id=req["case_id"], audit_at=req["audit_at"], reported_status=req["reported_status"],
            status=status, n_steps=len(steps), request_text=req["request_text"][:2000])}]
        format_errors = 0
        for turn in range(MAX_TURNS):
            resp = self.client.create(REVIEWER_SYSTEM, messages, TOOL_SPECS)
            result.turns = turn + 1
            messages.append({"role": "assistant", "content": resp["content"]})
            calls = [b for b in resp["content"] if b.get("type") == "tool_use"]
            if not calls:
                format_errors += 1
                if format_errors > MAX_FORMAT_RETRIES:
                    result.summary = "no valid review after retries"
                    return result
                messages.append({"role": "user", "content": RETRY_NUDGE.format(problem="no tool call")})
                continue
            results = []
            for call in calls:
                name, args = call.get("name"), call.get("input") or {}
                result.tool_calls.append(name)
                if name == "submit_review":
                    problem = validate_review(args, tools)
                    if problem is None:
                        result.verdict, result.summary = args["verdict"], args["summary"].strip()
                        result.concerns = args["concerns"]
                        return result
                    result.rejected.append(problem)
                    format_errors += 1
                    if format_errors > MAX_FORMAT_RETRIES:
                        result.summary = "review rejected by validation: " + problem
                        return result
                    results.append({"type": "tool_result", "tool_use_id": call["id"], "is_error": True,
                                    "content": json.dumps({"error": problem})})
                else:
                    text, is_error = tools.run(name, args)
                    results.append({"type": "tool_result", "tool_use_id": call["id"], "content": text,
                                    "is_error": is_error})
            if turn == MAX_TURNS - 2:   # last turn coming: ask for the decision instead of more digging
                results.append({"type": "text", "text": LAST_TURN_NUDGE})
            messages.append({"role": "user", "content": results})
        result.summary = f"iteration cap ({MAX_TURNS} turns) reached"
        return result
