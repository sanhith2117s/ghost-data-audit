"""Strict, dependency-free loading for all participant-facing CSV inputs."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


REQUEST_COLUMNS = (
    "case_id", "email", "requested_at", "reported_status", "reported_at", "audit_at", "request_text",
)
IDENTITY_COLUMNS = ("user_id", "name", "email", "phone", "email_hash", "updated_at")
AUDIT_COLUMNS = ("audit_id", "user_id", "event_time", "field_name", "old_value", "new_value")
ACTIVITY_COLUMNS = (
    "activity_id", "event_time", "event_type", "source_system", "target_system", "resource_id",
    "reported_result", "details",
)
RULE_COLUMNS = ("rule_id", "data_category", "condition", "required_action")
EVIDENCE_COLUMNS = ("file_id", "file_path", "origin", "captured_at")


def parse_timestamp(value: str) -> datetime:
    """Parse an ISO-8601 timestamp and require an explicit timezone."""
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    parsed = datetime.fromisoformat(candidate)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp has no timezone: {value!r}")
    return parsed.astimezone(timezone.utc)


def read_csv_strings(path: Path, expected_columns: Iterable[str]) -> list[dict[str, str]]:
    """Read every value as text; identifiers must never be coerced to numbers."""
    expected = tuple(expected_columns)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        actual = tuple(reader.fieldnames or ())
        if actual != expected:
            raise ValueError(f"{path.name}: expected columns {expected}, got {actual}")
        rows = []
        for index, row in enumerate(reader, start=2):
            if None in row:
                raise ValueError(f"{path.name}:{index}: too many columns")
            cleaned = {key: (value if value is not None else "") for key, value in row.items()}
            if any(value == "" for value in cleaned.values()):
                # Empty values are permitted only in evidence payloads, not these CSV
                # control planes. Fail early instead of silently fabricating an ID.
                raise ValueError(f"{path.name}:{index}: empty control-plane field")
            rows.append(cleaned)
    return rows


@dataclass(frozen=True)
class EvidenceFile:
    file_id: str
    file_path: Path
    origin: str
    captured_at: datetime


@dataclass
class DataBundle:
    dataset_dir: Path
    requests: list[dict[str, str]]
    identities: list[dict[str, str]]
    audit_log: list[dict[str, str]]
    activities: list[dict[str, str]]
    rules: list[dict[str, str]]
    evidence_files: list[EvidenceFile]

    @property
    def evidence_by_id(self) -> dict[str, EvidenceFile]:
        return {item.file_id: item for item in self.evidence_files}

    @property
    def evidence_ids(self) -> set[str]:
        return set(self.evidence_by_id)

    @property
    def audit_ids(self) -> set[str]:
        return {row["audit_id"] for row in self.audit_log}

    @property
    def activity_ids(self) -> set[str]:
        return {row["activity_id"] for row in self.activities}


def _assert_unique(rows: list[dict[str, str]], key: str, name: str) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for row in rows:
        value = row[key]
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    if duplicates:
        raise ValueError(f"{name}: duplicate {key} values: {sorted(duplicates)[:5]}")


def load_bundle(dataset_dir: Path) -> DataBundle:
    """Load and validate every required participant-facing control-plane file."""
    dataset_dir = dataset_dir.resolve()
    requests = read_csv_strings(dataset_dir / "deletion_requests.csv", REQUEST_COLUMNS)
    identities = read_csv_strings(dataset_dir / "user_identities.csv", IDENTITY_COLUMNS)
    audit_log = read_csv_strings(dataset_dir / "user_audit_log.csv", AUDIT_COLUMNS)
    activities = read_csv_strings(dataset_dir / "system_activity.csv", ACTIVITY_COLUMNS)
    rules = read_csv_strings(dataset_dir / "deletion_rules.csv", RULE_COLUMNS)
    manifest_rows = read_csv_strings(dataset_dir / "evidence_files.csv", EVIDENCE_COLUMNS)

    for rows, key, name in (
        (requests, "case_id", "deletion_requests.csv"),
        (identities, "user_id", "user_identities.csv"),
        (audit_log, "audit_id", "user_audit_log.csv"),
        (activities, "activity_id", "system_activity.csv"),
        (rules, "rule_id", "deletion_rules.csv"),
        (manifest_rows, "file_id", "evidence_files.csv"),
    ):
        _assert_unique(rows, key, name)

    for row in requests:
        parse_timestamp(row["requested_at"])
        parse_timestamp(row["reported_at"])
        parse_timestamp(row["audit_at"])
    for row in identities:
        parse_timestamp(row["updated_at"])
    for row in audit_log:
        parse_timestamp(row["event_time"])
    for row in activities:
        parse_timestamp(row["event_time"])

    evidence_files = []
    for row in manifest_rows:
        path = (dataset_dir / row["file_path"]).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"manifest file missing: {row['file_id']} -> {path}")
        evidence_files.append(EvidenceFile(
            file_id=row["file_id"], file_path=path, origin=row["origin"], captured_at=parse_timestamp(row["captured_at"]),
        ))

    return DataBundle(
        dataset_dir=dataset_dir,
        requests=requests,
        identities=identities,
        audit_log=audit_log,
        activities=activities,
        rules=rules,
        evidence_files=evidence_files,
    )
