from pathlib import Path

from scripts.architecture_guard import boundary_violations, check_repository


ROOT = Path(__file__).resolve().parents[1]


def test_dependency_complexity_cycle_and_legacy_ratchets_hold() -> None:
    assert check_repository(ROOT) == []


def test_dependency_guard_deliberately_catches_reverse_import_fixture() -> None:
    fixture = "from vibeos.core.adapters.database import CoreDatabase\n"

    violations = boundary_violations(fixture, "vibeos.core.domain.reverse_fixture")

    assert violations == ["domain may not import vibeos.core.adapters.database"]
