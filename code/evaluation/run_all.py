"""One command that reproduces every graded artifact and check.

    python code/evaluation/run_all.py

Stages:
  1. sample evaluation  - score the pipeline against the published sample answers
  2. extra cases        - participant-authored intent, ordering and contract cases
  3. full run           - regenerate output.csv for all 518 requests
  4. submission checks  - row count, sorting, schema, allowed operations, evidence ids

Every stage is deterministic; no network access and no model credentials are used.
"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CODE = ROOT / "code"
ARTIFACTS = CODE / "evaluation" / "artifacts"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(CODE))

from src.contract import ALLOWED_TRIPLES, OUTPUT_COLUMNS, STEP_FIELDS, natural_key  # noqa: E402


def run(command: list[str]) -> int:
    print(f"\n$ {' '.join(command)}")
    return subprocess.call(command, cwd=ROOT)


def submission_checks() -> int:
    """Validate the produced output.csv against the graded contract."""
    requests = list(csv.DictReader(open(ROOT / "dataset" / "deletion_requests.csv", encoding="utf-8")))
    expected_ids = [row["case_id"] for row in requests]
    path = ROOT / "output.csv"
    reader = csv.DictReader(open(path, encoding="utf-8", newline=""))
    failures: list[str] = []

    if tuple(reader.fieldnames or ()) != OUTPUT_COLUMNS:
        failures.append(f"header is {reader.fieldnames}, expected {OUTPUT_COLUMNS}")
    rows = list(reader)
    if len(rows) != len(expected_ids):
        failures.append(f"{len(rows)} data rows, expected {len(expected_ids)}")

    seen = [row["case_id"] for row in rows]
    if len(set(seen)) != len(seen):
        failures.append("duplicate case_id rows")
    if seen != sorted(seen, key=natural_key):
        failures.append("rows are not sorted by case_id")
    missing = set(expected_ids) - set(seen)
    extra = set(seen) - set(expected_ids)
    if missing:
        failures.append(f"missing cases: {sorted(missing)[:10]}")
    if extra:
        failures.append(f"unknown cases: {sorted(extra)[:10]}")

    status_counts: dict[str, int] = {}
    for row in rows:
        status = row["deletion_status"]
        status_counts[status] = status_counts.get(status, 0) + 1
        if status not in {"COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"}:
            failures.append(f"{row['case_id']}: invalid status {status!r}")
        try:
            steps = json.loads(row["steps_json"])
        except json.JSONDecodeError as exc:
            failures.append(f"{row['case_id']}: steps_json is not valid JSON ({exc})")
            continue
        if not isinstance(steps, list):
            failures.append(f"{row['case_id']}: steps_json must be a JSON array")
            continue
        if status == "NOT_APPLICABLE" and steps:
            failures.append(f"{row['case_id']}: NOT_APPLICABLE must have steps_json=[]")
        if status in {"COMPLETE", "INCOMPLETE"} and not steps:
            failures.append(f"{row['case_id']}: {status} case has no steps")
        if [step.get("step_no") for step in steps] != list(range(1, len(steps) + 1)):
            failures.append(f"{row['case_id']}: step numbers are not consecutive from 1")
        phases = [step.get("phase") for step in steps]
        if phases != sorted(phases, key=lambda name: {"FINDING": 0, "REMEDIATION": 1, "VERIFICATION": 2}.get(name, 9)):
            failures.append(f"{row['case_id']}: phase order is not FINDING/REMEDIATION/VERIFICATION")
        if status == "COMPLETE" and any(step.get("phase") != "VERIFICATION" for step in steps):
            failures.append(f"{row['case_id']}: COMPLETE contains non-verification steps")
        for step in steps:
            if tuple(step.keys()) != STEP_FIELDS:
                failures.append(f"{row['case_id']}: step fields {tuple(step.keys())}")
                break
            if (step["phase"], step["operation"], step["state"]) not in ALLOWED_TRIPLES:
                failures.append(
                    f"{row['case_id']}: illegal phase/operation/state "
                    f"{step['phase']}/{step['operation']}/{step['state']}"
                )
                break

    print("\nsubmission checks")
    print(f"  rows                : {len(rows)} (expected {len(expected_ids)})")
    print(f"  status distribution : {dict(sorted(status_counts.items()))}")
    if failures:
        print(f"  RESULT: FAIL ({len(failures)} problems)")
        for message in failures[:20]:
            print(f"    - {message}")
        return 1
    print("  RESULT: PASS")
    return 0


def main() -> int:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    status = 0

    status |= run([
        sys.executable, "code/main.py",
        "--requests", "dataset/sample_deletion_requests.csv",
        "--output", "code/evaluation/artifacts/pred_sample.csv",
    ])
    status |= run([
        sys.executable, "code/evaluation/evaluate.py",
        "--expected", "dataset/sample_output.csv",
        "--predicted", "code/evaluation/artifacts/pred_sample.csv",
        "--output-json", "code/evaluation/artifacts/pred_all.json",
    ])

    status |= run([sys.executable, "code/evaluation/run_extra_cases.py"])
    status |= run([sys.executable, "code/main.py"])
    status |= submission_checks()

    print("\noverall:", "PASS" if status == 0 else "FAIL")
    return 0 if status == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
