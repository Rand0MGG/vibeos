import pytest

from vibeos.provider_client import bounded_provider_timeout_seconds, model_request_budget


def test_command_model_budget_bounds_all_sequential_provider_calls(monkeypatch) -> None:
    monkeypatch.setenv("VIBEOS_PROVIDER_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("VIBEOS_COMMAND_MODEL_BUDGET_SECONDS", "3")
    ticks = iter((100.0, 101.0, 104.0))
    monkeypatch.setattr("vibeos.provider_client.monotonic", lambda: next(ticks))

    with model_request_budget():
        assert bounded_provider_timeout_seconds() == pytest.approx(2.0)
        with pytest.raises(TimeoutError, match="budget was exhausted"):
            bounded_provider_timeout_seconds()
