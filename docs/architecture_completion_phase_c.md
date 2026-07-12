# Architecture Completion - Phase C Review Persistence

Captured: 2026-07-11

This checkpoint implements the review persistence requirements of
[`architecture_completion_master_goal.md`](architecture_completion_master_goal.md)
without modifying that master contract.

## Authority and failure semantics

`ReviewStore` now uses SQLite as its only runtime authority for review reads
and mutations. JSONL is read only during one-time legacy import when an empty
SQLite event store is initialized. A SQLite error marks the store unavailable
and raises `ReviewPersistenceError`; it never appends or reads a JSONL fallback
to decide current state.

`CommandService` converts that controlled exception into a structured result:

```text
status: failed
overall_status: blocked
result.error_code: review_persistence_unavailable
```

The regression test verifies that this result is emitted before a reviewed
window-close action reaches its adapter.

## State machine and claim binding

The former broad `consume()` operation was removed. The public transitions are
now explicit:

```text
pending -> approved -> executing -> consumed     (complete_execution)
pending -> provided -> consumed                  (consume_input)
executing -> approved                            (release_execution)
pending|approved -> expired
approved|executing -> superseded
```

`claim_execution()` performs a SQLite `BEGIN IMMEDIATE` compare-and-swap. It
expires pending or approved reviews in the same transaction and, when supplied,
checks a stable binding over review kind, plan/step identity, plan and snapshot
content, and reviewed intent before moving to `executing`.

The database enables WAL mode, a 5-second busy timeout, structured event
columns, indexes for pending lookup and binding lookup, and an idempotent
`schema_migrations` table. Existing event-only SQLite files are migrated by
adding columns and rebuilding the current-state rows.

## Verification

```text
python -m pytest tests/test_reviews.py -q  -> 16 passed
python -m pytest tests/test_broker.py -q  -> 26 passed
```

The architecture test no longer marks the JSONL-fallback or broad-consume
checks as expected failures. Remaining expected failures belong only to the
later historical-runtime and CommandService-boundary phases.
