# VibeOS current status

Last verified: 2026-07-19 after the user-approved local `main` fast-forward and
the post-acceptance dry-run recovery repair.

## Supported runtime

VibeOS has one supported task path and one authoritative task store:

```text
CLI / D-Bus / loopback HTTP / Python
  -> CommandService
  -> TaskApplicationService
  -> DurableTaskEngine + pure TaskRun transitions
  -> SqliteTaskRepository
  -> registered tools -> adapters
  -> ActionReceipt + EvidenceBundle -> acceptance or recovery
```

`CapabilityBroker` is the composition and compatibility facade. It does not
own a private task loop, review store, HTTP state, or capability mutation
path. Review, clarification, timer, retry, cancellation, takeover, lease,
receipt, evidence, and reconciliation state are all persisted by the durable
repository.

D-Bus is the primary Linux local control plane. HTTP is a deprecated,
loopback-only compatibility adapter retained through the Goal 09 delivery
decision because repository VM/operations callers existed in Goal 01. It
serializes requests and responses around the same application service and
database; it has no independent task or review authority.

## Migration and compatibility

Alembic head is `0005_persist_dry_run_intent`. Goal 03 used the approved
pre-release exception to freeze the actual Goal 01 schema in `0001`, then made
all revisions self-contained. Revision `0005` was added without modifying the
frozen `0001`-`0004` history. It persists the immutable dry-run flag in each
new GoalContract; an active legacy task whose execution intent cannot be
reconstructed pauses instead of defaulting to live execution. The reason and
hashes are in
[`ADR 0003`](../product/decisions/0003-freeze-goal01-migration-history.md).

`0002` migrates old pending reviews and clarifications into durable task rows,
then removes the old review tables. Pending approvals are not trusted blindly:
the first post-upgrade approval recomputes the current safety binding and asks
for a fresh approval if it changed. Approved/executing legacy rows with an
unknown external outcome are paused for manual disposition rather than replayed.

The replacement and deletion evidence is in the
[`Goal 03 compatibility matrix`](goal03_replacement_compatibility_matrix.md).
The old GoalLoop, ReviewStore, AgentRuntime, loop snapshot, and run ledger
modules are physically removed after their public contracts passed.

## Verified Goal 03 main baseline

The repaired local `main` reports in FedoraLinux-44 WSL:

```text
python -m pytest -q                   -> 958 passed in 52.72s
ruff format --check                  -> 173 files already formatted
ruff check                           -> passed
mypy                                 -> 0 issues in 48 source files
python scripts/architecture_guard.py -> ok; 0 violations
```

The 19-capability matrix, old-data upgrade, CLI/D-Bus/HTTP/Python convergence,
review/clarification restart, user controls, worker recovery, and eight real
subprocess crash boundaries are included in that suite. The independent Goal
01 comparison, session D-Bus/WSLg real-action checks, and rollback rehearsal
are recorded in the
[`Goal 03 final acceptance`](goal03_final_acceptance_2026-07-17.md).

After explicit user approval, local `main` was fast-forwarded with
`git merge --ff-only codex/goal03-reconciliation`. The original smoke gates
passed, but a later fault-injection review found that recovery did not retain
`dry_run`, allowing an interrupted preview to enter a live adapter. The repair
persists that intent, restores it in daemon recovery, and adds three real
subprocess crash cases: before proposal, before external I/O, and before
receipt. All three finish as `dry_run` with zero external adapter calls. The
expanded Durable Task subset is `708 passed in 24.04s`.
Doctor reported 4 `ok`, 8 environment `warn`, and 0 `fail`; the CLI exposed all
19 capabilities, produced the expected offline `app.open` plan, and persisted
a dry-run task with two evidence bundles. The direct user-session D-Bus
verifier reached `ready`, exposed 19 capabilities, and completed E0. The
preservation branch `codex/goal02-unreconciled` remains at `7c77044` and no
remote push has been performed, so `origin/main` remains at `a6d809f`.

WSL does not prove GNOME Shell, Wayland portal UI, displayed notifications,
clipboard contents, browser content, or real window side effects. Browser,
Portal, and media tests report incomplete or clarification-required when
independent desktop observation is unavailable; they do not fabricate success.
