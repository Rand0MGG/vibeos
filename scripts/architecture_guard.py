from __future__ import annotations

import ast
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ModuleMetrics:
    lines: int
    max_function_complexity: int


@dataclass(frozen=True)
class GuardViolation:
    rule: str
    path: str
    message: str


def check_repository(root: Path) -> list[GuardViolation]:
    config = json.loads((root / "architecture_baseline.json").read_text(encoding="utf-8"))
    violations = check_boundaries(root / "src" / "vibeos" / "core")
    violations.extend(check_cycles(root / "src" / "vibeos" / "core"))
    violations.extend(check_quality(root, config))
    violations.extend(check_goal04_contracts(root / "src" / "vibeos"))
    violations.extend(check_goal04_test_contracts(root / "tests"))
    return violations


def check_goal04_contracts(source_root: Path) -> list[GuardViolation]:
    violations: list[GuardViolation] = []
    history_allowlist = {"history_v1.py", "task_history_v1.py"}
    forbidden_tokens = ("risk_level", "RiskLevel", "PermissionPolicy", "PermissionSummary", "permission_policy")
    forbidden_levels = {"L0", "L1", "L2", "L3", "L4"}
    for path in sorted(source_root.rglob("*.py")):
        if path.name in history_allowlist:
            continue
        source = path.read_text(encoding="utf-8")
        for token in forbidden_tokens:
            if token in source:
                violations.append(GuardViolation("goal04_effect_contract", str(path), f"live source contains forbidden token {token}"))
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in forbidden_levels:
                violations.append(GuardViolation("goal04_effect_contract", str(path), f"live source contains forbidden legacy level {node.value}"))
    foundation = source_root / "core" / "application" / "slices.py"
    foundation_source = foundation.read_text(encoding="utf-8")
    for token in ("ActionReceipt", "EvidenceBundle", "ActionRepository", ".commit("):
        if token in foundation_source:
            violations.append(GuardViolation("canonical_action_result", str(foundation), f"Foundation slice contains {token}"))
    composition = (source_root / "core" / "composition.py").read_text(encoding="utf-8")
    if "SqliteActionRepository" in composition:
        violations.append(GuardViolation("canonical_action_result", str(source_root / "core" / "composition.py"), "second action repository is composed"))
    return violations


def check_goal04_test_contracts(test_root: Path) -> list[GuardViolation]:
    violations: list[GuardViolation] = []
    fixture_allowlist = {"test_goal03_migrations.py", "test_goal04_execution_foundation.py"}
    forbidden_tokens = ("risk_level", "RiskLevel", "PermissionPolicy", "PermissionSummary", "permission_policy")
    forbidden_levels = {"L0", "L1", "L2", "L3", "L4"}
    for path in sorted(test_root.rglob("*.py")):
        if path.name in fixture_allowlist:
            continue
        source = path.read_text(encoding="utf-8")
        for token in forbidden_tokens:
            if token in source:
                violations.append(GuardViolation("goal04_test_contract", str(path), f"ordinary test contains forbidden token {token}"))
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in forbidden_levels:
                violations.append(GuardViolation("goal04_test_contract", str(path), f"ordinary test contains legacy level {node.value}"))
    return violations


def check_boundaries(core_root: Path) -> list[GuardViolation]:
    violations: list[GuardViolation] = []
    for path in sorted(core_root.rglob("*.py")):
        module = module_name(path, core_root.parents[1])
        source = path.read_text(encoding="utf-8")
        for message in boundary_violations(source, module):
            violations.append(GuardViolation("dependency_boundary", str(path), message))
    return violations


def boundary_violations(source: str, module: str) -> list[str]:
    layer = _core_layer(module)
    if layer not in {"domain", "ports", "application"}:
        return []
    allowed_core = {
        "domain": ("vibeos.core.domain",),
        "ports": ("vibeos.core.domain", "vibeos.core.ports"),
        "application": ("vibeos.core.domain", "vibeos.core.ports", "vibeos.core.application"),
    }[layer]
    violations: list[str] = []
    tree = ast.parse(source)
    for imported in imported_modules(tree, module):
        root = imported.split(".", 1)[0]
        if imported.startswith("vibeos.core"):
            if not imported.startswith(allowed_core):
                violations.append(f"{layer} may not import {imported}")
        elif root not in sys.stdlib_module_names and root != "__future__":
            violations.append(f"{layer} may not import external module {imported}")
    return violations


def check_cycles(core_root: Path) -> list[GuardViolation]:
    source_root = core_root.parents[1]
    modules = {module_name(path, source_root): path for path in sorted(core_root.rglob("*.py"))}
    graph: dict[str, set[str]] = {module: set() for module in modules}
    for module, path in modules.items():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for imported in imported_modules(tree, module):
            target = _nearest_module(imported, modules)
            if target is not None and target != module:
                graph[module].add(target)
    cycle = first_cycle(graph)
    if cycle is None:
        return []
    return [GuardViolation("import_cycle", str(core_root), " -> ".join(cycle))]


def check_quality(root: Path, config: dict[str, Any]) -> list[GuardViolation]:
    violations: list[GuardViolation] = []
    new_code = config["new_code"]
    forbidden = tuple(str(item) for item in new_code["forbidden_imports"])
    for configured in new_code["paths"]:
        path = root / str(configured)
        candidates = sorted(path.rglob("*.py")) if path.is_dir() else [path]
        for candidate in candidates:
            if not candidate.exists():
                violations.append(GuardViolation("missing_new_code", str(candidate), "configured path does not exist"))
                continue
            metrics = source_metrics(candidate.read_text(encoding="utf-8"))
            if metrics.lines > int(new_code["max_module_lines"]):
                violations.append(GuardViolation("module_lines", str(candidate), f"{metrics.lines} exceeds {new_code['max_module_lines']}"))
            if metrics.max_function_complexity > int(new_code["max_function_complexity"]):
                violations.append(
                    GuardViolation(
                        "function_complexity",
                        str(candidate),
                        f"{metrics.max_function_complexity} exceeds {new_code['max_function_complexity']}",
                    )
                )
            module = module_name(candidate, root / "src") if (root / "src") in candidate.parents else candidate.stem
            imports = imported_modules(ast.parse(candidate.read_text(encoding="utf-8")), module)
            for imported in imports:
                if imported.startswith(forbidden):
                    violations.append(GuardViolation("legacy_backflow", str(candidate), f"new code imports {imported}"))
    for relative, limits in config["legacy_debt"].items():
        path = root / relative
        if not path.exists():
            violations.append(GuardViolation("legacy_manifest", str(path), "legacy debt entry no longer exists; remove the manifest entry"))
            continue
        if not str(limits.get("owner", "")).strip() or not str(limits.get("deletion_gate", "")).strip():
            violations.append(GuardViolation("legacy_manifest", str(path), "owner and deletion_gate are required"))
        metrics = source_metrics(path.read_text(encoding="utf-8"))
        if metrics.lines > int(limits["max_lines"]):
            violations.append(GuardViolation("legacy_lines", str(path), f"{metrics.lines} exceeds ratchet {limits['max_lines']}"))
        if metrics.max_function_complexity > int(limits["max_function_complexity"]):
            violations.append(
                GuardViolation(
                    "legacy_complexity",
                    str(path),
                    f"{metrics.max_function_complexity} exceeds ratchet {limits['max_function_complexity']}",
                )
            )
    return violations


def source_metrics(source: str) -> ModuleMetrics:
    tree = ast.parse(source)
    complexities = [function_complexity(node) for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    return ModuleMetrics(lines=len(source.splitlines()), max_function_complexity=max(complexities, default=0))


def function_complexity(function: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    score = 1
    for node in ast.walk(function):
        if isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.IfExp, ast.Assert, ast.comprehension)):
            score += 1
        elif isinstance(node, ast.BoolOp):
            score += max(1, len(node.values) - 1)
        elif isinstance(node, ast.Try):
            score += len(node.handlers) + bool(node.orelse) + bool(node.finalbody)
        elif isinstance(node, ast.Match):
            score += len(node.cases)
    return score


def imported_modules(tree: ast.AST, module: str) -> set[str]:
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported = resolve_import(module, node.module, node.level)
            if imported:
                imports.add(imported)
    return imports


def resolve_import(module: str, imported: str | None, level: int) -> str:
    if level == 0:
        return imported or ""
    parts = module.split(".")
    base = parts[:-level]
    if imported:
        base.extend(imported.split("."))
    return ".".join(base)


def module_name(path: Path, source_root: Path) -> str:
    relative = path.relative_to(source_root).with_suffix("")
    return ".".join(relative.parts)


def first_cycle(graph: dict[str, set[str]]) -> list[str] | None:
    visiting: list[str] = []
    visited: set[str] = set()

    def visit(node: str) -> list[str] | None:
        if node in visiting:
            index = visiting.index(node)
            return [*visiting[index:], node]
        if node in visited:
            return None
        visiting.append(node)
        for target in sorted(graph[node]):
            cycle = visit(target)
            if cycle is not None:
                return cycle
        visiting.pop()
        visited.add(node)
        return None

    for node in sorted(graph):
        cycle = visit(node)
        if cycle is not None:
            return cycle
    return None


def _core_layer(module: str) -> str | None:
    parts = module.split(".")
    try:
        index = parts.index("core")
    except ValueError:
        return None
    return parts[index + 1] if len(parts) > index + 1 else None


def _nearest_module(imported: str, modules: dict[str, Path]) -> str | None:
    candidate = imported
    while candidate:
        if candidate in modules:
            return candidate
        package = candidate + ".__init__"
        if package in modules:
            return package
        candidate = candidate.rpartition(".")[0]
    return None


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    violations = check_repository(root)
    print(json.dumps({"ok": not violations, "violations": [asdict(item) for item in violations]}, ensure_ascii=False, indent=2))
    return 0 if not violations else 1


if __name__ == "__main__":
    raise SystemExit(main())
