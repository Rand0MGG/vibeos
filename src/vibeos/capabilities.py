from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class CapabilitySpec:
    action: str
    risk_level: str
    review_required: bool
    allowed: bool
    reason: str
    effects: tuple[str, ...]
    reversible: bool
    parallel_safe: bool = False
    constraints: tuple[str, ...] = ()


CAPABILITIES: dict[str, CapabilitySpec] = {
    "app.list": CapabilitySpec(
        action="app.list",
        risk_level="L0",
        review_required=False,
        allowed=True,
        reason="Observe-only capability with no system side effects.",
        effects=("Read available desktop applications.",),
        reversible=True,
        constraints=("No target accepted.",),
    ),
    "window.list": CapabilitySpec(
        action="window.list",
        risk_level="L0",
        review_required=False,
        allowed=True,
        reason="Observe-only capability with no system side effects.",
        effects=("Read current desktop window metadata.",),
        reversible=True,
        constraints=("No target accepted.",),
    ),
    "system.status": CapabilitySpec(
        action="system.status",
        risk_level="L0",
        review_required=False,
        allowed=True,
        reason="Observe-only capability with no system side effects.",
        effects=("Read current VibeOS and Linux session integration status.",),
        reversible=True,
        constraints=("No target accepted.",),
    ),
    "app.open": CapabilitySpec(
        action="app.open",
        risk_level="L1",
        review_required=False,
        allowed=True,
        reason="Low-risk user-session action with limited side effects.",
        effects=("May launch or focus an application.",),
        reversible=True,
        constraints=("Target must name an installed desktop application.",),
    ),
    "window.focus": CapabilitySpec(
        action="window.focus",
        risk_level="L1",
        review_required=False,
        allowed=True,
        reason="Low-risk user-session action with limited side effects.",
        effects=("May switch the active window.",),
        reversible=True,
        constraints=("Target must name a visible window or use current.",),
    ),
    "window.minimize": CapabilitySpec(
        action="window.minimize",
        risk_level="L1",
        review_required=False,
        allowed=True,
        reason="Low-risk user-session action with limited side effects.",
        effects=("May hide a window from the current workspace.",),
        reversible=True,
        constraints=("Target must name a visible window or use current.",),
    ),
    "window.maximize": CapabilitySpec(
        action="window.maximize",
        risk_level="L1",
        review_required=False,
        allowed=True,
        reason="Low-risk user-session action with limited side effects.",
        effects=("May resize a window to fill the workspace.",),
        reversible=True,
        constraints=("Target must name a visible window or use current.",),
    ),
    "notification.send": CapabilitySpec(
        action="notification.send",
        risk_level="L1",
        review_required=False,
        allowed=True,
        reason="Low-risk user-session action with limited side effects.",
        effects=("May display a desktop notification.",),
        reversible=True,
        constraints=("Notification title and body are length-limited.",),
    ),
    "window.close": CapabilitySpec(
        action="window.close",
        risk_level="L2",
        review_required=True,
        allowed=True,
        reason="Closing a window can discard unsaved work.",
        effects=("May close an application window and lose unsaved work.",),
        reversible=False,
        constraints=("Target must name a visible window or use current.",),
    ),
    "portal.open_uri": CapabilitySpec(
        action="portal.open_uri",
        risk_level="L2",
        review_required=True,
        allowed=True,
        reason="Opening a URI can launch another app or contact a remote site.",
        effects=("May open a URI in another application or browser.",),
        reversible=True,
        constraints=("Only http and https URI targets are allowed.",),
    ),
    "clipboard.write": CapabilitySpec(
        action="clipboard.write",
        risk_level="L2",
        review_required=True,
        allowed=True,
        reason="Writing the clipboard replaces user-controlled session state.",
        effects=("May replace the user's clipboard contents.",),
        reversible=True,
        constraints=("Clipboard text must be non-empty and length-limited.",),
    ),
    "browser.open_url": CapabilitySpec(
        action="browser.open_url",
        risk_level="L1",
        review_required=False,
        allowed=True,
        reason="Opening a normal browser URL is a bounded low-risk browsing action.",
        effects=("May open an https URL in a browser.",),
        reversible=True,
        constraints=("Only vetted URL schemes are allowed; credentials and local data schemes are rejected.",),
    ),
    "browser.search_web": CapabilitySpec(
        action="browser.search_web",
        risk_level="L1",
        review_required=False,
        allowed=True,
        reason="Searching the web with user-provided text is a bounded low-risk browsing action.",
        effects=("May perform a web search in a browser.",),
        reversible=True,
        constraints=("Only user-provided query text may be used without additional review.",),
    ),
    "browser.open_site_search": CapabilitySpec(
        action="browser.open_site_search",
        risk_level="L1",
        review_required=False,
        allowed=True,
        reason="Searching a site in the browser is a bounded low-risk browsing action.",
        effects=("May perform a site-scoped search in a browser.",),
        reversible=True,
        constraints=("Only user-provided site and query text may be used without additional review.",),
    ),
    "media.search": CapabilitySpec(
        action="media.search",
        risk_level="L1",
        review_required=False,
        allowed=True,
        reason="Media search is planner-visible but may be unavailable on the local host.",
        effects=("May search media catalog state in a dedicated player.",),
        reversible=True,
        constraints=("Execution availability depends on a dedicated media adapter.",),
    ),
    "media.play": CapabilitySpec(
        action="media.play",
        risk_level="L1",
        review_required=False,
        allowed=True,
        reason="Media play is planner-visible but may be unavailable on the local host.",
        effects=("May start media playback in a dedicated player.",),
        reversible=True,
        constraints=("Execution availability depends on a dedicated media adapter.",),
    ),
    "media.pause": CapabilitySpec(
        action="media.pause",
        risk_level="L1",
        review_required=False,
        allowed=True,
        reason="Media pause is planner-visible but may be unavailable on the local host.",
        effects=("May pause media playback in a dedicated player.",),
        reversible=True,
        constraints=("Execution availability depends on a dedicated media adapter.",),
    ),
}

UNKNOWN_CAPABILITY = CapabilitySpec(
    action="unknown",
    risk_level="L3",
    review_required=False,
    allowed=False,
    reason="Unsupported or unclear request.",
    effects=("No system capability will be executed.",),
    reversible=True,
    constraints=("No system capability will be executed.",),
)


def allowed_actions() -> set[str]:
    return set(CAPABILITIES) | {"unknown"}


def executable_actions() -> list[str]:
    return sorted(CAPABILITIES)


def capability_payload() -> list[dict[str, object]]:
    return [asdict(CAPABILITIES[action]) for action in executable_actions()]


def permission_summary() -> dict[str, str]:
    return {
        "L0": "automatic observe-only",
        "L1": "automatic low-risk action with audit",
        "L2": "requires stored review_id approval",
        "L3": "rejected",
    }
