# VibeOS current status

Last verified: 2026-07-17 during Goal 03 reconciliation.

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

Alembic head is `0004_goal_contract_version_index`. Goal 03 used the approved
pre-release exception to freeze the actual Goal 01 schema in `0001`, then made
all revisions self-contained. The reason and hashes are in
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

## Verified integration checkpoint

The FedoraLinux-44 WSL integration worktree currently reports:

```text
python -m pytest -q                   -> 954 passed in 40.93s
ruff format --check                  -> 171 files already formatted
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

WSL does not prove GNOME Shell, Wayland portal UI, displayed notifications,
clipboard contents, browser content, or real window side effects. Browser,
Portal, and media tests report incomplete or clarification-required when
independent desktop observation is unavailable; they do not fabricate success.
