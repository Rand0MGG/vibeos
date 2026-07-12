from dataclasses import asdict

from vibeos.browser_state import browser_attempt_scope, record_browser_navigation, record_browser_observation
from vibeos.domain_models import ContextBudget, DomainPack, ObservationRequest
from vibeos.domain_registry import ContextPackageDefinition, ContextPackageRegistry, DomainRegistry, RouteDefinition, default_domain_registry
from vibeos.observation import resolve_observation_request, validate_observation_request
from vibeos.run_trace import build_run_trace
from vibeos.verifiers import default_verifier_registry


def test_default_domain_registry_exposes_browser_and_media() -> None:
    registry = default_domain_registry(default_verifier_registry().ids())

    assert registry.get_pack("browser") is not None
    assert registry.get_pack("media") is not None
    assert registry.get_route("browser_open_url_route") is not None


def test_domain_registry_rejects_unknown_capability_references() -> None:
    registry = DomainRegistry(
        packs=(DomainPack(domain_id="browser", label="Browser", route_ids=("broken_route",), allowed_context_package_ids=("session_context",)),),
        routes=(
            RouteDefinition(
                route_id="broken_route",
                domain_id="browser",
                builder_name="missing",
                required_capability_ids=("not.real",),
                required_context_package_ids=("session_context",),
                default_verifier_ids=("browser_url_opened",),
            ),
        ),
        context_registry=ContextPackageRegistry(
            (
                ContextPackageDefinition(
                    package_id="session_context",
                    producer=lambda: {"captured_at": "now"},
                    budget=ContextBudget(max_items=1, max_bytes=128, ttl_ms=1000),
                    schema_name="session_context_v1",
                    redaction_policy="none",
                ),
            )
        ),
        known_verifier_ids=("browser_url_opened",),
    )

    errors = registry.validate()

    assert any("unknown capability" in error for error in errors)


def test_observation_request_rejects_duplicate_and_unknown_packages() -> None:
    registry = default_domain_registry(default_verifier_registry().ids())
    request = ObservationRequest(
        active_domain_ids=("browser",),
        requested_context_package_ids=("browser_context", "browser_context", "missing_context"),
        postcondition_package_ids=(),
    )

    errors = validate_observation_request(request, registry)

    assert any("duplicate context package id" in error for error in errors)
    assert any("unknown context package id" in error for error in errors)


def test_context_budget_truncates_oversized_payloads() -> None:
    context_registry = ContextPackageRegistry(
        (
            ContextPackageDefinition(
                package_id="session_context",
                producer=lambda: {"captured_at": "now", "items": list(range(10))},
                budget=ContextBudget(max_items=3, max_bytes=256, ttl_ms=1000),
                schema_name="session_context_v1",
                redaction_policy="none",
            ),
        )
    )
    registry = DomainRegistry(
        packs=(DomainPack(domain_id="browser", label="Browser", route_ids=("browser_open_url_route",), allowed_context_package_ids=("session_context",)),),
        routes=(
            RouteDefinition(
                route_id="browser_open_url_route",
                domain_id="browser",
                builder_name="noop",
                required_capability_ids=("browser.open_url",),
                required_context_package_ids=("session_context",),
                default_verifier_ids=("browser_url_opened",),
            ),
        ),
        context_registry=context_registry,
        known_verifier_ids=("browser_url_opened",),
    )

    receipt = resolve_observation_request(
        ObservationRequest(active_domain_ids=("browser",), requested_context_package_ids=("session_context",), postcondition_package_ids=()),
        registry,
    )

    assert receipt.packages[0].truncated is True
    assert receipt.packages[0].payload["items"] == [0, 1, 2]


def test_run_trace_contains_expected_v05_layer_keys() -> None:
    trace = build_run_trace(
        utterance_analysis={"type": "task"},
        goal_synthesis={"goal_type": "browser_open_url"},
        domain_routing={"active_domain_ids": ["browser"]},
        observation_request={"requested_context_package_ids": ["browser_context"]},
        observation_receipt={"loaded_package_ids": ["browser_context"]},
        capability_exposure={"exposed_route_ids": ["browser_open_url_route"]},
        candidate_plan_selection={"selected_route_id": "browser_open_url_route"},
        selected_route={"id": "browser_open_url_route"},
        validation={"ok": True},
        review={"status": "allowed"},
        execution={"status": "succeeded"},
        verification={"status": "passed"},
        acceptance={"status": "passed"},
        debug_trace_id="debug_trace_v0_5",
    )

    payload = asdict(trace)

    assert set(payload) == {
        "schema_version",
        "utterance_analysis",
        "goal_synthesis",
        "domain_routing",
        "observation_request",
        "observation_receipt",
        "capability_exposure",
        "candidate_plan_selection",
        "selected_route",
        "validation",
        "review",
        "execution",
        "verification",
        "acceptance",
        "debug_trace_id",
    }


def test_default_browser_context_uses_recent_navigation_and_window_observation(monkeypatch) -> None:
    from vibeos import domain_registry
    from vibeos.models import WindowEntry

    record_browser_navigation(uri="https://example.com/search?q=hello", adapter="xdg-open", status="opened")
    record_browser_observation(active_url="https://example.com/search?q=hello", adapter="xdg-open")

    class StubWindows:
        def list_windows(self):
            return [WindowEntry(window_id="1", app_id="firefox.desktop", title="Example Search", focused=True)]

    monkeypatch.setattr(domain_registry, "WindowRegistry", lambda: StubWindows())

    payload = domain_registry.default_browser_context()

    assert payload["status"] == "loaded"
    assert payload["active_url"] == "https://example.com/search?q=hello"
    assert payload["page_title"] == "Example Search"
    assert "example.com" in payload["known_sites"]


def test_default_browser_context_does_not_inherit_previous_attempt_navigation(monkeypatch) -> None:
    from vibeos import domain_registry
    from vibeos.models import WindowEntry

    class StubWindows:
        def list_windows(self):
            return [WindowEntry(window_id="1", app_id="firefox.desktop", title="Existing Browser", focused=True)]

    monkeypatch.setattr(domain_registry, "WindowRegistry", lambda: StubWindows())

    with browser_attempt_scope(run_id="run_one", attempt_id="attempt_one", route_id="browser_open_url_route"):
        record_browser_navigation(uri="https://example.com", adapter="xdg-open", status="opened")
        record_browser_observation(active_url="https://example.com", adapter="xdg-open")
        first = domain_registry.default_browser_context()

    with browser_attempt_scope(run_id="run_two", attempt_id="attempt_two", route_id="browser_open_url_route"):
        second = domain_registry.default_browser_context()

    assert first["status"] == "loaded"
    assert first["active_url"] == "https://example.com"
    assert second["status"] == "unavailable"
    assert second["active_url"] is None
