"""Entry point: dataset -> validated root output.csv.

Usage (from the repository root):
    python code/main.py
    python code/main.py --requests dataset/sample_deletion_requests.csv --output /tmp/sample_pred.csv
    python code/main.py --live-vision   # transcribe screenshots live (needs a provider key)
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.contract import OUTPUT_COLUMNS, natural_key, serialize_steps, validate_case  # noqa: E402
from src.evidence import EvidenceStore  # noqa: E402
from src.load import load_bundle, read_csv_strings, REQUEST_COLUMNS  # noqa: E402
from src.plan import PlanBuilder  # noqa: E402


def build_rows(
    dataset_dir: Path, requests_path: Path, live_vision: bool = False
) -> tuple[list[dict[str, str]], list[str]]:
    bundle = load_bundle(dataset_dir)
    requests = (
        bundle.requests
        if requests_path.resolve() == (dataset_dir / "deletion_requests.csv").resolve()
        else read_csv_strings(requests_path, REQUEST_COLUMNS)
    )
    store = EvidenceStore(bundle.evidence_files, live_vision=live_vision)
    builder = PlanBuilder(bundle, store)
    valid_evidence = bundle.evidence_ids | bundle.activity_ids | bundle.audit_ids

    rows: list[dict[str, str]] = []
    errors: list[str] = []
    for request in sorted(requests, key=lambda item: natural_key(item["case_id"])):
        plan = builder.build(request)
        errors.extend(validate_case(plan.case_id, plan.status, plan.steps, valid_evidence))
        rows.append({
            "case_id": plan.case_id,
            "deletion_status": plan.status,
            "steps_json": serialize_steps(plan.steps),
        })
    return rows, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "dataset")
    parser.add_argument(
        "--requests",
        type=Path,
        default=None,
        help="request CSV; defaults to <dataset>/deletion_requests.csv",
    )
    parser.add_argument("--output", type=Path, default=ROOT / "output.csv")
    parser.add_argument("--allow-invalid", action="store_true", help="write rows even if validation fails")
    parser.add_argument(
        "--live-vision",
        action="store_true",
        help="transcribe screenshot evidence live via the vision provider instead of using the cache",
    )
    args = parser.parse_args()

    requests_path = args.requests or (args.dataset / "deletion_requests.csv")
    rows, errors = build_rows(args.dataset, requests_path, live_vision=args.live_vision)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    counts: dict[str, int] = {}
    for row in rows:
        counts[row["deletion_status"]] = counts.get(row["deletion_status"], 0) + 1
    print(f"wrote {len(rows)} rows to {args.output}")
    print("status counts: " + ", ".join(f"{key}={value}" for key, value in sorted(counts.items())))
    if errors:
        print(f"VALIDATION FAILURES: {len(errors)}", file=sys.stderr)
        for message in errors[:25]:
            print(f"  - {message}", file=sys.stderr)
        if not args.allow_invalid:
            return 1
    else:
        print("validation: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
