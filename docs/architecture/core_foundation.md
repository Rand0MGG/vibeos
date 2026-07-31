# Core foundation and durable-kernel handoff

Goal 01 introduced strict contracts, SQLAlchemy Core/Alembic, current state,
domain events, outbox, a single asyncio supervisor, and the first E0/E1
vertical slices. Goal 03 reconciles those public behaviors with the Goal 02
Durable Task Engine.

```text
core/domain       pure task transitions and immutable domain values
core/ports        typed boundaries
core/application  supervisor, scheduler, outbox dispatcher
core/adapters     SQLite repositories, contracts/codecs, thin HTTP transport
```

`SqliteTaskRepository` is the only task-state writer. `0001` is the controlled
Goal 01 schema freeze; `0002` adds durable task artifacts and migrates pending
legacy interactions; `0003` repairs durable semantics; `0004` allows multiple
versioned GoalContracts per task; `0005` persists dry-run execution intent and
marks missing active legacy intent as unknown so recovery pauses fail-closed.
No revision imports mutable runtime metadata. After Goal 03, historical
revisions are immutable and schema changes require a new revision.

The supervisor owns database readiness, scheduler, outbox, D-Bus, and the
loopback HTTP compatibility listener on one lifecycle. Both transports call
the same application service. HTTP remains deprecated until Goal 10; it is not
a second runtime or persistence path.

Architecture ratchets live in `architecture_baseline.json`. New durable modules
remain under the shared line/complexity threshold. The frozen self-contained
`0002` migration and the temporary HTTP runtime surface are recorded as
explicit, owner-bound debt rather than weakening the general threshold.

See the [migration ADR](../product/decisions/0003-freeze-goal01-migration-history.md),
[durable engine](durable_task_engine.md), and
[replacement matrix](goal03_replacement_compatibility_matrix.md).
