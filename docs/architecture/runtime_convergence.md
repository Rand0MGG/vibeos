# Runtime convergence architecture

Last updated: 2026-07-17.

## One task path

```text
CLI / D-Bus / loopback HTTP / Python
  -> transport-neutral CommandRequest
  -> CommandService
  -> TaskApplicationService
  -> DurableTaskEngine
       planning -> review -> proposal -> execute -> observe -> accept/recover
  -> CommandResultProjector / AuditResultRecorder
```

`runtime_composition.py` builds one repository, task engine, planner, review
service, executor, acceptance service, and recovery service. `CapabilityBroker`
only exposes construction and compatibility methods. No transport performs
adapter mutation directly.

## Durable ownership

- `TaskRun` plus a pure transition table is the state machine.
- `SqliteTaskRepository` owns revision CAS, task artifacts, leases/fencing,
  waits, outbox delivery, receipts, evidence, and terminal outcomes.
- `PlanningService`, `ObservationService`, `ReviewService`,
  `StepExecutionService`, `AcceptanceService`, and `RecoveryService` are typed
  collaborators; none owns shadow task state.
- Pending review and clarification are task statuses with a durable interaction
  ID. Approval and supplemental input survive process reconstruction.
- `CommandResultProjector` preserves public status/run/attempt fields without
  becoming a state authority.

## Transport policy

D-Bus is tried first in `auto` mode. A loopback HTTP daemon is the deprecated
fallback, followed by local development mode when daemon use is not required.
Explicit `VIBEOS_RUNTIME=dbus`, `http`, or `local` remains supported.

The HTTP server rejects non-loopback addresses, adds deprecation headers, and
keeps the Goal 01 `/v1/status`, `/v1/command`, `/v1/apps`, `/v1/windows`,
`/v1/capabilities`, `/v1/reviews/pending`, and `/v1/audit/tail` contracts.
Task list/show/control endpoints are additive. All routes call the same broker
and repository used by D-Bus; there is no HTTP Task/Review store.

## Compatibility and recovery

Goal 01 pending rows migrate into durable state. A migrated approval is rebound
to the current safety digest before dispatch. Unknown historical external
effects pause. ActionProposal persists before I/O; receipts and evidence persist
after I/O; unresolved side effects reconcile rather than replay blindly.

The black-box compatibility suite covers all four entry surfaces, explicit
HTTP mode, approval/denial, clarification/supplemental input, restart, CAS user
controls, old pending data, eight crash boundaries, and all 19 capabilities.
Environment-incomplete outcomes remain recoverable and visible instead of
being projected as success.
