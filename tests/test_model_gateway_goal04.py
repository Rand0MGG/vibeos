from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.error

import pytest

from vibeos.cli import build_parser, run_secret_command
from vibeos.config import load_dotenv
from vibeos.core.domain.task import TaskRun, TaskStatus
from vibeos.core.domain.task_transitions import transition
from vibeos.model_gateway.contracts import (
    CancellationBinding,
    FailureCode,
    GatewayResult,
    ModelBudget,
    ModelUsage,
    ProviderRoute,
    RedactedTransportReceipt,
    SecretRef,
    SemanticWorkerInvocation,
    ServiceActionProposal,
    ServiceDiagnosis,
    TaskAttemptBinding,
    TransportEnvelope,
    build_model_request,
    facts_digest,
)
from vibeos.model_gateway.gateway import ModelGateway
from vibeos.model_gateway.provider import OpenAICompatibleTransport, ProviderHttpResponse
from vibeos.model_gateway.secrets import ProviderRouteRepository, SecretStatus, SecretStoreLocked, SecretToolSecretStore
from vibeos.model_gateway.task_integration import keyring_unlocked_event, keyring_wait_event
from vibeos.system_service_contracts import ServiceFactsV2, ServiceProcessFactV2


LEAK_CANARY = "goal04-secret-leak-canary"


def _facts(*, active_state: str = "failed", captured_at: str | None = None) -> ServiceFactsV2:
    return ServiceFactsV2(
        load_state="loaded",
        active_state=active_state,
        sub_state="failed" if active_state == "failed" else "dead",
        result="exit-code",
        restart_count=0,
        process=ServiceProcessFactV2(main_pid=0, running=False, exit_code=1, exit_status=1),
        source="systemd_user_dbus",
        captured_at=captured_at or datetime.now(timezone.utc).isoformat(),
        ttl_seconds=60,
        evidence_reference="synthetic://goal04/d0/service-facts",
    )


def _binding() -> TaskAttemptBinding:
    return TaskAttemptBinding(task_id="task-goal04", attempt_id="attempt-goal04-1", attempt_number=1)


def _budget(**overrides: object) -> ModelBudget:
    values: dict[str, object] = {
        "timeout_seconds": 5.0,
        "total_budget_seconds": 10.0,
        "max_output_tokens": 256,
        "max_total_tokens": 1024,
    }
    values.update(overrides)
    return ModelBudget.model_validate(values)


def _route() -> ProviderRoute:
    return ProviderRoute(
        route_id="goal04-provider",
        model="test-model",
        base_url="https://provider.invalid/v1",
        secret_ref=SecretRef(secret_id="goal04-provider", provider="openai-compatible"),
    )


def _request(*, facts: ServiceFactsV2 | None = None, cancellation: bool = False, budget: ModelBudget | None = None):
    invocation = SemanticWorkerInvocation(
        request_id="request-goal04-1",
        binding=_binding(),
        facts=facts or _facts(),
        budget=budget or _budget(),
        cancellation=CancellationBinding(token_id="cancel-goal04-1", requested=cancellation),
    )
    return build_model_request(invocation)


class FakeSecretStore:
    def __init__(self, value: str = LEAK_CANARY, *, locked: bool = False) -> None:
        self.value = value
        self.locked = locked
        self.values: dict[str, str] = {}

    def resolve(self, ref: SecretRef) -> str:
        if self.locked:
            raise SecretStoreLocked("locked")
        return self.values.get(ref.uri, self.value)

    def store(self, ref: SecretRef, secret: str) -> None:
        self.values[ref.uri] = secret

    def status(self, ref: SecretRef):
        if self.locked:
            from vibeos.model_gateway.secrets import SecretStatus

            return SecretStatus(ref.uri, "locked")
        from vibeos.model_gateway.secrets import SecretStatus

        return SecretStatus(ref.uri, "available" if ref.uri in self.values else "missing")

    def delete(self, ref: SecretRef) -> bool:
        return self.values.pop(ref.uri, None) is not None


class FakeHttpClient:
    def __init__(self, response: ProviderHttpResponse | Exception) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def post(self, *, url: str, body: bytes, headers: dict[str, str], timeout: float) -> ProviderHttpResponse:
        self.calls.append({"url": url, "body": body, "headers": headers, "timeout": timeout})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _provider_response(request, *, status: int = 200, action: str = "restart", total_tokens: int = 30) -> ProviderHttpResponse:
    inner = {
        "schema_version": "v1",
        "diagnosis": "The synthetic fixture failed during its pre-armed first start.",
        "confidence": 0.95,
        "proposal": {
            "action": action,
            "unit": "vibeos-goal04-fixture.service",
            "arguments": [],
            "effect_level": "E1" if action != "none" else "E0",
            "fact_digest": request.context.items[0].sha256,
        },
    }
    payload = {
        "id": "provider-request-1",
        "choices": [{"message": {"content": json.dumps(inner)}}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": total_tokens},
    }
    return ProviderHttpResponse(status=status, headers={}, body=json.dumps(payload).encode())


def test_service_diagnosis_contract_is_strict_and_bound_to_d0_facts() -> None:
    request = _request()
    assert request.schema_version == "v1"
    assert request.context.highest_data_classification == "D0"
    assert request.context.items[0].sha256 == facts_digest(request.context.items[0].payload)
    with pytest.raises(ValueError):
        ServiceActionProposal.model_validate(
            {
                "action": "restart",
                "unit": "some-real-user.service",
                "arguments": [],
                "effect_level": "E1",
                "fact_digest": "0" * 64,
            }
        )
    with pytest.raises(ValueError):
        ProviderRoute.model_validate({**_route().model_dump(), "base_url": "http://provider.invalid"})


def test_openai_transport_returns_strict_response_without_leaking_secret() -> None:
    request = _request()
    client = FakeHttpClient(_provider_response(request))
    result = OpenAICompatibleTransport(FakeSecretStore(), client).execute(_route(), request)
    assert result.status == "succeeded"
    assert result.response is not None
    assert result.response.result.proposal.action == "restart"
    assert result.response.receipt.secret_resolved is True
    assert client.calls[0]["headers"] == {
        "Authorization": f"Bearer {LEAK_CANARY}",
        "Content-Type": "application/json",
        "Idempotency-Key": request.request_id,
        "X-VibeOS-Request-Id": request.request_id,
    }
    assert LEAK_CANARY.encode() not in client.calls[0]["body"]
    request_body = json.loads(client.calls[0]["body"])
    assert request_body["thinking"] == {"type": "disabled"}
    assert request_body["response_format"] == {"type": "json_object"}
    user_content = json.loads(request_body["messages"][1]["content"])
    assert user_content["json_output_example"]["proposal"]["fact_digest"] == request.context.items[0].sha256
    assert LEAK_CANARY not in result.model_dump_json()
    assert LEAK_CANARY not in request.model_dump_json()
    assert LEAK_CANARY not in _route().model_dump_json()


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (ProviderHttpResponse(429, {}, b"{}"), FailureCode.RATE_LIMITED),
        (ProviderHttpResponse(503, {}, b"{}"), FailureCode.PROVIDER_SERVER_ERROR),
        (ProviderHttpResponse(200, {}, b"not-json"), FailureCode.INVALID_JSON),
        (ProviderHttpResponse(200, {}, b"{}"), FailureCode.SCHEMA_MISMATCH),
        (urllib.error.URLError("network interrupted"), FailureCode.UNKNOWN_DELIVERY),
        (TimeoutError("timeout"), FailureCode.PROVIDER_TIMEOUT),
    ],
)
def test_provider_failures_are_classified_and_fail_closed(response: ProviderHttpResponse | Exception, expected: FailureCode) -> None:
    result = OpenAICompatibleTransport(FakeSecretStore(), FakeHttpClient(response)).execute(_route(), _request())
    assert result.status == "failed"
    assert result.response is None
    assert result.failure is not None
    assert result.failure.code is expected


def test_inner_bad_json_and_stale_facts_are_distinct_fail_closed_results() -> None:
    request = _request()
    bad_inner = {
        "id": "provider-request-bad-inner",
        "choices": [{"message": {"content": "{not-json"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    invalid = OpenAICompatibleTransport(FakeSecretStore(), FakeHttpClient(ProviderHttpResponse(200, {}, json.dumps(bad_inner).encode()))).execute(
        _route(), request
    )
    assert invalid.failure is not None and invalid.failure.code is FailureCode.INVALID_JSON

    stale_request = _request(facts=_facts(captured_at="2020-01-01T00:00:00+00:00"))
    stale = OpenAICompatibleTransport(FakeSecretStore(), FakeHttpClient(_provider_response(stale_request))).execute(_route(), stale_request)
    assert stale.failure is not None and stale.failure.code is FailureCode.SCHEMA_MISMATCH


def test_budget_exhaustion_and_cancellation_are_fail_closed() -> None:
    cancelled = OpenAICompatibleTransport(FakeSecretStore(), FakeHttpClient(_provider_response(_request()))).execute(_route(), _request(cancellation=True))
    assert cancelled.failure is not None and cancelled.failure.code is FailureCode.CANCELLED

    request = _request(budget=_budget(max_total_tokens=25))
    exhausted = OpenAICompatibleTransport(FakeSecretStore(), FakeHttpClient(_provider_response(request, total_tokens=30))).execute(_route(), request)
    assert exhausted.failure is not None and exhausted.failure.code is FailureCode.BUDGET_EXHAUSTED


def test_locked_keyring_maps_to_explainable_durable_wait_key() -> None:
    result = OpenAICompatibleTransport(FakeSecretStore(locked=True), FakeHttpClient(_provider_response(_request()))).execute(_route(), _request())
    assert result.status == "waiting"
    assert result.failure is not None
    assert result.failure.code is FailureCode.KEYRING_LOCKED
    assert result.failure.wait_event_key == "secret-service:unlocked:goal04-provider"

    ready = TaskRun(
        task_id="task-goal04",
        contract_id="contract-goal04",
        status=TaskStatus.READY,
        revision=1,
        created_at="2026-07-22T00:00:00Z",
        updated_at="2026-07-22T00:00:00Z",
    )
    waiting = transition(
        ready,
        keyring_wait_event(state=ready, result=result, event_id="event-wait-keyring", occurred_at="2026-07-22T00:00:01Z"),
    ).state
    assert waiting.status is TaskStatus.WAITING
    assert waiting.pending_reason == result.failure.safe_message
    resumed = transition(
        waiting,
        keyring_unlocked_event(state=waiting, event_id="event-keyring-unlocked", occurred_at="2026-07-22T00:00:02Z"),
    ).state
    assert resumed.status is TaskStatus.READY
    assert resumed.wait_event_key is None


def test_semantic_worker_is_a_distinct_process_without_session_bus_or_secret_environment(monkeypatch) -> None:
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/secret-service-session-bus")
    monkeypatch.setenv("OPENAI_API_KEY", LEAK_CANARY)
    invocation = SemanticWorkerInvocation(
        request_id="request-process-isolation",
        binding=_binding(),
        facts=_facts(),
        budget=_budget(),
        cancellation=CancellationBinding(token_id="cancel-process-isolation"),
    )
    process = subprocess.run(
        [sys.executable, "-m", "vibeos.model_gateway.semantic_worker"],
        input=invocation.model_dump_json(),
        capture_output=True,
        text=True,
        env=ModelGateway._semantic_environment(),
        timeout=10,
        check=False,
    )
    assert process.returncode == 0
    payload = json.loads(process.stdout)
    assert payload["worker_pid"] != os.getpid()
    assert payload["session_bus_present"] is False
    assert payload["secret_environment_present"] is False
    assert LEAK_CANARY not in process.stdout


def test_transport_worker_is_a_distinct_process_and_missing_secret_fails_closed() -> None:
    route = ProviderRoute(
        route_id="goal04-offline-missing",
        model="test-model",
        base_url="https://provider.invalid/v1",
        secret_ref=SecretRef(secret_id="goal04-offline-missing", provider="openai-compatible"),
    )
    envelope = TransportEnvelope(route=route, request=_request())
    process = subprocess.run(
        [sys.executable, "-m", "vibeos.model_gateway.transport_worker"],
        input=envelope.model_dump_json(),
        capture_output=True,
        text=True,
        env=ModelGateway._transport_environment(),
        timeout=20,
        check=False,
    )
    assert process.returncode == 0
    result = GatewayResult.model_validate_json(process.stdout)
    assert result.status in {"failed", "waiting"}
    assert result.failure is not None
    assert result.failure.code in {FailureCode.SECRET_NOT_FOUND, FailureCode.KEYRING_LOCKED, FailureCode.TRANSPORT_ERROR}
    assert result.failure.receipt is not None
    assert result.failure.receipt.transport_pid != os.getpid()
    assert LEAK_CANARY not in process.stdout


def test_gateway_uses_semantic_and_transport_process_contracts() -> None:
    calls: list[str] = []
    request = _request()
    diagnosis = ServiceDiagnosis(
        diagnosis="synthetic failure",
        confidence=0.9,
        proposal=ServiceActionProposal(action="restart", effect_level="E1", fact_digest=request.context.items[0].sha256),
    )

    def runner(argv: list[str], payload: str, environment: dict[str, str], timeout: float) -> subprocess.CompletedProcess[str]:
        module = argv[-1]
        calls.append(module)
        if module.endswith("semantic_worker"):
            invocation = SemanticWorkerInvocation.model_validate_json(payload)
            output = {
                "schema_version": "v1",
                "request": build_model_request(invocation).model_dump(mode="json"),
                "worker_pid": 101,
                "session_bus_present": False,
                "secret_environment_present": False,
            }
            return subprocess.CompletedProcess(argv, 0, json.dumps(output), "")
        response = {
            "schema_version": "v1",
            "status": "succeeded",
            "response": {
                "schema_version": "v1",
                "request_id": request.request_id,
                "binding": request.binding.model_dump(mode="json"),
                "result": diagnosis.model_dump(mode="json"),
                "usage": ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2).model_dump(mode="json"),
                "receipt": RedactedTransportReceipt(
                    route_id=_route().route_id,
                    provider_request_id="provider-1",
                    delivery="confirmed",
                    transport_pid=202,
                    secret_ref_uri=_route().secret_ref.uri,
                    secret_resolved=True,
                ).model_dump(mode="json"),
            },
            "failure": None,
        }
        return subprocess.CompletedProcess(argv, 0, json.dumps(response), "")

    result = ModelGateway(runner).diagnose_service(
        route=_route(),
        binding=_binding(),
        facts=request.context.items[0].payload,
        budget=_budget(),
        cancellation=CancellationBinding(token_id="cancel-gateway"),
        request_id=request.request_id,
    )
    assert result.status == "succeeded"
    assert calls == ["vibeos.model_gateway.semantic_worker", "vibeos.model_gateway.transport_worker"]


def test_secret_tool_never_places_secret_in_argv() -> None:
    calls: list[tuple[list[str], str | None]] = []

    class RecordingStore(SecretToolSecretStore):
        def _run(self, argv: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
            calls.append((argv, input_text))
            return subprocess.CompletedProcess(argv, 0, "", "")

    RecordingStore().store(_route().secret_ref, LEAK_CANARY)
    assert calls[0][1] == LEAK_CANARY
    assert all(LEAK_CANARY not in argument for argument in calls[0][0])


def test_cli_explicit_environment_migration_unsets_value_and_persists_only_reference(tmp_path: Path, monkeypatch, capsys) -> None:
    repository = ProviderRouteRepository(tmp_path / "routes.json")
    store = FakeSecretStore(value="")
    monkeypatch.setenv("GOAL04_PROVIDER_KEY", LEAK_CANARY)
    args = build_parser().parse_args(
        [
            "secrets",
            "import",
            "goal04-provider",
            "--model",
            "test-model",
            "--base-url",
            "https://provider.invalid/v1",
            "--from-env",
            "GOAL04_PROVIDER_KEY",
            "--json",
        ]
    )
    assert run_secret_command(args, store=store, repository=repository) == 0
    assert "GOAL04_PROVIDER_KEY" not in os.environ
    assert LEAK_CANARY not in repository.path.read_text(encoding="utf-8")
    assert LEAK_CANARY not in capsys.readouterr().out
    assert store.values[_route().secret_ref.uri] == LEAK_CANARY


def test_dotenv_loader_refuses_secret_like_names(tmp_path: Path, monkeypatch) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"OPENAI_API_KEY={LEAK_CANARY}\nVIBEOS_REVIEW_TTL_SECONDS=123\n", encoding="utf-8")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("VIBEOS_REVIEW_TTL_SECONDS", raising=False)
    load_dotenv(dotenv)
    assert "OPENAI_API_KEY" not in os.environ
    assert os.environ["VIBEOS_REVIEW_TTL_SECONDS"] == "123"


def test_cli_secret_status_and_delete_are_redacted(tmp_path: Path, capsys) -> None:
    repository = ProviderRouteRepository(tmp_path / "routes.json")
    route = _route()
    repository.save(route)
    store = FakeSecretStore()
    store.store(route.secret_ref, LEAK_CANARY)
    status = argparse.Namespace(secrets_command="status", route_id=route.route_id, json=True)
    assert run_secret_command(status, store=store, status_store=store, repository=repository) == 0
    assert LEAK_CANARY not in capsys.readouterr().out
    delete = argparse.Namespace(secrets_command="delete", route_id=route.route_id, json=True)
    assert run_secret_command(delete, store=store, repository=repository) == 0
    assert repository.get(route.route_id) is None
    assert LEAK_CANARY not in capsys.readouterr().out


def test_cli_secret_status_uses_metadata_reader_without_resolving_secret(tmp_path: Path, capsys) -> None:
    repository = ProviderRouteRepository(tmp_path / "routes.json")
    route = _route()
    repository.save(route)

    class ResolveForbiddenStore(FakeSecretStore):
        def resolve(self, ref: SecretRef) -> str:
            raise AssertionError(f"status must not resolve {ref.uri}")

    class MetadataStatusReader:
        def status(self, ref: SecretRef) -> SecretStatus:
            return SecretStatus(ref.uri, "available")

    args = argparse.Namespace(secrets_command="status", route_id=route.route_id, json=True)
    assert run_secret_command(args, store=ResolveForbiddenStore(), status_store=MetadataStatusReader(), repository=repository) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "available"
