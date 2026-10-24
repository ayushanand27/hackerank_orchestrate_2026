"""PDFs and screenshots -> observations.

Business documents (invoices, hand-over sheets, console screenshots) rarely state a deletion
verdict.  A document is used for a resource only when the link is grounded:

* the activity catalogue registered the resource for the subject (``LINK_RESOURCE_IDENTITY``)
  and the document either shows the resource id (and the subject, or was captured shortly
  before the link) or shows the resource's catalogue alias (e.g. a file name); or
* the document shows the subject and exactly one resource of a given type.

The state is read from what the document shows (an e-mail still visible, "restore blocked" ...).
"""
from __future__ import annotations

import os
import re
from collections import defaultdict
from datetime import timedelta

from .config import DOC_LINK_WINDOW, MIN_ALIAS_LEN
from .context import ts
from .evidence import Document, Observation
from .identity import Subject


def squash(text: str) -> str:
    """Lower-case and drop everything but letters/digits (robust to OCR spacing and punctuation)."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def doc_state(d: Document, rid: str) -> tuple[str, str | None]:
    """(state, category override) of ``rid`` as shown by document ``d``."""
    t = d.text.lower()
    name = os.path.basename(d.path).lower()
    if rid.startswith("tombstone_"):
        return ("IDENTIFIABLE" if d.emails else "REDACTED"), "ACCOUNT_TOMBSTONE"
    if rid.startswith("support_") or rid.startswith("object_"):
        if "attachment" in name or "attachment" in t or rid.startswith("object_"):
            return "PRESENT", "SUPPORT_ATTACHMENT"
        return ("IDENTIFIABLE" if d.emails else "REDACTED"), None
    if rid.startswith("analytics_"):
        return ("IDENTIFIABLE" if d.emails else "ANONYMIZED"), None
    if rid.startswith("backup_"):
        return ("BLOCKED" if re.search(r"restore (blocked|denied|disabled)", t) else "ACTIVE"), None
    if rid.startswith("data_flow_"):
        return ("DISABLED" if re.search(r"\b(disabled|paused|stopped)\b", t) else "ACTIVE"), None
    return "PRESENT", None


def doc_observations(docs: list[Document], subj: Subject, links: dict, audit_at: str) -> list[Observation]:
    """Observations for every grounded (document, resource) pair captured by ``audit_at``.

    ``links`` maps resource id -> (catalogue activity, alias) for the subject's catalogue links."""
    out = []
    for d in docs:
        if d.file_time > audit_at:
            continue
        text_sq = squash(d.text)
        mentions_subject = (bool(subj.user_id) and subj.user_id in d.users) or bool(subj.emails & set(d.emails))
        chosen = {}
        for rid, (a, alias) in links.items():
            al = squash(re.sub(r"\.(pdf|zip|png|jpg)$", "", alias, flags=re.I))
            near = timedelta(0) <= a["_t"] - ts(d.file_time) <= DOC_LINK_WINDOW
            if (rid in d.resources and (near or mentions_subject)) or \
                    (near and len(al) >= MIN_ALIAS_LEN and al in text_sq):
                chosen[rid] = "link"
        if mentions_subject:
            by_type = defaultdict(list)
            for rid in d.resources:
                by_type[re.sub(r"_\d+$", "", rid)].append(rid)
            for rids in by_type.values():
                if len(rids) == 1:
                    chosen.setdefault(rids[0], "single")
        for rid in chosen:
            o = Observation(evidence_id=d.evidence_id, file_time=d.file_time, time=d.file_time,
                            resource_id=rid, kind="doc:" + d.kind, table=os.path.basename(d.path))
            o.finish()
            o.state, cat = doc_state(d, rid)
            o.category = cat or o.category
            if subj.user_id:
                o.keys.add(subj.user_id)
            out.append(o)
    return out
