from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from vibeos.apps import AppRegistry
from vibeos.audit import AuditLog
from vibeos.broker import CapabilityBroker
from vibeos.core.adapters.database import CoreDatabase
from vibeos.models import AppEntry, CommandRequest, Intent


class OpenAppIntentBroker:
    def parse(self, _utterance: str) -> Intent:
        return Intent(action="app.open", target={"name": "Firefox"}, reason="dry-run crash fixture")


class MarkerApps(AppRegistry):
    def __init__(self, marker: Path) -> None:
        self.marker = marker

    def list_apps(self) -> list[AppEntry]:
        return [AppEntry(desktop_id="firefox.desktop", name="Firefox", keywords=("browser",))]

    def open_app(self, app: AppEntry) -> dict[str, str]:
        self.marker.write_text(app.desktop_id, encoding="utf-8")
        return {"status": "opened", "desktop_id": app.desktop_id}


def main() -> None:
    boundary = sys.argv[1]
    database_path = Path(sys.argv[2])
    external_marker = Path(sys.argv[3])
    simulation_marker = Path(sys.argv[4])
    broker = CapabilityBroker(
        intent_broker=OpenAppIntentBroker(),
        apps=MarkerApps(external_marker),
        audit=AuditLog(database_path.with_suffix(".audit.jsonl")),
        database=CoreDatabase(database_path),
    )
    engine = broker.task_engine

    if boundary == "before_proposal":

        def crash_before_proposal(*_args: Any, **_kwargs: Any) -> Any:
            os._exit(86)

        engine.action_executor.execute = crash_before_proposal  # type: ignore[method-assign]
    elif boundary == "before_external_io":

        def crash_before_external_io(*_args: Any, **_kwargs: Any) -> Any:
            os._exit(86)

        engine.execution.execute_step = crash_before_external_io  # type: ignore[method-assign]
    elif boundary == "before_receipt":
        execute_step = engine.execution.execute_step

        def crash_before_receipt(*args: Any, **kwargs: Any) -> Any:
            result = execute_step(*args, **kwargs)
            simulation_marker.write_text(result.adapter_status, encoding="utf-8")
            os._exit(86)

        engine.execution.execute_step = crash_before_receipt  # type: ignore[method-assign]
    else:
        raise ValueError(boundary)

    broker.handle(CommandRequest("open Firefox", dry_run=True))
    raise AssertionError("fault injector did not terminate the worker")


if __name__ == "__main__":
    main()
