"""Deterministic, cache-backed extraction of participant-visible evidence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from .identity import IdentifierSet
from .load import EvidenceFile, parse_timestamp


DIRECT_PII_KEYS = {
    "email", "requester_email", "owner_email", "customer_email", "email_hash", "phone",
    "requester_phone", "name", "full_name", "display_name", "requester_name",
}
IDENTITY_VALUE_KEYS = DIRECT_PII_KEYS | {
    "user_id", "subject_reference", "customer_reference", "lookup_key", "subject_ref",
    "subject_key", "matched_value", "correlation_key",
}
# Fields that *name* the subject of a trace/log line rather than expose data about
# them. They link a record to a person but are not themselves leaked identifiers.
LINK_KEYS = {
    "lookup_key", "subject_ref", "subject_key", "customer_reference", "user_id",
    "subject_reference", "correlation_key",
}
TIME_KEYS = ("observed_at", "recorded_at", "updated_at", "captured_at", "event_time", "timestamp")
_LOG_LEVELS = re.compile(r"^(TRACE|DEBUG|INFO|NOTICE|WARN|WARNING|ERROR|FATAL)$")
VISION_CACHE_PATH = Path(__file__).resolve().parents[1] / "evidence_cache" / "images.json"
# Last-resort category for a system when the observation names no record type and
# its context carries no category hint.
SYSTEM_CATEGORY_DEFAULT = {
    "account_service": "USER_PROFILE",
    "analytics_platform": "ANALYTICS_PROFILE",
    "backup_service": "BACKUP_ARCHIVE",
    "cache_service": "CACHE_ENTRY",
    "email_delivery_service": "EMAIL_DELIVERY_LOG",
    "export_service": "USER_EXPORT",
    "identity_service": "ACCOUNT_TOMBSTONE",
    "integration_service": "DATA_FLOW",
    "marketing_platform": "MARKETING_PROFILE",
    "object_store": "USER_EXPORT",
    "search_service": "SEARCH_INDEX_ENTRY",
    "security_service": "SECURITY_AUDIT_LOG",
    "support_platform": "SUPPORT_MESSAGE",
}


@dataclass(frozen=True)
class Observation:
    file_id: str
    captured_at: datetime
    observed_at: datetime
    system_id: str
    resource_id: str
    category: str | None
    state: str
    identity_values: tuple[str, ...]
    direct_pii_present: bool
    subject_link_present: bool
    payload: dict[str, str]

    def matches(self, identifiers: IdentifierSet) -> bool:
        return any(identifiers.matches_value(value) for value in self.identity_values)


class EvidenceStore:
    """Parse each evidence file at most once, indexed by SHA-256 content hash."""

    def __init__(self, evidence_files: list[EvidenceFile], live_vision: bool = False) -> None:
        self.evidence_files = sorted(evidence_files, key=lambda item: (item.captured_at, item.file_id))
        self._content_cache: dict[str, list[Observation]] = {}
        self._by_file: dict[str, list[Observation]] = {}
        self.parse_failures: dict[str, str] = {}
        self._vision_entries: dict[str, dict] = {}
        self.live_vision = live_vision
        cache_path = Path(os.environ.get("EVIDENCE_VISION_CACHE", str(VISION_CACHE_PATH)))
        if cache_path.is_file():
            loaded = json.loads(cache_path.read_text(encoding="utf-8"))
            self._vision_entries = loaded.get("entries", {})

    @staticmethod
    def _content_hash(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def parse_all(self) -> list[Observation]:
        all_observations: list[Observation] = []
        for evidence in self.evidence_files:
            digest = self._content_hash(evidence.file_path)
            if digest not in self._content_cache:
                try:
                    self._content_cache[digest] = self._parse_file(evidence)
                except Exception as exc:  # failure is retained and reported, never hidden
                    self.parse_failures[evidence.file_id] = f"{type(exc).__name__}: {exc}"
                    self._content_cache[digest] = []
            cached = self._content_cache[digest]
            observations = [
                Observation(
                    file_id=evidence.file_id,
                    captured_at=evidence.captured_at,
                    observed_at=item.observed_at,
                    system_id=item.system_id,
                    resource_id=item.resource_id,
                    category=item.category,
                    state=item.state,
                    identity_values=item.identity_values,
                    direct_pii_present=item.direct_pii_present,
                    subject_link_present=item.subject_link_present,
                    payload=item.payload,
                )
                for item in cached
            ]
            self._by_file[evidence.file_id] = observations
            all_observations.extend(observations)
        if self.parse_failures:
            print("WARNING: evidence extraction gaps (these files contribute no observations):", file=sys.stderr)
            for file_id, message in sorted(self.parse_failures.items()):
                print(f"  - {file_id}: {message}", file=sys.stderr)
        return all_observations

    def relevant_observations(self, identifiers: IdentifierSet, audit_at: datetime) -> list[Observation]:
        if not self._by_file:
            self.parse_all()
        result = []
        for evidence in self.evidence_files:
            if evidence.captured_at > audit_at:
                continue
            result.extend(item for item in self._by_file[evidence.file_id] if item.matches(identifiers))
        return result

    def _parse_file(self, evidence: EvidenceFile) -> list[Observation]:
        suffix = evidence.file_path.suffix.casefold()
        if suffix == ".json":
            parsed = json.loads(evidence.file_path.read_text(encoding="utf-8"))
            return list(self._parse_json(parsed, evidence))
        if suffix == ".sql":
            return self._parse_sql(evidence)
        if suffix in {".log", ".txt"}:
            return self._parse_text(evidence.file_path.read_text(encoding="utf-8"), evidence)
        if suffix == ".pdf":
            extracted = subprocess.run(
                ["pdftotext", "-layout", str(evidence.file_path), "-"],
                check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
            ).stdout
            return self._parse_text(extracted, evidence)
        if suffix in {".png", ".jpg", ".jpeg"}:
            return self._parse_image(evidence)
        raise ValueError(f"unsupported evidence type: {suffix}")

    def _parse_image(self, evidence: EvidenceFile) -> list[Observation]:
        """Read screenshot facts from the hash-keyed vision transcription cache.

        OCR/VLM extraction cannot run inside the deterministic runtime, so the
        transcription is produced once, stored under the image's SHA-256, and
        re-verified on every run. An image with no cached transcription is
        reported as an extraction gap rather than assumed to be empty. With
        ``live_vision`` the cache is bypassed and a live model transcription is
        attempted instead (requires a provider key in the env var named by
        config/models.yaml).
        """
        if self.live_vision:
            return self._parse_image_live(evidence)
        entry = self._vision_entries.get(self._content_hash(evidence.file_path))
        if entry is None:
            raise RuntimeError(
                "no vision transcription cached for this screenshot "
                f"({evidence.file_path.name}); add it to {VISION_CACHE_PATH.name}"
            )
        return self._observations_from_entry(entry, evidence)

    def _parse_image_live(self, evidence: EvidenceFile) -> list[Observation]:
        """Transcribe one screenshot live via the configured vision provider."""
        config = self._load_models_config()
        vision = config.get("vision") or {}
        model = vision.get("model")
        provider = vision.get("provider")
        api_key = os.environ.get(vision.get("api_key_env", "")) if vision.get("api_key_env") else None
        if not api_key:
            raise RuntimeError(
                f"live vision requested for {evidence.file_path.name} but no API key "
                f"is set in env var {vision.get('api_key_env')}; "
                "set it or run without --live-vision to use the cache"
            )
        print(
            f"WARNING: live vision transcription for {evidence.file_path.name} "
            f"using {provider}/{model} (results are not cached)",
            file=sys.stderr,
        )
        # Router integration point: call the configured provider's vision
        # endpoint with `api_key`, `model`, `base_url` and the image bytes. The
        # graded pipeline does not wire a router endpoint; implement here when a
        # provider key is available.
        raise NotImplementedError(
            f"no vision router endpoint is wired for {evidence.file_path.name}; "
            "implement _parse_image_live against config/models.yaml"
        )

    @staticmethod
    def _load_models_config() -> dict:
        config_path = Path(__file__).resolve().parents[2] / "config" / "models.yaml"
        if not config_path.is_file():
            return {}
        import yaml

        with config_path.open(encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}

    def _observations_from_entry(self, entry: dict, evidence: EvidenceFile) -> list[Observation]:
        observations: list[Observation] = []
        for item in entry.get("observations", []):
            payload = {
                "resource_id": item["resource_id"],
                "system": item["system_id"],
                "record_type": item["category"],
                "state": item["state"],
                "email": next(iter(item.get("emails") or []), ""),
                "phone": next(iter(item.get("phones") or []), ""),
                "full_name": next(iter(item.get("names") or []), ""),
                "subject_reference": next(iter(item.get("references") or []), ""),
            }
            if item.get("observed_at"):
                payload["observed_at"] = item["observed_at"]
            observation = self._make_observation(payload, evidence, context_label=evidence.origin)
            if observation is None:
                continue
            extra = tuple(
                value
                for value in (*item.get("emails", []), *item.get("phones", []),
                              *item.get("names", []), *item.get("references", []))
                if value
            )
            merged = tuple(dict.fromkeys((*observation.identity_values, *extra)))
            observations.append(
                Observation(
                    file_id=observation.file_id,
                    captured_at=observation.captured_at,
                    observed_at=observation.observed_at,
                    system_id=observation.system_id,
                    resource_id=observation.resource_id,
                    category=item["category"],
                    state=item["state"],
                    identity_values=merged,
                    direct_pii_present=bool(item.get("pii")),
                    subject_link_present=bool(item.get("references")),
                    payload=observation.payload,
                )
            )
        return observations

    def _parse_json(self, document: Any, evidence: EvidenceFile) -> Iterator[Observation]:
        if not isinstance(document, dict):
            return
        # A reusable compact record export has an explicit `records` collection.
        if isinstance(document.get("records"), list):
            context = {key: value for key, value in document.items() if key != "records"}
            for record in document["records"]:
                if isinstance(record, dict):
                    merged = {**context, **record}
                    item = self._make_observation(merged, evidence, context_label=str(document.get("collection", "")))
                    if item:
                        yield item
        # Snapshot bundles use a per-store collection label.
        for snapshot in document.get("snapshots", []) if isinstance(document.get("snapshots"), list) else []:
            if not isinstance(snapshot, dict):
                continue
            context = {key: value for key, value in snapshot.items() if key != "records"}
            for record in snapshot.get("records", []) if isinstance(snapshot.get("records"), list) else []:
                if isinstance(record, dict):
                    item = self._make_observation({**context, **record}, evidence, context_label=str(snapshot.get("collection", "")))
                    if item:
                        yield item
        # Service exports nest lists below named systems (account_service, etc.).
        exports = document.get("service_exports")
        if isinstance(exports, dict):
            for system_id, groups in exports.items():
                if not isinstance(groups, dict):
                    continue
                for group_name, records in groups.items():
                    if not isinstance(records, list):
                        continue
                    for record in records:
                        if isinstance(record, dict):
                            merged = {"system": system_id, **record}
                            item = self._make_observation(merged, evidence, context_label=group_name)
                            if item:
                                yield item

    def _parse_sql(self, evidence: EvidenceFile) -> list[Observation]:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        try:
            connection.executescript(evidence.file_path.read_text(encoding="utf-8"))
            table_rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
            observations: list[Observation] = []
            for row in table_rows:
                table = str(row["name"])
                columns = [info["name"] for info in connection.execute(f'PRAGMA table_info("{table}")')]
                if "resource_id" not in columns:
                    continue
                for record in connection.execute(f'SELECT * FROM "{table}"'):
                    payload = dict(record)
                    item = self._make_observation(payload, evidence, context_label=table)
                    if item:
                        observations.append(item)
            return observations
        finally:
            connection.close()

    def _parse_text(self, text: str, evidence: EvidenceFile) -> list[Observation]:
        """Parse log/PDF text, propagating trace- and resource-level subject links.

        Traces are written as a `*_query_started` (or `*_policy_check_started`)
        line that names the subject, followed by `*_completed` lines that name the
        resource.  The identifying line establishes neither presence nor absence,
        so it is never a state observation, but it does bind the resource to the
        subject for every later line in the same trace.
        """
        observations: list[Observation] = []
        parsed_lines = [self._parse_pipe_record(line) or self._parse_key_values(line) for line in text.splitlines()]

        link_by_trace: dict[str, dict[str, str]] = {}
        link_by_resource: dict[str, dict[str, str]] = {}
        for payload in parsed_lines:
            links = {key: payload[key] for key in LINK_KEYS if payload.get(key) not in {None, "", "null", "NULL"}}
            if not links:
                continue
            trace_id = payload.get("trace_id")
            resource_id = payload.get("resource_id")
            if trace_id:
                link_by_trace.setdefault(trace_id, {}).update(links)
            if resource_id:
                link_by_resource.setdefault(resource_id, {}).update(links)

        for payload in parsed_lines:
            resource_id = payload.get("resource_id")
            if not resource_id:
                continue
            if not any(payload.get(key) for key in LINK_KEYS):
                for source in (
                    link_by_trace.get(payload.get("trace_id") or "", {}),
                    link_by_resource.get(resource_id, {}),
                ):
                    for key, value in source.items():
                        payload.setdefault(key, value)
            item = self._make_observation(payload, evidence, context_label=payload.get("event", ""))
            if item:
                observations.append(item)
        # PDFs are text-extracted business artifacts rather than structured logs.
        # When a document has a single resource reference, bind its directly stated
        # identifier(s) to that resource.  Do not use this broad fallback for a
        # multi-row handover document, where it would cross-contaminate subjects.
        if not observations:
            resource_ids = sorted(set(re.findall(
                r"\b(?:account|search|analytics|support|export|backup|data_flow|audit_record|tombstone|marketing|email_delivery|cache)_[A-Za-z0-9_-]+\b",
                text,
                flags=re.IGNORECASE,
            )))
            emails = re.findall(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", text)
            if len(resource_ids) == 1:
                payload = {"resource_id": resource_ids[0], "document_text": text}
                if len(set(emails)) == 1:
                    payload["email"] = emails[0]
                item = self._make_observation(
                    payload, evidence, context_label=f"{evidence.file_path.stem} {evidence.origin}"
                )
                if item:
                    observations.append(item)
        return observations

    @staticmethod
    def _parse_pipe_record(line: str) -> dict[str, str] | None:
        """Parse pipe-delimited records in either supported layout."""
        parts = [part.strip() for part in line.lstrip("- ").split("|")]
        if len(parts) < 6 or not re.match(r"^\d{4}-\d\d-\d\dT", parts[0]):
            return None
        if _LOG_LEVELS.match(parts[1]):
            #  time | LEVEL | service | trace_id | event | key=value ...
            record = {
                "event_time": parts[0],
                "level": parts[1],
                "service": parts[2],
                "trace_id": parts[3],
                "event": parts[4],
            }
            for key, quoted, bare in re.findall(
                r"([A-Za-z_][A-Za-z0-9_]*)=(?:\"([^\"]*)\"|([^\s|,]+))", "|".join(parts[5:])
            ):
                record[key] = quoted if quoted != "" else bare
            return record
        return {
            "event_time": parts[0],
            "system": parts[1],
            "resource_id": parts[2],
            "record_type": parts[3],
            "result": parts[4],
            "subject_reference": parts[5],
        }

    @staticmethod
    def _parse_key_values(line: str) -> dict[str, str]:
        result: dict[str, str] = {}
        timestamp = re.match(r"^(\d{4}-\d\d-\d\dT[^\s]+)", line)
        if timestamp:
            result["event_time"] = timestamp.group(1)
        try:
            object_line = json.loads(line)
            if isinstance(object_line, dict):
                result.update({str(key): str(value) for key, value in object_line.items()})
                return result
        except json.JSONDecodeError:
            pass
        # Values in supplied logs are shell-style atoms or double-quoted strings.
        for key, quoted, bare in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)=(?:\"([^\"]*)\"|([^\s|,]+))", line):
            result[key] = quoted if quoted != "" else bare
        # Pipes/colon logging styles retain key information in `service=...` form;
        # this extra pass captures a bare `event` field in a few PDF/text layouts.
        return result

    def _make_observation(
        self, payload: dict[str, Any], evidence: EvidenceFile, context_label: str = ""
    ) -> Observation | None:
        flat = self._flatten(payload)
        # Read-start traces establish neither presence nor absence. Their companion
        # completion event in the same evidence is the state-bearing observation.
        event_name = (flat.get("event") or "").casefold()
        if event_name.endswith("_started"):
            return None
        resource_id = self._pick(flat, "resource_id")
        if not resource_id:
            return None
        system_id = self._pick(flat, "system", "data_store", "service", "source_system")
        if system_id:
            system_id = self._canonical_system(system_id)
        category = self._pick(flat, "record_type", "data_category")
        if "email_opt_in" in flat:
            # An opt-in flag marks a marketing/consent profile rather than a
            # behavioural analytics record.
            category = "MARKETING_PROFILE"
        context = " ".join((context_label, system_id or "", category or "", resource_id)).casefold()
        if not category:
            category = self._category_from_context(context)
        if not system_id:
            system_id = self._system_from_context(context)
        if not system_id:
            return None
        if not category:
            category = SYSTEM_CATEGORY_DEFAULT.get(system_id)
        observed_at = evidence.captured_at
        for key in TIME_KEYS:
            value = flat.get(key)
            if value:
                try:
                    observed_at = parse_timestamp(value)
                    break
                except ValueError:
                    continue
        state = self._state_from_fields(flat)
        identity_values = tuple(
            value for key, value in flat.items()
            if key in IDENTITY_VALUE_KEYS and value and value.casefold() not in {"null", "none"}
        )
        # Free text can contain a direct email/phone/hash while no dedicated field
        # exists. It is intentionally limited to recognizable values to avoid fuzzy
        # accidental matches.
        leaked_values: tuple[str, ...] = ()
        # Any value in the record can carry a literal identifier: a dedicated field,
        # a nested JSON payload, or free text. Linkage-only fields (lookup_key,
        # subject_ref, customer_reference, user_id) name the subject without leaking
        # them, so they bind identity but do not count as exposed personal data.
        for key, value in flat.items():
            for found in self._embedded_identifiers([value]):
                if found not in identity_values:
                    identity_values += (found,)
                if key not in LINK_KEYS or key == "correlation_key":
                    if found not in leaked_values:
                        leaked_values += (found,)
        if category in {"SUPPORT_MESSAGE", "SUPPORT_ATTACHMENT"}:
            category = self._refine_support(category, flat, leaked_values, context)
        direct_pii_present = any(
            key in DIRECT_PII_KEYS and bool(value) and value.casefold() not in {"null", "none"}
            for key, value in flat.items()
        ) or bool(leaked_values)
        subject_reference = flat.get("subject_reference") or flat.get("lookup_key") or ""
        if "@" in subject_reference or re.search(r"\b[a-fA-F0-9]{64}\b", subject_reference):
            direct_pii_present = True
        subject_link_present = any(
            key in {"user_id", "subject_reference", "customer_reference", "subject_ref"}
            and bool(value)
            and value.casefold() not in {"null", "none", "no direct identifier"}
            for key, value in flat.items()
        )
        return Observation(
            file_id=evidence.file_id,
            captured_at=evidence.captured_at,
            observed_at=observed_at,
            system_id=system_id,
            resource_id=resource_id,
            category=category,
            state=state,
            identity_values=identity_values,
            direct_pii_present=direct_pii_present,
            subject_link_present=subject_link_present,
            payload=flat,
        )

    @staticmethod
    def _flatten(value: Any) -> dict[str, str]:
        flat: dict[str, str] = {}

        def walk(item: Any) -> None:
            if isinstance(item, dict):
                for key, nested in item.items():
                    if isinstance(nested, (dict, list)):
                        walk(nested)
                    elif key not in flat:
                        flat[str(key)] = "" if nested is None else str(nested)
            elif isinstance(item, list):
                for nested in item:
                    walk(nested)

        walk(value)
        return flat

    @staticmethod
    def _pick(flat: dict[str, str], *keys: str) -> str | None:
        for key in keys:
            value = flat.get(key)
            if value and value.casefold() not in {"null", "none"}:
                return value
        return None

    @staticmethod
    def _embedded_identifiers(values: Any) -> Iterator[str]:
        for value in values:
            if not value:
                continue
            text = str(value)
            yield from re.findall(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", text)
            yield from re.findall(r"\b[a-fA-F0-9]{64}\b", text)

    @staticmethod
    def _canonical_system(system_id: str) -> str:
        """Map replica/region variants onto the canonical service name.

        Replica rows name the physical node (`support_replica`, `analytics_read`)
        while the audited resource belongs to the logical service.  Deleting from
        the logical service is what the remediation plan must express.
        """
        value = system_id.strip().casefold()
        for suffix in ("_replica", "_primary", "_read", "_readonly", "_archive"):
            if value.endswith(suffix):
                value = value[: -len(suffix)]
        aliases = {
            "attachment_preview": "support_platform",
            "support": "support_platform",
            "account": "account_service",
            "identity": "identity_service",
            "security": "security_service",
            "analytics": "analytics_platform",
            "search": "search_service",
            "index": "search_service",
            "backup": "backup_service",
            "integration": "integration_service",
            "export": "export_service",
            "marketing": "marketing_platform",
            "object_storage": "object_store",
            "storage": "object_store",
            "cache": "cache_service",
            "email_delivery": "email_delivery_service",
            "connector": "integration_service",
        }
        return aliases.get(value, value)

    @staticmethod
    def _refine_support(category: str, flat: dict[str, str], leaked: tuple[str, ...], context: str) -> str:
        """Separate a support conversation from a subject-only support artifact.

        A message that carries a conversation excerpt (or a scanner hit on a direct
        identifier) is retained and redacted because it holds shared agent/order
        context. A support artifact with no conversation content — an attachment or
        a contact-index row — belongs only to the subject and is deleted in full.
        """
        if flat.get("record_type") or flat.get("data_category"):
            return category
        if "attachment" in context:
            return "SUPPORT_ATTACHMENT"
        if flat.get("message_excerpt") or flat.get("detector") or leaked:
            return "SUPPORT_MESSAGE"
        return "SUPPORT_ATTACHMENT"

    @staticmethod
    def _looks_like_direct_identifier(value: str) -> bool:
        text = (value or "").strip()
        if not text:
            return False
        if "@" in text:
            return True
        return bool(re.fullmatch(r"[a-fA-F0-9]{64}", text))

    @staticmethod
    def _state_from_fields(flat: dict[str, str]) -> str:
        result = (flat.get("result") or "").casefold()
        rows = (flat.get("rows") or "").casefold()
        http_status = flat.get("http_status") or flat.get("status_code") or ""
        if result in {"not_found", "missing", "absent"} or rows == "0" or http_status == "404":
            return "ABSENT"
        if result in {"active", "enabled"}:
            return "ACTIVE"
        if result in {"disabled", "stopped"}:
            return "DISABLED"
        if result in {"blocked", "restore_blocked"}:
            return "BLOCKED"
        if "restore_allowed" in flat:
            return "ACTIVE" if flat["restore_allowed"].casefold() in {"1", "true", "yes"} else "BLOCKED"
        if "deletion_block" in flat:
            return "BLOCKED" if flat["deletion_block"].casefold() in {"1", "true", "yes"} else "ACTIVE"
        if "marker_found" in flat:
            # A deletion-marker check reports whether the removal marker is present;
            # it is not by itself a statement that the record is gone.
            return "PRESENT"
        if "enabled" in flat:
            return "ACTIVE" if flat["enabled"].casefold() in {"1", "true", "yes"} else "DISABLED"
        if result in {"anonymized", "anonymous"}:
            return "ANONYMIZED"
        if result in {"redacted", "redaction_complete"}:
            return "REDACTED"
        return "PRESENT"

    @staticmethod
    def _category_from_context(context: str) -> str | None:
        if "tombstone" in context or "deleted_customer_reference" in context:
            return "ACCOUNT_TOMBSTONE"
        if "connector" in context or "integration" in context or "data_flow" in context:
            return "DATA_FLOW"
        if "backup" in context or "restore" in context:
            return "BACKUP_ARCHIVE"
        if "security" in context or "audit_record" in context:
            return "SECURITY_AUDIT_LOG"
        if "search" in context or "index" in context:
            return "SEARCH_INDEX_ENTRY"
        if "analytics" in context:
            return "ANALYTICS_PROFILE"
        if "attachment" in context:
            return "SUPPORT_ATTACHMENT"
        if "support" in context:
            return "SUPPORT_MESSAGE"
        if "export" in context or "object_store" in context:
            return "USER_EXPORT"
        if "marketing" in context:
            return "MARKETING_PROFILE"
        if "cache" in context:
            return "CACHE_ENTRY"
        if "delivery" in context or "email" in context:
            return "EMAIL_DELIVERY_LOG"
        if "telemetry" in context:
            return "PRODUCT_TELEMETRY"
        if "profile" in context or "account" in context:
            return "USER_PROFILE"
        return None

    @staticmethod
    def _system_from_context(context: str) -> str | None:
        systems = (
            "account_service", "analytics_platform", "backup_service", "export_service", "integration_service",
            "search_service", "security_service", "support_platform", "identity_service", "cache_service",
            "marketing_platform", "email_delivery_service", "object_store", "archive_service",
        )
        for system in systems:
            if system in context:
                return system
        if "search" in context or "index" in context:
            return "search_service"
        if "analytics" in context:
            return "analytics_platform"
        if "support" in context:
            return "support_platform"
        if "backup" in context or "restore" in context:
            return "backup_service"
        if "connector" in context or "integration" in context:
            return "integration_service"
        if "security" in context or "audit" in context:
            return "security_service"
        if "tombstone" in context or "identity" in context:
            return "identity_service"
        if "export" in context:
            return "export_service"
        if "profile" in context or "account" in context:
            return "account_service"
        return None
