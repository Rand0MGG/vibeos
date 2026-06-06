import json
from pathlib import Path
from uuid import uuid4

from vibeos.apps import AppRegistry
from vibeos.audit import AuditLog
from vibeos.broker import CapabilityBroker
from vibeos.cli import main
from vibeos.intent import RuleIntentBroker
from vibeos.models import AppEntry
from vibeos.reviews import ReviewStore
from vibeos.runtime import RuntimeSelectionError
from vibeos.runtime import LocalRuntime


class FakeApps(AppRegistry):
    def list_apps(self):
        return [AppEntry(desktop_id="firefox.desktop", name="Firefox", keywords=("browser",))]

    def open_app(self, app):
        return {"status": "opened", "desktop_id": app.desktop_id}


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
