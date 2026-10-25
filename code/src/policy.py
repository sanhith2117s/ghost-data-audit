"""Policy engine: required treatment and compliance, driven by deletion_rules.csv.

Each category has two independent things:

* the treatment the supplied policy requires (DELETE / REDACT / ANONYMIZE / RETAIN),
  read from `deletion_rules.csv` at runtime; and
* the *effective* state of the record, which is what the evidence shows once the
  category is taken into account.  An analytics or support row that carries no
  direct identifier is already anonymized/redacted in substance even though the
  row still exists, so its effective state is ANONYMIZED/REDACTED rather than
  PRESENT.

Compliance is then a simple comparison of effective state against the treatment.
"""

from __future__ import annotations

from dataclasses import dataclass

from .load import DataBundle
from .state import ResourceState


@dataclass(frozen=True)
class Rule:
    rule_id: str
    data_category: str
    condition: str
    required_action: str


@dataclass(frozen=True)
class Assessment:
    resource: ResourceState
    rule: Rule
    treatment: str
    effective_state: str
    compliant: bool
    finding_operation: str | None
    finding_state: str | None
    remediation_operation: str | None
    remediation_state: str | None
    verification_operation: str
    verification_state: str


# Categories whose retention is verified as "record still present".
_PRESENT_RETAINED = {"SECURITY_AUDIT_LOG", "LEGAL_HOLD_RECORD"}
# Categories that stay in place but must lose direct identifiers.
_REDACT_CATEGORIES = {"SUPPORT_MESSAGE", "EMAIL_DELIVERY_LOG"}
_ANONYMIZE_CATEGORIES = {"ANALYTICS_PROFILE", "PRODUCT_TELEMETRY"}


class PolicyEngine:
    def __init__(self, bundle: DataBundle) -> None:
        self.rules: dict[str, Rule] = {}
        self.derived_rules: dict[str, Rule] = {}
        for row in bundle.rules:
            self.rules[row["data_category"]] = Rule(
                rule_id=row["rule_id"],
                data_category=row["data_category"],
                condition=row["condition"],
                required_action=row["required_action"],
            )

    def rule_for(self, category: str) -> Rule:
        if category in self.rules:
            return self.rules[category]
        if category in {"DATA_FLOW", "SUPPORT_ATTACHMENT_RETAINED"}:
            # Control-plane categories are governed by the archive/retention rules.
            return Rule(
                rule_id="rule_backup_archive",
                data_category=category,
                condition="Restore access can be blocked",
                required_action="RETAIN",
            )
        # Unknown category: the supplied policy has no row, so fall back to the
        # privacy-conservative treatment (remove the record). The derived rule is
        # recorded so the run reports exactly where judgement was applied.
        derived = Rule(
            rule_id=f"derived:{category}",
            data_category=category,
            condition="No explicit policy row; privacy-conservative default",
            required_action="DELETE",
        )
        self.derived_rules[category] = derived
        return derived

    # ---------------------------------------------------------------- treatment
    def _treatment(self, category: str, resource: ResourceState) -> str:
        rule = self.rule_for(category)
        # Backup archives and live connectors are access-control objects: the
        # archive itself may be retained, but it must not be restorable/active.
        if category in {"BACKUP_ARCHIVE", "DATA_FLOW"}:
            return "RETAIN"
        if category in _PRESENT_RETAINED:
            return "RETAIN"
        if category == "SUPPORT_ATTACHMENT":
            return rule.required_action
        # Condition-bearing categories fall back to the policy's own treatment.
        return rule.required_action

    @staticmethod
    def _effective_state(category: str, resource: ResourceState) -> str:
        state = resource.state
        if category in _ANONYMIZE_CATEGORIES:
            if state == "ABSENT":
                return "ABSENT"
            return "PRESENT" if resource.direct_pii_present else "ANONYMIZED"
        if category in _REDACT_CATEGORIES or category == "ACCOUNT_TOMBSTONE":
            if state == "ABSENT":
                return "ABSENT"
            return "PRESENT" if resource.direct_pii_present else "REDACTED"
        if category == "BACKUP_ARCHIVE":
            if state == "ABSENT":
                return "ABSENT"
            return "BLOCKED" if state == "BLOCKED" else "ACTIVE"
        if category == "DATA_FLOW":
            if state == "ABSENT":
                return "ABSENT"
            return "DISABLED" if state in {"DISABLED", "BLOCKED"} else "ACTIVE"
        return state

    # ------------------------------------------------------------------- assess
    def assess(self, resources: list[ResourceState]) -> list[Assessment]:
        return [self._assess_one(resource) for resource in resources]

    def _assess_one(self, resource: ResourceState) -> Assessment:
        category = resource.category or ""
        rule = self.rule_for(category)
        treatment = self._treatment(category, resource)
        effective = self._effective_state(category, resource)

        if treatment == "DELETE":
            compliant = effective == "ABSENT"
            finding, finding_state = "REPORT_REMAINING_RECORD", "PRESENT"
            remediation, remediation_state = "DELETE_RECORD", "ABSENT"
            verification, verification_state = "VERIFY_RECORD_ABSENT", "ABSENT"
        elif treatment == "REDACT":
            compliant = effective == "REDACTED"
            finding, finding_state = "REPORT_EXPOSED_PII", "PRESENT"
            remediation, remediation_state = "REDACT_RECORD", "REDACTED"
            verification, verification_state = "VERIFY_RECORD_REDACTED", "REDACTED"
        elif treatment == "ANONYMIZE":
            compliant = effective == "ANONYMIZED"
            finding, finding_state = "REPORT_EXPOSED_PII", "PRESENT"
            remediation, remediation_state = "ANONYMIZE_RECORD", "ANONYMIZED"
            verification, verification_state = "VERIFY_RECORD_ANONYMIZED", "ANONYMIZED"
        elif treatment == "RETAIN":
            if category == "BACKUP_ARCHIVE":
                compliant = effective in {"BLOCKED", "ABSENT"}
                finding, finding_state = "REPORT_RESTORE_RISK", "ACTIVE"
                remediation, remediation_state = "BLOCK_RESTORE", "BLOCKED"
                verification, verification_state = "VERIFY_RESTORE_BLOCKED", "BLOCKED"
            elif category == "DATA_FLOW":
                compliant = effective in {"DISABLED", "ABSENT"}
                finding, finding_state = "REPORT_ACTIVE_DATA_FLOW", "ACTIVE"
                remediation, remediation_state = "DISABLE_DATA_FLOW", "DISABLED"
                verification, verification_state = "VERIFY_DATA_FLOW_DISABLED", "DISABLED"
            else:
                compliant = effective in {"PRESENT", "REDACTED", "ANONYMIZED"}
                finding, finding_state = None, None
                remediation, remediation_state = None, None
                verification, verification_state = "VERIFY_RECORD_PRESENT", "PRESENT"
        else:
            raise ValueError(f"unsupported treatment {treatment!r}")

        return Assessment(
            resource=resource,
            rule=rule,
            treatment=treatment,
            effective_state=effective,
            compliant=compliant,
            finding_operation=finding,
            finding_state=finding_state,
            remediation_operation=remediation,
            remediation_state=remediation_state,
            verification_operation=verification,
            verification_state=verification_state,
        )
