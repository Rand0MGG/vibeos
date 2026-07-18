# Goal 03 final acceptance evidence

Status: all executable-code and documentation gates passed; fast-forward to
`main` is waiting for explicit user approval.

## Frozen topology

| Purpose | Reference |
| --- | --- |
| Goal 01 baseline and unchanged `main`/`origin/main` | `a6d809ffb60a61c29380c04eebbbb134c7ddef9c` |
| Unreconciled Goal 02 preservation checkpoint | `7c77044063dfe513bb7742f600268b5913aa3c4a` (`codex/goal02-unreconciled`) |
| Final runtime code | `fbfd2ab1d0b3c9bce74dbc49db2e38e080e50aa4` |
| Final verified source/test head | `80811167b7d919bfa69aa24094ca9d6b0d058340` (`codex/goal03-reconciliation`) |
| Goal 01 detached worktree | `C:/Users/Rand0MG/AppData/Local/Temp/vibeos-goal01-7c77044` |
| Goal 03 reconciliation worktree | `C:/Users/Rand0MG/AppData/Local/Temp/vibeos-goal03-7c77044` |

Goal 01 and Goal 03 used separate editable virtual environments:
`/home/rand0mg/.venvs/vibeos-goal01-7c77044` and
`/home/rand0mg/.venvs/vibeos-goal03-7c77044`. The protected project-manager
contracts were carried byte-for-byte from the preservation checkpoint. The
final command
`git diff --exit-code 7c770440... -- docs/goals/agent_native` returned zero.

## Auditable commit sequence

| Commit | Logical group |
| --- | --- |
| `e722049` | durable kernel and frozen self-contained migrations |
| `952d02d` | shared CLI/D-Bus/HTTP/Python compatibility adapters |
| `9887586` | behavior, old-data, controls, worker, and crash contracts |
| `0519a1a` | replacement and compatibility deletion matrix |
| `b7be1dc` | deletion of the proven legacy task kernel |
| `d1c0b1c` | current architecture and rollback documentation |
| `3f61530` | restored durable concurrency benchmark gate and report |
| `fbfd2ab` | truthful explicit-offline provider diagnostic |
| `fa83360` | final evidence, status, WSL, and root README documentation |
| `fbdf9b7` | completion-audit corrections to active product documentation |
| `8081116` | exact 19-capability Registry/target/contract coverage gate |
| `self` | final metrics and completion-audit evidence only |

The branch is a descendant of Goal 01; it was not produced by merging the
unreconciled checkpoint. The final Git diff with this evidence is 120 files
changed, 10,236 insertions, and 10,665 deletions.

## Independent baseline and normalized compatibility

The Goal 01 detached worktree passed `302` tests in `24.42 s`. The Goal 03
worktree passed `954` tests in `40.93 s`. Both environments then received the
same black-box inputs with separate state directories:

| Input | Normalized comparison |
| --- | --- |
| `vibe capabilities --json` | identical sorted 19 capabilities and 19 detail rows |
| offline `plan open browser` | both `validated`, route `apps_open_route`, one `app.open`, target `{"name":"browser"}` |
| offline dry-run `search web for hello` | identical action/target, execution, acceptance, overall status, and plan step |
| offline dry-run `close firefox` | both `review_required`, `window.close`, target `{"name":"firefox"}`, `L2`, not started, `needs_review` |

The current environment has no model API key. Provider-backed `plan open
browser` therefore failed closed with `no candidate was generated`; explicit
offline planning validated the deterministic `app.open` plan. Doctor now
states this boundary accurately instead of claiming an automatic fallback.

## Quality, task, and public-contract gates

| Gate | Result |
| --- | --- |
| full pytest | `954 passed in 40.93s` |
| durable domain/repository/controls/eight-crash suite | `687 passed in 12.60s` |
| exact 19-capability Registry/target/contract suite | `39 passed in 13.85s` |
| public compatibility, behavior, migration, HTTP, and D-Bus subset | `13 passed in 5.81s` |
| provider/doctor/CLI subset | `15 passed in 5.48s` |
| Ruff lint | all checks passed across source, tests, scripts, and migrations |
| Ruff format | `171 files already formatted` |
| strict mypy | no issues in 48 source files |
| architecture guard | `ok: true`, zero violations |
| 64-task / 8-worker benchmark | wall `0.198 s`, p95 `56.46 ms`, `323.59 tasks/s`, zero lock/commit errors |

The benchmark is below the stored `20 s` wall and `2,500 ms` p95 thresholds.
The full suite includes all 19 capability contracts, revision-CAS controls,
review/clarification restart, old pending data, worker isolation, lease/fencing,
timer recovery, outbox deduplication, and eight real subprocess crash boundaries.

## Migration evidence

Historical revisions are self-contained and no longer import mutable runtime
metadata. The committed hashes are:

| Revision | SHA-256 |
| --- | --- |
| original Goal 01 `0001` | `82794445a47ac649959b9a8edb248ded6e9f082a5ad3a7fd1c4bb398bcb2275c` |
| frozen Goal 03 `0001` | `2e680152dd5652af8bb522864bcba7e98037229738e29ab1b395209d7da25b35` |
| `0002` | `ee88a0905489f299f52bd3d5103ebe79be6e7a067cecaac9137b480a0a965a6a` |
| `0003` | `32acb4d36c6729f3b66746ebf8f180eb6aa66ba39caccf556c58bdde8864e21e` |
| `0004` | `2b5983b1f1f301c5f107df18f2b3b35346aaefc0008ef1048e5ac2abc77a83dc` |

Migration contracts compared tables, columns, indexes, foreign keys, SQL
constraints, preserved data, and Alembic revision for empty, Goal 01, direct,
interrupted, restored, and retried upgrade paths. A real Goal 01 pending review
was publicly visible after upgrade and required a current safety binding before
dispatch. A pending clarification accepted supplemental input, created a newer
GoalContract version, and resumed. Unknown historical external effects pause
instead of being replayed.

## WSL runtime evidence

- `vibe doctor --json`: 3 `ok`, 9 `warn`, 0 `fail`. Warnings accurately report
  the non-GNOME WSL session, missing portal/bridge/apps/clipboard helpers,
  inactive installed daemon, and absent model key.
- `vibe capabilities --json`: 19 capabilities and 19 detail records.
- Offline dry-run produced a durable `dry_run` task and terminal outcome bound
  to two evidence IDs; it did not claim a real-world success.
- `dbus-run-session` is not installed. The documented direct-user-bus branch
  used `/run/user/1000/bus`; the verifier reported lifecycle `ready`, 19
  capabilities, successful E0 receipt, strict unknown-field rejection, and a
  created database. Without a notification service, its first E1 observation
  failed accurately.
- The optional WSLg real-action verifier then started dunst and passed. E0 and
  E1 receipts succeeded through `/usr/sbin/notify-send`; `dbus-monitor`
  independently observed two `Notify` calls and dunst reported two displayed
  notifications. The daemon D-Bus path was `ready` and also returned a
  successful E1 receipt.

These checks prove Linux user-space, session D-Bus, SQLite, process recovery,
and the representative WSLg notification path. They do not claim GNOME Shell,
real window control, Portal UI, clipboard contents, browser contents, or the
full Fedora GNOME Wayland VM boundary.

## Final rollback rehearsal

An isolated no-hardlink clone in the system temporary directory loaded the
exact executable Goal 03 commit `fbfd2ab1...`; `PYTHONPATH` verification showed
that commands imported code from that clone. With `VIBEOS_RUNTIME=local` and a
new isolated state directory, `vibe ask status --offline` completed and created
an Alembic `0004_goal_contract_version_index` database.

The read-only export contained 2 goal contracts, 1 task, 1 succeeded
`system.status` receipt, and 2 evidence bundles. The database SHA-256 before
and after export was
`96aa4358a7143e4afd9f6b22d8474fa1b6706ca7c2e740de0f71e3501ea96832`.

The isolated clone was then switched to Goal 01 `a6d809f`. Goal 01 used a
different new state directory, exposed 19 capabilities, and completed its own
offline status task. It was never pointed at the upgraded database. The
preserved Goal 03 database retained the same SHA-256 and remained readable.
After rehearsal, `main`, `origin/main`, the Goal 02 checkpoint, and the Goal 03
branch still resolved to their expected refs.

## Requirement-by-requirement completion audit

| Goal 03 acceptance condition | Authoritative current evidence | Status |
| --- | --- | --- |
| Preserve every confirmed Goal 02/PM path | checkpoint diff has 116 paths: 115 inventoried candidate paths plus the manifest itself; commit `7c77044` has parent `a6d809f` | passed |
| Keep `main` at Goal 01 and retain a repeatable detached baseline | `main`/`origin/main` resolve to `a6d809f`; independent Goal 01 environment passed 302 tests | passed |
| Reconcile from the clean ancestor in logical groups | first Goal 03 commit has ancestor `a6d809f`; the commit table above separates kernel, adapters, behavior, deletion, fixes, and docs | passed |
| Cover every public entry, deletion, and capability | compatibility matrix has all public surfaces, 12 legacy-module rows, and exactly 19 capability rows | passed |
| Use one Durable Task Store with no dual task kernel | architecture guard is clean; legacy source paths are absent; CLI/D-Bus/HTTP/Python one-repository contract passes | passed |
| Freeze `0001`; make `0002`–`0004` self-contained | ADR 0003, exact hashes, source scan, and migration contract pass | passed |
| Prove empty, Goal 01, and interrupted upgrade equivalence | structural/data/revision comparison and restored-retry migration tests pass | passed |
| Preserve CLI, D-Bus, thin HTTP, and explicit HTTP mode | public compatibility and transport tests pass; HTTP remains loopback-only and deprecated | passed |
| Prove 19 abilities, review, clarification, controls, and recovery | 19-row table suite, 687 durable tests, behavior tests, and eight subprocess crash boundaries pass | passed |
| Delete only after equivalent replacement evidence | matrix commit precedes deletion commit; all listed old production paths are absent after the post-deletion full suite | passed |
| Pass full quality gates without overstating WSL/mock/dry-run | 954 tests, Ruff, strict mypy, architecture guard, truthful dry-run evidence, D-Bus and WSLg observations are recorded above | passed |
| Keep a clean reviewable branch and rehearse rollback | clean status, ordered commits, read-only export, independent Goal 01 artifact/database pair, and stable hash are proven above | passed |
| Fast-forward only after explicit user approval | `main` is unchanged and no merge has run | pending user approval |

## Remaining bounded debt and merge gate

- Loopback HTTP remains deprecated but supported through Goal 09; it uses the
  same application service and task store as D-Bus.
- Frozen migration `0002` and the temporary HTTP runtime size exception remain
  explicit architecture-baseline debt rather than weakening global limits.
- Optional audit field `loop_snapshot_id` is retained only for historical read
  compatibility; no snapshot module or second state authority exists.
- Provider-backed smoke cannot succeed without an API key; explicit offline
  behavior and provider fail-closed behavior are both verified.
- GNOME VM acceptance remains a later environment gate.

No fast-forward has been performed. Before merge, recheck that both worktrees
are clean and `main` still equals `a6d809f`. Only after explicit user approval
may the main worktree run `git switch main` followed by
`git merge --ff-only codex/goal03-reconciliation`; squash, rebase, force-push,
or a normal merge are not permitted.
