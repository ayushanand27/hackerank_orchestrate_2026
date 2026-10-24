# Usage report — final full-dataset run

Run that produced the submitted `output.csv`:

```bash
python3 code/main.py      # dataset/deletion_requests.csv -> output.csv (518 rows)
```

## Model usage

The agent is **fully deterministic and makes no LLM / VLM / API calls**. This was a deliberate
design decision: every output field is derived from participant data by explicit rules, so the
run is reproducible bit-for-bit and cannot hallucinate resources, states or evidence ids.

| provider | model | model calls | input tokens | output tokens | total tokens | est. cost (USD) |
|---|---|---|---|---|---|---|
| — (none) | — | 0 | 0 | 0 | 0 | 0.00 |
| **overall** | | **0** | **0** | **0** | **0** | **0.00** |

| per request (518) | value |
|---|---|
| average model calls | 0 |
| average tokens (input + output) | 0 |
| estimated cost per request | $0.00 |

## Local compute actually used

| component | what it does | calls in the final run | notes |
|---|---|---|---|
| Tesseract OCR 5.3.4 (local, LSTM engine) | reads the 11 console screenshots | 22 (11 images × normal + inverted polarity) on a cold run; 0 on a warm run (cached in `code/.cache/`) | no network |
| pdftotext (poppler) | text of 4 PDFs | 4 | `pypdf` fallback |
| sqlite3 (stdlib) | loads 21 SQL snapshots in memory | 21 | |
| Python 3.13 | parsing, attribution, planning | — | |

| run | wall time | output |
|---|---|---|
| warm (OCR cache present) | ≈ 6.4 s for 518 requests (≈ 12 ms / request) | `output.csv` |
| cold (OCR from scratch) | ≈ 55 s | byte-identical `output.csv` |

Determinism check: the cold and warm runs were compared with `cmp` and are identical.

## Development-time AI usage (not part of the inference pipeline)

The solution was written with an AI coding assistant (Claude, via the claude.ai app) — see
`log.txt` / the agent trace for the full conversation. That assistant was used to read the
dataset, form hypotheses and write code; it is not called by `code/main.py`.
