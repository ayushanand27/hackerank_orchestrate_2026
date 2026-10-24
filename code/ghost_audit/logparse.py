"""Format-agnostic parser for the heterogeneous service logs.

Every evidence log line starts with an ISO timestamp, followed by one of several
layouts (bracketed, pipe-separated, logfmt, JSON, syslog, nested cloud-runtime
messages).  ``parse_line`` normalises all of them into a flat ``dict`` with the
keys ``ts``, ``service``, ``trace``, ``event`` plus every key/value attribute.
"""
from __future__ import annotations

import json
import re

TS_RE = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)\s+(.*)$")
KV_RE = re.compile(r'([A-Za-z_][\w.]*)=("(?:[^"\\]|\\.)*"|\S+)')
BRACKET_RE = re.compile(r"^\[(\w+)\]\s+\[([\w-]+)\]\s+\[([^\]]+)\]\s+(\w+):\s*(.*)$")
PIPE_RE = re.compile(r"^(\w+)\s*\|\s*([\w-]+)\s*\|\s*([^|]+?)\s*\|\s*(\w+)\s*\|\s*(.*)$")
SYSLOG_RE = re.compile(r"^<\d+>\d+\s+(\S+)\s+(\S+)\s+(\S+)\s+-\s+-\s+(.*)$")


def _unquote(v: str) -> str:
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        v = v[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return v


def parse_kv(text: str) -> dict:
    out = {}
    for k, v in KV_RE.findall(text):
        out[k] = _unquote(v)
    return out


def parse_line(line: str) -> dict | None:
    m = TS_RE.match(line.strip())
    if not m:
        return None
    ts, rest = m.group(1), m.group(2).strip()
    if rest.startswith("|"):
        rest = rest[1:].strip()
    rec: dict = {"ts": ts}
    if rest.startswith("{"):
        try:
            obj = json.loads(rest)
            rec.update({k: (v if v is None else str(v)) for k, v in obj.items()})
            rec.setdefault("service", obj.get("logger"))
            rec["trace"] = obj.get("trace_id") or obj.get("instance")
            return rec
        except json.JSONDecodeError:
            pass
    bm = BRACKET_RE.match(rest)
    if bm:
        rec.update(level=bm.group(1), service=bm.group(2), trace=bm.group(3), event=bm.group(4))
        rec.update(parse_kv(bm.group(5)))
        return rec
    pm = PIPE_RE.match(rest)
    if pm:
        rec.update(level=pm.group(1), service=pm.group(2), trace=pm.group(3).strip(), event=pm.group(4))
        rec.update(parse_kv(pm.group(5)))
        return rec
    sm = SYSLOG_RE.match(rest)
    if sm:
        rec.update(node=sm.group(1), service=sm.group(2), trace=sm.group(3))
        rec.update(parse_kv(sm.group(4)))
        return rec
    kv = parse_kv(rest)
    if "message" in kv and "=" in kv["message"]:
        inner = parse_kv(kv.pop("message"))
        kv.update(inner)
    rec.update(kv)
    rec.setdefault("service", kv.get("function") or kv.get("service"))
    if "function" in kv and "service" not in kv:
        rec["service"] = kv["function"]
    rec["trace"] = kv.get("trace_id") or kv.get("requestId") or kv.get("instance")
    return rec
