"""Run the participant-authored evaluation cases.

Three families are covered:

* ``intent_cases.json``    - is this message an erasure authorisation at all?
* ``ordering_cases.json``  - does the contract ordering produce the required order?
* ``contract_cases.json``  - does the validator reject malformed submission rows?

Run with:

    python code/evaluation/run_extra_cases.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "code") not in sys.path:
    sys.path.insert(0, str(ROOT / "code"))

from src.contract import sort_and_number, validate_case  # noqa: E402
from src.intent import is_deletion_authorized  # noqa: E402

CASES = Path(__file__).resolve().parent / "extra_cases"
VALID_EVIDENCE = {"evidence_01", "evidence_02", "evidence_03"}


def run_intent() -> tuple[int, int]:
    document = json.loads((CASES / "intent_cases.json").read_text(encoding="utf-8"))
    passed = 0
    for case in document["cases"]:
        actual = is_deletion_authorized(case["text"])
        ok = actual is case["expected"]
        passed += ok
        if not ok:
            print(f"  FAIL {case['id']}: expected authorized={case['expected']}, got {actual}")
            print(f"       why: {case['why']}")
    return passed, len(document["cases"])


def run_ordering() -> tuple[int, int]:
    document = json.loads((CASES / "ordering_cases.json").read_text(encoding="utf-8"))
    passed = 0
    for case in document["cases"]:
        steps = [
            {
                "step_no": 0,
                "phase": phase,
                "system_id": system_id,
                "resource_id": resource_id,
                "operation": operation,
                "state": state,
                "evidence_id": "",
            }
            for phase, operation, state, system_id, resource_id in case["input"]
        ]
        ordered = sort_and_number(steps)
        operations = [step["operation"] for step in ordered]
        numbering_ok = [step["step_no"] for step in ordered] == list(range(1, len(ordered) + 1))
        ok = operations == case["expected_operations"] and numbering_ok
        if "expected_resources" in case:
            ok = ok and [step["resource_id"] for step in ordered] == case["expected_resources"]
        passed += ok
        if not ok:
            print(f"  FAIL {case['id']}: order was {operations}")
    return passed, len(document["cases"])


def run_contract() -> tuple[int, int]:
    document = json.loads((CASES / "contract_cases.json").read_text(encoding="utf-8"))
    passed = 0
    for case in document["cases"]:
        steps = [dict(step) for step in case["steps"]]
        errors = validate_case(case["id"], "COMPLETE", steps, VALID_EVIDENCE)
        if not case["expected_valid"]:
            # A malformed row must be rejected for its own reason, not by accident.
            ok = bool(errors)
        else:
            ok = not errors
        passed += ok
        if not ok:
            print(f"  FAIL {case['id']}: expected valid={case['expected_valid']}, errors={errors}")
    return passed, len(document["cases"])


def main() -> int:
    total_passed = total = 0
    for name, runner in (
        ("intent", run_intent),
        ("ordering", run_ordering),
        ("contract", run_contract),
    ):
        passed, count = runner()
        total_passed += passed
        total += count
        print(f"{name:9s} {passed}/{count} passed")
    print(f"overall   {total_passed}/{total} passed")
    return 0 if total_passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
