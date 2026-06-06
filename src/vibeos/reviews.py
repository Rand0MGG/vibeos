from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .audit import default_audit_path
from .models import Intent, PermissionReview, ReviewRequest, utc_now_iso
from .task_models import StepReviewRecord, TaskPlanReviewResult

DEFAULT_REVIEW_TTL_SECONDS = 600


def default_review_path() -> Path:
    return default_audit_path().with_name("reviews.jsonl")


def default_review_ttl_seconds() -> int:
    raw = os.environ.get("VIBEOS_REVIEW_TTL_SECONDS")
    if not raw:
        return DEFAULT_REVIEW_TTL_SECONDS
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_REVIEW_TTL_SECONDS


class ReviewStore:
    """Append-only review store.

    Review approval is keyed by a review id so the user approves the exact
    intent that was reviewed, not a fresh model parse of the same utterance.
    """

    def __init__(self, path: Path | None = None, ttl_seconds: int | None = None) -> None:
        self.path = path or default_review_path()
        self.ttl_seconds = default_review_ttl_seconds() if ttl_seconds is None else ttl_seconds

    def create(self, utterance: str, intent: Intent, review: PermissionReview) -> ReviewRequest:
        now = datetime.now(UTC)
        created_at = isoformat_utc(now)
        expires_at = isoformat_utc(now + timedelta(seconds=self.ttl_seconds))
        review_id = make_review_id(utterance, intent, created_at)
        request = ReviewRequest(
            review_id=review_id,
            utterance=utterance,
            intent=intent,
            review=review,
            created_at=created_at,
            status="pending",
            expires_at=expires_at,
        )
        self._append({"event": "created", **review_to_payload(request)})
        return request

    def create_plan_review(self, utterance: str, plan_payload: dict[str, Any], plan_review: TaskPlanReviewResult) -> ReviewRequest:
        now = datetime.now(UTC)
        created_at = isoformat_utc(now)
        expires_at = isoformat_utc(now + timedelta(seconds=self.ttl_seconds))
        placeholder_intent = Intent.unknown("stored task plan approval", {"plan_id": plan_review.plan_id})
        review = PermissionReview(
            risk_level=plan_review.max_risk_level,
            review_required=True,
            allowed=True,
            reason="Stored task plan requires approval before execution.",
            effects=("May execute one or more reviewed task plan steps.",),
            reversible=False,
        )
        review_id = make_plan_review_id(plan_payload, created_at)
        request = ReviewRequest(
            review_id=review_id,
            utterance=utterance,
            intent=placeholder_intent,
            review=review,
            created_at=created_at,
            status="pending",
            expires_at=expires_at,
            review_kind="plan",
            plan_id=plan_review.plan_id,
            plan_payload=plan_payload,
            step_reviews=tuple(asdict(item) for item in plan_review.step_reviews),
            layer="permission_review",
        )
        self._append({"event": "created", **review_to_payload(request)})
        return request

    def approve(self, review_id: str) -> ReviewRequest | None:
        request = self.get(review_id)
        if not request or request.status != "pending":
            return request
        self._append({"event": "approved", "review_id": review_id, "timestamp": utc_now_iso()})
        return ReviewRequest(
            review_id=request.review_id,
            utterance=request.utterance,
            intent=request.intent,
            review=request.review,
            created_at=request.created_at,
            status="approved",
            expires_at=request.expires_at,
            review_kind=request.review_kind,
            plan_id=request.plan_id,
            plan_payload=request.plan_payload,
            step_reviews=request.step_reviews,
            layer=request.layer,
        )

    def reject(self, review_id: str) -> ReviewRequest | None:
        request = self.get(review_id)
        if not request or request.status != "pending":
            return request
        self._append({"event": "rejected", "review_id": review_id, "timestamp": utc_now_iso()})
        return ReviewRequest(
            review_id=request.review_id,
            utterance=request.utterance,
            intent=request.intent,
            review=request.review,
            created_at=request.created_at,
            status="rejected",
            expires_at=request.expires_at,
            review_kind=request.review_kind,
            plan_id=request.plan_id,
            plan_payload=request.plan_payload,
            step_reviews=request.step_reviews,
            layer=request.layer,
        )

    def consume(self, review_id: str) -> ReviewRequest | None:
        request = self.get(review_id)
        if not request or request.status != "approved":
            return request
        self._append({"event": "consumed", "review_id": review_id, "timestamp": utc_now_iso()})
        return ReviewRequest(
            review_id=request.review_id,
            utterance=request.utterance,
            intent=request.intent,
            review=request.review,
            created_at=request.created_at,
            status="consumed",
            expires_at=request.expires_at,
            review_kind=request.review_kind,
            plan_id=request.plan_id,
            plan_payload=request.plan_payload,
            step_reviews=request.step_reviews,
            layer=request.layer,
        )

    def get(self, review_id: str) -> ReviewRequest | None:
        latest: dict[str, Any] | None = None
        status = "pending"
        for entry in self._entries():
            if entry.get("review_id") != review_id:
                continue
            if entry.get("event") == "created":
                latest = entry
                status = "pending"
            elif entry.get("event") == "approved":
                status = "approved"
            elif entry.get("event") == "rejected":
                status = "rejected"
            elif entry.get("event") == "consumed":
                status = "consumed"
        if not latest:
            return None
        if status == "pending" and review_payload_expired(latest):
            status = "expired"
        return review_from_payload(latest, status=status)

    def list_pending(self) -> list[ReviewRequest]:
        created: dict[str, dict[str, Any]] = {}
        statuses: dict[str, str] = {}
        for entry in self._entries():
            review_id = entry.get("review_id")
            if not isinstance(review_id, str):
                continue
            event = entry.get("event")
            if event == "created":
                created[review_id] = entry
                statuses[review_id] = "pending"
            elif event == "approved":
                statuses[review_id] = "approved"
            elif event == "rejected":
                statuses[review_id] = "rejected"
            elif event == "consumed":
                statuses[review_id] = "consumed"

        pending = []
        for review_id, payload in created.items():
            if statuses.get(review_id) != "pending" or review_payload_expired(payload):
                continue
            pending.append(review_from_payload(payload, status="pending"))
        return sorted(pending, key=lambda request: request.created_at)

    def _entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        entries = []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        for line in lines:
            if not line.strip():
                continue
            entries.append(json.loads(line))
        return entries

    def _append(self, entry: dict[str, Any]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            fallback = Path.cwd() / ".vibeos" / "reviews.jsonl"
            fallback.parent.mkdir(parents=True, exist_ok=True)
            with fallback.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self.path = fallback


def make_review_id(utterance: str, intent: Intent, created_at: str) -> str:
    digest = hashlib.sha256(
        json.dumps(
            {"utterance": utterance, "intent": asdict(intent), "created_at": created_at},
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:12]
    return f"rev_{digest}"


def make_plan_review_id(plan_payload: dict[str, Any], created_at: str) -> str:
    digest = hashlib.sha256(
        json.dumps(
            {"plan": plan_payload, "created_at": created_at},
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:12]
    return f"rev_{digest}"


def review_to_payload(request: ReviewRequest) -> dict[str, Any]:
    return {
        "review_id": request.review_id,
        "utterance": request.utterance,
        "intent": asdict(request.intent),
        "review": asdict(request.review),
        "created_at": request.created_at,
        "expires_at": request.expires_at,
        "status": request.status,
        "review_kind": request.review_kind,
        "plan_id": request.plan_id,
        "plan_payload": request.plan_payload,
        "step_reviews": list(request.step_reviews),
        "layer": request.layer,
    }


def review_from_payload(payload: dict[str, Any], status: str) -> ReviewRequest:
    intent_data = payload["intent"]
    review_data = payload["review"]
    return ReviewRequest(
        review_id=payload["review_id"],
        utterance=payload["utterance"],
        intent=Intent(
            action=intent_data["action"],
            target=intent_data.get("target", {}),
            reason=intent_data.get("reason", ""),
            requires_confirmation=bool(intent_data.get("requires_confirmation", False)),
        ),
        review=PermissionReview(
            risk_level=review_data["risk_level"],
            review_required=bool(review_data["review_required"]),
            allowed=bool(review_data["allowed"]),
            reason=review_data["reason"],
            effects=tuple(review_data.get("effects", ())),
            reversible=bool(review_data.get("reversible", False)),
        ),
        created_at=payload["created_at"],
        status=status,
        expires_at=payload.get("expires_at"),
        review_kind=str(payload.get("review_kind", "intent")),
        plan_id=str(payload["plan_id"]) if payload.get("plan_id") is not None else None,
        plan_payload=payload.get("plan_payload") if isinstance(payload.get("plan_payload"), dict) else None,
        step_reviews=tuple(payload.get("step_reviews", ())),
        layer=str(payload["layer"]) if payload.get("layer") is not None else None,
    )


def isoformat_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_iso_utc(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def review_payload_expired(payload: dict[str, Any], now: datetime | None = None) -> bool:
    expires_at = payload.get("expires_at")
    if not isinstance(expires_at, str) or not expires_at:
        return True
    parsed = parse_iso_utc(expires_at)
    if parsed is None:
        return True
    return (now or datetime.now(UTC)) >= parsed
