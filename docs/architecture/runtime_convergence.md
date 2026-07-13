# Runtime Convergence Architecture

Last updated: 2026-07-12

## Default supported-task path

```text
CLI / HTTP / D-Bus
        -> CommandService
        -> TaskApplicationService
        -> GoalLoop
             -> observe -> review -> execute -> observe -> verify
             -> retry, repair, replan, suspend, resume, or finish
        -> CommandResultProjector / AuditResultRecorder
```

`CapabilityBroker.handle()` is a source-compatible facade over `CommandService`.
`runtime_composition.py` owns dependency assembly. Broker keeps only
construction compatibility, inspection helpers, and narrow delegation entry
points; it does not own a task loop, review resume, execution, acceptance, or
any registered domain-tool handler.

## Ownership boundaries

- `CommandService`: transport-neutral request dispatch and trace lifecycle.
- `TaskApplicationService`: starts tasks, handles stored approvals and
  supplemental input, and resumes the current GoalLoop.
- `GoalLoop`: the sole state machine for supported tasks. Its six typed ports
  are `PlanningPort`, `ObservationPort`, `ReviewPort`, `ExecutionPort`,
  `AcceptancePort`, and `RecoveryPort`.
- `src/vibeos/tools/`: independently owned app, window, browser, clipboard,
  notification, system, and fixture tool registrations. The composition root
  constructs their registry.
- `ReviewService`, `AcceptanceService`, and `RecoveryService`: respectively
  own safety review and suspension persistence, postcondition/acceptance, and
  failure/recovery decisions.
- `CommandResultProjector` and `AuditResultRecorder`: own public compatibility
  projection and final audit metadata persistence.
- `PermissionPolicy`: the sole authority for `allow`, `deny`, and
  `review_required` decisions.
- `ReviewStore`: the authority for review state; `review_events` is audit
  history, not the current-state authority.

Historical `review_kind=plan` payloads are an explicit compatibility path only.
They migrate only after immutable plan, step, target, safety-review, policy,
capability, and expiration checks; otherwise they fail closed with
`legacy_review_unverifiable`. They are never reached by fresh supported tasks.

## Per-run state and compatibility output

`RunContext` binds a run id, goal id, transport, dry-run/debug flags, and
review identity. Normal GoalLoop execution has no shared production agent
session. Older runtime-shaped result fields are produced by a pure projection
from planning artifacts, GoalLoop attempts, and immutable receipts.

The public semantic fields remain stable: `status`, `execution_status`,
`acceptance_status`, `overall_status`, `review_id`, `trace_run_id`, `audit_id`,
and the task-plan `run` / `attempts` payloads.

## Review durability and duplicate approval protection

SQLite contains a current `reviews` table with a monotonic version plus the
append-only `review_events` table. JSONL and event-only SQLite data are
replayed once and idempotently into current state. Claiming an approved review
uses a database compare-and-swap (`approved -> executing`); success consumes
it and a failed real execution releases it back to `approved`.

Routes with declared verifiers require observation-backed progress. Routes
without a verifier may accept a successful bounded adapter receipt. Dry runs
remain side-effect free and never consume a review.

## Trace privacy

Normal traces retain structural metadata, hashes, and bounded normalized data;
they omit raw user utterances, raw provider request/response data, and
supplemental input. Debug artifacts remain redacted and truncated.

## Verification and CI

The configured WSL verification command is:

```bash
python -m pytest -q
vibe capabilities --json
vibe ask "search web for hello" --json --offline --dry-run
```

The final Fedora 44 WSL verification passed `263` tests in `11.90s`, and the
offline dry-run returned `overall_status=dry_run`. `vibe doctor --json`
reported `warn` with zero failures, which is expected without a GNOME desktop
session. Exact static and smoke-test evidence is recorded in the
[`final acceptance audit`](../architecture_completion_final_audit.md).

[`.github/workflows/test.yml`](../../.github/workflows/test.yml) runs the same
deterministic checks plus Ruff lint/format and scoped strict typing on `push`
and `pull_request` with Python 3.11. The workflow has not been observed
running in GitHub yet.

WSL does not replace the Linux GNOME VM acceptance boundary: systemd user
service, GNOME extension, D-Bus window control, desktop notifications,
clipboard integration, portal navigation, and real browser observation remain
user-run VM checks.
