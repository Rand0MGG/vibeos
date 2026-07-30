from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from vibeos.audit import AuditLog
from vibeos.core.adapters.database import CoreDatabase
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
    facts_digest,
)
from vibeos.model_gateway.secrets import ProviderRouteRepository
from vibeos.runtime_composition import compose_runtime
from vibeos.system_service_contracts import ServiceFactsV2, ServiceJournalFactV2, ServiceProcessFactV2, SystemServiceActionSpecV2, SystemServiceAdapterResultV2
from vibeos.system_service_provider import SYNTHETIC_FAILURE_MARKER
from vibeos.system_service_task import FIXED_SERVICE_GOAL, SystemServiceTaskService
from vibeos.task_trace import TaskTraceStore


def route() -> ProviderRoute:
    return ProviderRoute(
        route_id="goal04-process-crash-route",
        model="test-model",
        base_url="https://provider.invalid/v1",
        secret_ref=SecretRef(secret_id="goal04-process-crash-route", provider="openai-compatible"),
    )


class FileSystemServiceProvider:
    def __init__(self, state_path: Path) -> None:
        self.state_path = state_path
        if not state_path.exists():
            self._write({"healthy": False, "action_calls": 0, "observation_calls": 0})

    def observe(self, *, include_journal: bool = True, journal_window_seconds: int = 120) -> ServiceFactsV2:
        del journal_window_seconds
        state = self._read()
        state["observation_calls"] = int(state["observation_calls"]) + 1
        self._write(state)
        captured = datetime.now(timezone.utc)
        lines = [f"{SYNTHETIC_FAILURE_MARKER} token_sha256=process-crash-fixture"]
        if bool(state["healthy"]):
            lines.append("VIBEOS_GOAL04_HEALTHY_V1 pid=4242")
        journal = (
            ServiceJournalFactV2(
                since=captured.isoformat(),
                until=captured.isoformat(),
                lines=tuple(lines),
                truncated=False,
            )
            if include_journal
            else None
        )
        healthy = bool(state["healthy"])
        return ServiceFactsV2(
            load_state="loaded",
            active_state="active" if healthy else "failed",
            sub_state="running" if healthy else "failed",
            result="success" if healthy else "exit-code",
            restart_count=0,
            process=ServiceProcessFactV2(main_pid=4242 if healthy else 0, running=healthy, exit_code=0 if healthy else 1, exit_status=0 if healthy else 23),
            journal=journal,
            source="systemd_user_dbus",
            captured_at=captured.isoformat(),
            ttl_seconds=30,
            evidence_reference=f"file-fixture://observation/{state['observation_calls']}",
        )

    def execute(self, spec: SystemServiceActionSpecV2) -> SystemServiceAdapterResultV2:
        state = self._read()
        state["healthy"] = True
        state["action_calls"] = int(state["action_calls"]) + 1
        self._write(state)
        return SystemServiceAdapterResultV2(
            operation=spec.operation,
            status="succeeded",
            adapter="systemd_user_dbus",
            adapter_status="job-dispatched",
            external_reference=f"/job/{state['action_calls']}",
        )

    def _read(self) -> dict[str, object]:
        value = json.loads(self.state_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise RuntimeError("invalid process-crash fixture state")
        return value

    def _write(self, value: dict[str, object]) -> None:
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
        temporary.replace(self.state_path)


class FileGateway:
    def __init__(self, state_path: Path, *, locked_once: bool) -> None:
        self.state_path = state_path
        self.locked_once = locked_once
        if not state_path.exists():
            state_path.write_text('{"calls":0}', encoding="utf-8")

    def diagnose_service(self, *, route, binding, facts, budget, cancellation, request_id) -> GatewayResult:
        del budget, cancellation
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        state["calls"] = int(state["calls"]) + 1
        self.state_path.write_text(json.dumps(state), encoding="utf-8")
        if self.locked_once and state["calls"] == 1:
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
        diagnosis = ServiceDiagnosis(
            diagnosis="The deterministic fixture failed once and should be restarted.",
            confidence=0.99,
            proposal=ServiceActionProposal(action="restart", effect_level="E1", fact_digest=facts_digest(facts)),
        )
        return GatewayResult(
            status="succeeded",
            response=ModelResponse(
                request_id=request_id,
                binding=binding,
                result=diagnosis,
                usage=ModelUsage(input_tokens=50, output_tokens=20, total_tokens=70),
                receipt=RedactedTransportReceipt(
                    route_id=route.route_id,
                    provider_request_id="process-crash-provider-request",
                    delivery="confirmed",
                    transport_pid=os.getpid(),
                    secret_ref_uri=route.secret_ref.uri,
                    secret_resolved=True,
                ),
            ),
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("mode", choices=("start", "resume"))
    parser.add_argument("boundary")
    parser.add_argument("--locked-once", action="store_true")
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True)
    provider = FileSystemServiceProvider(args.root / "provider-state.json")
    gateway = FileGateway(args.root / "gateway-state.json", locked_once=args.locked_once)
    routes = ProviderRouteRepository(args.root / "routes.json")
    routes.save(route())
    components = compose_runtime(
        intent_broker=RuleIntentBroker(),
        database=CoreDatabase(args.root / "core.sqlite3"),
        audit=AuditLog(args.root / "audit.jsonl"),
        trace_store=TaskTraceStore(args.root / "runs"),
        system_service_provider=provider,  # type: ignore[arg-type]
        model_gateway=gateway,
        provider_route_repository=routes,
    )

    def checkpoint(stage: str) -> None:
        if stage == args.boundary:
            os._exit(97)

    service = SystemServiceTaskService(
        engine=components.task_engine,
        repository=components.task_repository,
        planning=components.planning,
        observation=components.observation,
        gateway=gateway,
        route_repository=routes,
        checkpoint=checkpoint,
        lease_seconds=1,
    )
    if args.mode == "start":
        result = service.start(goal=FIXED_SERVICE_GOAL, route=route(), run_id=f"process-{args.boundary}")
    else:
        task = components.task_repository.list(limit=1)[0]
        result = service.resume(task.task_id, route=route(), run_id=f"resume-{args.boundary}", keyring_unlocked=args.locked_once)
    provider_state = json.loads((args.root / "provider-state.json").read_text(encoding="utf-8"))
    gateway_state = json.loads((args.root / "gateway-state.json").read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "status": result.task.status.value,
                "task_id": result.task.task_id,
                "action_calls": provider_state["action_calls"],
                "gateway_calls": gateway_state["calls"],
                "receipt_count": len(components.task_repository.receipts(result.task.task_id)),
            },
            sort_keys=True,
        )
    )
    return 0 if result.task.status.value == "succeeded" else 2


if __name__ == "__main__":
    raise SystemExit(main())
