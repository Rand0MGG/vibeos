from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
import subprocess
import sys
from time import monotonic

from pydantic import ValidationError

from ..system_service_contracts import ServiceFactsV2
from .contracts import (
    CancellationBinding,
    DeliveryState,
    FailureCode,
    GatewayFailure,
    GatewayResult,
    ModelBudget,
    ProviderRoute,
    SemanticWorkerInvocation,
    SemanticWorkerOutput,
    TaskAttemptBinding,
    TransportEnvelope,
)


ProcessRunner = Callable[[list[str], str, dict[str, str], float], subprocess.CompletedProcess[str]]


def _run_process(argv: list[str], payload: str, environment: dict[str, str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, input=payload, capture_output=True, text=True, env=environment, timeout=timeout, check=False)


class ModelGateway:
    """The sole Goal04 authority for request binding, budget and failure semantics."""

    def __init__(self, process_runner: ProcessRunner | None = None) -> None:
        self.process_runner = process_runner or _run_process

    def diagnose_service(
        self,
        *,
        route: ProviderRoute,
        binding: TaskAttemptBinding,
        facts: ServiceFactsV2,
        budget: ModelBudget,
        cancellation: CancellationBinding,
        request_id: str,
    ) -> GatewayResult:
        started = monotonic()
        if cancellation.requested:
            return self._preflight_failure(request_id, binding, FailureCode.CANCELLED, "model request was cancelled")
        invocation = SemanticWorkerInvocation(
            request_id=request_id,
            binding=binding,
            facts=facts,
            budget=budget,
            cancellation=cancellation,
        )
        try:
            semantic = self.process_runner(
                [sys.executable, "-m", "vibeos.model_gateway.semantic_worker"],
                invocation.model_dump_json(),
                self._semantic_environment(),
                budget.total_budget_seconds,
            )
        except subprocess.TimeoutExpired:
            return self._preflight_failure(request_id, binding, FailureCode.BUDGET_EXHAUSTED, "semantic request budget was exhausted")
        if semantic.returncode != 0:
            return self._preflight_failure(request_id, binding, FailureCode.SCHEMA_MISMATCH, "semantic worker rejected the request")
        try:
            semantic_output = SemanticWorkerOutput.model_validate_json(semantic.stdout)
        except ValidationError:
            return self._preflight_failure(request_id, binding, FailureCode.SCHEMA_MISMATCH, "semantic worker returned an invalid contract")
        if semantic_output.session_bus_present or semantic_output.secret_environment_present:
            return self._preflight_failure(request_id, binding, FailureCode.ISOLATION_VIOLATION, "semantic worker isolation check failed")
        remaining = budget.total_budget_seconds - (monotonic() - started)
        if remaining <= 0:
            return self._preflight_failure(request_id, binding, FailureCode.BUDGET_EXHAUSTED, "model request budget was exhausted")
        if cancellation.requested:
            return self._preflight_failure(request_id, binding, FailureCode.CANCELLED, "model request was cancelled")
        envelope = TransportEnvelope(route=route, request=semantic_output.request)
        try:
            transport = self.process_runner(
                [sys.executable, "-m", "vibeos.model_gateway.transport_worker"],
                envelope.model_dump_json(),
                self._transport_environment(),
                remaining,
            )
        except subprocess.TimeoutExpired:
            return self._preflight_failure(
                request_id,
                binding,
                FailureCode.UNKNOWN_DELIVERY,
                "provider transport exceeded the total budget; delivery is unknown",
                delivery="unknown",
            )
        if transport.returncode != 0:
            return self._preflight_failure(
                request_id,
                binding,
                FailureCode.UNKNOWN_DELIVERY,
                "provider transport failed without a classified result; delivery is unknown",
                delivery="unknown",
            )
        try:
            return GatewayResult.model_validate_json(transport.stdout)
        except ValidationError:
            return self._preflight_failure(
                request_id,
                binding,
                FailureCode.SCHEMA_MISMATCH,
                "provider transport returned an invalid contract",
                delivery="unknown",
            )

    @staticmethod
    def _base_environment() -> dict[str, str]:
        allowed = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL", "VIRTUAL_ENV")
        environment = {name: value for name in allowed if (value := os.environ.get(name))}
        source_root = str(Path(__file__).resolve().parents[2])
        existing = os.environ.get("PYTHONPATH", "")
        safe_paths = [entry for entry in existing.split(os.pathsep) if entry and not any(marker in entry.upper() for marker in ("SECRET", "TOKEN", "KEY"))]
        environment["PYTHONPATH"] = os.pathsep.join([source_root, *safe_paths])
        environment["PYTHONIOENCODING"] = "utf-8"
        return environment

    @classmethod
    def _semantic_environment(cls) -> dict[str, str]:
        return cls._base_environment()

    @classmethod
    def _transport_environment(cls) -> dict[str, str]:
        environment = cls._base_environment()
        session_bus = os.environ.get("DBUS_SESSION_BUS_ADDRESS")
        if session_bus:
            environment["DBUS_SESSION_BUS_ADDRESS"] = session_bus
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
        if runtime_dir:
            environment["XDG_RUNTIME_DIR"] = runtime_dir
        return environment

    @staticmethod
    def _preflight_failure(
        request_id: str,
        binding: TaskAttemptBinding,
        code: FailureCode,
        message: str,
        *,
        delivery: DeliveryState = "not_sent",
    ) -> GatewayResult:
        return GatewayResult(
            status="failed",
            failure=GatewayFailure(
                request_id=request_id,
                binding=binding,
                code=code,
                retryable=False,
                delivery=delivery,
                safe_message=message,
            ),
        )
