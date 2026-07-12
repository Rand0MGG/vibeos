from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any, Iterator

from .models import utc_now_iso


_CURRENT_RUN_ID: ContextVar[str | None] = ContextVar("vibeos_browser_run_id", default=None)
_CURRENT_ATTEMPT_ID: ContextVar[str | None] = ContextVar("vibeos_browser_attempt_id", default=None)
_CURRENT_ROUTE_ID: ContextVar[str | None] = ContextVar("vibeos_browser_route_id", default=None)

_BROWSER_EVENTS: dict[str, dict[str, Any]] = {}


def _blank_browser_event(*, run_id: str | None = None, attempt_id: str | None = None, route_id: str | None = None) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "attempt_id": attempt_id,
        "route_id": route_id,
        "status": "unavailable",
        "active_url": None,
        "requested_url": None,
        "requested_query": None,
        "query": None,
        "site": None,
        "page_title": None,
        "app_id": None,
        "error_state": None,
        "adapter": None,
        "captured_at": utc_now_iso(),
    }


def _current_attempt_key() -> str | None:
    return _CURRENT_ATTEMPT_ID.get()


def _event_for_attempt(attempt_id: str | None) -> dict[str, Any]:
    key = attempt_id or "__legacy__"
    event = _BROWSER_EVENTS.get(key)
    if event is None:
        event = _blank_browser_event(
            run_id=_CURRENT_RUN_ID.get(),
            attempt_id=attempt_id,
            route_id=_CURRENT_ROUTE_ID.get(),
        )
        _BROWSER_EVENTS[key] = event
    return event


@contextmanager
def browser_attempt_scope(*, run_id: str, attempt_id: str, route_id: str | None = None) -> Iterator[None]:
    run_token: Token[str | None] = _CURRENT_RUN_ID.set(run_id)
    attempt_token: Token[str | None] = _CURRENT_ATTEMPT_ID.set(attempt_id)
    route_token: Token[str | None] = _CURRENT_ROUTE_ID.set(route_id)
    _BROWSER_EVENTS[attempt_id] = _blank_browser_event(run_id=run_id, attempt_id=attempt_id, route_id=route_id)
    try:
        yield
    finally:
        _CURRENT_ROUTE_ID.reset(route_token)
        _CURRENT_ATTEMPT_ID.reset(attempt_token)
        _CURRENT_RUN_ID.reset(run_token)


@contextmanager
def browser_observation_scope(attempt_id: str | None) -> Iterator[None]:
    """Expose an existing attempt receipt to a later observation without resetting it."""

    if attempt_id is None:
        yield
        return
    event = _BROWSER_EVENTS.get(attempt_id)
    run_token: Token[str | None] = _CURRENT_RUN_ID.set(str(event.get("run_id")) if event and event.get("run_id") else None)
    attempt_token: Token[str | None] = _CURRENT_ATTEMPT_ID.set(attempt_id)
    route_token: Token[str | None] = _CURRENT_ROUTE_ID.set(str(event.get("route_id")) if event and event.get("route_id") else None)
    try:
        yield
    finally:
        _CURRENT_ROUTE_ID.reset(route_token)
        _CURRENT_ATTEMPT_ID.reset(attempt_token)
        _CURRENT_RUN_ID.reset(run_token)


def record_browser_navigation(
    *,
    uri: str | None,
    query: str | None = None,
    site: str | None = None,
    adapter: str | None = None,
    status: str = "opened",
    error_state: str | None = None,
) -> None:
    event = _event_for_attempt(_current_attempt_key())
    next_status = "requested" if status == "opened" else status
    if str(event.get("status") or "") == "loaded" and next_status == "requested":
        next_status = "loaded"
    event.update(
        {
            "run_id": event.get("run_id") or _CURRENT_RUN_ID.get(),
            "attempt_id": event.get("attempt_id") or _CURRENT_ATTEMPT_ID.get(),
            "route_id": event.get("route_id") or _CURRENT_ROUTE_ID.get(),
            "status": next_status,
            "requested_url": uri,
            "requested_query": query,
            "site": site,
            "adapter": adapter,
            "error_state": error_state,
            "captured_at": utc_now_iso(),
        }
    )


def record_browser_observation(
    *,
    active_url: str | None = None,
    query: str | None = None,
    page_title: str | None = None,
    app_id: str | None = None,
    adapter: str | None = None,
    error_state: str | None = None,
    status: str = "loaded",
) -> None:
    event = _event_for_attempt(_current_attempt_key())
    event.update(
        {
            "run_id": event.get("run_id") or _CURRENT_RUN_ID.get(),
            "attempt_id": event.get("attempt_id") or _CURRENT_ATTEMPT_ID.get(),
            "route_id": event.get("route_id") or _CURRENT_ROUTE_ID.get(),
            "status": status,
            "active_url": active_url if active_url is not None else event.get("active_url"),
            "query": query if query is not None else event.get("query"),
            "page_title": page_title if page_title is not None else event.get("page_title"),
            "app_id": app_id if app_id is not None else event.get("app_id"),
            "adapter": adapter if adapter is not None else event.get("adapter"),
            "error_state": error_state if error_state is not None else event.get("error_state"),
            "captured_at": utc_now_iso(),
        }
    )


def record_browser_window_observation(*, page_title: str | None, app_id: str | None) -> None:
    event = _event_for_attempt(_current_attempt_key())
    if page_title:
        event["page_title"] = page_title
    if app_id:
        event["app_id"] = app_id
    event["captured_at"] = utc_now_iso()


def browser_context_snapshot(attempt_id: str | None = None) -> dict[str, Any]:
    event = _BROWSER_EVENTS.get(attempt_id or _current_attempt_key() or "__legacy__")
    if event is None:
        return _blank_browser_event(
            run_id=_CURRENT_RUN_ID.get(),
            attempt_id=attempt_id or _CURRENT_ATTEMPT_ID.get(),
            route_id=_CURRENT_ROUTE_ID.get(),
        )
    return dict(event)
