# Investigation Notes

This file is an append-only engineering notebook for the deletion-audit solution.
It records only aggregate dataset observations and fictional resource examples; it does
not contain credentials or copied customer identities.

## 2026-10-25 — Exploration gate

### Repository and logging

- `README.md`, `AGENTS.md`, `CLAUDE.md`, `problem_statement.md`, and
  `REQUIREMENTS.md` were read before implementation.
- README requires the transcript at root `log.txt`; `AGENTS.md` supplies the required
  append-only session and turn-entry format. Root `log.txt` was created and the
  required session start entry was appended.
- The starter entry points were empty. The implementation will be standard-library
  Python, so a clean run does not require an API key or model provider.

### CSV profile

All CSVs were loaded as strings during profiling. There are no duplicate full rows.

| File | Rows | Key discovery |
| --- | ---: | --- |
| `deletion_requests.csv` | 518 | IDs run from `case_33` through `case_550`; request timestamps and audit cutoffs are ISO-8601 UTC; reported states are `COMPLETE` (254), `PARTIALLY_COMPLETE` (152), and `NOT_STARTED` (112). |
| `sample_deletion_requests.csv` / `sample_output.csv` | 32 / 32 | Samples cover 10 complete and 22 incomplete cases. They contain no `NOT_APPLICABLE` sample, so intent handling needs additional rule-derived tests. |
| `user_identities.csv` | 850 | Stable `user_id`, name, email, phone, SHA-256-sized email hash, and `updated_at`. |
| `user_audit_log.csv` | 306 | 196 users have audit history; fields are `email`, `email_hash`, and `phone`; audit IDs are valid evidence citations. |
| `system_activity.csv` | 4,011 | 17 event types, 3 reported results (`SUCCESS`, `FAILED`, `PARTIAL`); it is useful context but not state proof. |
| `deletion_rules.csv` | 15 | 15 categories, with 7 DELETE, 3 REDACT, 3 RETAIN, and 2 ANONYMIZE rules. |
| `evidence_files.csv` | 94 | Every manifest entry exists and there are no unlisted evidence files. |

The exact policy categories are `USER_PROFILE`, `SECONDARY_IDENTIFIER`,
`SEARCH_INDEX_ENTRY`, `CACHE_ENTRY`, `SUPPORT_MESSAGE`, `SUPPORT_ATTACHMENT`,
`ANALYTICS_PROFILE`, `PRODUCT_TELEMETRY`, `USER_EXPORT`, `MARKETING_PROFILE`,
`EMAIL_DELIVERY_LOG`, `SECURITY_AUDIT_LOG`, `LEGAL_HOLD_RECORD`,
`BACKUP_ARCHIVE`, and `ACCOUNT_TOMBSTONE`. Policy actions will be read at runtime;
they will not be encoded as category-to-action constants.

### Evidence profile

- 94 files: 28 JSON, 26 log, 24 SQL, 11 PNG, 4 PDF, and 1 text file.
- Structured evidence uses repeatable forms: JSON resource exports/snapshot bundles,
  SQL inventory snapshots/read replicas, and readback logs. SQL snapshots have
  explicit tables for profiles, search, analytics, support, exports, connectors,
  backup restore entries, and security audit records.
- `pdftotext` is installed and can extract PDFs. No local OCR executable is present;
  the pipeline must record image extraction gaps rather than silently treating image
  evidence as absent. It provides an optional, environment-configured VLM hook only
  if one is supplied later; the submitted run is deterministic with zero model calls.
- Each evidence object will be cache-indexed by SHA-256 file content, with source
  `file_id` and capture time retained. Capture time after a case's `audit_at` is
  excluded.

### Sample-output study (all 32 rows)

I inspected each sample request and its expected step array. The reasoning patterns
are:

| Sample cases | Pattern demonstrated |
| --- | --- |
| 01–02 | A point-in-time account read proves the sole profile is absent: complete with one absent verification. |
| 03–04 | Complete requires every known relevant state: absent account plus policy-retained security record and/or anonymized analytics profile. |
| 05 | A currently present account profile is a DELETE-policy violation. |
| 06–08 | A single remaining search, support attachment, or export record is a DELETE-policy violation while an already absent account is still verified. |
| 09, 14, 17 | A backup record may be retained, but `restore_allowed=true` is a restore risk; block restore before post-remediation checks. |
| 10 | Historical-identifier matching reaches a remaining support record. |
| 11, 18, 29–30 | A combined case can have DELETE, REDACT, ANONYMIZE, and active-flow violations at once. The flow is disabled before record treatment. |
| 12 | Resource type, not a resource-ID prefix, determines policy: an `analytics_` ID can still be a DELETE-policy record. |
| 13, 16, 20, 22–24 | Multiple remaining resources are all reported/remediated and compliant account/security records appear in final verification. |
| 15 | An enabled connector is independently a data-flow violation. |
| 19 | A retained support conversation with direct identifiers is REDACT, not DELETE. |
| 21, 25–28 | Complete cases prove absent account, retained security audit record, anonymized/redacted records, disabled flow, and blocked restore as applicable. |
| 31–32 | Account tombstones retain a stable user reference only. Direct identifiers yield exposed PII and require REDACT. |

Confirmed serialization pattern: every step has exactly seven keys; only array order
matters; incomplete findings cite the proving item while their remediation and future
verification steps use `""`; complete cases contain verification steps only and cite
the proving evidence on every step.

### Resolved verification/open-question log

| Requirement question | Resolution |
| --- | --- |
| Ascending ID sort | Evaluation case IDs are unpadded (`case_33`…`case_550`) and already numerically ascending but not lexicographically ascending. The solution uses a natural numeric-suffix key. Sample resource ties do not distinguish the two, so this is the only data-supported unambiguous ordering. |
| Email hash algorithm/normalization | All 850 identity hashes equal `sha256(email.encode('utf-8'))`; stored emails are already lowercase/trimmed, so their data cannot distinguish raw from lower/trim variants. Resolution uses Unicode trim+casefold for matching and SHA-256 of the canonical lowercase email for identifier search; no plus-address rewrite is used. |
| Policy columns/categories/actions | Confirmed above: columns are `rule_id,data_category,condition,required_action`; all 15 exact category/action rows are read at runtime. |
| Activity success/failure and flows | `reported_result` is exactly `SUCCESS`/`FAILED`/`PARTIAL`; flow-related event types include `CHECK_DATA_FLOW` and `DISABLE_DATA_FLOW`. These are claims only. Evidence reads of `enabled=true/false` and `restore_allowed=true/false` establish state. |
| Evidence-ID source | Every expected citation is an existing manifest `file_id` in the samples. The contract also permits activity/audit IDs; the validator will check all three source registries. Record findings use the direct observation's evidence file, never an invented ID. |
| Relevant retained copies | Security and legal retention records must remain and be verified present. Backup archives may remain only with restore blocked; the verification is `VERIFY_RESTORE_BLOCKED`, as the samples show. Tombstones retain only their stable subject reference after redaction. |
| Transcript format/path | Fully specified by README and AGENTS: root `log.txt`, append-only, with session start and per-turn sections. |
| Model/API availability | No challenge API/model key is provided in the repository. The runnable submitted path is deterministic and uses zero model calls; optional unstructured-image extraction cannot run without an explicitly supplied provider configuration. |

### Resolved assumptions

- **Confirmed:** the latest direct observation at or before `audit_at` is stronger than
  earlier activity claims for the same resource. Ties inside one capture break on the
  observation's own timestamp, and a surviving/enabled copy outranks a negative result
  from a sibling replica.
- **Confirmed:** identifiers in `lookup_key`, `subject_reference`, `customer_reference`,
  `correlation_key`, `subject_ref`/`subject_key`, email, phone and email hash ground a
  resource to a resolved user. Similar-address text alone is not a match.
- **Confirmed:** ownership (which resource belongs to whom) may be established by a
  capture taken after `audit_at`, because ownership is durable; the resource's *state*
  must come only from admissible evidence.
- **Resolved:** the evaluator was built first and every change since has been driven by
  its per-case diffs; see `code/evaluation/evaluation_report.md` §3–§4.

### Evidence-linkage mechanics discovered

| Mechanism | Where it appears | Handling |
| --- | --- | --- |
| `trace_id` pairs a `*_started` line (names the subject) with a `*_completed` line (names the resource) | application logs | two-pass parse: harvest `trace_id → subject` from any line, apply to resource rows, never treat `*_started` as proof of state |
| `correlation_key` holds the subject reference or an email hash | security/analytics rows | treated as a linkage value; a hash value also counts as exposed direct data |
| replica node names (`support_replica`, `*_read`, `*_primary`) | replica logs/SQL | canonicalised onto the logical service before planning |
| cohort-wide captures list a resource with a null identity (`profile: null`, `status_code: 404`) | snapshot exports | unattributable: no step is emitted rather than inventing a resource id |
| screenshots | 11 PNG artefacts | hash-keyed vision transcription cache in `code/evidence_cache/images.json`; missing entry = reported extraction gap |

### Trust boundary for evidence

State is reconstructed only from artefacts whose `captured_at` is at or before the case's
`audit_at`, except for record categories the policy requires to be *retained* (security
audit trail, legal hold). A retention record that appears only in a later capture is
created once and kept by policy, so its continued existence cannot change a deletion
decision; it is included so the plan verifies that the required record still exists.

### Vision cache (screenshot evidence)

- Cache file: `code/evidence_cache/images.json`, keyed by SHA-256 of image bytes.
- Schema (per entry): `file_name` + `observations[]`, where each observation holds
  `system_id`, `resource_id`, `category`, `state`, `observed_at`, `emails[]`,
  `phones[]`, `names[]`, `references[]`, `pii`. Only raw extracted content — no
  case IDs, case-level decisions, or expected labels.
- Model that produced the transcriptions: `deepseek-v4.1-flash-free` via provider
  `opencode` (the same conversation model read the images directly during
  development; no separate vision API was called).
- Regeneration tool: `code/tools/build_vision_cache.py --dry-run` lists uncached
  images; with the provider key in `OPENCODE_API_KEY` it (re)writes the cache,
  skipping already-cached hashes (idempotent).
- Runtime: `code/main.py --live-vision` bypasses the cache and attempts live
  transcription; without a provider key it errors clearly. A missing cache entry
  is reported as a warning naming the file, never silently skipped.
- Two screenshots have no cache entry because they expose only page-level
  references (object file names, business record numbers such as `ORD-20263245`)
  with no resource id in the evidence vocabulary: they are reported as extraction
  gaps (`deleted_account_reference_20260310.png`, `object_storage_browser_20260213.png`).

### Final status

The one-command pipeline (`python code/evaluation/run_all.py`) scores 100% case-status
accuracy and 90.4% step F1 on the published samples, passes 28/28 participant-authored
evaluation cases, regenerates `output.csv` for all 518 requests, and passes every
submission check (row count, sort order, schema, allowed operations, evidence
discipline).
