"""Request-intent classification: does the request text authorise deletion?

A deterministic multilingual rule classifier (no model calls).  It looks for an explicit
erasure verb about the account / personal data, and for signals that the person asked for
something else (access, portability, correction, marketing opt-out, login help) or explicitly
said "do not delete".

Precedence:
1. an explicit refusal ("not asking for deletion", "keep my account active") always wins;
2. otherwise an erasure cue authorises deletion (also when another action is mentioned);
3. otherwise a recognised non-erasure action, or no cue at all, means NOT_APPLICABLE.
"""
from __future__ import annotations

import re

DELETE_PATTERNS = [
    # English
    r"\b(delet\w*|erase\w*|erasure|remov\w*|clos\w*)\b.{0,60}\b(account|profile|personal (data|information|details)|data|records?|copy|copies|export|file|archive|name|contact details|it)\b",
    r"\b(account|profile|data|records?)\b.{0,60}\b(deleted|deleting|erased|removed|closed|gone)\b",
    r"\bpermanent(ly)? (account[- ])?delet",
    r"\b(deletion|erasure) (request|process|procedure|was complete|is complete)\b",
    r"\bforget me\b|\bright to (be forgotten|erasure)\b",
    # Spanish / Portuguese
    r"\b(eliminaci[oó]n|eliminar|borrar|supresi[oó]n|exclus[aã]o|excluir|apagar)\b",
    # French
    r"\b(suppression|supprimer|effacer|effacement)\b",
    # German / Dutch / Italian
    r"\b(l[öo]schen|l[öo]schung|verwijder|cancellazione|cancellare|eliminazione)\b",
    # Japanese / Korean / Chinese
    r"削除|消去|退会|삭제|탈퇴|删除|註銷|注销",
]

# explicit statements that the person does NOT want erasure (always win)
REFUSAL_PATTERNS = [
    r"\bnot (asking|requesting) (for )?(a |an )?(deletion|erasure|to (delete|erase|remove|close))",
    r"\b(don'?t|do not|never) (delete|erase|remove|close)\b",
    r"\bnot a request to (erase|delete|remove)\b",
    r"\bkeep (the|my) (account|profile) (as it is|active|open)\b",
    r"\b(leave|keep) (the account|my profile|my account)\b.{0,40}\b(active|in place|as it is)\b",
    r"\bwant to continue using\b",
    r"\bmarketing lists? only\b|\bonly (from )?marketing\b",
    r"\bwithout removing any\b",
    r"\bstill (need|use|want) (the account|it|my account|receipts)\b",
]

# other (non-erasure) actions; only decisive when no erasure cue exists
OTHER_ACTION_PATTERNS = [
    r"\b(stop|unsubscribe).{0,30}(promotional|marketing|newsletter)",
    r"\b(portable format|send me a copy|copy of (the )?(information|my data|last year))",
    r"\bwhat personal information (you|do you) hold\b|\binformation request\b",
    r"\b(correct|fix|update|change|replace)\b.{0,40}\b(surname|name|number|email address|address|phone|mobile)\b",
    r"\b(misspelled|out of date)\b",
    r"\b(reset(ting)? the login|can'?t access my profile)\b",
]


def classify(text: str) -> tuple[bool, float, str]:
    """Return (authorises_deletion, confidence, reason)."""
    t = (text or "").lower().replace("’", "'")
    refusal = [p for p in REFUSAL_PATTERNS if re.search(p, t)]
    other = [p for p in OTHER_ACTION_PATTERNS if re.search(p, t)]
    pos = [p for p in DELETE_PATTERNS if re.search(p, t)]
    if refusal:
        return False, 0.95, "explicit non-deletion: " + refusal[0]
    if pos:
        return True, (0.95 if not other else 0.7), "deletion cue: " + pos[0]
    if other:
        return False, 0.9, "non-deletion action: " + other[0]
    return False, 0.4, "no deletion cue found"
