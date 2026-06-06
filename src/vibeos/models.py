from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from .capabilities import allowed_actions

ALLOWED_ACTIONS = allowed_actions()

Action = Literal[
    "app.list",
    "app.open",
    "browser.open_site_search",
    "browser.open_url",
    "browser.search_web",
    "clipboard.write",
    "media.pause",
    "media.play",
    "media.search",
    "notification.send",
    "portal.open_uri",
    "window.list",
    "window.focus",
    "window.minimize",
    "window.maximize",
    "window.close",
    "system.status",
    "unknown",
]

RiskLevel = Literal["L0", "L1", "L2", "L3"]


@dataclass(frozen=True)
class Intent:
    action: Action
    target: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    requires_confirmation: bool = False

    @classmethod
    def unknown(cls, reason: str, target: dict[str, Any] | None = None) -> "Intent":
        return cls(action="unknown", target=target or {}, reason=reason, requires_confirmation=False)


@dataclass(frozen=True)
class CommandRequest:
    utterance: str
    mode: str = "auto_low_risk"
    dry_run: bool = False
    approve: bool = False
    review_id: str | None = None
    transport: str | None = None
    debug: bool = False


@dataclass(frozen=True)
class PermissionReview:
    risk_level: RiskLevel
    review_required: bool
    allowed: bool
    reason: str
    effects: tuple[str, ...] = ()
    reversible: bool = False


@dataclass(frozen=True)
class CommandResult:
    status: Literal["executed", "dry_run", "rejected", "ambiguous", "failed", "review_required"]
    intent: Intent
    result: Any = None
    selected_target: str | None = None
    audit_id: str | None = None
    review_id: str | None = None
    transport: str | None = None
    message: str = ""
    review: PermissionReview | None = None
    execution_status: str = "not_started"
    acceptance_status: str = "skipped"
    overall_status: str = "failed"


@dataclass(frozen=True)
class ReviewRequest:
    review_id: str
    utterance: str
    intent: Intent
    review: PermissionReview
    created_at: str
    status: Literal["pending", "approved", "rejected", "consumed", "expired"] = "pending"
    expires_at: str | None = None
    review_kind: Literal["intent", "plan"] = "intent"
    plan_id: str | None = None
    plan_payload: dict[str, Any] | None = None
    step_reviews: tuple[dict[str, Any], ...] = ()
    layer: str | None = None


@dataclass(frozen=True)
class AppEntry:
    desktop_id: str
    name: str
    exec_line: str | None = None
    keywords: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()


@dataclass(frozen=True)
class WindowEntry:
    window_id: str
    app_id: str
    title: str
    workspace: int | None = None
    focused: bool = False


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
