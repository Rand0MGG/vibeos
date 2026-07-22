# VibeOS current status

Last verified: 2026-07-19 after the user-approved local `main` fast-forward,
dry-run recovery repair, and Fedora GNOME VM remediation. The final remediation
is still an uncommitted worktree and has not been pushed.

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

## Verified Goal 03 remediation worktree

The final worktree reports in FedoraLinux-44 WSL:

```text
python -m pytest -q                   -> 974 passed in 52.07s
ruff format --check                  -> 164 files already formatted
ruff check                           -> passed
mypy                                 -> 0 issues in 48 source files
python scripts/architecture_guard.py -> ok; 0 violations
```

The fixed Goal 02 subset is `690 passed in 20.23s`. Its 64-task/8-worker
benchmark completed with p95 `56.25 ms`, wall `0.198 s`, throughput `323.26
tasks/s`, and zero lock/commit errors. The 19-capability matrix, old-data
upgrade, CLI/D-Bus/HTTP/Python convergence, review/clarification restart, user
controls, worker recovery, and real subprocess crash boundaries are included
in the full suite.

The dry-run migration persists execution intent and recovery restores it. Three
real subprocess crash cases—before proposal, before external I/O, and before
receipt—finish as `dry_run` with zero external adapter calls. A later GNOME VM
run found additional production-only defects. The remediation now:

- never replays a timed-out D-Bus command over HTTP when delivery is unknown;
- allows persisted `ready` work to enter clarification without an illegal
  transition;
- bounds all model calls for one command below the transport timeout;
- keeps deterministic public contracts stable while refusing to collapse a
  compound multi-domain goal into one partial action;
- pauses malformed persisted planning snapshots and safely projects unsupported
  destructive requests;
- returns scheduler/outbox health to ready after transient failures and starts
  transports without synchronously waiting for persisted recovery;
- sends mechanically completed but semantically unverified effects directly to
  clarification, retaining the original plan and receipt instead of creating
  an unbounded replan chain.

WSL doctor reports `4 ok / 8 warn / 0 fail` because it has no full GNOME
desktop. The direct session D-Bus verifier reaches ready, exposes exactly 19
capabilities, completes E0, and accurately reports notification E1 unavailable
in WSL. The preservation branch `codex/goal02-unreconciled` remains at
`7c77044`; no remote push has been performed.

WSL does not prove GNOME Shell, Wayland portal UI, displayed notifications,
clipboard contents, browser content, or real window side effects. Browser,
Portal, and media tests report incomplete or clarification-required when
independent desktop observation is unavailable; they do not fabricate success.

The final Fedora 44 GNOME Wayland VM worktree passes the same static gates and
`974 passed in 16.15s`. The final systemd user-session collector's VM doctor
reports `12 ok / 0 warn / 0 fail`; a direct SSH shell reports
`11 ok / 1 warn / 0 fail` only because that caller is a tty. GNOME, Wayland
portal, extension bridge, application registry, and action helpers are ready.
A fresh real browser regression reached `awaiting_clarification` with exactly one plan and
one successful receipt, while an independent window observation saw
`Example Domain — Mozilla Firefox`. This is intentionally not projected as
semantic completion because VibeOS cannot yet inspect Firefox's active URL.

The initial failures, remediation evidence, visible notification/clipboard/
Firefox checks, and final clean-state collector result are recorded in the
[`Goal 03 GNOME VM acceptance report`](goal03_gnome_vm_acceptance_2026-07-19.md).
