"""Identifier-grounded linking: which resources belong to a case's subject?

Sources, all restricted to what was known at ``audit_at``:

1. observations whose keys / PII fields carry one of the subject's identifiers;
2. ``LINK_RESOURCE_IDENTITY`` catalogue events naming the subject (e-mail or user id);
3. documents grounded by (2) or showing the subject (see ``documents.py``);
4. lineage: a connector / restore event that "repopulated" a downstream copy from a resource
   that is already linked.
"""
from __future__ import annotations

import re

from .config import Features
from .context import AuditData, CaseContext
from .documents import doc_observations
from .evidence import EMAIL_RE, RESOURCE_RE, USER_RE


def catalogue_events(data: AuditData, c: CaseContext) -> list[tuple[dict, str]]:
    """(activity, alias) for every catalogue event up to ``audit_at`` that names the subject.
    The alias is the artifact name in brackets after the resource id, e.g. ``(INV-2291.pdf)``."""
    subj, out = c.subject, []
    for a in data.activities:
        if a["event_time"] > c.audit_at or a["event_type"] != "LINK_RESOURCE_IDENTITY":
            continue
        det = a["details"]
        emails = {e.lower().rstrip(".") for e in EMAIL_RE.findall(det)}
        users = set(USER_RE.findall(det))
        if (subj.user_id and subj.user_id in users) or subj.emails & emails:
            m = re.search(re.escape(a["resource_id"]) + r" \(([^)]+)\)", det)
            out.append((a, m.group(1) if m else a["resource_id"]))
    return out


def link_direct(data: AuditData, c: CaseContext, features: Features):
    """Fill ``c.direct`` (and ``c.doc_obs``) with identifier-grounded links."""
    subj, audit_at = c.subject, c.audit_at
    for o in data.observations:
        if o.time <= audit_at and data.belongs(subj, o):
            c.direct[o.resource_id].add("obs:" + o.evidence_id)
    links = {}   # rid -> (latest catalogue activity, alias)
    for a, alias in (catalogue_events(data, c) if features.catalog_links else []):
        c.direct[a["resource_id"]].add("link:" + a["activity_id"])
        links[a["resource_id"]] = (a, alias)
    c.doc_obs = doc_observations(data.documents, subj, links, audit_at) if features.documents else []
    for o in c.doc_obs:
        c.direct[o.resource_id].add("doc:" + o.evidence_id)
    if features.lineage:
        for a in data.activities:
            if a["event_time"] > audit_at or "repopulated" not in a["details"]:
                continue
            for r in RESOURCE_RE.findall(a["details"]):
                if r != a["resource_id"] and a["resource_id"] in c.direct:
                    c.direct[r].add("lineage:" + a["activity_id"])
