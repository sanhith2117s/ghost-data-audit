"""Time-aware identity resolution across current snapshots and audit history."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from .load import DataBundle, parse_timestamp


def normalize_email(value: str) -> str:
    """Use only case/whitespace normalization justified by the supplied data."""
    return value.strip().casefold()


def normalize_phone(value: str) -> str:
    return re.sub(r"\D", "", value)


def normalize_name(value: str) -> str:
    return " ".join(value.casefold().split())


def normalize_hash(value: str) -> str:
    return value.strip().casefold()


def email_hash(email: str) -> str:
    """Observed dataset convention: SHA-256 of canonical email UTF-8 bytes."""
    return hashlib.sha256(normalize_email(email).encode("utf-8")).hexdigest()


@dataclass
class IdentifierSet:
    user_ids: set[str] = field(default_factory=set)
    emails: set[str] = field(default_factory=set)
    phones: set[str] = field(default_factory=set)
    names: set[str] = field(default_factory=set)
    email_hashes: set[str] = field(default_factory=set)
    audit_ids: set[str] = field(default_factory=set)

    def add_field(self, field_name: str, value: str) -> None:
        if not value:
            return
        if field_name in {"email", "requester_email", "owner_email", "customer_email", "lookup_key"} and "@" in value:
            normalized = normalize_email(value)
            self.emails.add(normalized)
            self.email_hashes.add(email_hash(normalized))
        elif field_name == "phone":
            normalized = normalize_phone(value)
            if normalized:
                self.phones.add(normalized)
        elif field_name in {"name", "full_name", "display_name", "requester_name"}:
            normalized = normalize_name(value)
            if normalized:
                self.names.add(normalized)
        elif field_name == "email_hash":
            self.email_hashes.add(normalize_hash(value))

    @property
    def values(self) -> set[str]:
        """Canonical values used for evidence-to-subject grounding."""
        return self.user_ids | self.emails | self.phones | self.names | self.email_hashes

    def matches_value(self, value: object) -> bool:
        if value is None:
            return False
        text = str(value).strip()
        if not text:
            return False
        candidates = {
            text.casefold(),
            normalize_email(text),
            normalize_phone(text),
            normalize_name(text),
            normalize_hash(text),
        }
        return bool(candidates & self.values)


class IdentityResolver:
    """Resolve a request's stable subject and all IDs known by its audit cutoff."""

    def __init__(self, bundle: DataBundle) -> None:
        self.identities_by_user = {row["user_id"]: row for row in bundle.identities}
        self.audit_by_user: dict[str, list[dict[str, str]]] = {}
        for row in bundle.audit_log:
            self.audit_by_user.setdefault(row["user_id"], []).append(row)
        for rows in self.audit_by_user.values():
            rows.sort(key=lambda row: parse_timestamp(row["event_time"]))

    def _candidate_users(self, request_email: str, audit_at: datetime) -> set[str]:
        normalized = normalize_email(request_email)
        candidates: set[str] = set()
        for user_id, row in self.identities_by_user.items():
            if parse_timestamp(row["updated_at"]) <= audit_at and normalize_email(row["email"]) == normalized:
                candidates.add(user_id)
        for user_id, rows in self.audit_by_user.items():
            for row in rows:
                if parse_timestamp(row["event_time"]) > audit_at:
                    break
                if row["field_name"] == "email" and any(
                    normalize_email(value) == normalized for value in (row["old_value"], row["new_value"])
                ):
                    candidates.add(user_id)
        return candidates

    def resolve(self, request_email: str, audit_at: datetime) -> IdentifierSet:
        user_ids = self._candidate_users(request_email, audit_at)
        identifiers = IdentifierSet(user_ids=set(user_ids))
        # A request email is grounded only after it resolved to a subject. This avoids
        # treating an unrelated unknown request as evidence for an arbitrary record.
        if user_ids:
            identifiers.add_field("email", request_email)
        for user_id in sorted(user_ids):
            current = self.identities_by_user[user_id]
            if parse_timestamp(current["updated_at"]) <= audit_at:
                for field in ("email", "phone", "name", "email_hash"):
                    identifiers.add_field(field, current[field])
            for entry in self.audit_by_user.get(user_id, []):
                if parse_timestamp(entry["event_time"]) > audit_at:
                    break
                identifiers.audit_ids.add(entry["audit_id"])
                identifiers.add_field(entry["field_name"], entry["old_value"])
                identifiers.add_field(entry["field_name"], entry["new_value"])
        return identifiers

    def resolve_request(self, request: dict[str, str]) -> IdentifierSet:
        return self.resolve(request["email"], parse_timestamp(request["audit_at"]))
