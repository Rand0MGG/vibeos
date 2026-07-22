from __future__ import annotations

from urllib.parse import urlparse

from .capabilities import CAPABILITIES, UNKNOWN_CAPABILITY
from .models import EffectAssessment, Intent


MAX_NAME_LENGTH = 160
MAX_URI_LENGTH = 2048
MAX_CLIPBOARD_LENGTH = 4096
MAX_NOTIFICATION_TITLE_LENGTH = 80
MAX_NOTIFICATION_BODY_LENGTH = 500


class EffectPolicy:
    """Deterministic effect policy for VibeOS system capabilities.

    The model may request capabilities, but this policy decides whether the
    broker can execute them automatically, must ask for review, or must reject.
    """

    def assess(self, intent: Intent) -> EffectAssessment:
        if intent.action == "unknown":
            return EffectAssessment(
                effect_level=UNKNOWN_CAPABILITY.effect_level,
                review_required=UNKNOWN_CAPABILITY.review_required,
                allowed=UNKNOWN_CAPABILITY.allowed,
                reason=intent.reason or "Unsupported or unclear request.",
                effects=UNKNOWN_CAPABILITY.effects,
                reversible=UNKNOWN_CAPABILITY.reversible,
            )

        spec = CAPABILITIES.get(intent.action)
        if spec:
            target_error = validate_target(intent)
            if target_error:
                return EffectAssessment(
                    effect_level=UNKNOWN_CAPABILITY.effect_level,
                    review_required=UNKNOWN_CAPABILITY.review_required,
                    allowed=UNKNOWN_CAPABILITY.allowed,
                    reason=target_error,
                    effects=UNKNOWN_CAPABILITY.effects,
                    reversible=UNKNOWN_CAPABILITY.reversible,
                )
            return EffectAssessment(
                effect_level=spec.effect_level,
                review_required=spec.review_required,
                allowed=spec.allowed,
                reason=spec.reason,
                effects=spec.effects,
                reversible=spec.reversible,
            )
        return EffectAssessment(
            effect_level=UNKNOWN_CAPABILITY.effect_level,
            review_required=UNKNOWN_CAPABILITY.review_required,
            allowed=UNKNOWN_CAPABILITY.allowed,
            reason=f"Capability {intent.action!r} is not allowed by VibeOS v0.1.",
            effects=UNKNOWN_CAPABILITY.effects,
            reversible=UNKNOWN_CAPABILITY.reversible,
        )


def validate_target(intent: Intent) -> str | None:
    """Reject targets that are outside the narrow v0.1 capability contract."""

    target = intent.target
    action = intent.action

    if action in {"app.list", "window.list", "system.status"}:
        return None

    if action == "app.open":
        name = _target_text(target, "name", "app")
        if not name:
            return "app.open requires an application name."
        return _validate_short_text(name, "application name", MAX_NAME_LENGTH)

    if action in {"window.focus", "window.minimize", "window.maximize", "window.close"}:
        name = _target_text(target, "name", "window") or "current"
        return _validate_short_text(name, "window target", MAX_NAME_LENGTH)

    if action == "notification.send":
        title = _target_text(target, "title") or "VibeOS"
        body = _target_text(target, "body", "message")
        title_error = _validate_short_text(title, "notification title", MAX_NOTIFICATION_TITLE_LENGTH)
        if title_error:
            return title_error
        if body:
            return _validate_short_text(body, "notification body", MAX_NOTIFICATION_BODY_LENGTH)
        return None

    if action == "portal.open_uri":
        uri = _target_text(target, "uri", "url", "name")
        if not uri:
            return "portal.open_uri requires a URI target."
        if len(uri) > MAX_URI_LENGTH:
            return f"URI target exceeds {MAX_URI_LENGTH} characters."
        parsed = urlparse(uri)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return "portal.open_uri only allows http or https URI targets."
        if parsed.username or parsed.password:
            return "portal.open_uri rejects URI targets containing credentials."
        return None

    if action == "browser.open_url":
        uri = _target_text(target, "uri", "url", "name")
        if not uri:
            return "browser.open_url requires a URI target."
        if len(uri) > MAX_URI_LENGTH:
            return f"URI target exceeds {MAX_URI_LENGTH} characters."
        parsed = urlparse(uri)
        if parsed.scheme != "https" or not parsed.netloc:
            return "browser.open_url only allows https URI targets."
        if parsed.username or parsed.password:
            return "browser.open_url rejects URI targets containing credentials."
        return None

    if action == "browser.search_web":
        query = _target_text(target, "query")
        if not query:
            return "browser.search_web requires a non-empty query."
        return _validate_short_text(query, "search query", MAX_CLIPBOARD_LENGTH)

    if action == "browser.open_named_target":
        name = _target_text(target, "name", "target_name")
        if not name:
            return "browser.open_named_target requires a named target."
        return _validate_short_text(name, "named browser target", MAX_NAME_LENGTH)

    if action == "browser.open_site_search":
        site = _target_text(target, "site")
        query = _target_text(target, "query")
        if not site:
            return "browser.open_site_search requires a site."
        site_error = _validate_short_text(site, "site", MAX_NAME_LENGTH)
        if site_error:
            return site_error
        if not query:
            return "browser.open_site_search requires a non-empty query."
        return _validate_short_text(query, "search query", MAX_CLIPBOARD_LENGTH)

    if action in {"media.search", "media.play", "media.pause"}:
        query = _target_text(target, "query")
        if action in {"media.search", "media.play"} and not query:
            return f"{action} requires a non-empty query."
        if query:
            return _validate_short_text(query, "media query", MAX_CLIPBOARD_LENGTH)
        return None

    if action == "app.search_history":
        app = _target_text(target, "app", "name")
        query = _target_text(target, "query")
        if not app:
            return "app.search_history requires an app name."
        app_error = _validate_short_text(app, "application name", MAX_NAME_LENGTH)
        if app_error:
            return app_error
        if not query:
            return "app.search_history requires a non-empty query."
        return _validate_short_text(query, "in-app search query", MAX_CLIPBOARD_LENGTH)

    if action == "clipboard.write":
        text = _target_text(target, "text", "content")
        if not text:
            return "clipboard.write requires non-empty text."
        if "\x00" in text:
            return "clipboard.write rejects text containing NUL bytes."
        if len(text) > MAX_CLIPBOARD_LENGTH:
            return f"clipboard.write text exceeds {MAX_CLIPBOARD_LENGTH} characters."
        return None

    return None


def _target_text(target: dict[str, object], *keys: str) -> str:
    for key in keys:
        value = target.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            return ""
        return value.strip()
    return ""


def _validate_short_text(value: str, label: str, max_length: int) -> str | None:
    if "\x00" in value:
        return f"{label} rejects NUL bytes."
    if len(value) > max_length:
        return f"{label} exceeds {max_length} characters."
    return None
