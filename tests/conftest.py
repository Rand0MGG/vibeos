from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for path in (str(ROOT), str(SRC)):
    if path not in sys.path:
        sys.path.insert(0, path)


def _state_dir_for_nodeid(nodeid: str) -> Path:
    digest = hashlib.sha256(nodeid.encode("utf-8")).hexdigest()[:12]
    safe = "".join(ch if ch.isalnum() else "_" for ch in nodeid)[:80].strip("_") or "test"
    return ROOT / ".vibeos" / "test-state" / f"{safe}-{digest}"


@pytest.fixture(autouse=True)
def _isolated_vibeos_state_dir(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> Path:
    state_dir = _state_dir_for_nodeid(request.node.nodeid)
    state_dir.mkdir(parents=True, exist_ok=True)
    config_dir = state_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("VIBEOS_STATE_DIR", str(state_dir))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_dir))
    monkeypatch.setenv("VIBEOS_MODEL_PROVIDER", "local")
    monkeypatch.setenv("VIBEOS_ENABLE_MODEL_UNDERSTANDING", "0")
    monkeypatch.setenv("VIBEOS_ENABLE_MODEL_GOAL_SYNTHESIS", "0")
    monkeypatch.setenv("VIBEOS_ENABLE_MODEL_CLARIFICATION", "0")
    monkeypatch.setenv("VIBEOS_ENABLE_MODEL_STRATEGY_SELECTION", "0")
    monkeypatch.setenv("VIBEOS_ENABLE_MODEL_ROUTE_SELECTION", "0")
    monkeypatch.setenv("VIBEOS_ENABLE_MODEL_REPLANNING", "0")
    monkeypatch.setenv("VIBEOS_ENABLE_MODEL_SEMANTIC_ACCEPTANCE", "0")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    request.node._vibeos_state_dir = state_dir  # type: ignore[attr-defined]
    return state_dir


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[object]):
    outcome = yield
    report = outcome.get_result()
    if not report.failed:
        return
    state_dir = getattr(item, "_vibeos_state_dir", None)
    if not isinstance(state_dir, Path) or not state_dir.exists():
        return
    report.sections.append(("VibeOS state dir", str(state_dir)))
    runs_root = state_dir / "runs"
    if runs_root.exists():
        summaries = sorted(runs_root.rglob("summary.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        if summaries:
            latest_summary = summaries[0]
            report.sections.append(("Latest trace summary path", str(latest_summary)))
            try:
                summary_payload = json.loads(latest_summary.read_text(encoding="utf-8"))
                report.sections.append(("Latest trace summary", json.dumps(summary_payload, ensure_ascii=False, indent=2)))
            except (OSError, json.JSONDecodeError):
                pass
            events_path = latest_summary.parent / "events.jsonl"
            if events_path.exists():
                try:
                    event_lines = [line for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
                except OSError:
                    event_lines = []
                if event_lines:
                    tail = "\n".join(event_lines[-10:])
                    report.sections.append(("Latest trace events tail", tail))
    audit_path = state_dir / "audit.jsonl"
    if audit_path.exists():
        try:
            audit_lines = [line for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except OSError:
            audit_lines = []
        if audit_lines:
            report.sections.append(("Audit tail", "\n".join(audit_lines[-10:])))
