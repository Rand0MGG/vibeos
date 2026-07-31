# Goal 03 replacement and compatibility matrix

This document is the deletion gate for reconciling the Goal 01 public behavior with the Goal 02 durable kernel. The authoritative task, review, clarification, recovery, receipt, and evidence state is the Durable Task Engine backed by `SqliteTaskRepository`. No legacy store is dual-written and no second task kernel is retained as a fallback.

Status vocabulary:

- `equivalent`: the new path has a passing public or black-box contract.
- `intentionally_changed`: the difference is deliberate, bounded, and documented here.
- `compatibility_missing`: the old path stays until an owner and test exist.
- `obsolete_with_evidence`: an implementation-private artifact is replaced by public behavior tests.

## Baselines and evidence

| Item | Frozen value or evidence |
| --- | --- |
| Goal 01 ancestor | `a6d809ffb60a61c29380c04eebbbb134c7ddef9c` |
| Unreconciled Goal 02 checkpoint | `7c77044063dfe513bb7742f600268b5913aa3c4a` on `codex/goal02-unreconciled` |
| Durable kernel and migrations | commit `e722049` |
| Public adapters | commit `952d02d` |
| Behavior and recovery | commit `9887586` |
| Combined behavior suite | 742 passed in Fedora WSL |
| Quality gate at behavior checkpoint | Ruff passed; strict mypy passed for 54 source files |

## Public entry points

| Goal 01 surface | Reconciled owner | Status | Contract evidence and compatibility decision |
| --- | --- | --- | --- |
| CLI (`vibe`) | `cli.py` -> runtime -> `TaskApplicationService` | `equivalent` | Commands, reviews, task list/show/control, dry-run, and JSON projection use the one repository. |
| D-Bus | `dbus_service.py` -> `TaskApplicationService` | `equivalent` | Remains the primary Linux local control plane; transport-only serialization has no task state. |
| Python facade | `CapabilityBroker` | `equivalent` | Capability calls, pending interactions, approval, supplemental input, rejection, and controls are durable black-box tested. |
| loopback HTTP | `core/adapters/http.py` and daemon router -> same service | `intentionally_changed` | Preserved through Goal 10, restricted to loopback, and marked deprecated with response headers. Goal 04 replaced the unreleased v1 task/effect payload with `/v2/status`, `/v2/command`, `/v2/apps`, `/v2/windows`, `/v2/capabilities`, `/v2/reviews/pending`, `/v2/audit/tail`, and v2 task routes. Goal 03 v1 evidence is historical only. |
| `VIBEOS_RUNTIME=http` | `HTTPDaemonRuntime` / `HTTPDaemonClient` | `equivalent` | Explicit HTTP mode and auto D-Bus -> HTTP -> local fallback preserve historical error behavior without introducing state. |
| systemd daemon | `daemon.py` | `equivalent` | One composed application service serves D-Bus and the thin HTTP adapter. |
| repository VM scripts | D-Bus-first scripts with HTTP compatibility retained | `intentionally_changed` | Operational callers migrate to D-Bus; HTTP is not removed and remains contract-tested. |

## Historical data and migration contracts

| Goal 01 data | Durable replacement | Status | Evidence |
| --- | --- | --- | --- |
| Core schema at `0001` | Frozen self-contained Goal 01 schema | `intentionally_changed` | Before/after SHA-256 and rationale are recorded in ADR 0003. |
| Pending action review | `task_runs.awaiting_review` plus plan revision and step | `equivalent` | A real Goal 01-shaped database upgrades and is visible through `pending_reviews`; approval is rebound to the current safety digest before execution. |
| Pending clarification | `task_runs.awaiting_clarification` plus versioned goal contract | `equivalent` | Supplemental input resumes after upgrade and restart, creates a new contract version, then executes through the durable path. |
| Legacy approved/executing/provided interaction | paused durable task requiring manual disposition | `intentionally_changed` | Unknown external effects are never replayed automatically. |
| Empty database / Goal 01 database / interrupted migration | Alembic head with identical schema and durable data | `equivalent` | Tables, columns, indexes, foreign keys, SQL constraints, data, and revision are compared in `test_goal03_migrations.py`. |

## Legacy production modules

The dependency scan before deletion found no production caller outside this legacy cluster. References remaining in old tests are implementation-private and are replaced by the public contracts listed below.

| Legacy module | Durable replacement | Status | Evidence required before deletion |
| --- | --- | --- | --- |
| `agent_runtime.py` | `durable_task_engine.py`, driver, resumer, recovery service | `equivalent` | Task lifecycle, retry, crash boundaries, controls, receipts, evidence, and completion tests pass. |
| `goal_loop.py` | pure task transitions plus durable driver | `equivalent` | Domain transition, repository, worker, crash, acceptance, and behavior suites pass. |
| `goal_ports.py` | typed planning, observation, review, execution, acceptance, and recovery services | `equivalent` | Runtime composition and one-repository entry-point test pass. |
| `loop_models.py` | `core.domain.task`, `durable_task_models.py`, persisted contracts | `equivalent` | State-machine and persistence round-trip tests pass. |
| `loop_policy.py` | `TaskEnginePolicy`, durable planning/recovery policies | `equivalent` | Retry, timeout, replan, and crash tests pass. |
| `loop_snapshot.py` | goal contracts, plan revisions, task runs, steps, attempts | `equivalent` | Restart, timer, review, clarification, and legacy-upgrade tests pass. |
| `reviews.py` | pending interaction fields and repository lookup/CAS | `equivalent` | Approval, denial, supplemental input, exactly-once binding, restart, and upgrade tests pass. |
| `review_resume_service.py` | `DurableTaskEngine.approve`, `provide_input`, and resumer | `equivalent` | Public restart and old-data black-box tests pass. |
| `legacy_review_migration.py` | self-contained Alembic `0002` migration | `equivalent` | Old action review safely rebinds; old clarification resumes; unrestorable effects pause. |
| `agent_runtime.py` projections and `projections.py` | `result_projection.py` and broker task projection | `equivalent` | CLI/D-Bus/HTTP/Python normalized projection tests pass. |
| `run_ledger.py` | attempts, proposals, receipts, evidence, terminal outcomes | `equivalent` | Repository, 19-capability, retry, and crash tests pass. |
| `core/adapters/http.py` | same thin adapter, retained | `compatibility_missing` for deletion | Goal 03 explicitly forbids deletion. It remains until the Goal 10 delivery decision. |

## Test replacement decisions

| Old test group | Replacement | Status |
| --- | --- | --- |
| Private `GoalLoop` unit tests | task domain/repository/worker/crash and black-box behavior tests | `obsolete_with_evidence` |
| Private `ReviewStore` tests | approval/denial/clarification/restart/legacy-upgrade public tests | `obsolete_with_evidence` |
| Private `AgentRuntime` and `RunLedger` tests | durable attempts/proposals/receipts/evidence and crash tests | `obsolete_with_evidence` |
| HTTP daemon tests | retained and extended public compatibility tests | `equivalent` |
| Current planner, observer, verifier, adapter, and acceptance tests | retained | `equivalent` |

## Nineteen capability contracts

`tests/test_durable_capability_migration.py` executes every row twice: once as dry-run and once through a real fixture or an explicitly unavailable environment. Every selected plan and normalized target is persisted. Executed actions have durable proposals, receipts, evidence, task/run/attempt projections; planning-time unavailability has a persisted clarification plus an observation receipt rather than a fabricated action receipt.

| Capability | Risk | Canonical valid target | Real or unavailable result | Fixed error boundary | Status |
| --- | --- | --- | --- | --- | --- |
| `app.list` | L0 | `{}` | succeeds | unexpected target is ignored by canonical no-target plan | `equivalent` |
| `window.list` | L0 | `{}` | succeeds | unexpected target is ignored by canonical no-target plan | `equivalent` |
| `system.status` | L0 | `{}` | succeeds | unexpected target is ignored by canonical no-target plan | `equivalent` |
| `app.open` | L1 | `name=Firefox` | succeeds | missing installed app name | `equivalent` |
| `window.focus` | L1 | `name=Firefox` | succeeds | missing visible window | `equivalent` |
| `window.minimize` | L1 | `name=Firefox` | succeeds | missing visible window | `equivalent` |
| `window.maximize` | L1 | `name=Firefox` | succeeds | missing visible window | `equivalent` |
| `notification.send` | L1 | `title=VibeOS, body=test` | succeeds | invalid/oversized notification contract | `equivalent` |
| `window.close` | L2 | `name=Firefox` | review, then succeeds | missing visible window | `equivalent` |
| `portal.open_uri` | L2 | `uri=https://example.com` | review, adapter dispatch, then incomplete when independent browser observation is unavailable | unsupported URI scheme | `equivalent` |
| `clipboard.write` | L2 | `text=test` | review, then succeeds | empty clipboard text | `equivalent` |
| `browser.open_url` | L1 | `uri=https://example.com` | dispatch recorded; incomplete without browser observation | unsupported URL scheme | `equivalent` |
| `browser.search_web` | L1 | `query=hello` | dispatch recorded; incomplete without browser observation | empty search query | `equivalent` |
| `browser.open_named_target` | L1 | `name=example` | dispatch recorded; incomplete without browser observation | unknown named target | `equivalent` |
| `browser.open_site_search` | L1 | `site=example.com, query=hello` | dispatch recorded; incomplete without browser observation | empty site or query | `equivalent` |
| `media.search` | L1 | `query=song` | durable clarification when dedicated adapter is unavailable | empty media query or unavailable adapter | `equivalent` |
| `media.play` | L1 | `query=song, selection=best_match` | browser fallback recorded; incomplete without observation | empty media query | `equivalent` |
| `media.pause` | L1 | `{}` | durable clarification when dedicated adapter is unavailable | dedicated adapter unavailable | `equivalent` |
| `app.search_history` | L1 | `app=chat, query=hello` | fixture-backed search succeeds | missing fixture or query | `equivalent` |

## Deletion rule and residual debt

- Only rows marked `equivalent` or `obsolete_with_evidence` may be removed in Goal 03.
- The HTTP adapter and its tests are retained despite the unreconciled candidate having deleted them.
- Audit payload field `loop_snapshot_id` remains as a read-compatible optional historical field; it is not a state authority.
- Environment-incomplete browser, Portal, and media outcomes are truthful compatibility results, not fake successes. Real desktop/session evidence is collected during final WSL acceptance when the environment supports it.
- If a caller scan after any deletion finds a production reference, deletion stops and that path returns to `compatibility_missing`.
