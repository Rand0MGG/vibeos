from vibeos.intent import RuleIntentBroker
from vibeos.nlu import analyze_utterance
from vibeos.planner import plan_payload, plan_utterance


def test_analyze_utterance_classifies_chat() -> None:
    analysis = analyze_utterance("how should VibeOS v0.3 be designed?")
    assert analysis.type == "chat"


def test_analyze_utterance_classifies_open_browser_as_task() -> None:
    analysis = analyze_utterance("open browser")
    assert analysis.type == "task"


def test_analyze_utterance_classifies_play_baby_as_task() -> None:
    analysis = analyze_utterance("play baby")
    assert analysis.type == "task"


def test_analyze_utterance_classifies_delete_downloads_as_rejected() -> None:
    analysis = analyze_utterance("delete downloads")
    assert analysis.type == "rejected"


def test_analyze_utterance_classifies_clarification() -> None:
    analysis = analyze_utterance("play")
    assert analysis.type == "clarification"
    assert analysis.chat_response == "What would you like to play?"


def test_analyze_utterance_classifies_mixed_and_extracts_task_span() -> None:
    analysis = analyze_utterance("explain clipboard permissions and then copy hello to clipboard")
    assert analysis.type == "mixed"
    assert analysis.chat_response == "explain clipboard permissions"
    assert analysis.task_spans[0].text == "copy hello to clipboard"


def test_plan_payload_for_mixed_request_returns_clipboard_task_plan() -> None:
    payload = plan_payload("explain clipboard permissions and then copy hello to clipboard")
    assert payload["status"] == "validated"
    assert payload["analysis"]["type"] == "mixed"
    assert payload["plan"]["steps"][0]["action"] == "clipboard.write"
    assert payload["plan"]["steps"][0]["target"]["text"] == "hello"


def test_plan_payload_for_clipboard_variants_returns_task_plan() -> None:
    variants = (
        "clipboard VibeOS evidence",
        "copy VibeOS evidence",
        "copy to clipboard VibeOS evidence",
        "write VibeOS evidence to clipboard",
    )

    for utterance in variants:
        payload = plan_payload(utterance)
        assert payload["status"] == "validated"
        assert payload["plan"]["steps"][0]["action"] == "clipboard.write"
        assert payload["plan"]["steps"][0]["target"]["text"] == "VibeOS evidence"


def test_plan_payload_for_media_request_selects_browser_route_when_media_capabilities_absent() -> None:
    payload = plan_payload("play baby")
    assert payload["status"] == "validated"
    assert payload["analysis"]["type"] == "task"
    assert payload["plan"]["selected_route_id"] == "browser_music_search_route"
    assert len(payload["plan"]["steps"]) == 1
    assert payload["plan"]["steps"][0]["action"] == "browser.open_site_search"
    assert payload["plan"]["steps"][0]["target"]["site"] == "youtube.com"
    assert tuple(payload["plan"]["steps"][0]["depends_on"]) == ()
    assert len(payload["candidates"]) == 2
    assert payload["candidates"][0]["score"] >= payload["candidates"][1]["score"]
    assert tuple(payload["domain_routing"]["active_domain_ids"]) == ("media", "browser")
    assert set(payload["capability_exposure"]["exposed_route_ids"]) == {
        "browser_open_url_route",
        "browser_search_web_route",
        "browser_site_search_route",
        "browser_music_search_route",
        "media_search_route",
        "media_play_route",
        "media_pause_route",
    }


def test_plan_utterance_prefers_music_route_when_media_capabilities_exist() -> None:
    analysis, plan, candidates = plan_utterance(
        "play baby",
        capability_context={"app.open", "media.search", "media.play", "browser.open_site_search"},
    )

    assert analysis.type == "task"
    assert plan is not None
    assert plan.selected_route_id == "media_play_route"
    assert candidates[0].selected_route_id in {"media_play_route", "browser_music_search_route"}


def test_plan_utterance_returns_no_plan_when_no_route_is_satisfiable() -> None:
    analysis, plan, candidates = plan_utterance("play baby", capability_context=set())

    assert analysis.type == "task"
    assert plan is None
    assert len(candidates) == 2


def test_plan_payload_returns_rejected_when_no_route_is_satisfiable() -> None:
    payload = plan_payload("play baby", capability_context=set())

    assert payload["status"] == "rejected"
    assert payload["plan"] is None
    assert payload["message"] == "no route satisfies required capabilities"
    assert len(payload["candidates"]) == 2


def test_plan_utterance_returns_no_plan_for_clarification() -> None:
    analysis, plan, candidates = plan_utterance("play")
    assert analysis.type == "clarification"
    assert plan is None
    assert candidates == []


def test_plan_payload_exposes_browser_domain_trace_for_open_url() -> None:
    payload = plan_payload("open https://example.com")

    assert payload["status"] == "validated"
    assert payload["plan"]["selected_route_id"] == "browser_open_url_route"
    assert payload["plan"]["steps"][0]["action"] == "browser.open_url"
    assert tuple(payload["domain_routing"]["active_domain_ids"]) == ("browser",)
    assert "media" in payload["capability_exposure"]["hidden_domain_ids"]
    assert payload["trace"]["selected_route"]["id"] == "browser_open_url_route"


def test_plan_payload_routes_named_web_targets_to_browser_search() -> None:
    payload = plan_payload("\u6253\u5f00\u767e\u5ea6\u5b98\u7f51", intent_broker=RuleIntentBroker())

    assert payload["status"] == "validated"
    assert tuple(payload["analysis"]["domains"]) == ("browser",)
    assert payload["plan"]["selected_route_id"] == "browser_search_web_route"
    assert payload["plan"]["steps"][0]["action"] == "browser.search_web"
    assert payload["plan"]["steps"][0]["target"]["query"] == "\u767e\u5ea6\u5b98\u7f51"


def test_plan_payload_routes_bare_domains_to_browser_open_url() -> None:
    payload = plan_payload("open baidu.com", intent_broker=RuleIntentBroker())

    assert payload["status"] == "validated"
    assert payload["plan"]["selected_route_id"] == "browser_open_url_route"
    assert payload["plan"]["steps"][0]["action"] == "browser.open_url"
    assert payload["plan"]["steps"][0]["target"]["uri"] == "https://baidu.com"


def test_plan_payload_supports_chinese_browser_and_media_examples() -> None:
    open_payload = plan_payload("打开 https://example.com")
    search_payload = plan_payload("搜索 hello")
    media_payload = plan_payload("我想听 baby")

    assert open_payload["plan"]["steps"][0]["action"] == "browser.open_url"
    assert search_payload["plan"]["steps"][0]["action"] == "browser.search_web"
    assert media_payload["plan"]["selected_route_id"] == "browser_music_search_route"
