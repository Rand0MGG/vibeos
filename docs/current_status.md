# VibeOS Current Status

Last updated: 2026-06-06

## Implemented

- v0.5 planning architecture:
  - typed goal synthesis layer
  - explicit domain packs for `apps`, `window_management`, `clipboard`, `notification`, `system_observation`, `browser`, and `media`
  - removal of the legacy single-intent main path for supported tasks
  - explicit post-execution acceptance engine
  - bounded `run -> attempt -> classify -> retry/replan -> exit` loop for supported tasks
  - structured `debug_trace` with model/provider exchange visibility
  - public `execution_status`, `acceptance_status`, and `overall_status`
  - public `run` and `attempts` metadata for task-plan execution paths
- Natural-language command path:
  - `vibe ask`
  - OpenAI-compatible model adapter
  - configured broker on the normal planning path
  - local rule parser for missing-model setups and `--offline` mode
  - explicit provider-failure surfacing instead of silent rule-parser fallback
  - web-like `open/打开` targets prefer browser semantics over eager `app.open`
  - bounded semantic replanning for failed supported-task routes
  - no raw keyword rejection for non-imperative mentions of words such as `delete`
  - DeepSeek `.env` configuration
- Capability broker:
  - fixed action allowlist
  - centralized capability registry
  - no arbitrary shell execution
  - no raw D-Bus path execution from model output
- Permission review layer:
  - L0 observe-only automatic execution
  - L1 low-risk automatic execution with audit
  - L2 medium-risk persistent `review_id`
  - L2 review expiration with configurable TTL
  - `vibe approve <review_id>` approval flow
  - one-time approval consumption after real execution attempts
  - `vibe reviews reject <review_id>` rejection flow
  - `vibe reviews pending` review inspection
  - L3 rejection
  - target-level constraints for URI scheme, clipboard content, notification text, and app/window targets
- Linux session adapters:
  - `.desktop` app registry
  - XDG portal status and URI open adapter
  - GNOME Shell extension bridge for window list/focus/minimize/maximize/close
  - attempt-scoped browser postcondition context bound to `run_id` / `attempt_id`
  - shared browser evidence for verifier and acceptance
  - GNOME Shell metadata declares versions 45-50
  - notification adapter
  - clipboard write adapter
- Operational tooling:
  - `vibe doctor`
  - `vibe capabilities`
  - `vibe reviews pending`
  - `vibe reviews reject`
  - daemon-required runtime selection for VM acceptance
  - structured daemon transport failures instead of raw runtime exceptions
  - `scripts/install_linux_session.sh` with daemon `.env` wiring
  - `scripts/run_vm_smoke_tests.sh`
  - `scripts/collect_vm_evidence.py`
  - `scripts/status_linux_session.sh`
  - `scripts/uninstall_linux_session.sh`
- Documentation:
  - DeepSeek setup
  - permission review model
  - capability registry
  - Linux session doctor
  - Linux VM permission test checklist
  - VM acceptance evidence workflow
  - v0 Linux session agent plan

## Local Verification

Run on the current development machine:

```powershell
conda activate vibeos
python scripts/verify_local.py
```

The local verification checks:

- unit tests and deterministic v0.5 planner coverage
- `vibe doctor --json`
- `vibe capabilities --json`
- `scripts/collect_vm_evidence.py` safe mode
- L1 dry-run command
- L2 `review_required`
- `vibe reviews pending --json`
- `vibe approve <review_id> --dry-run`
- unit-tested one-time approval consumption and rejection
- unit-tested review expiration
- L3 rejection

On Windows, `vibe doctor` is expected to report `overall: warn` because GNOME Wayland, D-Bus session services, XDG portal, and GNOME Shell extension are not available.

Latest local verification command:

```powershell
python scripts/verify_local.py
```

Expected current result:

```text
overall: ok
pytest: 177 passed
doctor: overall warn on Windows
capabilities: ok
L1 dry-run: ok
L2 review_required: ok
reviews pending: ok
approve review dry-run: ok
one-time approval/reject tests: ok
review expiration tests: ok
L3 rejection: ok
VM evidence safe mode: ok
```

Additional targeted suites run after the run-loop and transport changes:

```powershell
python -m pytest tests/test_runtime.py tests/test_broker_task_plans.py tests/test_v04_domain_architecture.py -q
python -m pytest tests/test_broker.py tests/test_cli.py tests/test_daemon.py tests/test_v05_supported_task_migration.py tests/test_goal_synthesizer.py tests/test_debug_trace.py tests/test_task_plan_boundaries.py tests/test_vm_evidence.py tests/test_capabilities.py -q
```

## Still Requires Linux VM Verification

The following items cannot be proven from the current Windows host:

- `vibed.service` actually starts under `systemd --user`
- GNOME Shell loads `vibeos@local`
- `org.vibeos.Shell.ListWindows` responds over D-Bus
- `window.focus`, `window.minimize`, `window.maximize`, and `window.close` affect real windows
- `notification.send` displays a real desktop notification
- `portal.open_uri` opens a real browser/app through the session
- `clipboard.write` writes to the real Linux desktop clipboard
- browser window observation reflects the real focused browser title and any desktop-specific error pages

The VM-side evidence path is now stricter than before:

- `collect_vm_evidence.py --real` requires daemon transport instead of silently falling back to `local`
- the report now captures `systemctl --user is-active vibed.service`
- the report now captures D-Bus introspection and `org.vibeos.Agent.Status()`
- the report now captures HTTP `/v1/status`

Use:

```bash
chmod +x scripts/*.sh
./scripts/install_linux_session.sh
vibe doctor
./scripts/run_vm_smoke_tests.sh
python scripts/collect_vm_evidence.py --real
```

## Completion Criteria

This milestone should be treated as complete only after:

1. Local verification passes.
2. Linux VM `vibe doctor` shows no `fail` checks.
3. The VM smoke test script completes.
4. At least one real L1 desktop action changes the GNOME session.
5. At least one L2 action creates a `review_id` and can be approved by ID.
6. Approved L2 review ids cannot be reused after consumption.
7. Expired L2 review ids cannot be approved or rejected.
8. Pending L2 reviews can be rejected and rejected ids cannot be approved.
9. Pending reviews are inspectable through CLI and service APIs.
10. A real VM evidence report from `python scripts/collect_vm_evidence.py --real` has `overall: ok`.
11. Audit log entries show utterance, intent, review id, risk level, decision, and result.
12. Supported task plans do not select `legacy_*_route` or `legacy_intent_bridge` on the main path.
13. Browser acceptance can fail even when launch/execution succeeded.
14. Supported-task runs preserve bounded attempt history across retry/replan outcomes.
15. Daemon transport failures return structured JSON results instead of raw Python tracebacks.
