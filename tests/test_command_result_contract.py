import json
from dataclasses import asdict
from pathlib import Path

from vibeos.models import CommandResult, Intent


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "command_result_contract.json"


def test_public_command_result_contract_is_explicit_and_serializable() -> None:
    contract = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    result = CommandResult(
        status="executed",
        intent=Intent(action="system.status"),
        execution_status="succeeded",
        acceptance_status="passed",
        overall_status="completed",
    )

    payload = asdict(result)

    assert set(contract["required_fields"]).issubset(payload)
    assert contract["semantic_statuses"]["completed"] == result.overall_status
