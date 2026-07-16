# ADR 0003: Freeze the Goal 01 migration history

- Status: accepted
- Date: 2026-07-17
- Owners: core runtime and persistence
- Supersedes: the mutable-metadata behavior of revision
  `0001_core_foundation` only

## Context

Goal 01 committed `0001_core_foundation` at
`a6d809ffb60a61c29380c04eebbbb134c7ddef9c`. That revision imported the
application's live SQLAlchemy metadata. After Goal 02 added Durable Task tables,
running the historical revision on a new database would therefore create a
schema determined by today's application code rather than the schema reviewed
for Goal 01. That makes an Alembic revision mutable, prevents reliable upgrade
and rollback rehearsal, and can make two databases carrying the same revision
identifier structurally different.

The project is still version `0.1.0`, has no release tag, and has not shipped a
supported database artifact. Goal 03 therefore permits one controlled rewrite
of the unreleased migration history before the revisions become immutable.

## Decision

1. `0001_core_foundation` contains a local, frozen copy of the exact Goal 01
   schema and no longer imports application metadata.
2. `0002_durable_task_engine` contains its own frozen task-schema snapshot.
   `0003_repair_durable_task_semantics` reflects only the schema installed by
   earlier revisions for data repair, and `0004_goal_contract_version_index`
   declares its local target table shape. None imports `vibeos` runtime
   metadata.
3. The committed Goal 03 versions of `0001` through `0004` are immutable.
   Every later schema change must add a new revision.
4. A Goal 01 database upgrades in place. Pending review data is converted to
   the single Durable Task store before the legacy review tables are removed;
   an unrestorable approval is paused fail-closed instead of being guessed.
5. Product rollback is not an Alembic downgrade after Durable Task data has
   been written. Operators export task/audit data and deploy a known Goal 01
   artifact or use an audited code revert as documented by Goal 03.

## Hash record

| Artifact | SHA-256 |
|---|---|
| Original `a6d809f:0001_core_foundation.py` | `82794445a47ac649959b9a8edb248ded6e9f082a5ad3a7fd1c4bb398bcb2275c` |
| Goal 02 checkpoint `7c77044:0001_core_foundation.py` | `2e680152dd5652af8bb522864bcba7e98037229738e29ab1b395209d7da25b35` |
| Goal 03 `0001_core_foundation.py` | `2e680152dd5652af8bb522864bcba7e98037229738e29ab1b395209d7da25b35` |
| Goal 03 `0002_durable_task_engine.py` | `ee88a0905489f299f52bd3d5103ebe79be6e7a067cecaac9137b480a0a965a6a` |
| Goal 03 `0003_repair_durable_task_semantics.py` | `32acb4d36c6729f3b66746ebf8f180eb6aa66ba39caccf556c58bdde8864e21e` |
| Goal 03 `0004_goal_contract_version_index.py` | `2b5983b1f1f301c5f107df18f2b3b35346aaefc0008ef1048e5ac2abc77a83dc` |

Hashes are calculated over the exact Git worktree bytes with SHA-256 and are
rechecked before the migration commit is created.

## Verification contract

The migration test suite must prove all of the following, comparing structural
metadata rather than only process exit codes:

- an empty database reaches the current head;
- a database stopped at the frozen Goal 01 revision reaches the same table,
  column, index, foreign-key, and constraint shape while preserving and safely
  translating its data;
- an injected failure after partial upgrade restores the exact pre-upgrade
  database, and a retry reaches the same final schema and data as a direct
  upgrade;
- the resulting Alembic revision is identical for all three paths;
- no historical revision imports mutable runtime metadata.

## Consequences

The migration files are longer because their schema declarations are local and
deliberately repetitive. In exchange, database history is deterministic and
reviewable. Any attempt to edit revisions `0001` through `0004` after Goal 03 is
a compatibility violation and requires a new ADR plus a new migration rather
than an in-place change.
