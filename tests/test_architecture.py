"""Architecture boundaries for the completion master goal.

The strict-xfail tests are intentional Phase A debt markers. They must be
converted to ordinary passing assertions when the owning phase removes the
remaining legacy path.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "vibeos"


def _modules() -> tuple[Path, ...]:
    return tuple(path for path in PACKAGE_ROOT.rglob("*.py") if path.name != "agent_runtime.py")


def _imports_broker(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {"broker", "vibeos.broker"}:
            return True
        if isinstance(node, ast.Import):
            if any(alias.name in {"broker", "vibeos.broker"} for alias in node.names):
                return True
    return False


def _callers_of(method_name: str) -> tuple[Path, ...]:
    callers: list[Path] = []
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        if any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == method_name for node in ast.walk(tree)):
            callers.append(path)
    return tuple(callers)


def test_goal_loop_never_imports_broker() -> None:
    assert not _imports_broker(PACKAGE_ROOT / "goal_loop.py")


def test_domain_tools_never_import_broker() -> None:
    offenders = [path for path in (PACKAGE_ROOT / "tools").glob("*.py") if _imports_broker(path)]
    assert offenders == []


def test_planning_and_review_resume_never_import_broker() -> None:
    assert not _imports_broker(PACKAGE_ROOT / "planning_service.py")
    assert not _imports_broker(PACKAGE_ROOT / "review_resume_service.py")


def test_production_has_no_continue_goal_callers() -> None:
    assert _callers_of("continue_goal") == ()


def test_production_has_no_legacy_agent_runtime_callers() -> None:
    assert _callers_of("start_goal") == ()
    assert _callers_of("advance_goal") == ()


def test_production_does_not_create_shared_broker_session() -> None:
    source = (PACKAGE_ROOT / "broker.py").read_text(encoding="utf-8")
    assert "broker_session" not in source
    assert "agent_session" not in source


def test_broker_does_not_define_direct_execute() -> None:
    tree = ast.parse((PACKAGE_ROOT / "broker.py").read_text(encoding="utf-8"))
    assert not any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_execute" for node in ast.walk(tree))


def test_broker_has_no_direct_capability_mutation_calls() -> None:
    tree = ast.parse((PACKAGE_ROOT / "broker.py").read_text(encoding="utf-8"))
    forbidden = {
        ("apps", "open_app"),
        ("windows", "focus"),
        ("windows", "minimize"),
        ("windows", "maximize"),
        ("windows", "close"),
        ("portal", "open_uri"),
        ("notifications", "send"),
        ("clipboard", "write"),
    }
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        owner = node.func.value
        if isinstance(owner, ast.Attribute) and isinstance(owner.value, ast.Name) and owner.value.id == "self" and (owner.attr, node.func.attr) in forbidden:
            offenders.append((owner.attr, node.func.attr, node.lineno))
    assert offenders == []


def test_command_service_does_not_expose_callable_port_bundle() -> None:
    tree = ast.parse((PACKAGE_ROOT / "command_service.py").read_text(encoding="utf-8"))
    assert not any(isinstance(node, ast.ClassDef) and node.name == "CommandPorts" for node in ast.walk(tree))


def test_core_services_do_not_import_broker() -> None:
    core_modules = (
        "planning_service.py",
        "loop_snapshot.py",
        "task_application.py",
        "review_service.py",
        "review_resume_service.py",
        "legacy_review_migration.py",
        "execution_service.py",
        "observation_service.py",
        "acceptance_service.py",
        "recovery_service.py",
        "result_projection.py",
        "runtime_composition.py",
        "reviews.py",
    )
    offenders = [name for name in core_modules if _imports_broker(PACKAGE_ROOT / name)]
    assert offenders == []


def test_compatibility_modules_do_not_import_or_invoke_adapters() -> None:
    paths = (
        PACKAGE_ROOT / "legacy_review_migration.py",
        PACKAGE_ROOT / "result_projection.py",
    )
    forbidden_imports = {"apps", "windows", "portal", "clipboard", "notifications", "tools"}
    for path in paths:
        assert not _imports_broker(path)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported_modules = {node.module.rsplit(".", 1)[-1] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module is not None}
        assert imported_modules.isdisjoint(forbidden_imports)


def test_core_port_interfaces_do_not_expose_any() -> None:
    paths = (PACKAGE_ROOT / "goal_ports.py", PACKAGE_ROOT / "command_service.py")
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        public_nodes = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_")]
        annotations = [
            annotation
            for node in public_nodes
            for annotation in [node.returns, *(argument.annotation for argument in (*node.args.args, *node.args.kwonlyargs))]
            if annotation is not None
        ]
        assert not any(isinstance(annotation, ast.Name) and annotation.id == "Any" for annotation in annotations)

    goal_loop_tree = ast.parse((PACKAGE_ROOT / "goal_loop.py").read_text(encoding="utf-8"))
    goal_loop_class = next(node for node in goal_loop_tree.body if isinstance(node, ast.ClassDef) and node.name == "GoalLoop")
    public_methods = [node for node in goal_loop_class.body if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")]
    annotations = [
        annotation
        for node in public_methods
        for annotation in [node.returns, *(argument.annotation for argument in (*node.args.args, *node.args.kwonlyargs))]
        if annotation is not None
    ]
    assert not any(isinstance(annotation, ast.Name) and annotation.id == "Any" for annotation in annotations)


def test_broker_is_a_facade_without_prohibited_implementation_methods() -> None:
    tree = ast.parse((PACKAGE_ROOT / "broker.py").read_text(encoding="utf-8"))
    method_names = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    prohibited = {
        "_execute",
        "_run_task_plan_goal_loop",
        "_finalize_goal_loop_result",
        "assess_task_plan_execution",
        "_approve_plan_review_v06",
        "_approve_plan_review_legacy",
        "_compatibility_runtime_result",
        "_task_plan_to_v06_strategy",
        "_build_v06_tool_registry",
    }
    assert method_names.isdisjoint(prohibited)


def test_review_store_has_no_jsonl_mutation_fallback() -> None:
    source = (PACKAGE_ROOT / "reviews.py").read_text(encoding="utf-8")
    assert 'self.path.open("a", encoding="utf-8")' not in source
    assert 'fallback.open("a", encoding="utf-8")' not in source


def test_review_store_does_not_expose_broad_consume_transition() -> None:
    tree = ast.parse((PACKAGE_ROOT / "reviews.py").read_text(encoding="utf-8"))
    assert not any(isinstance(node, ast.FunctionDef) and node.name == "consume" for node in ast.walk(tree))
