# Goal 03 reconciliation and rollback runbook

Last updated: 2026-07-17.

## Immutable references

- Goal 01 code baseline: `a6d809ffb60a61c29380c04eebbbb134c7ddef9c`.
- Unreconciled Goal 02 preservation checkpoint:
  `7c77044063dfe513bb7742f600268b5913aa3c4a` on
  `codex/goal02-unreconciled`.
- Reconciliation branch: `codex/goal03-reconciliation`, created from Goal 01.
- `main` and `origin/main` must remain at the Goal 01 reference until the user
  explicitly approves a fast-forward.

Do not delete either preservation reference during Goal 03 or use reset,
rebase, force-push, or a normal merge to hide the reconciliation history.

## Logical commit order

1. durable kernel and self-contained migrations;
2. public CLI/D-Bus/HTTP/Python compatibility adapters;
3. behavior, controls, old-data, worker and crash-recovery contracts;
4. replacement/compatibility matrix;
5. proven legacy task-kernel deletion;
6. current architecture, operations, and final acceptance evidence.

The exact commit IDs are recorded in the final Goal 03 evidence file. Revert
from newest to oldest. Migration/data compatibility must be considered before
reverting the kernel commit.

## Database boundary

Goal 03 upgrades to `0004_goal_contract_version_index`. Revision `0002`
migrates pending legacy reviews into durable task rows and drops the old
`reviews` and `review_events` tables. An Alembic downgrade can recreate table
shape, but it cannot reconstruct the original review semantics or safely infer
whether an external side effect occurred.

Therefore:

- never point Goal 01 code at a database already upgraded past `0001` and call
  that a product rollback;
- stop writers, preserve the original database and WAL/SHM files, and record
  the Alembic revision before any intervention;
- export durable contracts, tasks, pending interactions, plans, steps,
  attempts, proposals, receipts, evidence, and terminal outcomes as read-only
  JSON/CSV evidence;
- deploy the known Goal 01 artifact with a Goal 01-compatible database copy or
  clean database; reconcile/exported unfinished work manually;
- keep the upgraded database available for audit and forward recovery.

## Pre-merge rollback

Before the user-approved fast-forward, rollback is simply selecting the
unchanged `main`/Goal 01 artifact. The shared branch is not moved. The Goal 03
worktree and Goal 02 checkpoint remain available for diagnosis.

## Post-merge code rollback

Use an audit-visible revert series on a new repair branch, newest logical
commit first. Do not rewrite shared history. If only a compatibility adapter is
faulty, revert that bounded commit while leaving the durable schema readable.
If the task kernel must be rolled back, use the database boundary above and a
known artifact/database pair rather than an in-place code downgrade.

## Rehearsal acceptance

The Goal 03 rehearsal uses a temporary clone/worktree, never the shared
branches. It must prove:

- the integrated commit can create and read a durable task database;
- read-only export includes goal contract, task, receipt, evidence, and
  revision data;
- Goal 01 code can be checked out independently and run against its own
  isolated database;
- switching artifacts does not mutate the preserved upgraded database;
- `main`, the Goal 02 checkpoint, and the reconciliation branch remain at
  their expected refs.

The command transcript and observed hashes belong in the final acceptance
evidence; a successful command exit alone is insufficient.
