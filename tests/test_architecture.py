from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "vibeos"

LEGACY_MODULES = {
    "agent_runtime.py",
    "goal_loop.py",
    "goal_ports.py",
    "legacy_review_migration.py",
    "loop_models.py",
    "loop_policy.py",
    "loop_snapshot.py",
    "projections.py",
    "review_resume_service.py",
    "reviews.py",
    "run_ledger.py",
}


def _tree(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8-sig"))


def _imports(path: Path) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    return imported


def test_old_task_kernels_are_physically_deleted() -> None:
    assert [name for name in sorted(LEGACY_MODULES) if (PACKAGE_ROOT / name).exists()] == []


def test_production_has_no_legacy_task_imports() -> None:
    forbidden = {name.removesuffix(".py") for name in LEGACY_MODULES}
    offenders: list[tuple[str, str]] = []
    for path in PACKAGE_ROOT.rglob("*.py"):
        for imported in _imports(path):
            if imported.rsplit(".", 1)[-1] in forbidden:
                offenders.append((str(path.relative_to(PACKAGE_ROOT)), imported))
    assert offenders == []


def test_durable_engine_is_transport_and_broker_independent() -> None:
    imports = _imports(PACKAGE_ROOT / "durable_task_engine.py") | _imports(PACKAGE_ROOT / "durable_action_executor.py")
    assert "vibeos.broker" not in imports
    assert "broker" not in imports
    assert not any(item.endswith("dbus_service") or item.endswith("runtime") for item in imports)


def test_broker_has_no_direct_capability_mutation_calls() -> None:
    tree = _tree(PACKAGE_ROOT / "broker.py")
    forbidden = {"open_app", "focus", "minimize", "maximize", "close", "open_uri", "send", "write"}
    offenders = [
        node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in forbidden
    ]
    assert offenders == []


def test_transport_and_application_services_have_no_direct_execution_bypass() -> None:
    forbidden_fragments = (".execute_step(", ".apps.list_apps(", ".windows.list_windows(")
    offenders: list[tuple[str, str]] = []
    for relative in ("task_application.py", "runtime.py", "dbus_service.py", "daemon.py", "core/adapters/http.py"):
        source = (PACKAGE_ROOT / relative).read_text(encoding="utf-8")
        offenders.extend((relative, fragment) for fragment in forbidden_fragments if fragment in source)
    assert offenders == []


def test_new_task_kernel_modules_remain_bounded() -> None:
    paths = [
        PACKAGE_ROOT / "durable_task_engine.py",
        PACKAGE_ROOT / "durable_action_executor.py",
        PACKAGE_ROOT / "core" / "adapters" / "task_repository.py",
        PACKAGE_ROOT / "core" / "adapters" / "task_persistence.py",
        PACKAGE_ROOT / "core" / "domain" / "task_transitions.py",
    ]
    assert {path.name: len(path.read_text(encoding="utf-8").splitlines()) for path in paths if len(path.read_text(encoding="utf-8").splitlines()) > 400} == {}


def test_current_metadata_has_no_legacy_review_schema() -> None:
    source = (PACKAGE_ROOT / "core" / "adapters" / "metadata.py").read_text(encoding="utf-8")
    assert 'Table("reviews"' not in source
    assert 'Table("review_events"' not in source


def test_runtime_keeps_dbus_primary_and_http_as_thin_compatibility() -> None:
    runtime = (PACKAGE_ROOT / "runtime.py").read_text(encoding="utf-8")
    daemon = (PACKAGE_ROOT / "daemon.py").read_text(encoding="utf-8")
    http = (PACKAGE_ROOT / "core" / "adapters" / "http.py").read_text(encoding="utf-8")

    assert 'if mode == "dbus"' in runtime
    assert 'if mode == "http"' in runtime
    assert 'if mode != "auto"' in runtime
    assert "DBusDaemonClient" in runtime
    assert "HTTPDaemonClient" in runtime
    assert "TaskApplicationService" not in http
    assert "CapabilityBroker" in daemon
    assert "HTTP_DEPRECATION" in daemon
    assert "is_loopback" in http
