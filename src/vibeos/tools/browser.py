from __future__ import annotations

from typing import Any

from ..browser_state import browser_attempt_scope, browser_context_snapshot, record_browser_navigation
from ..intent import Intent
from ..planner import browser_semantic_uri
from ..portal import PortalAdapter
from ..tool_protocol import ToolExecutionContext, ToolResult, ToolSpec
from ..verifiers import VerifierHarness


def browser_tool_specs(portal: PortalAdapter, verifiers: VerifierHarness) -> tuple[ToolSpec, ...]:
    def action(tool_id: str, payload: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        uri, query, site = browser_runtime_target(tool_id, payload)
        if not uri:
            return ToolResult(status="failed", message="browser route did not produce a URI", failure_class="semantic_mismatch")
        if context.environment.dry_run:
            with browser_attempt_scope(run_id=context.turn_id, attempt_id=context.attempt_id, route_id=context.strategy_id):
                record_browser_navigation(uri=uri, query=query, site=site, adapter="browser.semantic", status="opened")
            state_updates = {"selected_target": uri, "last_uri": uri}
            if tool_id == "browser.search_web":
                state_updates["search_uri"] = uri
            if query:
                state_updates["observed_query"] = query
            return ToolResult(
                status="succeeded",
                output={"selected_target": uri, "uri": uri, "adapter": "browser.semantic", "adapter_status": "dry_run"},
                evidence={"uri": uri, "query": query, "site": site, "dry_run": True},
                state_updates=state_updates,
            )
        with browser_attempt_scope(run_id=context.turn_id, attempt_id=context.attempt_id, route_id=context.strategy_id):
            result = portal.open_uri(uri)
            if query or site:
                record_browser_navigation(
                    uri=uri,
                    query=query,
                    site=site,
                    adapter=str(result.get("adapter") or "browser.semantic"),
                    status="opened" if result.get("status") == "opened" else str(result.get("status") or "failed"),
                )
        status = str(result.get("status") or "failed")
        if status == "opened":
            state_updates = {"selected_target": uri, "last_uri": uri}
            if tool_id == "browser.search_web":
                state_updates["search_uri"] = uri
            if query:
                state_updates["observed_query"] = query
            return ToolResult(
                status="succeeded",
                output={"selected_target": uri, "uri": uri, "adapter": "browser.semantic", "adapter_status": "succeeded", **result},
                evidence={"uri": uri, "query": query, "site": site},
                state_updates=state_updates,
            )
        return ToolResult(
            status="failed",
            message=str(result.get("error") or "browser action failed"),
            output={"uri": uri, "adapter": "browser.semantic", "adapter_status": status, **result},
            evidence={"uri": uri, "query": query, "site": site},
            failure_class="tool_timeout" if status == "timeout" else "environment_unreachable",
        )

    def observe(_payload: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        snapshot = browser_context_snapshot(context.attempt_id)
        if not snapshot.get("active_url"):
            observed = verifiers.observation_for("browser_url_opened").get("opened_url")
            snapshot["active_url"] = observed or (snapshot.get("requested_url") if context.environment.dry_run else None)
        if not snapshot.get("query"):
            observed = verifiers.observation_for("browser_search_route_completed").get("query")
            snapshot["query"] = observed or (snapshot.get("requested_query") if context.environment.dry_run else None)
        return ToolResult(
            status="succeeded",
            output={"observed_url": snapshot.get("active_url"), "observed_query": snapshot.get("query")},
            evidence=snapshot,
            state_updates={"observed_url": snapshot.get("active_url"), "observed_query": snapshot.get("query")},
        )

    def resolve_named(payload: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        name = str(payload.get("name") or payload.get("target_name") or "").strip()
        resolution_mode = str(payload.get("resolution_mode") or "direct").strip()
        direct_url = str(getattr(context.environment, "browser_site_catalog", {}).get(name.lower(), "") or "")
        catalog = getattr(context.environment, "browser_search_catalog", {})
        search_item = catalog.get(name.lower(), {}) if isinstance(catalog, dict) else {}
        search_url = str(search_item.get("official_url") or "") if isinstance(search_item, dict) else ""
        resolved = direct_url if resolution_mode == "direct" else search_url if resolution_mode == "search_followup" else direct_url or search_url
        if not resolved:
            message = (
                "browser search results did not provide a follow-up destination"
                if resolution_mode == "search_followup"
                else "no local direct-open resolution matched the named website target"
            )
            return ToolResult(
                status="failed", message=message, evidence={"requested_name": name, "resolution_mode": resolution_mode}, failure_class="semantic_mismatch"
            )
        return ToolResult(
            status="succeeded",
            output={"resolved_url": resolved, "resolution_mode": resolution_mode},
            evidence={"requested_name": name, "resolved_url": resolved, "resolution_source": "local_catalog", "resolution_mode": resolution_mode},
            state_updates={"resolved_url": resolved},
        )

    def open_resolved(_payload: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        resolved = str(context.state.get("resolved_url") or "")
        return (
            action("browser.open_url", {"uri": resolved}, context)
            if resolved
            else ToolResult(status="failed", message="named browser target has not been resolved", failure_class="semantic_mismatch")
        )

    def observe_search(payload: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        query = str(payload.get("query") or context.state.get("observed_query") or "").strip()
        catalog = getattr(context.environment, "browser_search_catalog", {})
        item = catalog.get(query.lower(), {}) if isinstance(catalog, dict) else {}
        official_url = str(item.get("official_url") or "")
        return ToolResult(
            status="succeeded",
            output={"official_result_url": official_url, "result_count": 1 if official_url else 0},
            evidence={"query": query, "official_result_url": official_url, "result_source": "local_catalog"},
            state_updates={"official_result_url": official_url},
        )

    def follow_search(payload: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        url = str(context.state.get("official_result_url") or "")
        return (
            action("browser.open_url", {"uri": url}, context)
            if url
            else ToolResult(
                status="failed",
                message="browser search results did not provide a follow-up destination",
                evidence={"query": payload.get("query")},
                failure_class="semantic_mismatch",
            )
        )

    def verify_query(payload: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        expected = str(payload.get("query") or "")
        observed = str(context.state.get("observed_query") or "") or str(verifiers.observation_for("browser_search_route_completed").get("query") or "")
        return verification(
            expected, observed, "query", "browser verifier observed the requested search query", "browser verifier did not observe the expected search query"
        )

    def verify_url(payload: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        expected = str(payload.get("uri") or "")
        observed = str(context.state.get("observed_url") or "") or str(verifiers.observation_for("browser_url_opened").get("opened_url") or "")
        return verification(expected, observed, "url", "browser verifier observed the requested URL", "browser verifier did not observe the expected URL")

    def verify_identity(payload: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        name = str(payload.get("name") or "").strip()
        observed = str(context.state.get("observed_url") or "") or str(verifiers.observation_for("browser_url_opened").get("opened_url") or "")
        resolved = str(
            context.state.get("resolved_url")
            or context.state.get("official_result_url")
            or getattr(context.environment, "browser_site_catalog", {}).get(name.lower(), "")
            or ""
        )
        accepted = bool(observed) and bool(resolved) and observed == resolved
        return ToolResult(
            status="succeeded" if accepted else "failed",
            message="browser verifier observed the resolved goal page identity"
            if accepted
            else "browser verifier did not observe the resolved goal page identity",
            evidence={"target_name": name, "observed_url": observed, "resolved_url": resolved},
            accepted=accepted,
            failure_class="acceptance_failed" if not accepted else "none",
        )

    return (
        ToolSpec("browser.open_url", "action", "browser", lambda payload, context: action("browser.open_url", payload, context)),
        ToolSpec("browser.resolve_named_target", "resolver", "browser", resolve_named),
        ToolSpec("browser.open_resolved_target", "action", "browser", open_resolved),
        ToolSpec("browser.search_web", "action", "browser", lambda payload, context: action("browser.search_web", payload, context)),
        ToolSpec("browser.open_site_search", "action", "browser", lambda payload, context: action("browser.open_site_search", payload, context)),
        ToolSpec("browser.observe_context", "observer", "browser", observe),
        ToolSpec("browser.observe_search_results", "observer", "browser", observe_search),
        ToolSpec("browser.follow_search_result", "action", "browser", follow_search),
        ToolSpec("browser.verify_query", "verifier", "browser", verify_query),
        ToolSpec("browser.verify_url_opened", "verifier", "browser", verify_url),
        ToolSpec("browser.verify_goal_page_identity", "verifier", "browser", verify_identity),
    )


def browser_runtime_target(tool_id: str, payload: dict[str, Any]) -> tuple[str, str | None, str | None]:
    if tool_id == "browser.open_url":
        return str(payload.get("uri") or payload.get("url") or ""), None, None
    query = str(payload.get("query") or "")
    if tool_id == "browser.search_web":
        return browser_semantic_uri(Intent(action="browser.search_web", target={"query": query})), query or None, None
    if tool_id == "browser.open_site_search":
        site = str(payload.get("site") or "")
        return browser_semantic_uri(Intent(action="browser.open_site_search", target={"query": query, "site": site})), query or None, site or None
    return "", None, None


def verification(expected: str, observed: str, field: str, success: str, failure: str) -> ToolResult:
    accepted = bool(expected) and observed == expected
    return ToolResult(
        status="succeeded" if accepted else "failed",
        message=success if accepted else failure,
        evidence={f"expected_{field}": expected, f"observed_{field}": observed},
        accepted=accepted,
        failure_class="acceptance_failed" if not accepted else "none",
    )
