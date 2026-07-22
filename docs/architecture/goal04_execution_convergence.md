# Goal 04 execution convergence

## Authoritative owners

| Concern | Previous path | Goal 04 owner | Compatibility boundary | Deletion gate |
|---|---|---|---|---|
| Task, review, clarification and recovery state | Durable store plus legacy facades | `DurableTaskEngine` and `SqliteTaskRepository` | Public projections only | No second mutable repository |
| Task action receipt and evidence | Foundation inner aggregate plus durable outer aggregate | `DurableActionExecutor` transaction | Foundation returns `AdapterResult` and evidence material | Guard rejects receipt/evidence creation in Foundation |
| Executable tool registration | Recipes and `ToolRegistry` | `ToolRegistry` | Recipe registry maps validated steps only | Guard and registry tests |
| Effect decision | Permission policy plus scattered rank helpers | Deterministic `EffectPolicy` | None | Old tokens rejected in live source/contracts/tests |
| Observation/context | `ObservationService` and `ContextPackageRegistry` | Same canonical path, renamed O0-O2 | v1 historical decoder only | No live L/O aliases |
| Goal 01 action rows | Write-capable `SqliteActionRepository` | Frozen history only | `LegacyActionHistoryReader` has no write API | Remove after historical retention decision |

## Canonical result flow

```text
validated TaskStep
  -> ToolRegistry
  -> provider/Foundation AdapterResult
       status + adapter status + external reference + evidence material
  -> DurableActionExecutor
       one ActionReceipt + one EvidenceBundle
  -> SqliteTaskRepository.commit (single transaction)
  -> independent verification / terminal decision
```

An adapter result never asserts task completion and has no task receipt ID or evidence ID. Provider-local state is allowed only for idempotency and unknown-delivery reconciliation and must be linked by a non-secret external reference.

## Version and rollback boundary

New Task, Plan, Step, Review, Capability, Observation, Python, D-Bus and loopback HTTP payloads are v2. The production Model Gateway introduced in 04B is a separate v1 protocol and does not weaken the live task boundary.

`0006_effect_contract_v2` modifies only resumable, nonterminal task families. Deterministically classified work becomes v2; ambiguous work becomes safely paused. Terminal v1 families remain immutable and read-only. Rollback is a pair: the pre-04A artifact commit and the pre-migration SQLite snapshot created by `CoreDatabase`; mixing either artifact with the other database generation is unsupported.
