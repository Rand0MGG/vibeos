# VibeOS Current Status

Last updated: 2026-07-11

## Maintainability refactor status

- `CommandService` is the transport-neutral command ingress; the broker's
  `handle()` method delegates to it.
- `GoalLoop` remains the only supported-task state machine and now depends on
  named planning, observation, review, execution, acceptance, and recovery
  ports rather than a public callback bundle.
- Domain tool registrations are owned by `src/vibeos/tools/`; broker assembly
  preserves the existing capability IDs and adapters without embedding domain
  handlers.
- Review state is authoritative in SQLite current-state rows with versioned
  atomic claim/release/consume transitions. JSONL and event-only SQLite data
  are migration sources and `review_events` remains audit history.
- The dead fresh-task v0.6 runtime bridge was removed. Historical persisted
  `review_kind=plan` records remain a compatibility-resume-only path and are
  not used by fresh tasks.
- `.github/workflows/test.yml` is configured for deterministic Python 3.11
  checks on `push` and `pull_request`; no GitHub run is claimed yet.

## Runtime Convergence

- supported-task execution now uses `GoalLoop` as the sole default orchestration path;
- the legacy Broker-owned task loop and its `VIBEOS_ENABLE_GOAL_LOOP` split-path flag have been removed;
- review state is durable in local SQLite, with atomic in-process claim/release/consume transitions for the multi-threaded daemon;
- normal traces omit raw user/provider content, while debug artifacts remain credential-redacted and bounded;
- see [runtime_convergence.md](runtime_convergence.md) for the current ownership boundaries and verification coverage.

## In Progress

- v0.8 foundation work has started:
  - typed primary-understanding artifacts are now emitted on the planning path
  - primary understanding now supports a bounded provider layer for first-pass task/chat/mixed/clarification/rejection analysis, while still preserving host-owned normalization and uncertainty metadata
  - clarification generation now supports a bounded provider layer and persists clarification question metadata on the primary understanding artifact
  - host-generated candidate sets and bounded route-decision artifacts are now emitted on the planning path, and route selection now supports bounded provider-driven choice among host-generated candidates
  - goal synthesis now supports bounded provider-driven synthesis on top of host-owned analysis and capability/domain hints, while preserving `source_understanding_id` traceability
  - host-owned goal-synthesis normalization now accepts compact provider shapes such as `type/domain_id/capability_id` and maps them back into bounded `goal_type/candidate_domain_ids/required_capability_ids`
  - v0.6/v0.7 runtime strategy selection now emits structured `strategy_decision_id` artifacts with provider/model/fallback metadata
  - the runtime now supports bounded strategy-selection providers so host-owned eligible candidates can be ranked or selected by a constrained provider instead of a hard-coded scorer only
  - planning payloads, trace bundles, and audit records can now carry `understanding_id`, `candidate_set_id`, and `selected_route_decision_id`
  - task-plan replanning now emits structured `replan_decision_id` artifacts, preserves `understanding_id` continuity on the real replanning path, and supports bounded provider-driven selection among host-generated replanning options
  - when later planning analysis diverges from the earlier primary understanding, the runtime now emits explicit `UnderstandingRefinement` / `UnderstandingSupersession` artifacts instead of silently replacing the prior understanding basis
  - run-scoped traces now record structured understanding-transition events with artifact ids, source artifact ids, primary-understanding linkage, and changed semantic fields
  - task-plan acceptance now emits structured `semantic_summary_id` and `semantic_acceptance_decision_id` artifacts and supports bounded provider-driven semantic summary / decision stages on top of host-owned hard facts
  - ambiguous deictic site requests such as `open that site we discussed yesterday` now stop in clarification instead of attempting execution
  - the test harness now forces deterministic local providers by default so host-specific `.env` model configuration does not make WSL and Windows test results diverge
  - run-scoped trace summaries now expose explicit model-usage accounting fields including `full_context_call_count`, `model_reparse_count`, `artifact_reuse_count`, `semantic_summary_cache_hit_count`, `escalation_count`, and `model_call_kinds`
- v0.8 is not complete yet:
  - goal synthesis, route selection, strategy selection, replanning, and semantic acceptance now have bounded provider hooks, but the default shipped behavior still runs on deterministic fallback in local tests rather than real model-backed providers
  - clarification generation now has a bounded provider hook, but the default shipped behavior still runs on deterministic fallback in local tests rather than a real model-backed clarification provider
  - primary understanding now has a bounded provider hook, but the default shipped behavior still runs on deterministic fallback in local tests rather than a real model-backed understanding provider

## Implemented

- historical v0.6 runtime foundations:
  - `agent_runtime`, `strategy`, `tool_protocol`, and `run_ledger` remain to
    read older runtime-shaped data and produce compatible result projections.
  - fresh supported tasks do **not** enter the former broker runtime bridge;
    their `goal_runtime`, strategy, and ledger fields are pure projections of
    the CommandService/GoalLoop run.
  - only persisted legacy `review_kind=plan` records use the isolated
    compatibility resume path; its dry-run behavior is regression-tested and
    never consumes approval.
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
  - request-scoped browser evidence separates requested navigation from observed browser state
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

Latest local verification commands, run on 2026-07-10:

```powershell
python -m pytest -q
vibe doctor --json
vibe capabilities --json
vibe ask "search web for hello" --json --offline --dry-run
```

Expected current result:

```text
pytest: 237 passed in the configured Fedora 44 WSL environment (2026-07-11)
doctor: overall warn, 0 failures (desktop-session integration is not configured in WSL)
capabilities: 19 registered actions; L0/L1/L2/L3 policy boundary intact
offline dry-run: completed locally with overall_status=dry_run and no provider request
```

Additional targeted suites run after the run-loop and transport changes:

```powershell
python -m pytest tests/test_runtime.py tests/test_broker_task_plans.py tests/test_v04_domain_architecture.py -q
python -m pytest tests/test_broker.py tests/test_cli.py tests/test_daemon.py tests/test_v05_supported_task_migration.py tests/test_goal_synthesizer.py tests/test_debug_trace.py tests/test_task_plan_boundaries.py tests/test_vm_evidence.py tests/test_capabilities.py -q
```

Additional browser-evidence regression run after separating requested navigation from observed success:

```powershell
python -m pytest tests/test_acceptance_engine.py tests/test_broker_task_plans.py tests/test_v04_domain_architecture.py tests/test_debug_trace.py tests/test_goal_synthesizer.py tests/test_v05_supported_task_migration.py -q
```

Additional v0.6 runtime-slice verification:

```powershell
python -m pytest tests/test_v06_agent_runtime.py -q
```

Historical direct bridge checks from 2026-06-09:

```powershell
$env:PYTHONPATH='E:\codex_project\vibeos\src'; python -m vibeos.cli doctor --json
$env:PYTHONPATH='E:\codex_project\vibeos\src'; python -m vibeos.cli ask "search web for hello" --json --offline --dry-run
```

Historical result:

```text
python -m pytest tests/test_v06_agent_runtime.py -q -> 15 passed
python -m pytest -q -> 276 passed at that point in time
python scripts/verify_local.py -> overall ok
python -m vibeos.cli doctor --json -> overall warn on Windows host, no failures
python -m vibeos.cli ask "search web for hello" --json --offline --dry-run -> structured dry-run output with v0.6 goal_runtime, strategy_candidates, run_ledger, and passed verification evidence
```

## Separate Linux VM Integration Phase

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

## Codex Completion Criteria

For the v0.6 Codex-owned scope, this milestone is complete when:

1. Local verification passes.
2. `pytest -q` passes locally.
3. The deterministic v0.6 minimal vertical slice passes end to end with local fixtures.
4. Session runtime state, strategy candidates, selected strategy, run ledger, and environment profile are inspectable through local broker and CLI JSON surfaces.
5. Review-gated runtime paths preserve the same goal runtime across `review_required`, `approve --dry-run`, and approved execution.
6. Local completion does not depend on VM-only evidence, live network access, real browser state, or real desktop side effects.

Linux VM checks remain a separate user-run integration phase and are not part of the v0.6 Codex completion gate.
