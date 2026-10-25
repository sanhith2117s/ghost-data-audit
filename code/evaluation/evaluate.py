"""Evaluate deletion-audit predictions against an oracle CSV.

This module deliberately uses only the Python standard library so evaluation remains
available in a clean submission environment.  It compares semantic step content,
separate from order, and writes machine-readable per-case diffs for error-driven work.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


STATUSES = ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE")
STEP_FIELDS = (
    "phase",
    "system_id",
    "resource_id",
    "operation",
    "state",
    "evidence_id",
)


def _natural_case_key(case_id: str) -> tuple[str, int, str]:
    """Sort IDs with a terminal number numerically, matching the source request order."""
    match = re.match(r"^(.*?)(\d+)$", case_id)
    if not match:
        return (case_id, -1, case_id)
    return (match.group(1), int(match.group(2)), case_id)


def _step_token(step: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(step.get(field, "")) for field in STEP_FIELDS)


def _load_rows(path: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"case_id", "deletion_status", "steps_json"}
        if set(reader.fieldnames or ()) != required:
            raise ValueError(f"{path} must have exactly {sorted(required)}, got {reader.fieldnames}")
        for row in reader:
            case_id = row["case_id"]
            if case_id in rows:
                raise ValueError(f"{path}: duplicate case_id {case_id}")
            try:
                steps = json.loads(row["steps_json"])
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}: {case_id} has invalid steps_json: {exc}") from exc
            if not isinstance(steps, list):
                raise ValueError(f"{path}: {case_id} steps_json must be an array")
            if not all(isinstance(step, dict) for step in steps):
                raise ValueError(f"{path}: {case_id} has a non-object step")
            rows[case_id] = {"status": row["deletion_status"], "steps": steps}
    return rows


def _levenshtein(left: list[tuple[str, ...]], right: list[tuple[str, ...]]) -> int:
    """Levenshtein distance over semantic steps; intentionally independent of step_no."""
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for i, item in enumerate(left, start=1):
        current = [i]
        for j, other in enumerate(right, start=1):
            current.append(min(
                previous[j] + 1,
                current[j - 1] + 1,
                previous[j - 1] + (item != other),
            ))
        previous = current
    return previous[-1]


def _counter_difference(
    expected: Iterable[tuple[str, ...]], predicted: Iterable[tuple[str, ...]]
) -> tuple[list[tuple[str, ...]], list[tuple[str, ...]], int]:
    expected_counter, predicted_counter = Counter(expected), Counter(predicted)
    missing = list((expected_counter - predicted_counter).elements())
    extra = list((predicted_counter - expected_counter).elements())
    matches = sum((expected_counter & predicted_counter).values())
    return missing, extra, matches


def _is_holdout(case_id: str, modulus: int) -> bool:
    """Stable deterministic split, independent of row ordering."""
    digest = hashlib.sha256(case_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % modulus == 0


def evaluate(
    expected_path: Path,
    predicted_path: Path,
    split: str = "all",
    holdout_modulus: int = 4,
) -> dict[str, Any]:
    expected, predicted = _load_rows(expected_path), _load_rows(predicted_path)
    all_case_ids = sorted(set(expected) | set(predicted), key=_natural_case_key)
    if split == "holdout":
        case_ids = [case_id for case_id in all_case_ids if _is_holdout(case_id, holdout_modulus)]
    elif split == "train":
        case_ids = [case_id for case_id in all_case_ids if not _is_holdout(case_id, holdout_modulus)]
    else:
        case_ids = all_case_ids

    status_correct = 0
    confusion: dict[str, Counter[str]] = {status: Counter() for status in STATUSES}
    true_positive = false_positive = false_negative = 0
    field_correct = Counter()
    field_total = Counter()
    exact_sequences = 0
    total_edit_distance = 0
    total_sequence_length = 0
    diffs: list[dict[str, Any]] = []

    for case_id in case_ids:
        truth = expected.get(case_id)
        guess = predicted.get(case_id)
        truth_status = truth["status"] if truth else "<MISSING_CASE>"
        guess_status = guess["status"] if guess else "<MISSING_CASE>"
        if truth and guess and truth_status == guess_status:
            status_correct += 1
        if truth_status in confusion:
            confusion[truth_status][guess_status] += 1

        truth_steps = truth["steps"] if truth else []
        guess_steps = guess["steps"] if guess else []
        truth_tokens = [_step_token(step) for step in truth_steps]
        guess_tokens = [_step_token(step) for step in guess_steps]
        missing, extra, matches = _counter_difference(truth_tokens, guess_tokens)
        true_positive += matches
        false_positive += len(extra)
        false_negative += len(missing)

        paired = min(len(truth_steps), len(guess_steps))
        for index in range(paired):
            for field in STEP_FIELDS:
                field_total[field] += 1
                field_correct[field] += truth_steps[index].get(field) == guess_steps[index].get(field)
        # A missing/extra positional step is incorrect for every semantic field.
        for field in STEP_FIELDS:
            field_total[field] += abs(len(truth_steps) - len(guess_steps))

        edit_distance = _levenshtein(truth_tokens, guess_tokens)
        total_edit_distance += edit_distance
        total_sequence_length += max(len(truth_tokens), len(guess_tokens))
        exact = truth_tokens == guess_tokens
        exact_sequences += exact

        if truth_status != guess_status or missing or extra or not exact:
            diffs.append({
                "case_id": case_id,
                "expected_status": truth_status,
                "predicted_status": guess_status,
                "missing_steps": [dict(zip(STEP_FIELDS, token)) for token in missing],
                "extra_steps": [dict(zip(STEP_FIELDS, token)) for token in extra],
                "expected_sequence": [dict(zip(STEP_FIELDS, token)) for token in truth_tokens],
                "predicted_sequence": [dict(zip(STEP_FIELDS, token)) for token in guess_tokens],
                "edit_distance": edit_distance,
            })

    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "expected_path": str(expected_path),
        "predicted_path": str(predicted_path),
        "split": split,
        "holdout_modulus": holdout_modulus,
        "case_count": len(case_ids),
        "missing_prediction_cases": sorted(set(expected) - set(predicted), key=_natural_case_key),
        "unexpected_prediction_cases": sorted(set(predicted) - set(expected), key=_natural_case_key),
        "status_accuracy": status_correct / len(case_ids) if case_ids else 0.0,
        "status_confusion_matrix": {
            expected_status: {predicted_status: confusion[expected_status][predicted_status] for predicted_status in STATUSES + ("<MISSING_CASE>",)}
            for expected_status in STATUSES
        },
        "step_metrics": {
            "true_positive": true_positive,
            "false_positive": false_positive,
            "false_negative": false_negative,
            "precision": precision,
            "recall": recall,
            "f1": f1,
        },
        "field_accuracy": {
            field: field_correct[field] / field_total[field] if field_total[field] else 0.0
            for field in STEP_FIELDS
        },
        "sequence_metrics": {
            "exact_order_accuracy": exact_sequences / len(case_ids) if case_ids else 0.0,
            "total_edit_distance": total_edit_distance,
            "mean_edit_distance": total_edit_distance / len(case_ids) if case_ids else 0.0,
            "normalized_edit_similarity": 1 - total_edit_distance / total_sequence_length if total_sequence_length else 1.0,
        },
        "per_case_diffs": diffs,
    }


def _format_summary(result: dict[str, Any]) -> str:
    steps = result["step_metrics"]
    sequence = result["sequence_metrics"]
    return "\n".join((
        f"Cases: {result['case_count']} ({result['split']})",
        f"Status accuracy: {result['status_accuracy']:.3%}",
        f"Step P/R/F1: {steps['precision']:.3%} / {steps['recall']:.3%} / {steps['f1']:.3%}",
        f"Exact sequence accuracy: {sequence['exact_order_accuracy']:.3%}",
        f"Mean edit distance: {sequence['mean_edit_distance']:.3f}",
        f"Cases with diffs: {len(result['per_case_diffs'])}",
    ))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", type=Path, required=True)
    parser.add_argument("--predicted", type=Path, required=True)
    parser.add_argument("--split", choices=("all", "train", "holdout"), default="all")
    parser.add_argument("--holdout-modulus", type=int, default=4)
    parser.add_argument("--output-json", type=Path)
    args = parser.parse_args()
    if args.holdout_modulus < 2:
        parser.error("--holdout-modulus must be at least 2")
    result = evaluate(args.expected, args.predicted, args.split, args.holdout_modulus)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(_format_summary(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
