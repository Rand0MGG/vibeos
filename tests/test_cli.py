import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from vibeos.apps import AppRegistry
from vibeos.audit import AuditLog
from vibeos.browser_state import record_browser_observation
from vibeos.broker import CapabilityBroker
from vibeos.cli import main
from vibeos.intent import RuleIntentBroker
from vibeos.models import AppEntry
from vibeos.portal import PortalAdapter
from vibeos.reviews import ReviewStore
from vibeos.runtime import RuntimeSelectionError
from vibeos.runtime import LocalRuntime
from vibeos.models import WindowEntry


class FakeApps(AppRegistry):
    def list_apps(self):
        return [AppEntry(desktop_id="firefox.desktop", name="Firefox", keywords=("browser",))]

    def open_app(self, app):
        return {"status": "opened", "desktop_id": app.desktop_id}


class ObservedPortal(PortalAdapter):
    def open_uri(self, uri: str) -> dict[str, object]:
        parsed = urlparse(uri)
        params = parse_qs(parsed.query)
        observed_query = ""
        for key in ("q", "query", "wd", "p", "text", "search_query"):
            values = params.get(key)
            if values:
                observed_query = str(values[0])
                break
        record_browser_observation(active_url=uri, query=observed_query or None, adapter="cli-browser")
        return {"status": "opened", "uri": uri, "adapter": "cli-browser"}


class FakeWindows:
    def list_windows(self):
        return [WindowEntry(window_id="1", app_id="firefox.desktop", title="Firefox", focused=True)]

    def resolve(self, query):
        return self.list_windows() if query.lower() in {"firefox", "browser", "current"} else []

    def focus(self, window):
        return {"status": "focused", "window_id": window.window_id}

    def minimize(self, window):
        return {"status": "minimized", "window_id": window.window_id}

    def maximize(self, window):
        return {"status": "maximized", "window_id": window.window_id}

    def close(self, window):
        return {"status": "closed", "window_id": window.window_id}


class EmptyWindows:
    def list_windows(self):
        return []

    def resolve(self, query):
        return []

    def focus(self, window):
        raise AssertionError("dry-run preview should not call focus")

    def minimize(self, window):
        raise AssertionError("dry-run preview should not call minimize")

    def maximize(self, window):
        raise AssertionError("dry-run preview should not call maximize")

    def close(self, window):
        raise AssertionError("dry-run preview should not call close")


def test_doctor_does_not_require_runtime(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "vibeos.cli.build_runtime",
        lambda: (_ for _ in ()).throw(
            RuntimeSelectionError(
                "daemon transport is required",
                mode="auto",
                configured_transport="daemon",
                require_daemon=True,
            )
        ),
    )

    class FakeDoctor:
        def run(self):
            return {
                "summary": {"overall": "warn", "ok": 0, "warn": 1, "fail": 0},
                "checks": [{"name": "runtime_entry", "status": "warn", "message": "test"}],
            }

    monkeypatch.setattr("vibeos.cli.SessionDoctor", FakeDoctor)

    exit_code = main(["doctor", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["summary"]["overall"] == "warn"


def test_ask_json_reports_runtime_error(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "vibeos.cli.build_runtime",
        lambda: (_ for _ in ()).throw(
            RuntimeSelectionError(
                "daemon transport is required, but neither D-Bus nor HTTP is available",
                mode="auto",
                configured_transport="daemon",
                require_daemon=True,
            )
        ),
    )

    exit_code = main(["ask", "open browser", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["status"] == "failed"
    assert payload["error"] == "runtime_unavailable"


def test_audit_tail_uses_runtime(monkeypatch, capsys) -> None:
    class FakeRuntime:
        def audit_tail(self, count):
            assert count == 3
            return [{"audit_id": "aud_1", "transport": "dbus"}]

    monkeypatch.setattr("vibeos.cli.build_runtime", lambda: FakeRuntime())

    exit_code = main(["audit", "tail", "-n", "3"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload == [{"audit_id": "aud_1", "transport": "dbus"}]


def test_plan_json_returns_validated_task_plan(capsys) -> None:
    exit_code = main(["plan", "clipboard VibeOS evidence", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["status"] == "validated"
    assert payload["plan"]["schema_version"] == "v0.5"
    assert payload["plan"]["steps"][0]["action"] == "clipboard.write"
    assert payload["validation"]["ok"] is True


def test_plan_json_returns_rejected_for_unsupported_request(capsys) -> None:
    exit_code = main(["plan", "delete downloads", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["status"] == "rejected"
    assert payload["plan"] is None


def test_plan_json_offline_returns_browser_v04_payload(capsys) -> None:
    exit_code = main(["plan", "open https://example.com", "--json", "--offline"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["plan"]["schema_version"] == "v0.5"
    assert payload["plan"]["steps"][0]["action"] == "browser.open_url"
    assert payload["domain_routing"]["active_domain_ids"] == ["browser"]


def test_ask_dry_run_json_returns_plan_review_metadata_for_clipboard(monkeypatch, capsys) -> None:
    runtime = LocalRuntime(
        CapabilityBroker(
            intent_broker=RuleIntentBroker(),
            audit=AuditLog(make_audit_path("cli-clipboard-dry-run")),
            reviews=ReviewStore(make_review_path("cli-clipboard-dry-run")),
        )
    )
    monkeypatch.setattr("vibeos.cli.build_runtime", lambda: runtime)

    exit_code = main(["ask", "clipboard VibeOS evidence", "--dry-run", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["status"] == "review_required"
    assert payload["result"]["plan"]["schema_version"] == "v0.5"
    assert payload["result"]["plan_review"]["status"] == "review_required"
    assert payload["result"]["goal_runtime"]["status"] == "needs_review"
    assert payload["result"]["run_ledger"]["terminal_outcome"]["status"] == "needs_review"
    assert payload["review_id"]


def test_ask_dry_run_json_returns_mixed_analysis_and_task_plan(monkeypatch, capsys) -> None:
    runtime = LocalRuntime(
        CapabilityBroker(
            intent_broker=RuleIntentBroker(),
            audit=AuditLog(make_audit_path("cli-mixed-dry-run")),
            reviews=ReviewStore(make_review_path("cli-mixed-dry-run")),
        )
    )
    monkeypatch.setattr("vibeos.cli.build_runtime", lambda: runtime)

    exit_code = main(["ask", "explain clipboard permissions and then copy hello to clipboard", "--dry-run", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["status"] == "review_required"
    assert payload["result"]["analysis"]["type"] == "mixed"
    assert payload["result"]["analysis"]["chat_response"] == "explain clipboard permissions"
    assert payload["result"]["plan"]["steps"][0]["action"] == "clipboard.write"
    assert payload["result"]["goal_runtime"]["status"] == "needs_review"


def test_ask_dry_run_json_returns_v06_review_runtime_for_window_close(monkeypatch, capsys) -> None:
    runtime = LocalRuntime(
        CapabilityBroker(
            intent_broker=RuleIntentBroker(),
            windows=FakeWindows(),
            audit=AuditLog(make_audit_path("cli-window-close-dry-run")),
            reviews=ReviewStore(make_review_path("cli-window-close-dry-run")),
        )
    )
    monkeypatch.setattr("vibeos.cli.build_runtime", lambda: runtime)

    exit_code = main(["ask", "close firefox", "--dry-run", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["status"] == "review_required"
    assert payload["result"]["plan_review"]["status"] == "review_required"
    assert payload["result"]["goal_runtime"]["status"] == "needs_review"
    assert payload["result"]["selected_strategy_id"] == "strategy_window_close_route"
    assert payload["result"]["run_ledger"]["terminal_outcome"]["status"] == "needs_review"
    assert payload["review_id"]


def test_ask_offline_dry_run_browser_preview_completes_with_runtime_evidence(monkeypatch, capsys) -> None:
    runtime = LocalRuntime(
        CapabilityBroker(
            intent_broker=RuleIntentBroker(),
            audit=AuditLog(make_audit_path("cli-browser-dry-run-preview")),
            reviews=ReviewStore(make_review_path("cli-browser-dry-run-preview")),
        )
    )
    monkeypatch.setattr("vibeos.cli.build_runtime", lambda: runtime)

    exit_code = main(["ask", "search web for hello", "--offline", "--dry-run", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["status"] == "dry_run"
    assert payload["result"]["goal_runtime"]["status"] == "completed"
    assert payload["result"]["selected_strategy_id"] == "strategy_browser_search_web_route"
    assert payload["result"]["preview"]["acceptance_status"] == "passed"
    assert payload["result"]["preview"]["verification_status"] == "passed"


def test_ask_json_executes_allowed_task_plan(monkeypatch, capsys) -> None:
    runtime = LocalRuntime(
        CapabilityBroker(
            intent_broker=RuleIntentBroker(),
            apps=FakeApps(),
            audit=AuditLog(make_audit_path("cli-open-browser")),
            reviews=ReviewStore(make_review_path("cli-open-browser")),
        )
    )
    monkeypatch.setattr("vibeos.cli.build_runtime", lambda: runtime)

    exit_code = main(["ask", "open browser", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["status"] == "executed"
    assert payload["result"]["analysis"]["type"] == "task"
    assert payload["result"]["execution"]["status"] == "succeeded"
    assert payload["overall_status"] == "completed"
    assert payload["result"]["goal_runtime"]["status"] == "completed"
    assert payload["result"]["selected_strategy_id"] == "strategy_apps_open_route"


def test_ask_json_browser_request_exposes_v06_runtime_payload(monkeypatch, capsys) -> None:
    runtime = LocalRuntime(
        CapabilityBroker(
            intent_broker=RuleIntentBroker(),
            portal=ObservedPortal(),
            audit=AuditLog(make_audit_path("cli-browser-v06")),
            reviews=ReviewStore(make_review_path("cli-browser-v06")),
        )
    )
    monkeypatch.setattr("vibeos.cli.build_runtime", lambda: runtime)

    exit_code = main(["ask", "search web for hello", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["status"] == "executed"
    assert payload["result"]["goal_runtime"]["status"] == "completed"
    assert payload["result"]["environment_profile"]["search_policy"] == "browser_first"
    assert payload["result"]["selected_strategy_id"] == "strategy_browser_search_web_route"
    assert payload["result"]["run_ledger"]["terminal_outcome"]["status"] == "completed"


def test_approve_json_executes_window_close_with_same_v06_goal_runtime(monkeypatch, capsys) -> None:
    reviews = ReviewStore(make_review_path("cli-window-close-approve"))
    runtime = LocalRuntime(
        CapabilityBroker(
            intent_broker=RuleIntentBroker(),
            windows=FakeWindows(),
            audit=AuditLog(make_audit_path("cli-window-close-approve")),
            reviews=reviews,
        )
    )
    monkeypatch.setattr("vibeos.cli.build_runtime", lambda: runtime)

    first_exit = main(["ask", "close firefox", "--json"])
    pending_payload = json.loads(capsys.readouterr().out)
    approve_exit = main(["approve", pending_payload["review_id"], "--json"])
    approved_payload = json.loads(capsys.readouterr().out)

    assert first_exit == 1
    assert approve_exit == 0
    assert pending_payload["status"] == "review_required"
    assert approved_payload["status"] == "executed"
    assert pending_payload["result"]["goal_runtime"]["goal_id"] == approved_payload["result"]["goal_runtime"]["goal_id"]
    assert approved_payload["result"]["goal_runtime"]["status"] == "completed"
    assert approved_payload["result"]["selected_strategy_id"] == "strategy_window_close_route"
    assert approved_payload["result"]["run_ledger"]["terminal_outcome"]["status"] == "completed"


def test_approve_dry_run_json_previews_window_close_without_real_window_state(monkeypatch, capsys) -> None:
    reviews = ReviewStore(make_review_path("cli-window-close-approve-dry-run"))
    runtime = LocalRuntime(
        CapabilityBroker(
            intent_broker=RuleIntentBroker(),
            windows=EmptyWindows(),
            audit=AuditLog(make_audit_path("cli-window-close-approve-dry-run")),
            reviews=reviews,
        )
    )
    monkeypatch.setattr("vibeos.cli.build_runtime", lambda: runtime)

    first_exit = main(["ask", "close firefox", "--json"])
    pending_payload = json.loads(capsys.readouterr().out)
    approve_exit = main(["approve", pending_payload["review_id"], "--dry-run", "--json"])
    approved_payload = json.loads(capsys.readouterr().out)
    step = approved_payload["result"]["step_results"][0]

    assert first_exit == 1
    assert approve_exit == 0
    assert pending_payload["status"] == "review_required"
    assert approved_payload["status"] == "dry_run"
    assert approved_payload["result"]["goal_runtime"]["status"] == "completed"
    assert approved_payload["result"]["selected_strategy_id"] == "strategy_window_close_route"
    assert step["adapter"] == "windows.registry"
    assert step["adapter_status"] == "dry_run"
    assert step["result"]["selected_target"].startswith("preview:")


def test_ask_json_debug_exposes_v06_provider_artifacts(monkeypatch, capsys) -> None:
    runtime = LocalRuntime(
        CapabilityBroker(
            intent_broker=RuleIntentBroker(),
            portal=ObservedPortal(),
            audit=AuditLog(make_audit_path("cli-browser-v06-debug")),
            reviews=ReviewStore(make_review_path("cli-browser-v06-debug")),
        )
    )
    monkeypatch.setattr("vibeos.cli.build_runtime", lambda: runtime)

    exit_code = main(["ask", "search web for hello", "--json", "--debug"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["result"]["debug_trace"]["runtime_v0_6"]["provider_artifacts"]
    assert payload["result"]["debug_trace"]["runtime_v0_6"]["environment_profile"]["search_policy"] == "browser_first"


def test_ask_offline_uses_local_rule_broker_even_with_runtime_available(monkeypatch, capsys) -> None:
    def fail_runtime():
        raise AssertionError("build_runtime should not be used in offline mode")

    def fail_remote(*args, **kwargs):
        raise AssertionError("offline mode must not attempt a remote broker call")

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setattr("urllib.request.urlopen", fail_remote)
    monkeypatch.setattr("vibeos.cli.build_runtime", fail_runtime)

    exit_code = main(["ask", "open https://example.com", "--dry-run", "--json", "--offline"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["status"] == "dry_run"
    assert payload["result"]["plan"]["steps"][0]["action"] == "browser.open_url"


def make_review_path(name: str) -> Path:
    return Path(".vibeos") / f"test-{name}-{uuid4().hex}.jsonl"


def make_audit_path(name: str) -> Path:
    return Path(".vibeos") / f"audit-{name}-{uuid4().hex}.jsonl"
