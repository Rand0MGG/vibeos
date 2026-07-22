# VibeOS current status

Last verified: 2026-07-22. Goal 04 starts from frozen planning commit
`efe2267` on `codex/goal04-execution-foundation`. The 13 files under
`.codex_vm_artifacts` remain committed Goal 03 evidence assets. Goal 04A has
replaced the unreleased effect/observation contracts and converged canonical
action-result ownership; 04B and 04C remain gated follow-on work.

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

Alembic head is `0006_effect_contract_v2`. Goal 03 used the approved
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

## Goal 04A execution foundation

The live task path now has one effect vocabulary and one canonical action-result owner:

- capability, task, plan, step, review, CLI, D-Bus, HTTP and Python live
  contracts use v2 `effect_level` E0-E4; observation depth uses O0-O2;
- the deterministic `EffectPolicy` is the only policy. E0 observes, E1 permits
  bounded reversible task actions, E2 is declared but unimplemented, E3 needs
  stored per-action approval, and E4 rejects;
- `FoundationSliceService` and providers return strict `AdapterResult` plus
  evidence material. Only `DurableActionExecutor` creates the task receipt and
  evidence and commits them through `SqliteTaskRepository`;
- revision `0006` upgrades deterministically classifiable nonterminal JSON,
  pauses ambiguous effect bindings for manual disposition, and leaves terminal
  v1 rows byte-preserving and read-only;
- `/v2` is the loopback HTTP task/effect contract. D-Bus and Python projections
  carry the same schema and vocabulary;
- fixed system-service facts and E1 start/restart ActionSpec contracts exist
  for the single Goal 04 fixture; no arbitrary unit, shell, root or system-bus
  authority is exposed.

The classification and ownership details are in the
[`effect matrix`](goal04_effect_reclassification_matrix.md) and
[`execution convergence matrix`](goal04_execution_convergence.md).

## Remaining Goal 04 work

- establish the production Model Gateway v1, SecretRef/keyring transport and
  verifiable semantic-worker/secret-transport process boundary;
- prove the locked/unlocked, leak-canary and one controlled real-provider smoke
  gates before starting the real-provider-dependent system-service scenario;
- implement and validate the deterministic systemd user fixture, independent
  fact collection/action/verification path, crash matrix and Fedora GNOME
  evidence.

Existing pre-04A debt that is owned by 04B:

- semantic modules still call `provider_client` directly, provider keys can
  still come from `.env` or ordinary environment variables, and the session
  install script injects that environment into the user service;
- no production Model Gateway or SecretRef/Broker process boundary exists yet.

Goal 05 must extend the Gateway/SecretRef/transport v1 contract delivered by
04B rather than replace it.

## Committed Goal 03 remediation baseline

The committed remediation baseline reported the following in FedoraLinux-44 WSL:

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
`7c77044` without an upstream. The remediation branch and `main` have been
pushed; the preservation branch is retained only as local rollback evidence.

WSL does not prove GNOME Shell, Wayland portal UI, displayed notifications,
clipboard contents, browser content, or real window side effects. Browser,
Portal, and media tests report incomplete or clarification-required when
independent desktop observation is unavailable; they do not fabricate success.

The final Fedora 44 GNOME Wayland VM evidence records the same static gates and
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
