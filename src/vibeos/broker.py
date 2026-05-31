from __future__ import annotations

from dataclasses import asdict

from .apps import AppRegistry
from .audit import AuditLog
from .capabilities import capability_payload, executable_actions, permission_summary
from .clipboard import ClipboardAdapter
from .intent import IntentBroker, OpenAICompatibleIntentBroker
from .notifications import NotificationAdapter
from .models import CommandRequest, CommandResult, Intent
from .permissions import PermissionPolicy
from .portal import PortalAdapter
from .reviews import ReviewStore, review_to_payload
from .windows import WindowRegistry


class CapabilityBroker:
    def __init__(
        self,
        intent_broker: IntentBroker | None = None,
        apps: AppRegistry | None = None,
        windows: WindowRegistry | None = None,
        portal: PortalAdapter | None = None,
        notifications: NotificationAdapter | None = None,
        clipboard: ClipboardAdapter | None = None,
        policy: PermissionPolicy | None = None,
        audit: AuditLog | None = None,
        reviews: ReviewStore | None = None,
    ) -> None:
        self.intent_broker = intent_broker or OpenAICompatibleIntentBroker()
        self.apps = apps or AppRegistry()
        self.windows = windows or WindowRegistry()
        self.portal = portal or PortalAdapter()
        self.notifications = notifications or NotificationAdapter()
        self.clipboard = clipboard or ClipboardAdapter()
        self.policy = policy or PermissionPolicy()
        self.audit = audit or AuditLog()
        self.reviews = reviews or ReviewStore()

    def capabilities(self) -> dict[str, object]:
        return {
            "capabilities": executable_actions(),
            "capability_details": capability_payload(),
            "permission_policy": permission_summary(),
        }

    def pending_reviews(self) -> list[dict[str, object]]:
        return [review_to_payload(request) for request in self.reviews.list_pending()]

    def handle(self, request: CommandRequest) -> CommandResult:
        if request.review_id:
            return self.approve_review(request.review_id, dry_run=request.dry_run)

        intent = self.intent_broker.parse(request.utterance)
        review = self.policy.review(intent)
        if not review.allowed:
            result = CommandResult(status="rejected", intent=intent, message=review.reason, review=review)
        elif review.review_required and not request.dry_run:
            if request.approve:
                result = CommandResult(
                    status="rejected",
                    intent=intent,
                    message="L2 approval must use a stored review id; run without approval first, then `vibe approve <review_id>`",
                    review=review,
                )
            else:
                review_request = self.reviews.create(request.utterance, intent, review)
                result = CommandResult(
                    status="review_required",
                    intent=intent,
                    result={"review_id": review_request.review_id, "review": asdict(review)},
                    review_id=review_request.review_id,
                    message=f"explicit approval is required; run `vibe approve {review_request.review_id}` after reviewing the request",
                    review=review,
                )
        else:
            result = self._execute(request, intent, review)
        audit_id = self.audit.record(
            request=request,
            intent=intent,
            status=result.status,
            result=result.result,
            selected_target=result.selected_target,
            message=result.message,
            review=result.review,
            review_id=result.review_id,
        )
        return CommandResult(
            status=result.status,
            intent=result.intent,
            result=result.result,
            selected_target=result.selected_target,
            audit_id=audit_id,
            review_id=result.review_id,
            message=result.message,
            review=result.review,
        )

    def approve_review(self, review_id: str, dry_run: bool = False) -> CommandResult:
        if dry_run:
            review_request = self.reviews.get(review_id)
            if not review_request:
                fallback = Intent.unknown("review request not found", {"review_id": review_id})
                return CommandResult(status="rejected", intent=fallback, review_id=review_id, message="review request not found")
            if review_request.status != "pending":
                return CommandResult(
                    status="rejected",
                    intent=review_request.intent,
                    review=review_request.review,
                    review_id=review_id,
                    message=f"review request is not pending; current status is {review_request.status}",
                )
            request = CommandRequest(
                utterance=review_request.utterance,
                dry_run=True,
                approve=True,
                review_id=review_id,
            )
            result = self._execute(request, review_request.intent, review_request.review)
            audit_id = self.audit.record(
                request=request,
                intent=review_request.intent,
                status=result.status,
                result=result.result,
                selected_target=result.selected_target,
                message=result.message,
                review=result.review,
                review_id=review_id,
            )
            return CommandResult(
                status=result.status,
                intent=result.intent,
                result=result.result,
                selected_target=result.selected_target,
                audit_id=audit_id,
                review_id=review_id,
                message=result.message,
                review=result.review,
            )

        review_request = self.reviews.approve(review_id)
        if not review_request:
            fallback = Intent.unknown("review request not found", {"review_id": review_id})
            return CommandResult(status="rejected", intent=fallback, review_id=review_id, message="review request not found")
        if review_request.status != "approved":
            return CommandResult(
                status="rejected",
                intent=review_request.intent,
                review=review_request.review,
                review_id=review_id,
                message=f"review request is not pending; current status is {review_request.status}",
            )

        request = CommandRequest(
            utterance=review_request.utterance,
            dry_run=dry_run,
            approve=True,
            review_id=review_id,
        )
        result = self._execute(request, review_request.intent, review_request.review)
        self.reviews.consume(review_id)
        audit_id = self.audit.record(
            request=request,
            intent=review_request.intent,
            status=result.status,
            result=result.result,
            selected_target=result.selected_target,
            message=result.message,
            review=result.review,
            review_id=review_id,
        )
        return CommandResult(
            status=result.status,
            intent=result.intent,
            result=result.result,
            selected_target=result.selected_target,
            audit_id=audit_id,
            review_id=review_id,
            message=result.message,
            review=result.review,
        )

    def reject_review(self, review_id: str) -> CommandResult:
        review_request = self.reviews.reject(review_id)
        if not review_request:
            fallback = Intent.unknown("review request not found", {"review_id": review_id})
            return CommandResult(status="rejected", intent=fallback, review_id=review_id, message="review request not found")
        if review_request.status != "rejected":
            return CommandResult(
                status="rejected",
                intent=review_request.intent,
                review=review_request.review,
                review_id=review_id,
                message=f"review request is not pending; current status is {review_request.status}",
            )
        request = CommandRequest(
            utterance=review_request.utterance,
            approve=False,
            review_id=review_id,
        )
        audit_id = self.audit.record(
            request=request,
            intent=review_request.intent,
            status="rejected",
            result={"review_id": review_id, "review_status": "rejected"},
            selected_target=None,
            message="review request rejected by user",
            review=review_request.review,
            review_id=review_id,
        )
        return CommandResult(
            status="rejected",
            intent=review_request.intent,
            result={"review_id": review_id, "review_status": "rejected"},
            audit_id=audit_id,
            review_id=review_id,
            message="review request rejected by user",
            review=review_request.review,
        )

    def _execute(self, request: CommandRequest, intent: Intent, review) -> CommandResult:
        if intent.action == "unknown":
            return CommandResult(status="rejected", intent=intent, message=intent.reason or "unsupported request", review=review)
        if intent.requires_confirmation:
            return CommandResult(status="rejected", intent=intent, message="model requested unsupported confirmation flow", review=review)

        if intent.action == "app.list":
            apps = [asdict(app) for app in self.apps.list_apps()]
            return CommandResult(status="dry_run" if request.dry_run else "executed", intent=intent, result=apps, review=review)

        if intent.action == "app.open":
            name = str(intent.target.get("name") or intent.target.get("app") or "").strip()
            if not name:
                return CommandResult(status="rejected", intent=intent, message="missing app name", review=review)
            candidates = self.apps.resolve(name)
            if not candidates:
                if request.dry_run:
                    return CommandResult(
                        status="dry_run",
                        intent=intent,
                        message=f"intent accepted; no local application registry match for {name!r}",
                        review=review,
                    )
                return CommandResult(status="failed", intent=intent, message=f"no application matched {name!r}", review=review)
            if len(candidates) > 1 and not decisive(candidates[0].name, name):
                return CommandResult(
                    status="ambiguous",
                    intent=intent,
                    result=[asdict(app) for app in candidates[:5]],
                    message=f"multiple applications matched {name!r}",
                    review=review,
                )
            app = candidates[0]
            if request.dry_run:
                return CommandResult(status="dry_run", intent=intent, selected_target=app.desktop_id, review=review)
            opened = self.apps.open_app(app)
            status = "executed" if opened.get("status") == "opened" else "failed"
            return CommandResult(status=status, intent=intent, selected_target=app.desktop_id, result=opened, review=review)

        if intent.action == "window.list":
            windows = [asdict(window) for window in self.windows.list_windows()]
            return CommandResult(status="dry_run" if request.dry_run else "executed", intent=intent, result=windows, review=review)

        if intent.action in {"window.focus", "window.minimize", "window.maximize", "window.close"}:
            name = str(intent.target.get("name") or intent.target.get("window") or "").strip()
            if not name:
                name = "current"
            candidates = self.windows.resolve(name)
            if not candidates:
                if request.dry_run:
                    return CommandResult(
                        status="dry_run",
                        intent=intent,
                        message=f"intent accepted; no local window match for {name!r}",
                        review=review,
                    )
                return CommandResult(status="failed", intent=intent, message=f"no window matched {name!r}", review=review)
            if len(candidates) > 1:
                return CommandResult(
                    status="ambiguous",
                    intent=intent,
                    result=[asdict(window) for window in candidates[:5]],
                    message=f"multiple windows matched {name!r}",
                    review=review,
                )
            window = candidates[0]
            if request.dry_run:
                return CommandResult(status="dry_run", intent=intent, selected_target=window.window_id, review=review)
            action = {
                "window.focus": self.windows.focus,
                "window.minimize": self.windows.minimize,
                "window.maximize": self.windows.maximize,
                "window.close": self.windows.close,
            }[intent.action]
            action_result = action(window)
            status = "executed" if action_result.get("status") in {"focused", "minimized", "maximized", "closed"} else "failed"
            return CommandResult(status=status, intent=intent, selected_target=window.window_id, result=action_result, review=review)

        if intent.action == "notification.send":
            title = str(intent.target.get("title") or "VibeOS").strip() or "VibeOS"
            body = str(intent.target.get("body") or intent.target.get("message") or "").strip()
            if request.dry_run:
                return CommandResult(status="dry_run", intent=intent, selected_target=title, review=review)
            sent = self.notifications.send(title, body)
            status = "executed" if sent.get("status") == "sent" else "failed"
            return CommandResult(status=status, intent=intent, selected_target=title, result=sent, review=review)

        if intent.action == "portal.open_uri":
            uri = str(intent.target.get("uri") or intent.target.get("url") or "").strip()
            if not uri:
                return CommandResult(status="rejected", intent=intent, message="missing URI", review=review)
            if request.dry_run:
                return CommandResult(status="dry_run", intent=intent, selected_target=uri, review=review)
            opened = self.portal.open_uri(uri)
            status = "executed" if opened.get("status") == "opened" else "failed"
            return CommandResult(status=status, intent=intent, selected_target=uri, result=opened, review=review)

        if intent.action == "clipboard.write":
            text = str(intent.target.get("text") or "").strip()
            if not text:
                return CommandResult(status="rejected", intent=intent, message="missing clipboard text", review=review)
            if request.dry_run:
                return CommandResult(status="dry_run", intent=intent, selected_target="clipboard", review=review)
            written = self.clipboard.write(text)
            status = "executed" if written.get("status") == "written" else "failed"
            return CommandResult(status=status, intent=intent, selected_target="clipboard", result=written, review=review)

        if intent.action == "system.status":
            return CommandResult(
                status="dry_run" if request.dry_run else "executed",
                intent=intent,
                result={
                    "portal": self.portal.status(),
                    **self.capabilities(),
                },
                review=review,
            )

        return CommandResult(status="rejected", intent=intent, message=f"unsupported action {intent.action}", review=review)


def decisive(candidate_name: str, query: str) -> bool:
    candidate = candidate_name.strip().lower()
    query_norm = query.strip().lower()
    return candidate == query_norm or query_norm in {"browser", "浏览器", "terminal", "终端"}
