# VibeOS Current Status

Last verified: 2026-07-12

## Supported runtime

VibeOS has one supported-task path:

```text
CLI / HTTP / D-Bus -> CommandService -> TaskApplicationService
                    -> GoalLoop -> registered domain tools -> adapters
```

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
- SQLite is the authoritative review store. Claim, release, execution
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
python -m ruff format --check src tests -> 122 files already formatted
python -m mypy --strict                 -> 0 issues in 16 core files
python -m pytest -q                     -> 263 passed in 11.90s
vibe capabilities --json                -> exit 0; 19 capabilities
vibe ask "search web for hello" --json --offline --dry-run
                                         -> exit 0; status=dry_run
```

The complete evidence, including capability ownership, review transitions,
strict-typing scope, and CI configuration, is in
[`../architecture_completion_final_audit.md`](../architecture_completion_final_audit.md).

## Deliberately separate environment work

WSL is a deterministic development and pre-verification environment. It does
not prove GNOME desktop integration. A real GNOME Wayland environment must
still verify the user daemon, GNOME extension, D-Bus window control, real app
opening, clipboard and notification delivery, portal navigation, and browser
observation. Use [GNOME VM acceptance](../operations/gnome_vm_acceptance.md)
for that boundary and [the WSL standard](../zh_cn/07_wsl_test_standard.md) for
local work.
