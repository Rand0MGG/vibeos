# Durable Task Engine

The authoritative flow is:

```text
public request or scheduler event
  -> pure transition(current TaskRun, TaskEvent)
  -> SQLite transaction: state + domain event + outbox + artifacts
  -> leased worker performs registered-tool I/O
  -> ActionReceipt + EvidenceBundle feed acceptance or recovery
```

## State and schema

The repository persists versioned goal contracts, task runs, plan revisions,
steps, attempts, waits, proposals, receipts, evidence, terminal outcomes,
leases, outbox deliveries, current state, and domain events. Every mutation uses
revision CAS; worker mutations also validate owner, expiry, and fencing token.
Heartbeat renewal keeps the same token, while stale or expired workers fail.

Timers and external-event waits are indexed rows, not process sleeps. Daemon
startup scans recoverable and due tasks. Workers isolate per-task failures.
Outbox delivery is at-least-once and consumer-deduplicated by
`(message_id, consumer)`; it does not claim globally exactly-once effects.

## Side-effect recovery

An ActionProposal and stable idempotency key commit before adapter I/O. After
restart, an existing receipt advances without replay; a safe read may retry;
an unresolved side effect reconciles to `succeeded`, `not_applied`, or
`unknown`. Only `not_applied` can return to ordinary execution; `unknown`
pauses. Cancellation remains requested until no external action is active or a
safe reconciliation disposition exists.

Transient failures schedule a durable retry and require a fresh plan revision.
Persisted action payloads redact credentials and omit content-bearing fields.

## Public control and proof

CLI, D-Bus, loopback HTTP, and Python expose task list/show and revision-bound
pause, resume, cancel, takeover, and release. `dry_run` is a distinct terminal
state with evidence, never a successful real-world outcome.

The suite covers the complete transition matrix, terminal revival rejection,
atomic fault injection, CAS, concurrent leases, heartbeat/expiry fencing,
timer/event waits, deadlines, restart scans, outbox deduplication, review and
clarification restart, all controls, privacy, old-data migration, 19 capability
contracts, and eight subprocess crash boundaries. See the
[compatibility matrix](goal03_replacement_compatibility_matrix.md) for deletion
evidence and environment-specific outcomes.

The 2026-07-17 Fedora WSL benchmark completed 64 tasks with 8 workers in
`0.198 s`, with `56.46 ms` p95 commit latency and zero lock/commit errors. This
is below the stored `2,500 ms` p95 and `20 s` wall thresholds; the complete
machine-readable result is in
[`durable_task_benchmark.json`](durable_task_benchmark.json).
