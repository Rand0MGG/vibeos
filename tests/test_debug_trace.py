from vibeos.planner import plan_payload


def test_debug_trace_hides_raw_provider_output_by_default() -> None:
    payload = plan_payload("open browser")
    exchange = payload["debug_trace"]["model_exchange"][0]

    assert "normalized_output" in exchange
    assert "raw_output" not in exchange


def test_debug_trace_exposes_raw_provider_output_only_with_debug_flag() -> None:
    payload = plan_payload("open browser", debug=True)
    exchange = payload["debug_trace"]["model_exchange"][0]

    assert exchange["raw_output"]
    assert exchange["provider_name"] == "local"
    assert exchange["fallback_used"] is True
