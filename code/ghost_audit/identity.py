"""Identity resolution: current snapshot + audit history -> every identifier of a subject.

Matching is exact (lower-cased e-mail, digits-only phone, SHA-256 of the lower-cased e-mail), so
look-alike decoys such as ``maria.lopez.jr@...`` never match ``maria.lopez@...``.
"""
from __future__ import annotations

import csv
import hashlib
import os
import re
from dataclasses import dataclass, field


def norm_email(e: str) -> str:
    return (e or "").strip().lower()


def norm_phone(p: str) -> str:
    return re.sub(r"\D", "", p or "")


def email_hash(e: str) -> str:
    return hashlib.sha256(norm_email(e).encode()).hexdigest()


@dataclass
class Subject:
    user_id: str
    name: str
    emails: set = field(default_factory=set)           # all, lower-case
    current_email: str = ""
    phones: set = field(default_factory=set)           # digits only
    hashes: set = field(default_factory=set)
    names: set = field(default_factory=set)
    history: list = field(default_factory=list)        # (time, field, old, new, audit_id)

    def matches(self, value: str) -> bool:
        """Does a raw identifier value belong to this subject?"""
        if value is None:
            return False
        v = str(value).strip()
        if not v:
            return False
        if v == self.user_id:
            return True
        lv = v.lower()
        if "@" in lv:
            return lv in self.emails
        if re.fullmatch(r"[0-9a-f]{64}", lv):
            return lv in self.hashes
        if v.startswith("+") or re.fullmatch(r"[\d ()+\-]{7,}", v):
            d = norm_phone(v)
            return len(d) >= 7 and d in self.phones
        return False

    def audit_ids(self) -> list[str]:
        return [h[4] for h in self.history]


class IdentityIndex:
    """All known subjects, indexed by every e-mail they have ever used.

    ``historical=False`` ignores ``user_audit_log.csv`` (only used by the ablation study)."""

    def __init__(self, dataset_dir: str, historical: bool = True):
        self.subjects: dict[str, Subject] = {}
        with open(os.path.join(dataset_dir, "user_identities.csv"), encoding="utf-8") as fh:
            ids = list(csv.DictReader(fh))
        for r in ids:
            s = Subject(user_id=r["user_id"], name=r["name"], current_email=norm_email(r["email"]))
            s.emails.add(norm_email(r["email"]))
            if r.get("phone"):
                s.phones.add(norm_phone(r["phone"]))
            if r.get("email_hash"):
                s.hashes.add(r["email_hash"].lower())
            s.hashes.add(email_hash(r["email"]))
            s.names.add(r["name"].strip().lower())
            self.subjects[s.user_id] = s
        path = os.path.join(dataset_dir, "user_audit_log.csv")
        history = []
        if historical and os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                history = list(csv.DictReader(fh))
        for a in history:
            s = self.subjects.get(a["user_id"])
            if not s:
                continue
            s.history.append((a["event_time"], a["field_name"], a["old_value"], a["new_value"], a["audit_id"]))
            for v in (a["old_value"], a["new_value"]):
                if not v:
                    continue
                if a["field_name"] == "email":
                    s.emails.add(norm_email(v))
                    s.hashes.add(email_hash(v))
                elif a["field_name"] == "phone":
                    s.phones.add(norm_phone(v))
                elif a["field_name"] == "email_hash":
                    s.hashes.add(v.lower())
                elif a["field_name"] in ("name", "full_name"):
                    s.names.add(v.strip().lower())
        self.by_email: dict[str, set] = {}
        for s in self.subjects.values():
            for e in s.emails:
                self.by_email.setdefault(e, set()).add(s.user_id)

    def resolve(self, email: str) -> Subject | None:
        uids = self.by_email.get(norm_email(email), set())
        if not uids:
            return None
        # prefer the subject whose *current* email it is
        for u in sorted(uids):
            if self.subjects[u].current_email == norm_email(email):
                return self.subjects[u]
        return self.subjects[sorted(uids)[0]]
