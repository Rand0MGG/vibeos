# Core Foundation Replacement

Last verified: 2026-07-15

## Implemented scope

Goal 01 replaces the technical foundation used by two production capabilities
without introducing the durable task engine scheduled for Goal 02. The new
foundation is a local modular monolith with these boundaries:

```text
domain        typed state, values, receipts, evidence, events
application   FoundationSliceService and AsyncSupervisor
ports         repository, clock, id, status, notification, lifecycle protocols
adapters      strict contracts, SQLite, tools, D-Bus, HTTP, desktop adapters
composition   compose_foundation() called only by runtime_composition.py
```

`src/vibeos/core/domain` does not import framework or adapter code. The
application layer depends on domain types and ports. Pydantic 2 strict models
validate transport requests and persisted payloads at the adapter boundary;
unknown fields, unsupported versions, invalid enums, and coercion attempts
fail closed.

The architecture guard is machine-readable through
[`architecture_baseline.json`](../../architecture_baseline.json) and
[`scripts/architecture_guard.py`](../../scripts/architecture_guard.py). It
checks layer direction, import cycles, module size, function complexity,
forbidden legacy imports, and the non-increasing legacy-debt baseline. Its test
fixture deliberately introduces a reverse dependency and proves the guard
detects it.

## Production path for the migrated slices

The public compatibility path remains stable while the two migrated tools are
owned by the new foundation:

```text
CLI / D-Bus / thin HTTP adapter
  -> CommandService / TaskApplicationService / GoalLoop compatibility planning
  -> the sole ToolSpec for system.status or notification.send
  -> strict Pydantic adapter contract
  -> FoundationSliceService
  -> typed status/notification ports
  -> typed ActionTransition
  -> SqliteActionRepository atomic commit
```

`system.status` and `notification.send` have no second production ToolSpec,
feature flag, shadow state, or dual-write path. The old notification tool
module was removed, and the old system tool module no longer implements
`system.status`. GoalLoop is still the compatibility task state machine for
the other 17 capabilities until Goal 02; it does not own the migrated slices'
business logic.

| Slice | Effect | New authoritative result | Adapter observation |
| --- | --- | --- | --- |
| `system.status` | E0 | typed action receipt plus capability/portal observation evidence | successful through CLI, HTTP integration, and a real WSL session D-Bus bus |
| `notification.send` | E1 | typed delivery receipt plus adapter evidence | successful with the deterministic integration adapter, the production `notify-send` adapter on configured WSLg, and the live-provider production daemon on a Fedora GNOME Wayland VM; D-Bus returned a succeeded receipt and GNOME displayed the notification |

The frozen legacy-compatibility matrices cover E0 execute/dry-run and E1
sent/unavailable/timeout/dry-run plus missing and blank title/body inputs. Valid
legacy defaults remain compatible: blank titles become `VibeOS`, empty bodies
remain allowed, and the `message` alias retains its fallback behavior. The v1
strict boundary intentionally rejects non-string and over-limit values. It also
omits notification body content from evidence; that security-governed redaction
is compared through a normalized evidence projection rather than weakening the
privacy gate.

The configured WSLg real-action verifier observed two actual `Notify` method
calls, a production `/usr/sbin/notify-send` delivery receipt, and two displayed
notifications reported independently by dunst. This is stronger WSL integration
evidence, but it still does not replace the supported GNOME VM acceptance
boundary.

The 2026-07-16 Fedora GNOME Wayland VM acceptance now closes that boundary for
the Goal 01 E1 slice. A live-provider CLI request traversed the production D-Bus
daemon and returned a typed E1 succeeded receipt, passed acceptance, and a
completed overall result. The production `/usr/bin/notify-send` adapter was
recorded in evidence, `dbus-monitor` independently observed the `Notify` call,
and a VMware screenshot showed the notification. Daemon health returned to zero
active requests. The run also caught and fixed generic provider `name`/`kind`
notification targets before the strict foundation contract.

## Authoritative database

One `CoreDatabase` instance is shared by the runtime composition and the
temporary `ReviewStore` compatibility API. SQLAlchemy 2 Core owns metadata and
transactions; Alembic revision `0001_core_foundation` owns schema creation and
upgrade.

| Table | Authority |
| --- | --- |
| `reviews` | current compatibility review state |
| `review_events` | append-only compatibility review history |
| `current_state` | current typed action outcome |
| `domain_events` | append-only action outcome event |
| `outbox` | same-transaction message intent |
| `schema_migrations` / `alembic_version` | legacy and Alembic migration markers |

Every non-dry-run slice outcome writes `current_state`, `domain_events`, and
`outbox` in one transaction. Fault injection proves all three roll back
together. The database enables foreign keys, WAL, `synchronous=FULL`, and a
bounded busy timeout, and rejects configured network-filesystem paths.

Migration supports an empty database and the real legacy event-only review
shape. Upgrade is idempotent. Before migration, an existing database is
checkpointed and copied to a same-directory backup; a failed migration restores
that backup and removes partial new-database files. Downgrade removes only the
three foundation tables and preserves review data.

Daemon startup owns an explicit migration/readiness gate. The database component
applies Alembic before any transport starts, then requires the current Alembic
head and every authoritative table in addition to the SQLite PRAGMAs. Only after
those checks pass is the ReviewStore compatibility connection rebound. A
migration failure, missing table, wrong revision, or failed rebind leaves the
supervisor failed and prevents request acceptance.

The recovery tests cover concurrent writers, lock waiting, busy timeout,
process termination inside an uncommitted WAL transaction, `quick_check`, and
safe downgrade.

Notification content is not stored in foundation receipt/evidence payloads.
Recorded tool inputs, normal traces, and audit data redact user-content and
secret-shaped fields; canary tests scan the serialized audit, trace, tool
envelope, and new database payloads.

## Daemon lifecycle and transports

`vibed` now has one `AsyncSupervisor` that owns database readiness, the asyncio
HTTP compatibility server, and the D-Bus service on one event loop. Its state
machine is:

```text
stopped -> starting -> ready -> draining -> stopped
                    \-> failed -> stopped
```

Requests are rejected before readiness and after drain begins. Active requests
are allowed to finish. Duplicate start, component startup failure, database
health failure, SIGTERM, drain, and reverse-order component shutdown have
deterministic outcomes and structured health.

`vibed --offline` selects the deterministic local intent broker for WSL and
diagnostic verification without cloud calls. It does not change capability,
policy, repository, or adapter composition.

HTTP remains a thin adapter because there are current callers. It contains no
separate planning or action logic.

| Caller | Current use | Owner | Deletion gate | Latest removal stage |
| --- | --- | --- | --- | --- |
| `src/vibeos/runtime.py` | configured/automatic daemon fallback and compatibility client | runtime transport | D-Bus is the sole supported local transport and fallback users have migrated | Goal 02 |
| `scripts/status_linux_session.sh` | operational `/v1/status` probe | Linux operations | equivalent D-Bus health probe is installed and documented | Goal 02 |
| `scripts/collect_vm_evidence.py` | VM status/evidence collection | VM acceptance | collector reads structured D-Bus/systemd health | Goal 02 |

## Remaining migration inventory

| Legacy boundary | Real production callers | Current owner | Deletion gate | Latest removal stage |
| --- | --- | --- | --- | --- |
| `GoalLoop` | `TaskApplicationService` fresh execution and review resume | task runtime | all 19 capabilities and resumes use the durable task engine | Goal 02 |
| `ReviewStore` API | runtime composition, review/resume/projector services, broker compatibility facade | persistence compatibility | typed unified repositories directly serve review state and all callers migrate | Goal 02 |
| `agent_runtime.py` | no production runtime caller; projection types and isolated historical tests only | compatibility test surface | remaining projections/tests use durable-kernel types | Goal 02 |
| `runtime.py` compatibility transports | CLI runtime selection and the HTTP callers listed above | runtime transport | D-Bus-only local transport with migrated operational callers | Goal 02 |

The ratchet prevents any of these modules from growing beyond the audited
line/complexity baseline and prevents new core modules from importing them.

## Verification

Run the deterministic gates in the configured Fedora WSL environment:

```bash
python -m ruff check src tests
python -m ruff format --check src tests
python -m mypy --strict
python scripts/architecture_guard.py
python -m pytest -q
python scripts/verify_foundation_dbus.py
python scripts/verify_wsl_real_actions.py  # optional WSLg real side effect
```

The D-Bus verifier starts a temporary `vibed --dbus --offline --port 0`, uses
the real WSL user-session bus, checks readiness and 19-capability discovery,
executes E0 and E1, verifies strict unknown-field rejection, confirms creation
of the authoritative database, and then terminates the daemon. If no existing
user bus is available, run it under `dbus-run-session` on a host that provides
that command.

The final Fedora 44 WSL result was: Ruff lint passed, 146 files were already
formatted, strict mypy reported no issues in 35 source files, the architecture
guard reported zero violations, and all 302 tests passed in 25.54 seconds. The
configured WSLg real-action verifier reported E0 and E1 succeeded receipts,
`ready` over the daemon D-Bus transport, two independently observed D-Bus
`Notify` calls, and two displayed notifications reported by dunst.

See [current status](current_status.md) for the latest exact test counts and
[GNOME VM acceptance](../operations/gnome_vm_acceptance.md) for the desktop-only
evidence boundary.
