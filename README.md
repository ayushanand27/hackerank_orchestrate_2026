# Ghost Data Still Exists — deterministic deletion-audit agent

HackerRank Orchestrate (October 2026). The agent decides, for every request in
`dataset/deletion_requests.csv`, whether a deletion is `COMPLETE`, `INCOMPLETE` or
`NOT_APPLICABLE`, and emits the ordered findings → remediation → verification plan.

**Design choice: no LLM at inference time.** Every decision is a traceable function of the
supplied files, so the same input always yields the same `output.csv` and nothing can be
hallucinated. Screenshots are read with local OCR (Tesseract); PDFs with `pdftotext`/`pypdf`.

## Quick start

```bash
# from the repository root
pip install -r code/requirements.txt          # pillow, pypdf (tesseract + poppler are system tools)
python3 code/main.py                          # -> ./output.csv  (518 rows, ~6 s)
python3 code/evaluation/main.py               # sample + custom-case metrics + contract checks
python3 code/main.py --explain case_44        # reasoning trail for one case
```

System tools: `tesseract` (OCR, only needed for the 11 screenshots) and `pdftotext`
(poppler; `pypdf` is used as a fallback). OCR text is cached in `code/.cache/`, so a run on a
machine without Tesseract still reproduces the submitted output.

## Architecture

```
code/
├── main.py                    entry point: loads data, joint attribution pass, writes output.csv
├── ghost_audit/
│   ├── logparse.py            one parser for 7 log layouts (bracket, pipe, logfmt, JSON, syslog, nested)
│   ├── evidence.py            file adapters -> normalised Observation(resource, system, category,
│   │                          state, identifiers, time, evidence_id); SQL via sqlite3; PDF/OCR scan
│   ├── identity.py            current + historical e-mails, phones, e-mail hashes, user_id
│   ├── intent.py              multilingual rule classifier: does the text authorise deletion?
│   ├── policy.py              deletion_rules.csv -> required end state; step vocabulary + ordering
│   └── engine.py              linking, attribution, point-in-time state, planning
└── evaluation/
    ├── main.py                evaluation workflow (sample, custom scenarios, contract validation)
    ├── metrics.py             status / step / field / sequence metrics
    ├── custom_cases.py        5 synthetic scenarios with hand-written expected answers
    ├── evaluation_report.md
    └── usage_report.md
```

### Pipeline per request

1. **Intent** – erasure verbs in EN/ES/PT/FR/DE/JA/KO/ZH vs. explicit refusals
   ("not asking for deletion", "keep the account", "marketing lists only"). Non-deletion → `NOT_APPLICABLE`, `[]`.
2. **Identity** – resolve the request e-mail (current *or* historical) to a `user_id`, then
   collect every e-mail, phone, e-mail hash and the user id from `user_identities.csv` +
   `user_audit_log.csv`. Matching is exact (case-insensitive e-mail, digits-only phone), so
   look-alike decoys such as `name.jr@…` are never attributed.
3. **Evidence → observations** – every artifact becomes observations of the form
   *"at time T, file F saw resource R of system S in state X with identifiers I"*:
   snapshot bundles, service exports, lookup ledgers, hand-over notes, SQL snapshots
   (primary, regional, reporting, read and failover replicas are all separate observations),
   logs (trace-id joins the lookup key to the completion event), PDFs and OCR'd screenshots.
4. **Linking** – a resource belongs to the case if an observation carries one of the subject's
   identifiers, a `LINK_RESOURCE_IDENTITY` catalogue event names the subject, a document shows
   the subject together with the resource, or an *active connector / restore* re-populated it
   from a linked resource (lineage).
5. **Attribution of identifier-free records** (deleted profiles return 404 without an e-mail,
   anonymised analytics have no identifiers). The privacy workflow leaves a reproducible
   timing signature in `system_activity.csv`:
   * the primary profile is submitted 45–75 min before the reported status, with the worker
     result mirroring the reported status (`COMPLETE→SUCCESS`, `PARTIALLY_COMPLETE→PARTIAL`,
     `NOT_STARTED→FAILED`);
   * inside an audit batch, consecutive requests advance that offset by a constant cadence —
     learnt per batch from the unambiguous cases and used to resolve ties;
   * the case's follow-up "policy action completed" events share the seconds-phase of its
     primary submission (slot k: +40.5 s + 29.5 s·k), which ties anonymised analytics and
     retained legal holds to exactly one case. 246/247 checkable policy events agree.
6. **State at `audit_at`** – only observations at or before `audit_at` and after the request
   count (retained records excepted); the latest one wins, so "old reports" and
   post-audit recreations are both ignored. A complete SQL / snapshot listing from the case's
   own evidence batch also proves absence by omission. Workflow results are treated as claims.
7. **Policy** – `deletion_rules.csv` maps each category to DELETE / REDACT / ANONYMIZE / RETAIN;
   data flows must be disabled and backups restore-blocked. The worker's own operation
   (`DELETE_RECORD` vs `REDACT_RECORD` on a support item) disambiguates attachment vs message.
8. **Planning** – findings, then remediation (disable flows → block restores → delete →
   redact → anonymise), then verification; within a phase by the operation table order, then
   `system_id`, `resource_id`. Remediation and post-remediation checks cite `""`; `COMPLETE`
   verifications cite the in-time evidence that proves the state (files captured after
   `audit_at` are never cited).

## Results (see `code/evaluation/evaluation_report.md`)

| set | status acc. | exact rows | step F1 |
|---|---|---|---|
| public sample (32) | 100 % | 29/32 | 0.992 |
| custom scenarios (5) | 100 % | 5/5 | 1.000 |

The three non-exact sample rows reference resource ids that appear nowhere in the
participant data (e.g. `audit_record_6645`), so they are not recoverable from evidence.

## Notes

* `output.csv` at the repository root is the submission file.
* No API keys, no network access, no organizer-only data. The public sample answers are used
  only by the evaluation workflow, never by the agent.
