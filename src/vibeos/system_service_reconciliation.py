from __future__ import annotations

import json

from .core.domain.task import ActionProposal
from .observation_service import ObservationService
from .system_service_contracts import FIXTURE_UNIT
from .task_models import StepExecutionResult
from .task_reconciliation import ReconciliationResult


class SystemServiceActionReconciler:
    """Re-observes the fixed unit before deciding whether redispatch is safe."""

    def __init__(self, observation: ObservationService) -> None:
        self.observation = observation

    def reconcile(self, proposal: ActionProposal) -> ReconciliationResult:
        if proposal.action != "system.service.recover_fixture":
            return ReconciliationResult("unknown", "proposal is outside the fixed system-service recovery action")
        try:
            payload = json.loads(proposal.request_json)
            target = payload["target"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return ReconciliationResult("unknown", "persisted system-service proposal is malformed")
        if not isinstance(target, dict) or target.get("unit") != FIXTURE_UNIT or target.get("operation") not in {"start", "restart"}:
            return ReconciliationResult("unknown", "persisted system-service proposal escaped its fixed binding")
        try:
            facts = self.observation.observe_service_fixture(include_journal=False)
        except Exception:
            return ReconciliationResult("unknown", "independent system-service reconciliation observation failed")
        if facts.active_state == "active" and facts.sub_state == "running" and facts.process.running:
            return ReconciliationResult(
                "succeeded",
                "independent systemd observation proved the prior action succeeded",
                StepExecutionResult(
                    step_id=proposal.step_id,
                    layer="system_service_reconciliation",
                    status="succeeded",
                    adapter=facts.source,
                    capability_id=proposal.capability_id,
                    attempt_id=proposal.attempt_id,
                    adapter_status="reconciled-active",
                    result={"unit": facts.unit, "active_state": facts.active_state, "sub_state": facts.sub_state, "reconciled": True},
                ),
            )
        return ReconciliationResult(
            "unknown",
            f"systemd action outcome remains {facts.active_state}/{facts.sub_state}; absence of health does not prove the restart was never dispatched",
        )
