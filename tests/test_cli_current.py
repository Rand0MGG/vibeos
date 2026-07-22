import json
import os
from pathlib import Path
from uuid import uuid4

from vibeos.apps import AppRegistry
from vibeos.audit import AuditLog
from vibeos.broker import CapabilityBroker
from vibeos.cli import main
from vibeos.core.adapters.database import CoreDatabase
from vibeos.models import AppEntry
from vibeos.models import WindowEntry
from vibeos.portal import PortalAdapter
from vibeos.runtime import LocalRuntime, RuntimeSelectionError
from tests.support_intent_broker import FixtureIntentBroker


class FakeApps(AppRegistry):
    def list_apps(self):
        return [AppEntry(desktop_id="firefox.desktop", name="Firefox", keywords=("browser",))]

    def open_app(self, app):
        return {"status": "opened", "desktop_id": app.desktop_id}


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


class ObservedPortal(PortalAdapter):
    def open_uri(self, uri: str) -> dict[str, object]:
        return {"status": "opened", "uri": uri, "adapter": "cli-browser"}


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


def test_ask_json_executes_app_open_with_explicit_broker(monkeypatch, capsys) -> None:
    runtime = LocalRuntime(
        CapabilityBroker(
            intent_broker=FixtureIntentBroker(),
            apps=FakeApps(),
            audit=AuditLog(make_audit_path("cli-open-browser-current")),
            database=CoreDatabase(make_database_path("cli-open-browser-current")),
        )
    )
    monkeypatch.setattr("vibeos.cli.build_runtime", lambda: runtime)

    exit_code = main(["ask", "open browser", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["status"] == "executed"
    assert payload["result"]["selected_strategy_id"] == "strategy_apps_open_route"


def test_offline_dry_run_uses_the_local_parser_without_building_default_runtime(monkeypatch, capsys) -> None:
    monkeypatch.setenv("VIBEOS_ENABLE_MODEL_UNDERSTANDING", "1")
    monkeypatch.setattr(
        "vibeos.cli.build_runtime",
        lambda: (_ for _ in ()).throw(AssertionError("offline requests must not select a daemon or provider-backed runtime")),
    )

    exit_code = main(["ask", "search web for hello", "--offline", "--dry-run", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["intent"]["action"] == "browser.search_web"
    assert payload["overall_status"] == "dry_run"
    assert os.environ["VIBEOS_ENABLE_MODEL_UNDERSTANDING"] == "1"


def test_approve_json_executes_window_close(monkeypatch, capsys) -> None:
    runtime = LocalRuntime(
        CapabilityBroker(
            intent_broker=FixtureIntentBroker(),
            windows=FakeWindows(),
            audit=AuditLog(make_audit_path("cli-window-close-current")),
            database=CoreDatabase(make_database_path("cli-window-close-current")),
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


def make_database_path(name: str) -> Path:
    return Path(".vibeos") / f"test-{name}-{uuid4().hex}.sqlite3"


def make_audit_path(name: str) -> Path:
    return Path(".vibeos") / f"audit-{name}-{uuid4().hex}.jsonl"
