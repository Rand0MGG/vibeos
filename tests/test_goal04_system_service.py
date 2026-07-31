from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from vibeos.audit import AuditLog
from vibeos.capabilities import CAPABILITIES
from vibeos.core.adapters.database import CoreDatabase
from vibeos.core.domain.task import TaskStatus
from vibeos.intent import RuleIntentBroker
from vibeos.model_gateway.contracts import (
    FailureCode,
    GatewayFailure,
    GatewayResult,
    ModelResponse,
    ModelUsage,
    ProviderRoute,
    RedactedTransportReceipt,
    SecretRef,
    ServiceActionProposal,
    ServiceDiagnosis,
)
from vibeos.model_gateway.secrets import ProviderRouteRepository
from vibeos.runtime_composition import RuntimeComponents, compose_runtime
from vibeos.system_service_contracts import (
    ServiceFactsV2,
    ServiceJournalFactV2,
    ServiceProcessFactV2,
    SystemServiceActionSpecV2,
    SystemServiceAdapterResultV2,
)
from vibeos.system_service_provider import SYNTHETIC_FAILURE_MARKER, ServiceProviderError
from vibeos.system_service_task import FIXED_SERVICE_GOAL, SystemServiceTaskService
from vibeos.system_service_task_support import SystemServiceRecoveryGuard
from vibeos.task_trace import TaskTraceStore


class SimulatedCrash(RuntimeError):
    pass


class FakeSystemServiceProvider:
    def __init__(
        self,
        *,
        load_state: str = "loaded",
        journal_available: bool = True,
        effective: bool = True,
        stale: bool = False,
        observation_error: ServiceProviderError | None = None,
    ) -> None:
        self.load_state = load_state
        self.journal_available = journal_available
        self.effective = effective
        self.stale = stale
        self.observation_error = observation_error
        self.healthy = False
        self.action_calls = 0
        self.observation_calls = 0

    def observe(self, *, include_journal: bool = True, journal_window_seconds: int = 120) -> ServiceFactsV2:
        del journal_window_seconds
        self.observation_calls += 1
        if self.observation_error is not None:
            raise self.observation_error
        captured = datetime.now(timezone.utc) - (timedelta(minutes=10) if self.stale else timedelta())
        journal = None
        if include_journal and self.journal_available and self.load_state == "loaded":
            lines = [f"{SYNTHETIC_FAILURE_MARKER} token_sha256=fixture"]
            if self.healthy:
                lines.append("VIBEOS_GOAL04_HEALTHY_V1 pid=4242")
            journal = ServiceJournalFactV2(
                since=(captured - timedelta(seconds=30)).isoformat(),
                until=captured.isoformat(),
                lines=tuple(lines),
                truncated=False,
            )
        return ServiceFactsV2(
            load_state=self.load_state,
            active_state="active" if self.healthy else "failed",
            sub_state="running" if self.healthy else "failed",
            result="success" if self.healthy else "exit-code",
            restart_count=0,
            process=ServiceProcessFactV2(
                main_pid=4242 if self.healthy else 0,
                running=self.healthy,
                exit_code=0 if self.healthy else 1,
                exit_status=0 if self.healthy else 23,
            ),
            journal=journal,
            source="systemd_user_dbus",
            captured_at=captured.isoformat(),
            ttl_seconds=30,
            evidence_reference=f"synthetic://goal04/service/{self.observation_calls}",
        )

    def execute(self, spec: SystemServiceActionSpecV2) -> SystemServiceAdapterResultV2:
        self.action_calls += 1
        if self.effective:
            self.healthy = True
        return SystemServiceAdapterResultV2(
            operation=spec.operation,
            status="succeeded",
            adapter="systemd_user_dbus",
            adapter_status="job-dispatched",
            external_reference=f"/job/{self.action_calls}",
        )


class FakeGateway:
    def __init__(self, *, locked_once: bool = False, failure: FailureCode | None = None) -> None:
        self.locked_once = locked_once
        self.failure = failure
        self.calls = 0

    def diagnose_service(self, *, route, binding, facts, budget, cancellation, request_id) -> GatewayResult:
        del budget, cancellation
        self.calls += 1
        if self.locked_once and self.calls == 1:
            return GatewayResult(
                status="waiting",
                failure=GatewayFailure(
                    request_id=request_id,
                    binding=binding,
                    code=FailureCode.KEYRING_LOCKED,
                    retryable=True,
                    delivery="not_sent",
                    safe_message="provider credential is waiting for keyring unlock",
                    wait_event_key=f"secret-service:unlocked:{route.secret_ref.secret_id}",
                ),
            )
        if self.failure is not None:
            return GatewayResult(
                status="failed",
                failure=GatewayFailure(
                    request_id=request_id,
                    binding=binding,
                    code=self.failure,
                    retryable=False,
                    delivery="unknown" if self.failure is FailureCode.PROVIDER_TIMEOUT else "confirmed",
                    safe_message=f"model gateway failed: {self.failure.value}",
                ),
            )
        from vibeos.model_gateway.contracts import facts_digest

        digest = facts_digest(facts)
        diagnosis = ServiceDiagnosis(
            diagnosis="The pre-armed fixture start failed and a bounded restart is appropriate.",
            confidence=0.99,
            proposal=ServiceActionProposal(action="restart", effect_level="E1", fact_digest=digest),
        )
        receipt = RedactedTransportReceipt(
            route_id=route.route_id,
            provider_request_id="provider-goal04-1",
            delivery="confirmed",
            transport_pid=os.getpid(),
            secret_ref_uri=route.secret_ref.uri,
            secret_resolved=True,
        )
        return GatewayResult(
            status="succeeded",
            response=ModelResponse(
                request_id=request_id,
                binding=binding,
                result=diagnosis,
                usage=ModelUsage(input_tokens=100, output_tokens=40, total_tokens=140),
                receipt=receipt,
            ),
        )


def _route() -> ProviderRoute:
    return ProviderRoute(
        route_id="goal04-test-route",
        model="test-model",
        base_url="https://provider.invalid/v1",
        secret_ref=SecretRef(secret_id="goal04-test-route", provider="openai-compatible"),
    )


def _components(
    tmp_path: Path,
    provider: FakeSystemServiceProvider,
    gateway: FakeGateway,
) -> tuple[RuntimeComponents, ProviderRouteRepository]:
    routes = ProviderRouteRepository(tmp_path / "provider-routes.json")
    routes.save(_route())
    components = compose_runtime(
        intent_broker=RuleIntentBroker(),
        database=CoreDatabase(tmp_path / "core.sqlite3"),
        audit=AuditLog(tmp_path / "audit.jsonl"),
        trace_store=TaskTraceStore(tmp_path / "runs"),
        system_service_provider=provider,  # type: ignore[arg-type]
        model_gateway=gateway,
        provider_route_repository=routes,
    )
    return components, routes


def test_goal04_golden_path_uses_one_canonical_receipt_and_independent_verification(tmp_path: Path) -> None:
    provider = FakeSystemServiceProvider()
    gateway = FakeGateway()
    components, _ = _components(tmp_path, provider, gateway)

    result = components.system_service_tasks.start(goal=FIXED_SERVICE_GOAL, route=_route(), run_id="golden")

    assert result.task.status is TaskStatus.SUCCEEDED
    assert provider.action_calls == 1
    assert provider.observation_calls >= 2
    assert gateway.calls == 1
    receipts = components.task_repository.receipts(result.task.task_id)
    assert len(receipts) == 1
    assert receipts[0].status == "succeeded"
    evidence_kinds = [json.loads(item.payload_json).get("kind") for item in components.task_repository.evidence(result.task.task_id)]
    assert evidence_kinds[:4] == ["service_facts", "context_manifest", "model_request_dispatched", "model_result"]
    assert evidence_kinds[-1] == "independent_verification"
    assert result.task.terminal_outcome is not None
    assert result.task.terminal_outcome.action == "restart"
    assert result.task.terminal_outcome.diagnosis
    assert result.task.terminal_outcome.current_state == "loaded/active/running/pid=4242"
    assert result.task.terminal_outcome.completion_judgment
    assert result.task.terminal_outcome.unresolved_risks == ()
    assert len(CAPABILITIES) == 19
    assert "system.service.recover_fixture" not in CAPABILITIES


def test_ambiguous_goal_awaits_clarification_without_observation_or_model(tmp_path: Path) -> None:
    provider = FakeSystemServiceProvider()
    gateway = FakeGateway()
    components, _ = _components(tmp_path, provider, gateway)

    result = components.system_service_tasks.start(goal="repair whichever service looks broken", route=_route(), run_id="ambiguous")

    assert result.task.status is TaskStatus.AWAITING_CLARIFICATION
    assert provider.observation_calls == 0
    assert provider.action_calls == 0
    assert gateway.calls == 0


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        (FakeSystemServiceProvider(load_state="not-found"), "unit_not_found"),
        (FakeSystemServiceProvider(journal_available=False), "journal_unavailable"),
        (FakeSystemServiceProvider(stale=True), "stale_fact"),
        (
            FakeSystemServiceProvider(observation_error=ServiceProviderError("permission_denied", "systemd user D-Bus denied access")),
            "permission_denied",
        ),
    ],
)
def test_observation_failures_reach_safe_terminal_without_model_or_action(tmp_path: Path, provider: FakeSystemServiceProvider, expected: str) -> None:
    gateway = FakeGateway()
    components, _ = _components(tmp_path, provider, gateway)
    result = components.system_service_tasks.start(goal=FIXED_SERVICE_GOAL, route=_route(), run_id=expected)
    assert result.task.status is TaskStatus.FAILED
    assert result.task.terminal_outcome is not None
    assert expected in result.task.terminal_outcome.completion_judgment
    assert gateway.calls == 0
    assert provider.action_calls == 0


@pytest.mark.parametrize("failure", [FailureCode.PROVIDER_TIMEOUT, FailureCode.SCHEMA_MISMATCH])
def test_gateway_failure_reaches_safe_terminal_without_action(tmp_path: Path, failure: FailureCode) -> None:
    provider = FakeSystemServiceProvider()
    components, _ = _components(tmp_path, provider, FakeGateway(failure=failure))
    result = components.system_service_tasks.start(goal=FIXED_SERVICE_GOAL, route=_route(), run_id=failure.value)
    assert result.task.status is TaskStatus.FAILED
    assert provider.action_calls == 0


def test_locked_keyring_waits_durably_then_unlock_resumes_same_task(tmp_path: Path) -> None:
    provider = FakeSystemServiceProvider()
    gateway = FakeGateway(locked_once=True)
    components, _ = _components(tmp_path, provider, gateway)
    waiting = components.system_service_tasks.start(goal=FIXED_SERVICE_GOAL, route=_route(), run_id="locked")
    assert waiting.task.status is TaskStatus.WAITING
    assert waiting.task.suspended_status is TaskStatus.PLANNING
    assert provider.action_calls == 0

    still_waiting = components.system_service_tasks.resume(waiting.task.task_id, route=_route(), run_id="still-locked")
    assert still_waiting.task.status is TaskStatus.WAITING
    resumed = components.system_service_tasks.resume(
        waiting.task.task_id,
        route=_route(),
        run_id="unlocked",
        keyring_unlocked=True,
    )
    assert resumed.task.status is TaskStatus.SUCCEEDED
    assert resumed.task.task_id == waiting.task.task_id
    assert provider.action_calls == 1
    assert gateway.calls == 2


def test_ineffective_recovery_fails_after_one_dispatch(tmp_path: Path) -> None:
    provider = FakeSystemServiceProvider(effective=False)
    components, _ = _components(tmp_path, provider, FakeGateway())
    result = components.system_service_tasks.start(goal=FIXED_SERVICE_GOAL, route=_route(), run_id="ineffective")
    assert result.task.status is TaskStatus.FAILED
    assert provider.action_calls == 1
    assert result.task.terminal_outcome is not None
    assert result.task.terminal_outcome.unresolved_risks == ("fixture remains unhealthy",)


@pytest.mark.parametrize(
    "boundary",
    [
        "before_fact_collection",
        "after_context_manifest",
        "before_model_call",
        "after_model_dispatch_commit",
        "after_model_call_before_commit",
        "after_typed_proposal_commit",
        "before_external_action",
        "after_dispatch_proposal_commit",
        "after_external_action_before_receipt",
        "before_independent_verify",
    ],
)
def test_crash_boundaries_resume_without_duplicate_unknown_side_effect(tmp_path: Path, boundary: str) -> None:
    provider = FakeSystemServiceProvider()
    gateway = FakeGateway()
    components, routes = _components(tmp_path, provider, gateway)
    crashed = False

    def checkpoint(stage: str) -> None:
        nonlocal crashed
        if stage == boundary and not crashed:
            crashed = True
            raise SimulatedCrash(stage)

    service = SystemServiceTaskService(
        engine=components.task_engine,
        repository=components.task_repository,
        planning=components.planning,
        observation=components.observation,
        gateway=gateway,
        route_repository=routes,
        checkpoint=checkpoint,
    )
    with pytest.raises(SimulatedCrash, match=boundary):
        service.start(goal=FIXED_SERVICE_GOAL, route=_route(), run_id=f"crash-{boundary}")
    task = components.task_repository.list(limit=1)[0]

    resumed_service = SystemServiceTaskService(
        engine=components.task_engine,
        repository=components.task_repository,
        planning=components.planning,
        observation=components.observation,
        gateway=gateway,
        route_repository=routes,
    )
    resumed = resumed_service.resume(task.task_id, route=_route(), run_id=f"resume-{boundary}")

    if boundary in {"after_model_dispatch_commit", "after_model_call_before_commit", "after_dispatch_proposal_commit"}:
        assert resumed.task.status is TaskStatus.PAUSED
        assert provider.action_calls == 0
        assert len(components.task_repository.receipts(task.task_id)) == 0
        assert gateway.calls == (1 if boundary == "after_model_call_before_commit" else 0 if boundary == "after_model_dispatch_commit" else 1)
    else:
        assert resumed.task.status is TaskStatus.SUCCEEDED
        assert provider.action_calls == 1
        assert len(components.task_repository.receipts(task.task_id)) == 1


def test_concurrent_dispatch_cannot_claim_the_same_task(tmp_path: Path) -> None:
    provider = FakeSystemServiceProvider()
    gateway = FakeGateway()
    components, routes = _components(tmp_path, provider, gateway)

    def crash_after_plan(stage: str) -> None:
        if stage == "before_external_action":
            raise SimulatedCrash(stage)

    service = SystemServiceTaskService(
        engine=components.task_engine,
        repository=components.task_repository,
        planning=components.planning,
        observation=components.observation,
        gateway=gateway,
        route_repository=routes,
        checkpoint=crash_after_plan,
    )
    with pytest.raises(SimulatedCrash):
        service.start(goal=FIXED_SERVICE_GOAL, route=_route(), run_id="concurrent-setup")
    task = components.task_repository.list(limit=1)[0]
    lease = components.task_repository.claim(task.task_id, owner="other-worker", now="2099-01-01T00:00:00Z", expires_at="2099-01-01T00:01:00Z")
    assert lease is not None
    blocked = components.system_service_tasks.resume(task.task_id, route=_route(), run_id="concurrent-second")
    assert blocked.task.revision == task.revision
    assert provider.action_calls == 0


def test_goal04_secret_canary_does_not_escape_gateway_memory(tmp_path: Path) -> None:
    canary = "goal04-high-entropy-provider-key-7f542c4bd0f946af"

    class SecretBearingGateway(FakeGateway):
        def __init__(self) -> None:
            super().__init__()
            self.transport_only_secret = canary

    provider = FakeSystemServiceProvider()
    components, _ = _components(tmp_path, provider, SecretBearingGateway())
    result = components.system_service_tasks.start(goal=FIXED_SERVICE_GOAL, route=_route(), run_id="leak-canary")
    assert result.task.status is TaskStatus.SUCCEEDED
    persisted = b"\n".join(path.read_bytes() for path in tmp_path.rglob("*") if path.is_file())
    exported = json.dumps(result.payload(), ensure_ascii=False, default=str)
    assert canary.encode() not in persisted
    assert canary not in exported
    assert canary not in "\0".join(sys.argv)
    assert canary not in "\0".join(f"{key}={value}" for key, value in os.environ.items())


def test_fixture_unit_is_disabled_notify_service_with_one_writable_state_scope() -> None:
    root = Path(__file__).resolve().parents[1]
    unit = (root / "fixtures/systemd/vibeos-goal04-fixture.service.in").read_text(encoding="utf-8")
    installer = (root / "scripts/install_goal04_fixture.sh").read_text(encoding="utf-8")
    assert "Type=notify" in unit
    assert "ProtectHome=read-only" in unit
    assert "ReadWritePaths=%h/.local/state/vibeos/goal04-fixture" in unit
    assert "RestrictAddressFamilies=AF_UNIX" in unit
    assert "disable --now vibeos-goal04-fixture.service" in installer
    assert "systemctl --user enable" not in installer


@pytest.mark.parametrize(
    "boundary",
    [
        "before_fact_collection",
        "after_fact_collection_before_commit",
        "after_context_manifest",
        "before_model_call",
        "after_model_dispatch_commit",
        "after_model_call_before_commit",
        "after_typed_proposal_commit",
        "before_external_action",
        "after_dispatch_proposal_commit",
        "after_external_action_before_receipt",
        "before_independent_verify",
        "while_waiting",
    ],
)
def test_real_worker_process_crash_matrix_recovers_once(tmp_path: Path, boundary: str) -> None:
    worker = Path(__file__).with_name("goal04_crash_worker.py")
    root = tmp_path / boundary
    locked = ["--locked-once"] if boundary == "while_waiting" else []
    crashed = subprocess.run(
        [sys.executable, str(worker), str(root), "start", boundary, *locked],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert crashed.returncode == 97, crashed.stderr
    time.sleep(1.1)
    resumed = subprocess.run(
        [sys.executable, str(worker), str(root), "resume", "never", *locked],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    paused_boundaries = {"after_model_dispatch_commit", "after_model_call_before_commit", "after_dispatch_proposal_commit"}
    expected_returncode = 2 if boundary in paused_boundaries else 0
    assert resumed.returncode == expected_returncode, resumed.stderr
    payload = json.loads(resumed.stdout.splitlines()[-1])
    if boundary in paused_boundaries:
        assert payload["status"] == "paused"
        assert payload["action_calls"] == 0
        assert payload["receipt_count"] == 0
        assert payload["gateway_calls"] == (1 if boundary == "after_model_call_before_commit" else 0 if boundary == "after_model_dispatch_commit" else 1)
    else:
        assert payload["status"] == "succeeded"
        assert payload["action_calls"] == 1
        assert payload["receipt_count"] == 1


def test_specialized_resumer_enforces_expired_task_deadline_before_model_call(tmp_path: Path, monkeypatch) -> None:
    provider = FakeSystemServiceProvider()
    gateway = FakeGateway()
    components, routes = _components(tmp_path, provider, gateway)

    def crash_before_model(stage: str) -> None:
        if stage == "before_model_call":
            raise SimulatedCrash(stage)

    service = SystemServiceTaskService(
        engine=components.task_engine,
        repository=components.task_repository,
        planning=components.planning,
        observation=components.observation,
        gateway=gateway,
        route_repository=routes,
        checkpoint=crash_before_model,
    )
    with pytest.raises(SimulatedCrash, match="before_model_call"):
        service.start(goal=FIXED_SERVICE_GOAL, route=_route(), run_id="expired-specialized")
    task = components.task_repository.list(limit=1)[0]
    assert gateway.calls == 0

    monkeypatch.setattr(SystemServiceRecoveryGuard, "deadline_remaining_seconds", staticmethod(lambda _state: -1.0))
    components.task_engine.resume_task(task.task_id)

    expired = components.task_repository.get(task.task_id)
    assert expired is not None
    assert expired.status is TaskStatus.FAILED
    assert expired.last_event == "timeout"
    assert gateway.calls == 0
    assert provider.action_calls == 0
