from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Concatenate, Never, ParamSpec, TypeVar

from .audit import default_audit_path
from .core.adapters.database import CoreDatabase, DatabaseMigrationError
from .models import Intent, PermissionReview, ReviewRequest, utc_now_iso
from .review_identifiers import canonical_payload_hash, make_plan_review_id, make_review_id
from .task_trace import record_trace_event
from .task_models import TaskPlanReviewResult

DEFAULT_REVIEW_TTL_SECONDS = 600
_REVIEW_LOCKS_GUARD = threading.Lock()
_REVIEW_LOCKS: dict[str, threading.RLock] = {}
P = ParamSpec("P")
R = TypeVar("R")


class ReviewPersistenceError(RuntimeError):
    """SQLite could not authoritatively read or mutate review state."""


def review_execution_binding(request: ReviewRequest) -> dict[str, str | None]:
    """Immutable fields an approval claim must still match at dispatch time."""

    return {
        "review_kind": request.review_kind,
        "plan_id": request.plan_id,
        "step_id": _step_id_from_payload(review_to_payload(request)),
        "plan_hash": canonical_payload_hash(request.plan_payload),
        "snapshot_hash": canonical_payload_hash(request.snapshot_payload),
        "intent_hash": canonical_payload_hash(asdict(request.intent)),
    }


def _synchronized(method: Callable[Concatenate["ReviewStore", P], R]) -> Callable[Concatenate["ReviewStore", P], R]:
    def wrapper(self: "ReviewStore", /, *args: P.args, **kwargs: P.kwargs) -> R:
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


def default_review_path() -> Path:
    return Path(default_audit_path()).with_name("reviews.jsonl")


def _review_lock_for(path: Path) -> threading.RLock:
    key = str(path.expanduser().resolve())
    with _REVIEW_LOCKS_GUARD:
        lock = _REVIEW_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _REVIEW_LOCKS[key] = lock
        return lock


def default_review_ttl_seconds() -> int:
    raw = os.environ.get("VIBEOS_REVIEW_TTL_SECONDS")
    if not raw:
        return DEFAULT_REVIEW_TTL_SECONDS
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_REVIEW_TTL_SECONDS


class ReviewStore:
    """Durable review store with an authoritative current-state row and audit events.

    Review approval is keyed by a review id so the user approves the exact
    intent that was reviewed, not a fresh model parse of the same utterance.
    """

    def __init__(
        self,
        path: Path | None = None,
        ttl_seconds: int | None = None,
        *,
        database: CoreDatabase | None = None,
    ) -> None:
        self.path = path or default_review_path()
        self.db_path = self.path.with_suffix(".sqlite3")
        if database is not None and database.path != self.db_path.expanduser().resolve():
            raise ValueError("ReviewStore path and CoreDatabase path must identify the same authoritative database")
        self.database = database or CoreDatabase(self.db_path)
        self.ttl_seconds = default_review_ttl_seconds() if ttl_seconds is None else ttl_seconds
        self._lock = _review_lock_for(self.path)
        self._connection = self._open_state_connection()
        self._import_legacy_jsonl_if_needed()

    @_synchronized
    def create(self, utterance: str, intent: Intent, review: PermissionReview) -> ReviewRequest:
        with self._lock:
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
            record_trace_event(
                phase="review",
                event_type="review_created",
                status="pending",
                actor="review_store",
                review_id=review_id,
                data=review_to_payload(request),
            )
            return request

    @_synchronized
    def create_plan_review(self, utterance: str, plan_payload: dict[str, object], plan_review: TaskPlanReviewResult) -> ReviewRequest:
        with self._lock:
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
                snapshot_payload=plan_payload.get("loop_snapshot") if isinstance(plan_payload.get("loop_snapshot"), dict) else None,
            )
            self._append({"event": "created", **review_to_payload(request)})
            record_trace_event(
                phase="review",
                event_type="review_created",
                status="pending",
                actor="review_store",
                plan_id=plan_review.plan_id,
                review_id=review_id,
                data=review_to_payload(request),
            )
            return request

    @_synchronized
    def create_loop_review(
        self,
        utterance: str,
        *,
        plan_payload: dict[str, object],
        snapshot_payload: dict[str, object],
        pending_reason: str,
        step_id: str | None,
        review_kind: str = "loop",
    ) -> ReviewRequest:
        now = datetime.now(UTC)
        created_at = isoformat_utc(now)
        expires_at = isoformat_utc(now + timedelta(seconds=self.ttl_seconds))
        placeholder_intent = Intent.unknown("stored goal loop approval", {"plan_id": plan_payload.get("plan_id"), "step_id": step_id})
        review = PermissionReview(
            risk_level="L2",
            review_required=review_kind == "loop",
            allowed=True,
            reason=pending_reason,
            effects=("May resume a stored goal loop from the pending step.",),
            reversible=False,
        )
        review_id = make_plan_review_id(
            {
                "plan_payload": plan_payload,
                "snapshot_payload": snapshot_payload,
                "review_kind": review_kind,
                "step_id": step_id,
            },
            created_at,
        )
        enriched_snapshot_payload = dict(snapshot_payload)
        if review_kind == "loop":
            enriched_snapshot_payload["pending_review_id"] = review_id
        else:
            enriched_snapshot_payload["pending_user_input_id"] = review_id
        request = ReviewRequest(
            review_id=review_id,
            utterance=utterance,
            intent=placeholder_intent,
            review=review,
            created_at=created_at,
            status="pending",
            expires_at=expires_at,
            review_kind="loop" if review_kind == "loop" else "user_input",
            plan_id=str(plan_payload.get("plan_id")) if plan_payload.get("plan_id") is not None else None,
            plan_payload=plan_payload,
            step_reviews=(),
            layer="goal_loop",
            snapshot_payload=enriched_snapshot_payload,
            pending_reason=pending_reason,
        )
        self._append({"event": "created", **review_to_payload(request)})
        record_trace_event(
            phase="review",
            event_type="review_created",
            status="pending",
            actor="review_store",
            plan_id=request.plan_id,
            review_id=review_id,
            data=review_to_payload(request),
        )
        return request

    @_synchronized
    def approve(self, review_id: str) -> ReviewRequest | None:
        request = self._transition(review_id, from_statuses=("pending",), event="approved")
        if not request or request.status != "approved":
            return request
        record_trace_event(
            phase="review",
            event_type="review_approved",
            status="approved",
            actor="review_store",
            plan_id=request.plan_id,
            review_id=review_id,
            data={"utterance": request.utterance, "review_kind": request.review_kind},
        )
        return request

    @_synchronized
    def reject(self, review_id: str) -> ReviewRequest | None:
        request = self._transition(review_id, from_statuses=("pending",), event="rejected")
        if not request or request.status != "rejected":
            return request
        record_trace_event(
            phase="review",
            event_type="review_rejected",
            status="rejected",
            actor="review_store",
            plan_id=request.plan_id,
            review_id=review_id,
            data={"utterance": request.utterance, "review_kind": request.review_kind},
        )
        return request

    @_synchronized
    def complete_execution(self, review_id: str) -> ReviewRequest | None:
        """Consume exactly one claimed execution review."""

        request = self._transition(review_id, from_statuses=("executing",), event="consumed")
        if not request or request.status != "consumed":
            return request
        record_trace_event(
            phase="review",
            event_type="review_execution_completed",
            status="consumed",
            actor="review_store",
            plan_id=request.plan_id,
            review_id=review_id,
            data={"utterance": request.utterance, "review_kind": request.review_kind},
        )
        return request

    @_synchronized
    def claim_execution(self, review_id: str, *, expected_binding: dict[str, str | None] | None = None) -> bool:
        """Atomically reserve an approved review for one real execution.

        A second HTTP worker may observe an approved review while the first
        worker is already dispatching its desktop side effect. The claim event
        prevents both workers from treating that approval as executable.
        """

        return self._claim_execution(review_id, expected_binding=expected_binding)

    def _claim_execution(self, review_id: str, *, expected_binding: dict[str, str | None] | None) -> bool:
        connection = self._require_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            now = utc_now_iso()
            self._expire_review_in_transaction(connection, review_id=review_id, now=now)
            row = connection.execute(
                "SELECT status, payload, supplemental_input FROM reviews WHERE review_id = ?",
                (review_id,),
            ).fetchone()
            if row is None or str(row[0]) != "approved":
                connection.commit()
                return False
            request = self._request_from_row(row)
            if expected_binding is not None and review_execution_binding(request) != expected_binding:
                connection.commit()
                return False
            cursor = connection.execute(
                "UPDATE reviews SET status = 'executing', version = version + 1 WHERE review_id = ? AND status = 'approved' AND expires_at > ?",
                (review_id, now),
            )
            claimed = cursor.rowcount == 1
            if claimed:
                self._insert_event(connection, {"event": "executing", "review_id": review_id, "timestamp": now})
            connection.commit()
        except sqlite3.Error as exc:
            self._rollback_and_raise(exc)
        if not claimed:
            return False
        record_trace_event(
            phase="review",
            event_type="review_execution_claimed",
            status="executing",
            actor="review_store",
            plan_id=request.plan_id,
            review_id=review_id,
            data={"review_kind": request.review_kind},
        )
        return True

    @_synchronized
    def release_execution(self, review_id: str) -> ReviewRequest | None:
        """Make a failed execution retryable without replaying a successful one."""

        request = self._transition(review_id, from_statuses=("executing",), event="approved")
        if request is None or request.status != "approved":
            return request
        record_trace_event(
            phase="review",
            event_type="review_execution_released",
            status="approved",
            actor="review_store",
            plan_id=request.plan_id,
            review_id=review_id,
            data={"review_kind": request.review_kind},
        )
        return request

    @_synchronized
    def provide_input(self, review_id: str, supplemental_input: str) -> ReviewRequest | None:
        existing = self.get(review_id)
        if existing is None or existing.review_kind != "user_input":
            return existing
        request = self._transition(
            review_id,
            from_statuses=("pending",),
            event="provided",
            supplemental_input=supplemental_input,
        )
        if not request or request.status != "provided":
            return request
        record_trace_event(
            phase="review",
            event_type="review_input_provided",
            status="provided",
            actor="review_store",
            plan_id=request.plan_id,
            review_id=review_id,
            data={"supplemental_input": supplemental_input, "review_kind": request.review_kind},
        )
        return request

    @_synchronized
    def consume_input(self, review_id: str) -> ReviewRequest | None:
        """Consume exactly one user-input submission after a successful resume."""

        existing = self.get(review_id)
        if existing is None or existing.review_kind != "user_input":
            return existing
        request = self._transition(review_id, from_statuses=("provided",), event="consumed")
        if not request or request.status != "consumed":
            return request
        record_trace_event(
            phase="review",
            event_type="review_input_consumed",
            status="consumed",
            actor="review_store",
            plan_id=request.plan_id,
            review_id=review_id,
            data={"review_kind": request.review_kind},
        )
        return request

    @_synchronized
    def supersede(self, review_id: str, reason: str) -> ReviewRequest | None:
        return self._transition(review_id, from_statuses=("approved", "executing"), event="superseded", pending_reason=reason)

    @_synchronized
    def expire(self, review_id: str) -> ReviewRequest | None:
        return self._transition(review_id, from_statuses=("pending", "approved"), event="expired")

    @_synchronized
    def get(self, review_id: str) -> ReviewRequest | None:
        connection = self._require_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._expire_review_in_transaction(connection, review_id=review_id, now=utc_now_iso())
            row = connection.execute(
                "SELECT status, payload, supplemental_input FROM reviews WHERE review_id = ?",
                (review_id,),
            ).fetchone()
            connection.commit()
            return self._request_from_row(row) if row is not None else None
        except (sqlite3.Error, json.JSONDecodeError, KeyError) as exc:
            self._rollback_and_raise(exc)

    @_synchronized
    def list_pending(self) -> list[ReviewRequest]:
        connection = self._require_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._expire_reviews_in_transaction(connection, now=utc_now_iso())
            rows = connection.execute("SELECT status, payload, supplemental_input FROM reviews WHERE status = 'pending' ORDER BY created_at").fetchall()
            connection.commit()
            return [self._request_from_row(row) for row in rows]
        except (sqlite3.Error, json.JSONDecodeError, KeyError) as exc:
            self._rollback_and_raise(exc)

    def _entries(self) -> list[dict[str, Any]]:
        connection = self._require_connection()
        try:
            rows = connection.execute("SELECT payload FROM review_events ORDER BY event_id").fetchall()
        except sqlite3.Error as exc:
            self._mark_unavailable()
            raise ReviewPersistenceError("review persistence is unavailable") from exc
        entries: list[dict[str, Any]] = []
        for row in rows:
            try:
                entry = json.loads(str(row[0]))
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                entries.append(entry)
        return entries

    def _legacy_entries(self) -> list[dict[str, Any]]:
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
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                # Audit-style storage must not turn one interrupted append into
                # a denial of service for unrelated pending approvals.
                continue
            if isinstance(entry, dict):
                entries.append(entry)
        return entries

    def _transition(
        self,
        review_id: str,
        *,
        from_statuses: tuple[str, ...],
        event: str,
        supplemental_input: str | None = None,
        pending_reason: str | None = None,
    ) -> ReviewRequest | None:
        connection = self._require_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            now = utc_now_iso()
            self._expire_review_in_transaction(connection, review_id=review_id, now=now)
            placeholders = ", ".join("?" for _ in from_statuses)
            target_status = {
                "approved": "approved",
                "rejected": "rejected",
                "executing": "executing",
                "consumed": "consumed",
                "provided": "provided",
                "superseded": "superseded",
                "expired": "expired",
            }[event]
            cursor = connection.execute(
                f"UPDATE reviews SET status = ?, supplemental_input = COALESCE(?, supplemental_input), pending_reason = COALESCE(?, pending_reason), version = version + 1 WHERE review_id = ? AND status IN ({placeholders})",
                (target_status, supplemental_input, pending_reason, review_id, *from_statuses),
            )
            if cursor.rowcount == 1:
                entry: dict[str, Any] = {"event": event, "review_id": review_id, "timestamp": now}
                if supplemental_input is not None:
                    entry["supplemental_input"] = supplemental_input
                if pending_reason is not None:
                    entry["pending_reason"] = pending_reason
                self._insert_event(connection, entry)
            if target_status == "approved":
                self._expire_review_in_transaction(connection, review_id=review_id, now=now)
            row = connection.execute(
                "SELECT status, payload, supplemental_input FROM reviews WHERE review_id = ?",
                (review_id,),
            ).fetchone()
            connection.commit()
            return self._request_from_row(row) if row is not None else None
        except (sqlite3.Error, json.JSONDecodeError, KeyError) as exc:
            self._rollback_and_raise(exc)

    def _append(self, entry: dict[str, Any]) -> None:
        """Persist creation/import events in the authoritative SQLite store."""

        connection = self._require_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._insert_event(connection, entry)
            self._apply_event_to_current_state(entry)
            connection.commit()
        except sqlite3.Error as exc:
            self._rollback_and_raise(exc)

    def _insert_event(self, connection: sqlite3.Connection, entry: dict[str, Any]) -> None:
        connection.execute(
            "INSERT INTO review_events (review_id, event_type, created_at, payload) VALUES (?, ?, ?, ?)",
            (
                str(entry.get("review_id") or "") or None,
                str(entry.get("event") or "unknown"),
                str(entry.get("timestamp") or entry.get("created_at") or utc_now_iso()),
                json.dumps(entry, ensure_ascii=False),
            ),
        )

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise ReviewPersistenceError("review persistence is unavailable")
        return self._connection

    def _mark_unavailable(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass

    def _rollback_and_raise(self, exc: BaseException) -> Never:
        connection = self._connection
        if connection is not None:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
        self._mark_unavailable()
        raise ReviewPersistenceError("review persistence is unavailable") from exc

    def _request_from_row(self, row: tuple[object, object, object]) -> ReviewRequest:
        payload = json.loads(str(row[1]))
        if not isinstance(payload, dict):
            raise ReviewPersistenceError("review persistence contains an invalid payload")
        if row[2] is not None:
            payload["supplemental_input"] = str(row[2])
        return review_from_payload(payload, status=str(row[0]))

    def _expire_review_in_transaction(self, connection: sqlite3.Connection, *, review_id: str, now: str) -> None:
        row = connection.execute(
            "SELECT status FROM reviews WHERE review_id = ? AND status IN ('pending', 'approved') AND (expires_at IS NULL OR expires_at <= ?)",
            (review_id, now),
        ).fetchone()
        if row is None:
            return
        cursor = connection.execute(
            "UPDATE reviews SET status = 'expired', version = version + 1 WHERE review_id = ? AND status IN ('pending', 'approved') AND (expires_at IS NULL OR expires_at <= ?)",
            (review_id, now),
        )
        if cursor.rowcount == 1:
            self._insert_event(connection, {"event": "expired", "review_id": review_id, "timestamp": now})

    def _expire_reviews_in_transaction(self, connection: sqlite3.Connection, *, now: str) -> None:
        rows = connection.execute(
            "SELECT review_id FROM reviews WHERE status IN ('pending', 'approved') AND (expires_at IS NULL OR expires_at <= ?)",
            (now,),
        ).fetchall()
        for (review_id,) in rows:
            self._expire_review_in_transaction(connection, review_id=str(review_id), now=now)

    def _apply_event_to_current_state(self, entry: dict[str, Any]) -> None:
        """Apply one historic event while an SQLite transaction is active."""
        if self._connection is None:
            return
        event = str(entry.get("event") or "")
        review_id = entry.get("review_id")
        if not isinstance(review_id, str):
            return
        if event == "created":
            payload = json.dumps(entry, ensure_ascii=False)
            self._connection.execute(
                """INSERT OR IGNORE INTO reviews (
                    review_id, status, review_kind, plan_id, step_id, utterance,
                    intent_payload, review_payload, plan_payload, snapshot_payload,
                    supplemental_input, pending_reason, created_at, expires_at, version, payload
                ) VALUES (?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)""",
                (
                    review_id,
                    str(entry.get("review_kind") or "intent"),
                    str(entry["plan_id"]) if entry.get("plan_id") is not None else None,
                    _step_id_from_payload(entry),
                    str(entry.get("utterance") or ""),
                    json.dumps(entry.get("intent") or {}, ensure_ascii=False),
                    json.dumps(entry.get("review") or {}, ensure_ascii=False),
                    json.dumps(entry.get("plan_payload"), ensure_ascii=False) if entry.get("plan_payload") is not None else None,
                    json.dumps(entry.get("snapshot_payload"), ensure_ascii=False) if entry.get("snapshot_payload") is not None else None,
                    str(entry["supplemental_input"]) if entry.get("supplemental_input") is not None else None,
                    str(entry["pending_reason"]) if entry.get("pending_reason") is not None else None,
                    str(entry.get("created_at") or utc_now_iso()),
                    str(entry["expires_at"]) if entry.get("expires_at") is not None else None,
                    payload,
                ),
            )
            return
        status = {
            "approved": "approved",
            "executing": "executing",
            "rejected": "rejected",
            "consumed": "consumed",
            "provided": "provided",
            "expired": "expired",
            "superseded": "superseded",
        }.get(event)
        if status is None:
            return
        supplemental_input = entry.get("supplemental_input") if event == "provided" else None
        pending_reason = entry.get("pending_reason") if event == "superseded" else None
        self._connection.execute(
            "UPDATE reviews SET status = ?, supplemental_input = COALESCE(?, supplemental_input), pending_reason = COALESCE(?, pending_reason), version = version + 1 WHERE review_id = ?",
            (status, supplemental_input, pending_reason, review_id),
        )

    def _open_state_connection(self) -> sqlite3.Connection | None:
        try:
            with self._lock:
                self.database.upgrade()
                return self.database.compatibility_connection()
        except (sqlite3.Error, DatabaseMigrationError):
            return None

    def reconnect_after_database_ready(self) -> None:
        """Bind the compatibility facade after daemon-owned migration succeeds."""

        with self._lock:
            previous = self._connection
            self._connection = None
            if previous is not None:
                previous.close()
            try:
                connection = self.database.compatibility_connection()
                self._connection = connection
                self._import_legacy_jsonl_if_needed()
                if self._connection is None:
                    connection.close()
                    raise ReviewPersistenceError("review schema is unavailable after database migration")
            except (sqlite3.Error, ReviewPersistenceError) as exc:
                self._connection = None
                raise ReviewPersistenceError("review persistence could not bind to the authoritative database") from exc

    def _import_legacy_jsonl_if_needed(self) -> None:
        if self._connection is None:
            return
        try:
            has_events = self._connection.execute("SELECT 1 FROM review_events LIMIT 1").fetchone() is not None
        except sqlite3.Error:
            self._connection = None
            return
        if not has_events:
            legacy_entries = self._legacy_entries()
            if legacy_entries:
                try:
                    self._connection.executemany(
                        "INSERT INTO review_events (payload) VALUES (?)",
                        [(json.dumps(entry, ensure_ascii=False),) for entry in legacy_entries],
                    )
                    self._connection.commit()
                except sqlite3.Error:
                    self._connection = None
                    return
        try:
            has_current_state = self._connection.execute("SELECT 1 FROM reviews LIMIT 1").fetchone() is not None
            if not has_current_state:
                self._rebuild_current_state_from_events()
        except sqlite3.Error:
            self._connection = None

    def _rebuild_current_state_from_events(self) -> None:
        if self._connection is None:
            return
        entries = self._entries()
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._connection.execute("DELETE FROM reviews")
            for entry in entries:
                self._apply_event_to_current_state(entry)
            self._connection.commit()
        except sqlite3.Error:
            self._connection.rollback()
            raise


def _step_id_from_payload(payload: dict[str, Any]) -> str | None:
    snapshot = payload.get("snapshot_payload")
    if isinstance(snapshot, dict) and snapshot.get("current_step_id") is not None:
        return str(snapshot["current_step_id"])
    target = payload.get("intent")
    if isinstance(target, dict) and target.get("target") and isinstance(target["target"], dict):
        step_id = target["target"].get("step_id")
        return str(step_id) if step_id is not None else None
    return None


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
        "snapshot_payload": request.snapshot_payload,
        "pending_reason": request.pending_reason,
        "supplemental_input": request.supplemental_input,
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
        snapshot_payload=payload.get("snapshot_payload") if isinstance(payload.get("snapshot_payload"), dict) else None,
        pending_reason=str(payload["pending_reason"]) if payload.get("pending_reason") is not None else None,
        supplemental_input=str(payload["supplemental_input"]) if payload.get("supplemental_input") is not None else None,
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
