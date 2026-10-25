"""Evidence-first reconstruction of resource state at a case audit cutoff."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from .evidence import EvidenceStore, Observation
from .identity import IdentifierSet
from .load import DataBundle, parse_timestamp


@dataclass(frozen=True)
class ResourceState:
    system_id: str
    resource_id: str
    category: str | None
    state: str
    direct_pii_present: bool
    subject_link_present: bool
    evidence_id: str
    captured_at: datetime
    observed_at: datetime


RETAINED_CATEGORIES = {"SECURITY_AUDIT_LOG", "LEGAL_HOLD_RECORD"}

_STATE_STRENGTH = {
    # At a common capture time, a surviving copy or enabled route takes priority over
    # a negative result from another replica. This protects against replication drift.
    "PRESENT": 7,
    "ACTIVE": 6,
    "ABSENT": 5,
    "REDACTED": 4,
    "ANONYMIZED": 3,
    "DISABLED": 2,
    "BLOCKED": 1,
}


class StateReconstructor:
    def __init__(self, bundle: DataBundle, evidence: EvidenceStore) -> None:
        self.activities = bundle.activities
        self.evidence = evidence

    def _activity_links(self, identifiers: IdentifierSet, audit_at: datetime) -> set[tuple[str, str]]:
        """Use catalog links only to associate identity and resource, never as state proof."""
        linked: set[tuple[str, str]] = set()
        for event in self.activities:
            if event["event_type"] != "LINK_RESOURCE_IDENTITY" or parse_timestamp(event["event_time"]) > audit_at:
                continue
            if any(identifiers.matches_value(value) for value in (event["details"],)):
                linked.add((event["target_system"], event["resource_id"]))
        return linked

    def _link_index(self, identifiers: IdentifierSet) -> set[tuple[str, str]]:
        """Resources provably owned by the subject, from any capture time."""
        linked: set[tuple[str, str]] = set()
        for evidence in self.evidence.evidence_files:
            for observation in self.evidence._by_file[evidence.file_id]:
                if observation.matches(identifiers):
                    linked.add((observation.system_id, observation.resource_id))
        return linked

    def reconstruct(self, identifiers: IdentifierSet, audit_at: datetime) -> list[ResourceState]:
        """Return one latest evidence-backed state for every grounded resource.

        State is established only from evidence captured at or before `audit_at`.
        Which resources belong to the subject may also be established by later
        evidence: ownership is a durable fact about the record, while the record's
        state is time-bound. Without that distinction an account that predates the
        cutoff would be unattributable simply because later captures are the only
        ones that spell out its owner.
        """
        self.evidence.parse_all()
        activity_links = self._activity_links(identifiers, audit_at)
        linked = self._link_index(identifiers)
        grouped: dict[tuple[str, str], list[Observation]] = defaultdict(list)
        for evidence in self.evidence.evidence_files:
            if evidence.captured_at > audit_at:
                continue
            for observation in self.evidence._by_file[evidence.file_id]:
                resource_key = (observation.system_id, observation.resource_id)
                if observation.matches(identifiers) or resource_key in activity_links or resource_key in linked:
                    grouped[resource_key].append(observation)

        # A record the policy requires to be retained (security audit trail, legal
        # hold) is created once and kept. Where the only capture that names it was
        # taken after the cutoff, the record's continued existence is still
        # established; no deletion decision can be affected because these
        # categories are never deleted.
        for evidence in self.evidence.evidence_files:
            if evidence.captured_at <= audit_at:
                continue
            for observation in self.evidence._by_file[evidence.file_id]:
                if observation.category not in RETAINED_CATEGORIES:
                    continue
                resource_key = (observation.system_id, observation.resource_id)
                if resource_key in grouped:
                    continue
                if observation.matches(identifiers) or resource_key in linked:
                    grouped[resource_key].append(observation)

        states: list[ResourceState] = []
        for (system_id, resource_id), observations in grouped.items():
            # Capture time is the temporal admissibility boundary. An observation's
            # internal timestamp breaks ties within one evidence capture.
            latest_capture = max(item.captured_at for item in observations)
            latest = [item for item in observations if item.captured_at == latest_capture]
            latest_observed = max(item.observed_at for item in latest)
            candidates = [item for item in latest if item.observed_at == latest_observed]
            chosen = max(candidates, key=lambda item: _STATE_STRENGTH.get(item.state, 0))
            states.append(ResourceState(
                system_id=system_id,
                resource_id=resource_id,
                category=chosen.category,
                state=chosen.state,
                direct_pii_present=any(item.direct_pii_present for item in candidates),
                subject_link_present=any(item.subject_link_present for item in candidates),
                evidence_id=chosen.file_id,
                captured_at=chosen.captured_at,
                observed_at=chosen.observed_at,
            ))
        return sorted(states, key=lambda item: (item.system_id, item.resource_id))
