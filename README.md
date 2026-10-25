# Ghost Data Still Exists — deletion-audit agent

A deletion-audit agent for the *HackerRank Orchestrate (October 2026)* challenge.
For every request in `dataset/deletion_requests.csv` it decides whether the person
actually authorised deletion, reconstructs what is *actually* true of their data at
the case's `audit_at` time, applies the supplied retention policy, and emits an
ordered findings -> remediation -> verification plan.

Output: `output.csv` at the repository root, one row per `case_id`, sorted.

---

## Quick start

```bash
# Python 3.10+ standard library only; no third-party packages, no API keys.
python code/main.py                 # writes ./output.csv for all 518 requests
python code/evaluation/run_all.py   # samples + extra cases + full run + submission checks
```

Optional arguments:

```bash
python code/main.py \
  --dataset dataset \
  --requests dataset/sample_deletion_requests.csv \
  --output code/evaluation/artifacts/pred_sample.csv
```

A full 518-case run takes roughly 40 seconds on a laptop. The run is deterministic:
the same inputs always produce byte-identical `output.csv` (no sampling, no
timestamps, stable iteration and sort order everywhere).

---

## Architecture

```
code/
├── main.py                      CLI: dataset -> validated output.csv
├── src/
│   ├── load.py                  strict CSV loading + evidence manifest checks
│   ├── contract.py              step schema, allowed triples, ordering, validator
│   ├── identity.py              current + historical identifier resolution
│   ├── evidence.py              hash-cached extraction of every evidence artefact
│   ├── state.py                 resource-state reconstruction at audit_at
│   ├── policy.py                deletion_rules.csv -> required treatment/compliance
│   ├── intent.py                multilingual request-authorisation classification
│   └── plan.py                  findings/remediation/verification assembly
├── evidence_cache/images.json   vision transcription cache for screenshots
└── evaluation/                  scoring harness and participant-authored cases
    ├── evaluate.py              status, step, field and sequence metrics
    ├── baseline.py              trivial baseline for the metrics to beat
    ├── run_extra_cases.py       intent / ordering / contract case runner
    ├── run_all.py               one command that reproduces every artefact
    ├── extra_cases/             the case files themselves
    └── artifacts/               generated predictions and metrics

evaluation/
├── evaluation_report.md         metrics, custom cases, failures and fixes
└── usage_report.md              model usage, tokens and cost
```

### Pipeline

1. **Intent** (`intent.py`). Erasure is authorised unless the message contains an
   explicit refusal of deletion (`do not delete`, `not asking for deletion`, ...) or
   an explicit retention instruction (`leave the account active`,
   `without removing any of my records`, ...). Erasure follow-ups often contain no
   deletion verb at all ("confirm that the profile is gone"), so a missing verb is
   not treated as evidence of non-deletion, while incidental negatives
   ("systems that do not display the email") never suppress a real request.
   Non-authorised requests become `NOT_APPLICABLE` with `steps_json=[]`.

2. **Identity** (`identity.py`). The request email is resolved against the current
   snapshot *and* `user_audit_log.csv`, so historical emails, phones and names keep
   matching old systems and old evidence. The email hash is recomputed as SHA-256 of
   the canonical address.

3. **Evidence** (`evidence.py`). Every artefact in `evidence_files.csv` is parsed
   once, cached by SHA-256 of its bytes: JSON records and snapshot bundles, SQLite
   dumps (read through an in-memory SQLite database), application logs, pipe-delimited
   PDF/text records, and screenshots through a hash-keyed vision transcription cache.
   Two mechanisms do the heavy lifting:
   * **trace propagation** — a `*_started` line names the subject for a `trace_id`
     and the following `*_completed` line names the resource, so a subject-less
     resource row is still attributed to the right person; the `*_started` line
     itself is never used as proof of state;
   * **replica normalisation** — `support_replica`, `analytics_read`, ... map onto the
     logical service, because a leftover replica row is a real leftover copy.
   Unparseable artefacts are recorded in `parse_failures`, never silently dropped.

4. **State** (`state.py`). For each resource, the latest capture at or before
   `audit_at` decides the state; ties inside one capture break on the observation's
   own timestamp, and a surviving/enabled copy outranks a negative result from a
   sibling replica. Workflow statuses and activity "SUCCESS" claims are never proof.
   Ownership (which resource belongs to the subject) may be established by later
   captures, because ownership is durable while state is not.

5. **Policy** (`policy.py`). Treatment is read from `deletion_rules.csv` at runtime.
   Compliance compares the *effective* state to the treatment: an analytics or
   support row that no longer carries a direct identifier is already
   `ANONYMIZED`/`REDACTED` in substance even though the row exists.

6. **Plan** (`plan.py`). Non-compliant resources produce findings and remediation;
   verification always covers the required final state per resource. Findings cite
   participant-visible evidence ids (`file_id`, `activity_id`, `audit_id`); future
   remediation and post-remediation verification steps carry `evidence_id=""`.
   Steps are deduplicated, contract-sorted and numbered from 1.

---

## Policy and decisions taken

* `deletion_rules.csv` is the only source of required treatment. If a category has
  no policy row, the engine applies a privacy-conservative `DELETE` and records the
  derived rule (`PolicyEngine.derived_rules`) so the judgement is visible.
* Support records split on content: a row holding a conversation excerpt or a
  direct-identifier scanner hit is a `SUPPORT_MESSAGE` (retain + redact), while a
  support artefact with no conversation content is a `SUPPORT_ATTACHMENT` (delete).
* `ACCOUNT_TOMBSTONE` keeps the stable `user_id` reference and must lose name,
  email, phone and hash; the leaked-identifier test decides `REDACTED` vs `PRESENT`.
* An account that was removed from every snapshot survives only as a workflow
  reference; the agent then falls back to the identifier derived from the stable
  user reference, preferring one the activity stream actually records.
* When a subject has several account records (an old deleted profile plus the live
  one), only the most recent capture's account is audited.

## Known limitations

* Screenshots cannot be OCR'd by the standard library. Nine of the eleven supplied
  screenshots have a transcription in `code/evidence_cache/images.json`; the two
  that only expose page-level references without resource ids
  (`object_storage_browser_20260213.png`, `deleted_account_reference_20260310.png`)
  are reported as extraction gaps. A different image will invalidate its cache entry
  by hash and be reported rather than mis-read.
* Some evidence captures are cohort-wide and list a subject's resource without any
  identifier on that row (for example a 404 `profile_lookup` with `profile: null`).
  Where no other capture links such a row to a person, the agent cannot attribute it
  and says so by omitting the step instead of inventing a resource id.

---

## Evaluation

See `evaluation/evaluation_report.md` (metrics, custom cases, failures found and
fixes made) and `evaluation/usage_report.md` (model calls, tokens, cost).

```bash
python code/evaluation/evaluate.py \
  --expected dataset/sample_output.csv \
  --predicted code/evaluation/artifacts/pred_sample.csv
```

## Secrets

The agent needs no credentials. Configuration is read from the environment only:

| Variable | Purpose |
|---|---|
| `EVIDENCE_VISION_CACHE` | optional path to an alternative screenshot transcription cache |
| `OPENCODE_API_KEY` | provider key required only for live vision transcription (`--live-vision`) or cache rebuilds |

`.env.example` documents the variable; no `.env` file is required or included.

## Vision evidence (screenshots)

Eleven evidence artefacts are screenshots. The standard library cannot OCR them,
so their contents were transcribed once into a hash-keyed cache:

```
code/evidence_cache/images.json
```

Each entry is keyed by the SHA-256 of the image bytes and stores only raw extracted
content (resource reference, record type, displayed state, direct identifiers,
timestamp as shown) — never case IDs, case-level decisions, or expected labels.

Two screenshots expose only page-level references with no resource id in the
evidence vocabulary, so they are intentionally absent from the cache and reported
as extraction gaps rather than mis-read:

* `deleted_account_reference_20260310.png`
* `object_storage_browser_20260213.png`

### Regenerating the cache

`code/tools/build_vision_cache.py` regenerates the cache from the images in
`dataset/evidence/images/`. It is idempotent (skips already-cached SHA-256 hashes)
and reads the provider key from `OPENCODE_API_KEY` only:

```
# List images that would be transcribed; makes no model calls:
python code/tools/build_vision_cache.py --dry-run

# Regenerate (requires the provider key):
OPENCODE_API_KEY=... python code/tools/build_vision_cache.py
```

The model/provider are declared in `config/models.yaml` (`deepseek-v4.1-flash-free`
via `opencode`). No model router endpoint is wired, so a live regeneration requires
implementing `transcribe_image_live` against the configured endpoint.

### Live vision at runtime

`python code/main.py --live-vision` bypasses the cache and attempts live
transcription of screenshots. Without `OPENCODE_API_KEY` it errors clearly; with a
wired router it transcribes uncached images on the fly. The graded (default) run
uses the cache and makes zero model calls.


## Submission contents

`code.zip` contains `code/`, `evaluation/` (both reports), `README.md`, `NOTES.md` and
`.env.example`. It deliberately excludes `dataset/`, `output.csv`, generated
prediction artifacts, virtual environments and any credentials.
