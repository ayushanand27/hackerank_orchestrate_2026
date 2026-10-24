"""Deletion-audit engine: orchestrates the pipeline.

Pipeline
--------
1. intent       does the request text authorise deletion?                   ``intent.py``
2. identity     current + historical e-mails, phones, hashes, user_id       ``identity.py``
3. linking      identifier-grounded links: observations, catalogue links,
                documents, lineage                                          ``linking.py``
4. attribution  identifier-free records via workflow timing (joint over
                all requests)                                               ``attribution.py``
5. state        latest trustworthy observation at or before ``audit_at``    ``state.py``
6. policy       ``deletion_rules.csv`` decides the required end state       ``policy.py``
7. planning     findings / remediation / verification in mandated order    ``planner.py``

Steps 1-4 run once for all requests (:meth:`Auditor.prepare`), because workflow events are
shared between requests and must be attributed jointly; steps 5-7 run per request
(:meth:`Auditor.audit`).
"""
from __future__ import annotations

import time
from collections import defaultdict

from .attribution import (attribute_policy_actions, attribute_primary_accounts,
                          refine_accounts_by_batch_cadence)
from .config import Features
from .context import AuditData, CaseContext, rid_num, ts
from .identity import Subject
from .intent import classify
from .linking import link_direct
from .planner import plan
from .state import build_views, linked_resources


class Auditor:
    """Runs the audit pipeline over a dataset; ``ctx`` holds one CaseContext per request."""
    def __init__(self, dataset_dir: str, cache_dir: str | None = None, features: Features | None = None,
                 evidence=None):
        """Load the dataset (or reuse a parsed evidence store) with the given feature switches."""
        self.features = features or Features()
        t0 = time.perf_counter()
        self.data = AuditData.load(dataset_dir, cache_dir, self.features.historical_identity, evidence)
        self.timings: dict[str, float] = {"load": time.perf_counter() - t0}
        self.ctx: dict[str, CaseContext] = {}

    # ------------------------------------------------------------------ all requests jointly
    def prepare(self, requests: list[dict]):
        """Classify intent, resolve identity and link evidence for ``requests``, then (re-)run the
        joint timing attribution over every request known to this auditor."""
        t0 = time.perf_counter()
        for r in requests:
            ok, _conf, why = classify(r["request_text"])
            subj = None
            if ok:
                # unknown requester e-mail: audit by that e-mail alone rather than giving up
                subj = self.data.identities.resolve(r["email"]) or Subject.from_email(r["email"])
            c = CaseContext(req=r, subject=subj, deletion=ok, intent_reason=why)
            if subj is not None:
                link_direct(self.data, c, self.features)
            self.ctx[r["case_id"]] = c
        t1 = time.perf_counter()

        all_cases = list(self.ctx.values())
        groups: dict[str, list[CaseContext]] = defaultdict(list)   # reporting group = same reported_at
        for c in all_cases:
            c.reset_attribution()
            if c.auditable:
                groups[c.req["reported_at"]].append(c)
        for cases in groups.values():
            cases.sort(key=lambda c: rid_num(c.case_id))
        for R, cases in groups.items():
            attribute_primary_accounts(self.data, ts(R), cases, all_cases, self.features)
        if self.features.timing_attribution and self.features.batch_cadence:
            refine_accounts_by_batch_cadence(self.data, all_cases)
        if self.features.policy_phase:
            for R, cases in groups.items():
                attribute_policy_actions(self.data, ts(R), cases, all_cases, self.features)
        t2 = time.perf_counter()
        self.timings["linking"] = self.timings.get("linking", 0.0) + (t1 - t0)
        self.timings["attribution"] = self.timings.get("attribution", 0.0) + (t2 - t1)

    # ------------------------------------------------------------------ one request
    def audit(self, req: dict) -> tuple[str, list[dict], dict]:
        """Return (deletion_status, steps, trail) for one request."""
        t0 = time.perf_counter()
        c = self.ctx.get(req["case_id"])
        if c is None:
            self.prepare([req])
            c = self.ctx[req["case_id"]]
        trail: dict = {"case_id": c.case_id, "intent": c.intent_reason,
                       "subject": (c.subject.user_id or f"unknown ({c.subject.current_email})") if c.subject else None}
        if not c.deletion:
            status, steps = "NOT_APPLICABLE", []
        else:
            views = build_views(self.data, c, self.features)
            status, steps = plan(views, c.primary_account, self.data.rules)
            trail.update(primary_account=c.primary_account,
                         links={k: sorted(v) for k, v in sorted(linked_resources(c).items())},
                         views={k: views[k] for k in sorted(views)})
        trail["status"] = status
        self.timings["audit"] = self.timings.get("audit", 0.0) + (time.perf_counter() - t0)
        return status, steps, trail
