"""Output ordering and validation shared by plan generation and the CLI."""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any, Iterable


OUTPUT_COLUMNS = ("case_id", "deletion_status", "steps_json")
STATUSES = {"COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"}
STEP_FIELDS = ("step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id")
ALLOWED_TRIPLES = {
    ("FINDING", "REPORT_REMAINING_RECORD", "PRESENT"),
    ("FINDING", "REPORT_EXPOSED_PII", "PRESENT"),
    ("FINDING", "REPORT_ACTIVE_DATA_FLOW", "ACTIVE"),
    ("FINDING", "REPORT_RESTORE_RISK", "ACTIVE"),
    ("REMEDIATION", "DISABLE_DATA_FLOW", "DISABLED"),
    ("REMEDIATION", "BLOCK_RESTORE", "BLOCKED"),
    ("REMEDIATION", "DELETE_RECORD", "ABSENT"),
    ("REMEDIATION", "REDACT_RECORD", "REDACTED"),
    ("REMEDIATION", "ANONYMIZE_RECORD", "ANONYMIZED"),
    ("VERIFICATION", "VERIFY_RECORD_ABSENT", "ABSENT"),
    ("VERIFICATION", "VERIFY_RECORD_PRESENT", "PRESENT"),
    ("VERIFICATION", "VERIFY_RECORD_REDACTED", "REDACTED"),
    ("VERIFICATION", "VERIFY_RECORD_ANONYMIZED", "ANONYMIZED"),
    ("VERIFICATION", "VERIFY_DATA_FLOW_DISABLED", "DISABLED"),
    ("VERIFICATION", "VERIFY_RESTORE_BLOCKED", "BLOCKED"),
}
PHASE_ORDER = {"FINDING": 0, "REMEDIATION": 1, "VERIFICATION": 2}
OPERATION_ORDER = {
    "REPORT_REMAINING_RECORD": 0,
    "REPORT_EXPOSED_PII": 1,
    "REPORT_ACTIVE_DATA_FLOW": 2,
    "REPORT_RESTORE_RISK": 3,
    "DISABLE_DATA_FLOW": 0,
    "BLOCK_RESTORE": 1,
    "DELETE_RECORD": 2,
    "REDACT_RECORD": 3,
    "ANONYMIZE_RECORD": 4,
    "VERIFY_RECORD_ABSENT": 0,
    "VERIFY_RECORD_PRESENT": 1,
    "VERIFY_RECORD_REDACTED": 2,
    "VERIFY_RECORD_ANONYMIZED": 3,
    "VERIFY_DATA_FLOW_DISABLED": 4,
    "VERIFY_RESTORE_BLOCKED": 5,
}


def natural_key(value: str) -> tuple[tuple[int, object], ...]:
    """Natural, case-insensitive sort for IDs such as account_9 and account_10."""
    return tuple((1, int(part)) if part.isdigit() else (0, part.casefold()) for part in re.split(r"(\d+)", value))


def step_sort_key(step: dict[str, Any]) -> tuple[Any, ...]:
    # Unknown phases/operations sort last instead of raising, so a malformed row is
    # reported by the validator rather than crashing the run.
    return (
        PHASE_ORDER.get(step.get("phase"), len(PHASE_ORDER)),
        OPERATION_ORDER.get(step.get("operation"), len(OPERATION_ORDER)),
        natural_key(str(step.get("system_id"))),
        natural_key(str(step.get("resource_id"))),
    )


def sort_and_number(steps: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate semantic steps, contract-sort them, then number consecutively."""
    unique: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for step in steps:
        key = (step["phase"], step["operation"], step["system_id"], step["resource_id"])
        existing = unique.get(key)
        if existing and existing != step:
            raise ValueError(f"conflicting duplicate plan step: {key}")
        unique[key] = dict(step)
    ordered = sorted(unique.values(), key=step_sort_key)
    for number, step in enumerate(ordered, start=1):
        step["step_no"] = number
    return ordered


def validate_case(
    case_id: str,
    status: str,
    steps: list[dict[str, Any]],
    valid_evidence_ids: set[str],
) -> list[str]:
    errors: list[str] = []
    if status not in STATUSES:
        errors.append(f"{case_id}: invalid status {status!r}")
    if status == "NOT_APPLICABLE" and steps:
        errors.append(f"{case_id}: NOT_APPLICABLE must have no steps")
    if status == "COMPLETE" and any(step.get("phase") != "VERIFICATION" for step in steps):
        errors.append(f"{case_id}: COMPLETE may contain verification steps only")
    if status in {"COMPLETE", "INCOMPLETE"} and not steps:
        errors.append(f"{case_id}: authorized deletion has no evidence-backed plan")
    for expected_number, step in enumerate(steps, start=1):
        if tuple(step.keys()) != STEP_FIELDS:
            errors.append(f"{case_id}: step {expected_number} fields must be exactly {STEP_FIELDS}")
            continue
        if step["step_no"] != expected_number:
            errors.append(f"{case_id}: step numbers are not consecutive")
        triple = (step["phase"], step["operation"], step["state"])
        if triple not in ALLOWED_TRIPLES:
            errors.append(f"{case_id}: invalid phase/operation/state {triple}")
        if not all(isinstance(step[field], str) and step[field] for field in ("system_id", "resource_id", "operation", "state", "phase")):
            errors.append(f"{case_id}: step {expected_number} has a blank semantic field")
        evidence_id = step["evidence_id"]
        if step["phase"] in {"REMEDIATION"} or (status == "INCOMPLETE" and step["phase"] == "VERIFICATION"):
            if evidence_id != "":
                errors.append(f"{case_id}: future step {expected_number} must have empty evidence_id")
        elif evidence_id not in valid_evidence_ids:
            errors.append(f"{case_id}: unknown/missing evidence ID {evidence_id!r}")
    if steps != sorted(steps, key=step_sort_key):
        errors.append(f"{case_id}: steps violate phase/operation/system/resource ordering")
    semantic_keys = [(s["phase"], s["operation"], s["system_id"], s["resource_id"]) for s in steps]
    if [key for key, count in Counter(semantic_keys).items() if count > 1]:
        errors.append(f"{case_id}: duplicate semantic steps")
    return errors


def serialize_steps(steps: list[dict[str, Any]]) -> str:
    return json.dumps(steps, ensure_ascii=False, separators=(",", ":"))
