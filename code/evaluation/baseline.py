"""Write a deliberately trivial, schema-valid baseline for evaluator smoke tests."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--requests", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with args.requests.open("r", encoding="utf-8-sig", newline="") as source:
        requests = list(csv.DictReader(source))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as destination:
        writer = csv.DictWriter(destination, fieldnames=("case_id", "deletion_status", "steps_json"))
        writer.writeheader()
        for row in requests:
            writer.writerow({"case_id": row["case_id"], "deletion_status": "NOT_APPLICABLE", "steps_json": "[]"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
