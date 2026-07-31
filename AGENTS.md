# VibeOS repository instructions

These instructions apply to the entire repository. A more deeply nested
`AGENTS.md`, if one is added later, may narrow implementation details for its
subtree but must not weaken the product and safety invariants below.

## Before changing the repository

1. Read the active Goal in `docs/goals/agent_native/` completely.
2. Read `docs/goals/agent_native/README.md`, the product charter, the accepted
   ADRs referenced by the Goal, and `docs/architecture/current_status.md`.
3. Inspect the actual branch, HEAD, remotes, worktree, database revision,
   production callers and relevant tests. A Goal describes an expected entry
   state, not a permanent Git snapshot.
4. Treat existing uncommitted or untracked work as user-owned. Do not discard,
   overwrite, stage, commit, move between worktrees or publish it unless the
   active Goal or user explicitly authorizes that action.
5. If the active Goal's prerequisites are not genuinely satisfied, stop the
   dependent implementation, collect precise evidence and handle it as the
   named remediation. Do not build a parallel replacement to hide the gap.

## Scope and authority

- Implement only the active Goal and work required to verify it. Do not start a
  later Goal, broaden a fixture into a platform, or add speculative framework
  layers.
- User instructions and accepted product decisions outrank a Goal. A newer Goal
  may supersede an older roadmap statement, but it does not rewrite historical
  evidence.
- Ask before acting when the target, external consequence, data scope,
  completion condition or another material part of the user's intent is
  ambiguous. Do not ask the user to choose ordinary technical details that are
  already bounded by the Goal and policy.
- Never report a Goal complete because code compiles, a mock passes or the work
  is large. Completion requires every stated acceptance condition or an honest
  external blocker recorded where the Goal permits one.

## Architecture invariants

- Keep one production Durable Task Engine, one canonical Task Store and one
  TaskRun state machine.
- Only the durable action boundary owns canonical ActionReceipt/Evidence.
  Facades, providers, adapters, semantic modules and extensions do not own
  private task, approval, recovery or completion state.
- Keep one production owner for each boundary: Effect Policy, ToolRegistry,
  Observation/Context, whole-goal planning and coverage, Model Gateway, Secret
  Broker, daemon lifecycle and completion judgment.
- Models may understand goals, propose bounded structures and explain evidence.
  Deterministic host code owns capability boundaries, whole-goal coverage,
  effect classification, permissions, secret scope, routing policy and reality
  checks.
- Prefer system/application API, then D-Bus, then allowlisted structured CLI,
  then AT-SPI, then a user-authorized portal/visual input session. Do not skip
  directly to shell or coordinate injection when a stronger path exists.
- Effects use only E0-E4 and observations use only O0-O2 in live production
  contracts. Do not reintroduce `risk_level`, effect `L0-L3`, aliases or dual
  policies.
- Preserve proposal-before-effect, bounded timeout, idempotency, independent
  verification and unknown-outcome reconciliation for every real action.

## Effect, permission and rollback rules

- E0 observation and bounded E1 user-scope actions may execute autonomously
  only inside the current GoalContract, data scope and resource policy.
- E2 requires the single deterministic Effect Policy, an isolated Reviewer,
  least privilege, an operation-specific transaction and independently verified
  compensation/rollback.
- E3 external commitments, private-data disclosure, irreversible destruction or
  major security changes require explicit user approval for the exact action.
- E4 is refused. Unknown effect, target, policy, verifier or rollback semantics
  fail closed; do not downgrade them to make a scenario pass.
- A runtime failure may leave real partial effects. Preserve their receipts and
  report the incomplete whole goal; never fabricate atomicity or overall
  success.

## Secrets and private data

- Core, models, CLI, D-Bus, HTTP, Task Store, logs and extensions must not have
  a plaintext secret getter.
- Secrets are referenced by opaque `SecretRef` values and used only by a narrow
  bound transport for an exact approved operation. Do not fall back to `.env`,
  argv, ordinary environment variables, temporary files or user messages.
- Minimize model context and persisted evidence. Full provider payloads,
  screenshots, UI text and private content are not stored by default; use
  redacted metadata/digests and explicit bounded debug controls.

## Compatibility, migrations and deletion

- Extend the existing production owner in place. A compatibility facade may
  forward or project, but it must not retain network, budget, secret, policy or
  state authority.
- Do not modify an existing historical database migration. Add a new revision
  and document active-data disposition, historical-read boundaries and the
  matching artifact/database rollback pair.
- Delete a production path only after real callers, public contracts, data,
  failure behavior and rollback have replacement evidence and the active Goal's
  deletion gate is satisfied.
- Preserve completed Goal documents, acceptance reports, manifests, checksums
  and evidence as historical records. Update living navigation/status documents
  when the roadmap changes; do not retroactively claim old evidence proved a
  newer contract.

## Verification and handoff

- Test the narrow changed behavior first, then the active Goal's regression and
  architecture gates. Do not weaken, delete or mock away a safety test merely to
  obtain green output.
- Label evidence honestly as unit/mock, controlled fixture, WSL, Fedora GNOME
  VM, real provider or real external service. One category does not prove
  another.
- Before handoff, check file names, dependency links, schemas, migrations,
  public entry points, docs and `git diff`. Report tests actually run, tests not
  run, external blockers, remaining risks and any user-owned worktree state.
- Do not commit, push, open a pull request, install privileged components, call
  paid providers or produce real external effects unless the user or active
  Goal explicitly authorizes that action.
