# VibeOS current status

Last verified: 2026-07-30. Goal 04 starts from frozen planning commit
`efe2267`. The 13 files under
`.codex_vm_artifacts` remain committed Goal 03 evidence assets. Goal 04A has
replaced the unreleased effect/observation contracts and converged canonical
action-result ownership. Goal 04B's offline Gateway/SecretRef/process contract
is implemented. Goal 04C's fixed systemd user-service slice, real worker-crash
matrix and WSL systemd D-Bus evidence are implemented. The controlled real
provider and Fedora GNOME VM gates remain explicitly not run. Review findings
against the offline implementation were remediated on
`codex/goal04-review-remediation`; this is an implementation candidate, not a
claim that the external Goal 04 gates have passed.

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

Alembic head is `0007_repair_effect_policy_summary`. Goal 03 used the approved
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

Revision `0006` now replaces a legacy policy-summary dictionary with the
canonical E0-E4 definitions instead of mechanically renaming L-level keys.
Revision `0007` repairs active databases that had already run the original
0006 implementation; terminal rows remain untouched.

The classification and ownership details are in the
[`effect matrix`](goal04_effect_reclassification_matrix.md) and
[`execution convergence matrix`](goal04_execution_convergence.md).

## Goal 04B model and secret boundary

Model Gateway v1 now binds the fixed service diagnosis request to a task and
attempt, one D0 context manifest, strict schema, cancellation and wall/token
budgets. A scrubbed semantic subprocess has neither session bus nor secret-like
environment names. A separate transport subprocess alone resolves an opaque
SecretRef through `secret-tool` and calls the OpenAI-compatible HTTPS adapter.

The old `provider_client` transport is disabled and no longer loads or sends
environment credentials. Its callers remain as a ratcheted Goal 05 migration
inventory and fail closed until they acquire typed Gateway purposes. The Linux
user-service installer no longer injects `.env`. TTY import/status/delete and
one-shot explicit environment migration are documented in the
[`SecretRef runbook`](../operations/goal04_secretref_runbook.md); architecture,
failure semantics and the threat model are in the
[`Gateway boundary`](goal04_model_gateway_and_secret_boundary.md).

Offline tests cover D0 success, 429/5xx/timeout/bad JSON/schema/budget/cancel/
unknown-delivery failures, locked-to-wait/unlock-to-resume, leak canary, strict
route persistence and the real subprocess isolation probe. The controlled
real-provider smoke remains `not run` until a user-owned credential is present;
it is not claimed as passed.

`vibe secrets status` now calls Secret Service `SearchItems` over the session
D-Bus and inspects only locked/unlocked item paths. It never invokes
`secret-tool lookup` and therefore does not resolve provider plaintext into the
ordinary CLI process.

## Goal 04C fixed system-service slice

`vibeos-goal04-fixture.service` is a disabled, `Type=notify` user fixture. The
pre-task controller proves one token-bound synthetic startup failure; after
task start it does not participate. Only the internal fixed-unit E1 tool may
start or restart it. That tool uses the existing ToolRegistry and
DurableActionExecutor, so the provider returns only an adapter result and the
task boundary remains the sole receipt/evidence owner. The public capability
count remains 19.

Bounded D-Bus facts and fixed-unit journal lines are persisted before a D0
context manifest. Before provider I/O the task persists a stable request ID and
dispatch record. A crash after dispatch but before result persistence pauses as
unknown instead of replaying the provider call; the stable ID is also sent as
the idempotency and trace header. The provider budget is capped by the task
deadline, and the specialized recovery path terminates expired tasks before
model or action I/O. A post-action crash enters real-state
reconciliation. The independent verifier polls bounded fresh observations
until systemd leaves `activating`, with no action redispatch. TerminalOutcome
now carries diagnosis, action, current state, evidence IDs, completion judgment
and unresolved risks.

The 04C suite has 36 tests, including twelve real `os._exit(97)` worker-process
crash points and the required fault/concurrency matrix. A FedoraLinux-44 WSL
run used the real systemd user D-Bus provider: the controller proved one failed
start and one synthetic line, the Agent produced one canonical receipt, and
independent observation found `active/running`, a live PID and the healthy log.
The model side of that WSL run was the strict offline fixture because WSL has
no `secret-tool` or user-owned provider route. See the
[`Goal 04C acceptance report`](goal04_system_service_acceptance_2026-07-22.md).
The 2026-07-30 remediation regression is `1072 passed in 113.13s`; Ruff formatting/lint,
strict mypy over 67 source files and the architecture guard are green.
The safe/offline VM evidence collector reports `overall: ok` with no failed or
blocked steps. The direct session D-Bus script is contract-covered by tests but
was not rerun because this WSL image lacks `dbus-run-session`.

## Remaining external Goal 04 evidence

- run and record one controlled real-provider smoke with a user-owned SecretRef;
- run and record Secret Service locked/unlocked plus the complete fixture path
  in the Fedora GNOME VM. VMware execution was skipped at the user's request
  for this pass and is not represented by WSL evidence.

Existing compatibility debt owned by Goal 05:

- semantic modules retain the disabled `provider_client` import surface and
  cannot use remote models until each purpose is migrated to Gateway v1.

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
