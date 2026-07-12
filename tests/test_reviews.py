import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

import pytest

from vibeos.models import Intent
from vibeos.permissions import PermissionPolicy
from vibeos.reviews import ReviewPersistenceError, ReviewStore, review_execution_binding


def test_review_store_round_trips_pending_review() -> None:
    path = make_review_path("store")
    store = ReviewStore(path)
    intent = Intent(action="clipboard.write", target={"text": "hello"})
    review = PermissionPolicy().review(intent)

    request = store.create("写入剪贴板 内容是 hello", intent, review)
    loaded = store.get(request.review_id)

    assert loaded
    assert loaded.review_id == request.review_id
    assert loaded.status == "pending"
    assert loaded.intent.action == "clipboard.write"
    assert loaded.expires_at


def test_review_state_uses_sqlite_as_the_authoritative_store() -> None:
    path = make_review_path("sqlite-authority")
    store = ReviewStore(path)
    request = store.create(
        "close Firefox",
        Intent(action="window.close", target={"name": "Firefox"}),
        PermissionPolicy().review(Intent(action="window.close", target={"name": "Firefox"})),
    )

    assert store.db_path.exists()
    assert not path.exists()
    reloaded = ReviewStore(path).get(request.review_id)
    assert reloaded
    assert reloaded.status == "pending"


def test_current_state_row_tracks_status_and_version_alongside_events() -> None:
    path = make_review_path("current-state")
    store = ReviewStore(path)
    request = store.create(
        "close Firefox",
        Intent(action="window.close", target={"name": "Firefox"}),
        PermissionPolicy().review(Intent(action="window.close", target={"name": "Firefox"})),
    )
    assert store.approve(request.review_id)

    with sqlite3.connect(store.db_path) as connection:
        row = connection.execute("SELECT status, version FROM reviews WHERE review_id = ?", (request.review_id,)).fetchone()
        event_count = connection.execute("SELECT COUNT(*) FROM review_events WHERE payload LIKE ?", (f'%"review_id": "{request.review_id}"%',)).fetchone()[0]

    assert row == ("approved", 1)
    assert event_count == 2


def test_review_schema_has_migrations_structured_events_and_lookup_indexes() -> None:
    store = ReviewStore(make_review_path("schema-contract"))

    with sqlite3.connect(store.db_path) as connection:
        migrations = connection.execute("SELECT version FROM schema_migrations").fetchall()
        event_columns = {row[1] for row in connection.execute("PRAGMA table_info(review_events)").fetchall()}
        indexes = {row[1] for row in connection.execute("PRAGMA index_list(reviews)").fetchall()}

    assert migrations == [(1,)]
    assert {"review_id", "event_type", "created_at", "payload"} <= event_columns
    assert {"idx_reviews_status_created_at", "idx_reviews_lookup_kind_step"} <= indexes


def test_event_only_sqlite_is_migrated_to_current_state_idempotently() -> None:
    path = make_review_path("event-migration")
    db_path = path.with_suffix(".sqlite3")
    intent = Intent(action="window.close", target={"name": "Firefox"})
    review = PermissionPolicy().review(intent)
    created = {
        "event": "created",
        "review_id": "rev_event_only",
        "utterance": "close Firefox",
        "intent": asdict(intent),
        "review": asdict(review),
        "created_at": "2099-01-01T00:00:00.000Z",
        "expires_at": "2099-01-01T00:10:00.000Z",
    }
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE review_events (event_id INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT NOT NULL)")
        connection.execute("INSERT INTO review_events (payload) VALUES (?)", (json.dumps(created),))
        connection.execute("INSERT INTO review_events (payload) VALUES (?)", (json.dumps({"event": "approved", "review_id": "rev_event_only"}),))

    assert ReviewStore(path).get("rev_event_only").status == "approved"
    assert ReviewStore(path).get("rev_event_only").status == "approved"


def test_review_store_marks_approved() -> None:
    path = make_review_path("approved")
    store = ReviewStore(path)
    intent = Intent(action="window.close", target={"name": "Firefox"})
    review = PermissionPolicy().review(intent)
    request = store.create("关闭 Firefox", intent, review)

    approved = store.approve(request.review_id)
    loaded = store.get(request.review_id)

    assert approved
    assert approved.status == "approved"
    assert loaded
    assert loaded.status == "approved"


def test_review_store_rejects_pending_review() -> None:
    path = make_review_path("rejected")
    store = ReviewStore(path)
    intent = Intent(action="window.close", target={"name": "Firefox"})
    review = PermissionPolicy().review(intent)
    request = store.create("关闭 Firefox", intent, review)

    rejected = store.reject(request.review_id)
    loaded = store.get(request.review_id)

    assert rejected
    assert rejected.status == "rejected"
    assert loaded
    assert loaded.status == "rejected"


def test_review_store_completes_only_claimed_execution_review() -> None:
    path = make_review_path("consumed")
    store = ReviewStore(path)
    intent = Intent(action="window.close", target={"name": "Firefox"})
    review = PermissionPolicy().review(intent)
    request = store.create("关闭 Firefox", intent, review)

    store.approve(request.review_id)
    assert store.complete_execution(request.review_id).status == "approved"
    assert store.claim_execution(request.review_id) is True
    consumed = store.complete_execution(request.review_id)
    loaded = store.get(request.review_id)

    assert consumed
    assert consumed.status == "consumed"
    assert loaded
    assert loaded.status == "consumed"


def test_review_store_lists_only_pending_reviews() -> None:
    path = make_review_path("pending")
    store = ReviewStore(path)
    first = store.create(
        "写入剪贴板 内容是 hello",
        Intent(action="clipboard.write", target={"text": "hello"}),
        PermissionPolicy().review(Intent(action="clipboard.write", target={"text": "hello"})),
    )
    second = store.create(
        "关闭 Firefox",
        Intent(action="window.close", target={"name": "Firefox"}),
        PermissionPolicy().review(Intent(action="window.close", target={"name": "Firefox"})),
    )

    store.approve(first.review_id)
    pending = store.list_pending()

    assert [request.review_id for request in pending] == [second.review_id]


def test_review_store_expires_pending_reviews() -> None:
    path = make_review_path("expired")
    store = ReviewStore(path, ttl_seconds=-1)
    intent = Intent(action="window.close", target={"name": "Firefox"})
    review = PermissionPolicy().review(intent)
    request = store.create("关闭 Firefox", intent, review)

    loaded = store.get(request.review_id)
    approved = store.approve(request.review_id)
    pending = store.list_pending()

    assert loaded
    assert loaded.status == "expired"
    assert approved
    assert approved.status == "expired"
    assert pending == []


def test_review_store_treats_legacy_reviews_without_expiration_as_expired() -> None:
    path = make_review_path("legacy-expired")
    intent = Intent(action="window.close", target={"name": "Firefox"})
    review = PermissionPolicy().review(intent)
    payload = {
        "event": "created",
        "review_id": "rev_legacy",
        "utterance": "关闭 Firefox",
        "intent": asdict(intent),
        "review": asdict(review),
        "created_at": "2026-05-31T00:00:00.000Z",
        "status": "pending",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    store = ReviewStore(path)

    loaded = store.get("rev_legacy")

    assert loaded
    assert loaded.status == "expired"


def test_review_execution_claim_is_atomic_across_store_instances() -> None:
    path = make_review_path("execution-claim")
    store = ReviewStore(path)
    request = store.create(
        "close Firefox",
        Intent(action="window.close", target={"name": "Firefox"}),
        PermissionPolicy().review(Intent(action="window.close", target={"name": "Firefox"})),
    )
    assert store.approve(request.review_id)

    def claim() -> bool:
        return ReviewStore(path).claim_execution(request.review_id)

    with ThreadPoolExecutor(max_workers=8) as executor:
        claims = list(executor.map(lambda _index: claim(), range(8)))

    assert claims.count(True) == 1
    loaded = store.get(request.review_id)
    assert loaded
    assert loaded.status == "executing"

    released = store.release_execution(request.review_id)
    assert released
    assert released.status == "approved"
    assert store.claim_execution(request.review_id) is True


def test_claim_rejects_expired_approved_review() -> None:
    store = ReviewStore(make_review_path("approved-expired"), ttl_seconds=-1)
    request = store.create(
        "close Firefox",
        Intent(action="window.close", target={"name": "Firefox"}),
        PermissionPolicy().review(Intent(action="window.close", target={"name": "Firefox"})),
    )

    assert store.approve(request.review_id).status == "expired"
    assert store.claim_execution(request.review_id) is False
    assert store.get(request.review_id).status == "expired"


def test_claim_validates_the_expected_approval_binding() -> None:
    store = ReviewStore(make_review_path("claim-binding"))
    request = store.create_loop_review(
        "search web",
        plan_payload={"plan_id": "plan_binding"},
        snapshot_payload={"current_step_id": "step_search"},
        pending_reason="approval required",
        step_id="step_search",
    )
    approved = store.approve(request.review_id)
    binding = review_execution_binding(approved)

    assert store.claim_execution(request.review_id, expected_binding={**binding, "step_id": "other"}) is False
    assert store.get(request.review_id).status == "approved"
    assert store.claim_execution(request.review_id, expected_binding=binding) is True


def test_user_input_consumption_is_not_an_execution_transition() -> None:
    store = ReviewStore(make_review_path("input-transition"))
    request = store.create_loop_review(
        "which site?",
        plan_payload={"plan_id": "plan_input"},
        snapshot_payload={"current_step_id": "step_input"},
        pending_reason="provide more detail",
        step_id="step_input",
        review_kind="user_input",
    )

    assert store.complete_execution(request.review_id).status == "pending"
    assert store.provide_input(request.review_id, "example.com").status == "provided"
    assert store.consume_input(request.review_id).status == "consumed"


def test_sqlite_failure_never_creates_a_jsonl_mutation_fallback() -> None:
    path = make_review_path("persistence-failure")
    store = ReviewStore(path)
    store._mark_unavailable()

    with pytest.raises(ReviewPersistenceError):
        store.create(
            "close Firefox",
            Intent(action="window.close", target={"name": "Firefox"}),
            PermissionPolicy().review(Intent(action="window.close", target={"name": "Firefox"})),
        )

    assert not path.exists()


def make_review_path(name: str) -> Path:
    return Path(".vibeos") / f"test-{name}-{uuid4().hex}.jsonl"
