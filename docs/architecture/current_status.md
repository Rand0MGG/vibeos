# VibeOS Current Status

Last verified: 2026-07-15

## Supported runtime

VibeOS has one supported-task path:

```text
CLI / HTTP / D-Bus -> CommandService -> TaskApplicationService
                    -> GoalLoop -> registered domain tools -> adapters
```

For the migrated `system.status` and `notification.send` capabilities, the
registered tool is now a strict adapter into `FoundationSliceService`, typed
ports, and the unified SQLAlchemy/Alembic repository. The remaining 17
capabilities retain the compatibility path until Goal 02. See
[`core_foundation.md`](core_foundation.md) for the new/legacy boundary and
migration inventory.

`CapabilityBroker` is a construction and compatibility facade only. It does
not own planning, GoalLoop transitions, execution, adapter calls, acceptance,
review resume, or result projection. `runtime_composition.py` is the explicit
composition root.

Fresh tasks and current-format stored-review resumes use GoalLoop. Historical
`review_kind=plan` records are migrated only when their immutable plan, step,
target, safety-review, capability, policy, and expiry binding is verifiable;
otherwise they fail closed with `legacy_review_unverifiable` and require a
fresh command.

## Safety and persistence

- Capability definitions live in `src/vibeos/capabilities.py`; execution is
  bounded by `StepExecutionService`, a host recipe registry, and registered
  domain tools.
- One SQLite database is authoritative for review compatibility state and the
  new `current_state`, `domain_events`, and `outbox` tables. Claim, release, execution
  completion, input provision, and input consumption are explicit atomic state
  transitions. JSONL is migration input only, never a mutation fallback.
- A persistence failure returns `review_persistence_unavailable` before a
  review-backed side effect is dispatched.
- Attempt history is retained separately from accepted step receipts, so failed
  attempts remain auditable without invalidating a successful retry.

## Verified local baseline

The documented Fedora 44 WSL environment (`rand0mg` and the VibeOS virtual
environment) produced:

```text
python -m ruff check src tests          -> passed
python -m ruff format --check src tests scripts/verify_foundation_dbus.py scripts/verify_wsl_real_actions.py
                                         -> 146 files already formatted
python -m mypy --strict                 -> 0 issues in 35 source files
python scripts/architecture_guard.py    -> ok; 0 violations
python -m pytest -q                     -> 302 passed in 25.54s
vibe capabilities --json                -> exit 0; 19 capabilities
vibe ask "search web for hello" --json --offline --dry-run
                                         -> exit 0; status=dry_run
vibe ask "status" --json --offline       -> exit 0; E0 receipt succeeded
python scripts/verify_foundation_dbus.py -> exit 0; ready; 19 capabilities
python scripts/verify_wsl_real_actions.py
                                         -> exit 0; real CLI/D-Bus E1 succeeded
```

The database lifecycle now applies Alembic and verifies its head plus every
authoritative table before reporting ready; a missing schema cannot pass solely
because WAL and foreign keys are configured. It then rebinds the ReviewStore
compatibility connection, and any failure keeps the supervisor non-ready.

The base WSL D-Bus check still accepts an accurate `unavailable` result when no
notification service is configured. In the configured WSLg environment,
`libnotify` and a temporary real dunst service allowed the Agent and daemon/D-Bus
paths to return E1 succeeded receipts. `dbus-monitor` independently observed two
`Notify` calls and dunst reported two displayed notifications. This is real WSLg
evidence, not a claim about the supported GNOME VM.

The live-provider `open browser` plan smoke validated. One live-provider
`open https://example.com` attempt returned an invalid intent and failed
closed. The deterministic offline URL plan exposed and led to correction of an
older `portal.open_uri`/`browser.open_url` domain mismatch, then validated.

The first 2026-07-16 GNOME VM browser smoke exposed a transport timeout
mismatch: the subprocess allowed the configured 45 seconds while `gdbus`
retained its own shorter default. The CLI failed after about 25 seconds while
the daemon still had one active request and later opened Firefox. The D-Bus
client now passes `VIBEOS_TRANSPORT_TIMEOUT_SECONDS` through `gdbus --timeout`,
with a regression test. After the fix, the same production-daemon request ran
for 67 seconds, returned through D-Bus, and left `active_requests=0`. Firefox was
focused with the requested query, but the page showed a TLS connection failure;
the runtime accurately reported mechanical execution succeeded while semantic
acceptance was indeterminate and `overall_status=incomplete`. This is valid
browser side-effect and failure-reporting evidence, not an end-to-end web-search
pass.

The same VM run exposed and fixed a second production-boundary mismatch: the
live provider represented a notification as `name`/`kind`, while the strict
Goal 01 slice accepts `title`/`body`/`message`. Notification target
canonicalization now maps the generic provider `name` into `body` and removes
the non-contract metadata. The post-fix live-provider request completed in 42
seconds with D-Bus transport, E1 `action_receipt.status=succeeded`,
`acceptance_status=passed`, `overall_status=completed`, and the production
`/usr/bin/notify-send` adapter. `dbus-monitor` independently observed the
`Notify` call, a VMware screenshot showed the notification on the GNOME
desktop, and daemon health reported `active_requests=0`. See the
[Goal 01 GNOME VM evidence report](../archive/vm-evidence/goal01_gnome_vm_acceptance_2026-07-16.md).

The complete earlier evidence, including capability ownership, review transitions,
strict-typing scope, and CI configuration, is in
[`../architecture_completion_final_audit.md`](../architecture_completion_final_audit.md).

## Deliberately separate environment work

WSL is a deterministic development and pre-verification environment. It does
not prove GNOME desktop integration. The 2026-07-16 GNOME VM run now verifies
the user daemon, a real notification, D-Bus notification dispatch, real browser
opening, and honest incomplete reporting when browser content cannot load.
Later desktop goals must still complete the broader GNOME extension, window
control, clipboard, portal, and browser-content checklist. Use
[GNOME VM acceptance](../operations/gnome_vm_acceptance.md) for that boundary
and [the WSL standard](../zh_cn/07_wsl_test_standard.md) for local work.
