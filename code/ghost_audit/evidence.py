"""Evidence ingestion: turns every supplied artifact into normalised observations.

An :class:`Observation` is one statement of the form "at time T, source S saw
resource R of system Y in state Z, carrying identifiers I".  All downstream
reasoning (identity linking, point-in-time state, policy) works only on these
observations, so each file family has a small adapter here.

Supported families
------------------
* ``records/*.json``  – snapshot bundles, record-lookup exports, service exports
* ``records/*.txt``   – shift hand-over notes (pipe separated lookups)
* ``logs/*.log``      – service traces in many layouts (see ``logparse``)
* ``databases/*.sql`` – SQLite snapshots incl. primary / replica tables
* ``documents/*.pdf`` – business documents (text extracted with pdftotext/pypdf)
* ``images/*.png``    – console screenshots (local OCR via Tesseract, cached)

Complete listings (a SQL snapshot of a system, a snapshot-bundle collection) are also recorded
as :class:`Coverage`, which later allows "absent because not listed" reasoning.
"""
from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
import subprocess
from dataclasses import dataclass, field
from .logparse import parse_line

# --------------------------------------------------------------------------
# Resource-id conventions -> owning system and default data category
# --------------------------------------------------------------------------
PREFIX_SYSTEM = [
    ("data_flow_", "integration_service", "DATA_FLOW"),
    ("audit_record_", "security_service", "SECURITY_AUDIT_LOG"),
    ("legal_hold_", "legal_service", "LEGAL_HOLD_RECORD"),
    ("email_delivery_", "email_delivery_service", "EMAIL_DELIVERY_LOG"),
    ("tombstone_", "identity_service", "ACCOUNT_TOMBSTONE"),
    ("marketing_", "marketing_platform", "MARKETING_PROFILE"),
    ("telemetry_", "telemetry_service", "PRODUCT_TELEMETRY"),
    ("analytics_", "analytics_platform", "ANALYTICS_PROFILE"),
    ("account_", "account_service", "USER_PROFILE"),
    ("search_", "search_service", "SEARCH_INDEX_ENTRY"),
    ("support_", "support_platform", "SUPPORT_MESSAGE"),
    ("export_", "export_service", "USER_EXPORT"),
    ("backup_", "backup_service", "BACKUP_ARCHIVE"),
    ("cache_", "cache_service", "CACHE_ENTRY"),
    ("object_", "object_storage", "SUPPORT_ATTACHMENT"),
]
RESOURCE_RE = re.compile(
    r"\b(?:data_flow|audit_record|legal_hold|email_delivery|tombstone|marketing|telemetry|"
    r"analytics|account|search|support|export|backup|cache|object)_\d+\b"
)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
USER_RE = re.compile(r"\buser_\d+\b")
HASH_RE = re.compile(r"\b[0-9a-f]{64}\b")
PHONE_RE = re.compile(r"\+\d[\d ()\-]{6,}\d")


def system_for(resource_id: str) -> tuple[str | None, str | None]:
    for pre, system, cat in PREFIX_SYSTEM:
        if resource_id.startswith(pre):
            return system, cat
    return None, None


NULLS = {None, "", "null", "None", "NULL", "none"}


def nn(v):
    """Normalise null-ish values to None."""
    if isinstance(v, str):
        v = v.strip()
    return None if v in NULLS else v


PII_FIELDS = {
    "email": "email", "owner_email": "email", "customer_email": "email", "requester_email": "email",
    "phone": "phone", "owner_phone": "phone", "requester_phone": "phone",
    "email_hash": "email_hash",
    "full_name": "name", "display_name": "name", "requester_name": "name", "name": "name",
    "customer_reference": "user_ref", "user_id": "user_ref", "subject_reference": "user_ref",
}


@dataclass
class Observation:
    evidence_id: str
    file_time: str
    time: str
    resource_id: str
    system: str | None = None
    category: str | None = None
    # normalised state token: ABSENT / PRESENT / IDENTIFIABLE / ANONYMIZED / REDACTED /
    # ACTIVE / DISABLED / BLOCKED  (None = unknown)
    state: str | None = None
    pii: dict = field(default_factory=dict)       # field-kind -> list of values present
    keys: set = field(default_factory=set)        # identifiers used to find the record
    kind: str = ""                                # source family
    table: str = ""                               # sub-source (replica table, collection)
    extra: dict = field(default_factory=dict)

    def finish(self):
        if not self.system or not self.category:
            s, c = system_for(self.resource_id)
            self.system = self.system or s
            self.category = self.category or c
        return self


def add_pii(obs: Observation, rec: dict):
    for k, v in rec.items():
        kind = PII_FIELDS.get(k)
        v = nn(v) if not isinstance(v, (dict, list)) else None
        if kind and v is not None:
            obs.pii.setdefault(kind, []).append(str(v))


RESULT_STATE = {
    "NOT_FOUND": "ABSENT", "FOUND": "PRESENT", "IDENTIFIABLE": "IDENTIFIABLE",
    "ANONYMIZED": "ANONYMIZED", "REDACTED": "REDACTED", "ACTIVE": "ACTIVE",
    "DISABLED": "DISABLED", "BLOCKED": "BLOCKED", "ABSENT": "ABSENT", "PRESENT": "PRESENT",
    "DELETED": "ABSENT",
}

def lookup_obs(eid, ftime, rec, kind, table="") -> Observation:
    """Record-lookup rows: {recorded_at, system, resource_id, record_type, result, subject_reference}"""
    ref = nn(rec.get("subject_reference")) or nn(rec.get("lookup_value"))
    o = Observation(
        evidence_id=eid, file_time=ftime, time=rec.get("recorded_at") or ftime,
        resource_id=rec["resource_id"], system=rec.get("system"), category=rec.get("record_type"),
        state=RESULT_STATE.get(str(rec.get("result", "")).upper()), kind=kind, table=table,
    )
    if ref:
        o.keys.add(ref)
    return o.finish()


# --------------------------------------------------------------------------
# Snapshot-record interpretation (fields present -> state)
# --------------------------------------------------------------------------
def snapshot_obs(eid, ftime, t, rec: dict, system=None, collection="", kind="snapshot") -> Observation:
    rid = rec.get("resource_id")
    o = Observation(evidence_id=eid, file_time=ftime, time=t or ftime, resource_id=rid,
                    system=system, kind=kind, table=collection)
    o.finish()
    flat = dict(rec)
    doc = rec.get("document")
    if isinstance(doc, dict):
        flat.update(doc)
    prof = rec.get("profile")
    if isinstance(prof, dict):
        flat.update(prof)
    hits = rec.get("hits")
    add_pii(o, flat)
    if collection == "ticket_attachments" or "file_name" in rec and str(rid).startswith("support_"):
        o.category = "SUPPORT_ATTACHMENT"
        if nn(rec.get("uploaded_by")):
            o.pii.setdefault("email", []).append(rec["uploaded_by"])
    if o.category == "DATA_FLOW":
        en = rec.get("enabled")
        if en is not None:
            o.state = "ACTIVE" if str(en).lower() in ("true", "1") else "DISABLED"
        o.extra["connector"] = rec.get("connector_name")
        sel = nn(rec.get("subject_selector"))
        if sel:
            o.keys.add(sel)
    elif o.category == "BACKUP_ARCHIVE":
        ra = rec.get("restore_allowed")
        if ra is not None:
            o.state = "ACTIVE" if str(ra).lower() in ("true", "1") else "BLOCKED"
        sel = nn(rec.get("subject_reference"))
        if sel:
            o.keys.add(sel)
    elif o.category == "ACCOUNT_TOMBSTONE":
        o.state = "PRESENT"
    elif o.category == "USER_PROFILE" and "status_code" in rec:
        o.state = "PRESENT" if str(rec["status_code"]) == "200" else "ABSENT"
    elif isinstance(hits, dict):
        o.state = "PRESENT" if int(hits.get("total") or 0) > 0 else "ABSENT"
        q = nn(rec.get("query"))
        if q:
            o.keys.add(q)
        for d in hits.get("documents") or []:
            add_pii(o, d)
    elif o.category == "USER_EXPORT" and "download_available" in rec:
        o.state = "PRESENT" if str(rec["download_available"]).lower() == "true" else "ABSENT"
        o.extra["expires_at"] = rec.get("expires_at")
    else:
        o.state = "PRESENT"
    msg = rec.get("message_text") or rec.get("message_excerpt")
    if msg is not None:
        o.extra["message"] = msg
        # free text can leak direct identifiers even when structured fields are empty
        for e in EMAIL_RE.findall(str(msg)):
            o.pii.setdefault("email", []).append(e)
        for ph in PHONE_RE.findall(str(msg)):
            o.pii.setdefault("phone", []).append(ph)
    if rec.get("account_status"):
        o.extra["account_status"] = rec["account_status"]
    return o


@dataclass
class Coverage:
    """A complete point-in-time listing of one system: anything of that system that is not
    listed was absent at ``time`` (used for absence-by-omission)."""
    evidence_id: str
    file_time: str
    time: str
    system: str
    resource_ids: set


# --------------------------------------------------------------------------
# File-family adapters
# --------------------------------------------------------------------------
def parse_json(eid, ftime, path, coverage: list | None = None) -> list[Observation]:
    """Snapshot bundles, service exports and record-lookup exports."""
    coverage = [] if coverage is None else coverage
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    out: list[Observation] = []
    if "snapshots" in d:
        for s in d["snapshots"]:
            coll = s.get("collection", "")
            if coll != "operational_record_history" and s.get("data_store") and s.get("data_store") != "support_platform":
                coverage.append(Coverage(eid, ftime, s.get("captured_at") or ftime, s["data_store"],
                                         {r.get("resource_id") for r in s.get("records", [])}))
            for rec in s.get("records", []):
                if coll == "operational_record_history" or ("record_type" in rec and "result" in rec):
                    out.append(lookup_obs(eid, ftime, rec, "lookup", s.get("data_store", "")))
                else:
                    out.append(snapshot_obs(eid, ftime, s.get("captured_at"), rec, s.get("data_store"), coll))
    elif "service_exports" in d:
        for system, groups in d["service_exports"].items():
            for gname, recs in groups.items():
                for rec in recs:
                    out.append(snapshot_obs(eid, ftime, rec.get("observed_at"), rec, system, gname, "service_export"))
    elif "records" in d:
        coll = d.get("collection", "")
        for rec in d["records"]:
            if "record_type" in rec and "result" in rec:
                out.append(lookup_obs(eid, ftime, rec, "lookup", d.get("origin", "")))
            else:
                out.append(snapshot_obs(eid, ftime, d.get("captured_at"), rec, None, coll))
    return out


HANDOVER_RE = re.compile(
    r"^-\s*(\S+)\s*\|\s*(\w+)\s*\|\s*(\w+)\s*\|\s*(\w+)\s*\|\s*(\w+)\s*\|\s*(.+?)\s*$")


def parse_txt(eid, ftime, path) -> list[Observation]:
    """Shift hand-over notes: ``- time | system | resource | record_type | result | reference``."""
    out = []
    with open(path, encoding="utf-8") as fh:
        lines = fh.readlines()
    for line in lines:
        m = HANDOVER_RE.match(line.strip())
        if not m:
            continue
        ts, system, rid, rtype, result, ref = m.groups()
        rec = dict(recorded_at=ts, system=system, resource_id=rid, record_type=rtype, result=result,
                   subject_reference=None if ref.lower().startswith("no direct") else ref)
        out.append(lookup_obs(eid, ftime, rec, "handover"))
    return out


LOOKUP_KEYS = ("lookup_key", "subject_key", "match_key", "scan_key", "subject_ref", "owner_key",
               "correlation_key", "lookup_value")


def _log_state(ev: str, r: dict) -> tuple[str | None, str | None]:
    """Return (state, category override) for a completion-type log event."""
    rid = r.get("resource_id", "")
    rows = r.get("rows")
    http = r.get("http_status")
    if ev in ("record_lookup",):
        return RESULT_STATE.get(r.get("result", "").upper()), r.get("record_type")
    if ev.startswith("profile_lookup_completed") or ev == "contact_query_completed" or ev == "contact_lookup_completed":
        if rows == "0" or http == "404":
            return "ABSENT", None
        return "PRESENT", None
    if ev == "index_query_completed":
        if r.get("result") == "miss" or r.get("total_hits") == "0":
            return "ABSENT", None
        return "PRESENT", None
    if ev in ("message_pii_scan",):
        return "IDENTIFIABLE", "SUPPORT_MESSAGE"
    if ev in ("attachment_pii_scan",):
        return "IDENTIFIABLE", "SUPPORT_ATTACHMENT"
    if ev == "message_read":
        return ("REDACTED" if "REDACTED" in r.get("message_text", "") else "IDENTIFIABLE"), "SUPPORT_MESSAGE"
    if ev == "analytics_identity_scan":
        return "IDENTIFIABLE", None
    if ev == "analytics_profile_read":
        return ("ANONYMIZED" if r.get("direct_identifiers") == "0" else "IDENTIFIABLE"), None
    if ev in ("analytics_profile_read_completed",):
        ids = [nn(r.get(k)) for k in ("email", "phone", "email_hash", "customer_reference")]
        return ("IDENTIFIABLE" if any(ids) else "ANONYMIZED"), None
    if ev == "support_ticket_read_completed":
        direct = [nn(r.get(k)) for k in ("requester_email", "requester_phone", "requester_name")]
        return ("IDENTIFIABLE" if any(direct) else "REDACTED"), None
    if ev in ("audit_record_read_completed",):
        return ("PRESENT" if rows != "0" else "ABSENT"), None
    if ev in ("restore_candidate_evaluated",):
        ok = r.get("eligible") == "true" and r.get("deletion_block") != "true"
        return ("ACTIVE" if ok else "BLOCKED"), None
    if ev in ("restore_policy_read_completed",):
        return ("ACTIVE" if r.get("restore_allowed") == "true" else "BLOCKED"), None
    if ev in ("restore_authorization_completed",):
        return ("BLOCKED" if r.get("decision") == "deny" else "ACTIVE"), None
    if ev in ("connector_heartbeat", "connector_config_loaded", "connector_config_read_completed"):
        return ("ACTIVE" if r.get("enabled") == "true" else "DISABLED"), None
    if ev in ("export_download_check_completed", "export_metadata_read_completed"):
        return ("PRESENT" if r.get("available") == "true" else "ABSENT"), None
    if ev == "retained_order_loaded":
        return "PRESENT", "LEGAL_HOLD_RECORD"
    return None, None


SKIP_EVENTS = {"connection_acquired", "dependency_retry", "dependency_recovered", "cache_lookup",
               "export_started", "export_completed", "thumbnail_failed"}


def parse_log(eid, ftime, path) -> list[Observation]:
    """Service traces.  The lookup key is logged on the *start* line of a trace and the outcome on
    the *completion* line, so keys are joined through the trace id."""
    with open(path, encoding="utf-8") as fh:
        recs = [r for r in (parse_line(line) for line in fh) if r]
    trace_keys: dict[str, set] = {}
    for r in recs:
        tr = r.get("trace") or r.get("trace_id")
        for k in LOOKUP_KEYS:
            v = nn(r.get(k))
            if v and tr:
                trace_keys.setdefault(tr, set()).add(v)
    out = []
    for r in recs:
        ev = r.get("event") or ""
        rid = r.get("resource_id")
        if not rid or ev in SKIP_EVENTS or ev.endswith("_started"):
            continue
        state, cat = _log_state(ev, r)
        if state is None and ev not in ("deletion_marker_check",):
            continue
        tr = r.get("trace") or r.get("trace_id")
        o = Observation(evidence_id=eid, file_time=ftime, time=r["ts"], resource_id=rid,
                        system=r.get("system") if ev == "record_lookup" else None,
                        category=cat, state=state, kind="log", table=ev)
        o.finish()
        if ev == "record_lookup":
            ref = nn(r.get("subject_reference"))
            if ref:
                o.keys.add(ref)
        else:
            o.keys |= set(trace_keys.get(tr, set()))
            for k in ("correlation_key", "matched_value"):
                v = nn(r.get(k))
                if v:
                    o.keys.add(v)
            add_pii(o, r)
            mv = nn(r.get("matched_value"))
            if mv:
                kind = "phone" if mv.startswith("+") else "email"
                o.pii.setdefault(kind, []).append(mv)
        for k in ("marker_found", "last_write", "source", "connector_name", "file", "email_opt_in"):
            if k in r:
                o.extra[k] = r[k]
        if ev == "deletion_marker_check":
            o.state = "PRESENT" if r.get("marker_found") == "false" else "ABSENT"
        out.append(o)
    return out


def parse_sql(eid, ftime, path, coverage: list | None = None) -> list[Observation]:
    """SQLite snapshot exports.  Every table row becomes an observation dated at the snapshot time
    of its system (``snapshot_scope``); each system in scope is recorded as complete coverage."""
    coverage = [] if coverage is None else coverage
    con = sqlite3.connect(":memory:")
    with open(path, encoding="utf-8") as fh:
        sql = fh.read()
    con.executescript(sql)
    cur = con.cursor()
    tables = [r[0] for r in cur.execute("select name from sqlite_master where type='table'")]
    scope = {}
    if "snapshot_scope" in tables:
        scope = dict(cur.execute("select origin_system, captured_at from snapshot_scope").fetchall())
    m = re.search(r"Captured at:\s*(\S+)", sql)
    cap = m.group(1) if m else ftime
    topo = re.search(r"Database topology:\s*(\S+)", sql)
    out = []
    sql_seen: dict = {}
    for t in tables:
        if t == "snapshot_scope":
            continue
        cols = [c[1] for c in cur.execute(f'pragma table_info("{t}")')]
        for row in cur.execute(f'select * from "{t}"'):
            rec = dict(zip(cols, row))
            if "record_json" in rec:
                inner = json.loads(rec["record_json"])
                inner.setdefault("resource_id", rec["resource_id"])
                system = rec.get("origin_system")
                coll = rec.get("collection_name", "")
                o = snapshot_obs(eid, ftime, rec.get("updated_at") or cap, inner, system, coll, "sql")
            else:
                system = None
                o = snapshot_obs(eid, ftime, rec.get("updated_at") or rec.get("indexed_at") or cap, rec,
                                 system, t, "sql")
                if "source_resource_id" in rec and rec["source_resource_id"]:
                    o.extra["source_resource_id"] = rec["source_resource_id"]
                if t == "connector_subscriptions":
                    o.state = "ACTIVE" if rec.get("enabled") in (1, "1") else "DISABLED"
                if t == "backup_restore_entries":
                    o.state = "ACTIVE" if rec.get("restore_allowed") in (1, "1") else "BLOCKED"
            o.table = t
            sql_seen.setdefault(o.system, set()).add(o.resource_id)
            o.extra["topology"] = topo.group(1) if topo else ""
            sys_scope = scope.get(o.system)
            o.extra["scope_time"] = sys_scope or cap
            # snapshot time of the capture is what the state refers to
            o.time = sys_scope or cap
            out.append(o)
    # absence knowledge: a snapshot lists *all* rows of the systems in its scope
    for system in set(scope) | {k for k in sql_seen if k}:
        coverage.append(Coverage(eid, ftime, scope.get(system) or cap, system, sql_seen.get(system, set())))
    con.close()
    return out


# --------------------------------------------------------------------------
# Documents and images: text extraction then entity spotting
# --------------------------------------------------------------------------
def pdf_text(path) -> str:
    try:
        return subprocess.run(["pdftotext", "-layout", path, "-"], capture_output=True, text=True,
                              timeout=60).stdout
    except Exception:
        try:
            from pypdf import PdfReader
            return "\n".join(p.extract_text() or "" for p in PdfReader(path).pages)
        except Exception:
            return ""


def image_text(path, cache_dir=None, stats: dict | None = None) -> str:
    """OCR an image (2x upscaled grayscale, normal and inverted polarity) with Tesseract.

    Dark-theme consoles OCR badly in one polarity and well in the other, so both are read and
    concatenated.  The text is cached on disk, which makes later runs independent of Tesseract."""
    stats = {} if stats is None else stats
    cache = None
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        cache = os.path.join(cache_dir, os.path.basename(path) + ".ocr.txt")
        if os.path.exists(cache):
            stats["ocr_cache_hits"] = stats.get("ocr_cache_hits", 0) + 1
            with open(cache, encoding="utf-8") as fh:
                return fh.read()
    texts = []
    try:
        from PIL import Image, ImageOps
        import tempfile
        img = Image.open(path).convert("L")
        img = img.resize((img.width * 2, img.height * 2))
        variants = [img, ImageOps.invert(img)]
        for v in variants:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
                v.save(tf.name)
                r = subprocess.run(["tesseract", tf.name, "-", "--psm", "11"], capture_output=True,
                                   text=True, timeout=120)
                stats["ocr_calls"] = stats.get("ocr_calls", 0) + 1
                texts.append(r.stdout)
                os.unlink(tf.name)
    except Exception:
        try:
            r = subprocess.run(["tesseract", path, "-"], capture_output=True, text=True, timeout=120)
            stats["ocr_calls"] = stats.get("ocr_calls", 0) + 1
            texts.append(r.stdout)
        except Exception:
            pass
    text = "\n".join(texts)
    if cache:
        with open(cache, "w", encoding="utf-8") as fh:
            fh.write(text)
    return text


@dataclass
class Document:
    evidence_id: str
    file_time: str
    path: str
    kind: str
    text: str
    resources: list = field(default_factory=list)
    emails: list = field(default_factory=list)
    users: list = field(default_factory=list)


def scan_document(eid, ftime, path, kind, cache_dir=None, stats: dict | None = None) -> Document:
    """Extract text, then spot resource ids, e-mails and user ids in it."""
    text = pdf_text(path) if kind == "pdf" else image_text(path, cache_dir, stats)
    # OCR repairs for identifiers: "search 1874" -> search_1874, "data_flow.1121" -> data_flow_1121
    norm = re.sub(r"\b(data[_ ]flow|audit[_ ]record|account|search|support|analytics|export|backup|"
                  r"marketing|tombstone|object)[ ._](\d+)\b",
                  lambda m: m.group(1).replace(" ", "_") + "_" + m.group(2), text)
    doc = Document(eid, ftime, path, kind, norm)
    doc.resources = sorted(set(RESOURCE_RE.findall(norm)))
    doc.emails = sorted(set(e.lower().rstrip(".") for e in EMAIL_RE.findall(norm)))
    doc.users = sorted(set(USER_RE.findall(norm)))
    return doc


# --------------------------------------------------------------------------
# Loader
# --------------------------------------------------------------------------
@dataclass
class EvidenceStore:
    """Everything extracted from ``dataset/evidence`` (via the ``evidence_files.csv`` manifest)."""
    observations: list
    documents: list
    coverage: list
    stats: dict = field(default_factory=dict)


def load_manifest(dataset_dir) -> list[dict]:
    with open(os.path.join(dataset_dir, "evidence_files.csv"), encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


FAMILY = {".json": "json", ".txt": "txt", ".log": "log", ".sql": "sql", ".pdf": "pdf",
          ".png": "image", ".jpg": "image", ".jpeg": "image"}


def load_all(dataset_dir, cache_dir=None) -> EvidenceStore:
    store = EvidenceStore([], [], [], {"files": {}, "missing_files": 0, "parse_failures": 0})
    obs, docs, coverage, stats = store.observations, store.documents, store.coverage, store.stats
    for m in load_manifest(dataset_dir):
        path = os.path.join(dataset_dir, m["file_path"])
        eid, ftime = m["file_id"], m["captured_at"]
        if not os.path.exists(path):
            stats["missing_files"] += 1
            continue
        family = FAMILY.get(os.path.splitext(path)[1].lower())
        if family is None:
            continue
        stats["files"][family] = stats["files"].get(family, 0) + 1
        try:
            if family == "json":
                obs += parse_json(eid, ftime, path, coverage)
            elif family == "txt":
                obs += parse_txt(eid, ftime, path)
            elif family == "log":
                obs += parse_log(eid, ftime, path)
            elif family == "sql":
                obs += parse_sql(eid, ftime, path, coverage)
            else:
                docs.append(scan_document(eid, ftime, path, family, cache_dir, stats))
        except Exception as exc:  # keep going on a malformed artifact
            stats["parse_failures"] += 1
            print(f"[warn] failed to parse {path}: {exc}")
    stats["observations"] = len(obs)
    stats["documents"] = len(docs)
    stats["coverage_listings"] = len(coverage)
    return store
