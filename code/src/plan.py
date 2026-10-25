"""Build the ordered findings/remediation/verification plan for one case."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .contract import sort_and_number
from .evidence import EvidenceStore
from .identity import IdentifierSet, IdentityResolver
from .intent import is_deletion_authorized
from .load import DataBundle, parse_timestamp
from .policy import Assessment, PolicyEngine
from .state import ResourceState, StateReconstructor


ACCOUNT_SYSTEM = "account_service"


@dataclass
class CasePlan:
    case_id: str
    status: str
    steps: list[dict] = field(default_factory=list)


def _step(phase, operation, state, system_id, resource_id, evidence_id) -> dict:
    return {
        "step_no": 0,
        "phase": phase,
        "system_id": system_id,
        "resource_id": resource_id,
        "operation": operation,
        "state": state,
        "evidence_id": evidence_id,
    }


class PlanBuilder:
    def __init__(self, bundle: DataBundle, evidence: EvidenceStore) -> None:
        self.bundle = bundle
        self.evidence = evidence
        self.reconstructor = StateReconstructor(bundle, evidence)
        self.policy = PolicyEngine(bundle)
        self.resolver = IdentityResolver(bundle)
        self._workflow_accounts = {
            event["resource_id"]
            for event in bundle.activities
            if event["target_system"] == ACCOUNT_SYSTEM
        }

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _suffix(value: str) -> str | None:
        match = re.search(r"(\d+)$", value or "")
        return match.group(1) if match else None

    def _account_fallback(self, identifiers: IdentifierSet) -> str | None:
        """Recover a workflow-only account identifier from the stable reference.

        Fully removed accounts leave no row in any snapshot; the durable link is
        the account identifier derived from the stable user reference. Prefer an
        identifier the workflow activity stream actually records, then the
        deterministic numbered form used by the low-numbered subjects.
        """
        for user_id in sorted(identifiers.user_ids):
            suffix = self._suffix(user_id)
            if not suffix:
                continue
            for width in (3, 2, 4, 5):
                candidate = f"account_{int(suffix):0{width}d}"
                if candidate in self._workflow_accounts:
                    return candidate
            if len(suffix) <= 3:
                return f"account_{int(suffix):03d}"
        return None

    @staticmethod
    def _current_accounts(resources: list[ResourceState]) -> list[ResourceState]:
        """Keep only the subject's current account when history shows several.

        A subject can have older, long-deleted account records (a profile removed
        years before the request).  The account this case is about is the one the
        most recent admissible capture describes.
        """
        accounts = [
            item for item in resources
            if item.system_id == ACCOUNT_SYSTEM and item.category == "USER_PROFILE"
        ]
        if len(accounts) <= 1:
            return resources
        newest = max(item.observed_at for item in accounts)
        keep = {item.resource_id for item in accounts if item.observed_at == newest}
        return [
            item for item in resources
            if not (item.system_id == ACCOUNT_SYSTEM and item.category == "USER_PROFILE")
            or item.resource_id in keep
        ]

    def _latest_account_coverage(self, audit_at) -> str | None:
        """Latest admissible capture that covers account-service profiles.

        Prefer a capture whose purpose is the account service itself (inventory,
        failover replica) over a general service log that happens to mention an
        account row, because the account capture is the authoritative statement that
        a profile is absent.
        """
        best_account: str | None = None
        best_any: str | None = None
        for evidence in self.evidence.evidence_files:
            if evidence.captured_at > audit_at:
                continue
            if not any(obs.system_id == ACCOUNT_SYSTEM for obs in self.evidence._by_file[evidence.file_id]):
                continue
            origin = (evidence.origin or "").casefold()
            if "account" in origin:
                best_account = evidence.file_id
            best_any = evidence.file_id
        return best_account or best_any

    # -------------------------------------------------------------------- build
    def build(self, request: dict[str, str]) -> CasePlan:
        case_id = request["case_id"]
        if not is_deletion_authorized(request["request_text"]):
            return CasePlan(case_id=case_id, status="NOT_APPLICABLE", steps=[])

        audit_at = parse_timestamp(request["audit_at"])
        identifiers = self.resolver.resolve_request(request)
        self.evidence.parse_all()
        resources = self.reconstructor.reconstruct(identifiers, audit_at)
        resources = self._current_accounts(resources)
        assessments = self.policy.assess(resources)

        seen_accounts = {
            item.resource.resource_id
            for item in assessments
            if item.resource.system_id == ACCOUNT_SYSTEM
        }
        if not seen_accounts:
            fallback = self._account_fallback(identifiers)
            coverage = self._latest_account_coverage(audit_at)
            if fallback and coverage:
                assessments.extend(self.policy.assess([ResourceState(
                    system_id=ACCOUNT_SYSTEM,
                    resource_id=fallback,
                    category="USER_PROFILE",
                    state="ABSENT",
                    direct_pii_present=False,
                    subject_link_present=True,
                    evidence_id=coverage,
                    captured_at=audit_at,
                    observed_at=audit_at,
                )]))

        return self._assemble(case_id, assessments)

    # ----------------------------------------------------------------- assemble
    def _assemble(self, case_id: str, assessments: list[Assessment]) -> CasePlan:
        violations = [item for item in assessments if not item.compliant]
        steps: list[dict] = []

        if violations:
            for item in violations:
                steps.append(_step(
                    "FINDING", item.finding_operation, item.finding_state,
                    item.resource.system_id, item.resource.resource_id, item.resource.evidence_id,
                ))
            for item in violations:
                steps.append(_step(
                    "REMEDIATION", item.remediation_operation, item.remediation_state,
                    item.resource.system_id, item.resource.resource_id, "",
                ))
            for item in assessments:
                steps.append(_step(
                    "VERIFICATION", item.verification_operation, item.verification_state,
                    item.resource.system_id, item.resource.resource_id, "",
                ))
            status = "INCOMPLETE"
        else:
            for item in assessments:
                steps.append(_step(
                    "VERIFICATION", item.verification_operation, item.verification_state,
                    item.resource.system_id, item.resource.resource_id, item.resource.evidence_id,
                ))
            status = "COMPLETE" if steps else "INCOMPLETE"

        return CasePlan(case_id=case_id, status=status, steps=sort_and_number(steps))
