# VibeOS Documentation

This directory separates current operating facts from completed work and
historical proposals. When documents disagree, prefer current source code, the
current verification record, and the documents in **Current documentation**.
Archived material is retained for traceability; it is not an implementation
instruction.

## Start here

1. [Product charter](product/product_charter.md) — mission, target users,
   product boundaries, principles, and north-star outcome.
2. [Strategic goals](product/strategic_goals.md) — the outcome hierarchy and
   sequence from the current prototype to a trusted Linux personal agent.
3. [Agent system framework](product/agent_system_framework.md) — the target
   control plane, execution hierarchy, privilege review, rollback, secrets,
   durable tasks, and model routing.
4. [Agent-native direction decision](product/decisions/0001-agent-native-direction.md)
   — accepted product decisions and unresolved design questions.
5. [Implementation foundation decision](product/decisions/0002-implementation-foundation.md)
   — accepted runtime, persistence, sandbox, privilege, secret, and migration choices.
6. [Agent-native implementation goals](goals/agent_native/README.md) — nine
   dependency-ordered, directly executable Codex goal contracts.
7. [Current status](architecture/current_status.md) — supported scope, exact
   verification evidence, and remaining environment limits.
8. [Runtime architecture](architecture/runtime_convergence.md) — durable task
   ownership and the single production execution path.
9. [Durable task engine](architecture/durable_task_engine.md) — Goal 02 state,
   persistence, recovery, controls, deletion evidence, and benchmark.
10. [Core foundation replacement](architecture/core_foundation.md) — Goal 01
   foundation and the Goal 02 handoff.
11. [Capability registry](architecture/capability_registry.md) — current
   capability surface and review levels.
12. [Goal 03 compatibility matrix](architecture/goal03_replacement_compatibility_matrix.md)
   — public-entry, migration, deletion, and 19-capability evidence.
13. [WSL test standard](zh_cn/07_wsl_test_standard.md) — the required Fedora
    WSL workflow. This is the primary local verification guide.
14. [Goal 03 final acceptance](architecture/goal03_final_acceptance_2026-07-17.md)
    — independent comparison, quality gates, WSL evidence, and rollback rehearsal.
15. [Goal 04 system-service acceptance](architecture/goal04_system_service_acceptance_2026-07-22.md)
    — fixed systemd fixture, crash/fault matrix, WSL evidence, and explicit external gaps.

## Current documentation

| Area | Document | Use it for |
| --- | --- | --- |
| Product | [Product documentation](product/README.md) | authority rules, reading order, and the product-document backlog |
| Product | [Product charter](product/product_charter.md) | mission, users, problem, boundaries, principles, and north-star result |
| Product | [Strategic goals](product/strategic_goals.md) | outcome hierarchy, dependencies, priorities, and governance |
| Product architecture | [Agent system framework](product/agent_system_framework.md) | target components, control flow, risk tiers, privilege review, rollback, secrets, and model routing |
| Product decision | [Agent-native direction](product/decisions/0001-agent-native-direction.md) | accepted product choices, consequences, constraints, and open questions |
| Technical decision | [Implementation foundation](product/decisions/0002-implementation-foundation.md) | modular-monolith stack, persistence, model, sandbox, privilege, secret, desktop, and migration choices |
| Implementation plan | [Agent-native Codex goals](goals/agent_native/README.md) | alignment audit, dependency order, stage goals, acceptance gates, and handoff requirements |
| Historical completion | [Goal 01-era final audit](architecture_completion_final_audit.md) | superseded evidence retained for traceability |
| Architecture completion | [Master contract](architecture_completion_master_goal.md) | immutable historical acceptance contract; do not edit as a status document |
| Runtime | [Current status](architecture/current_status.md) | present scope and local verification result |
| Runtime | [Runtime convergence](architecture/runtime_convergence.md) | dependency boundaries and production paths |
| Runtime | [Durable task engine](architecture/durable_task_engine.md) | Goal 02 state machine, persistence, crash recovery, controls, and benchmark |
| Runtime foundation | [Core foundation replacement](architecture/core_foundation.md) | Goal 01 layers, schema, lifecycle, slices, compatibility callers, and migration gates |
| Runtime | [Capability registry](architecture/capability_registry.md) | action ownership, risk, and review policy |
| Runtime | [Goal 03 compatibility matrix](architecture/goal03_replacement_compatibility_matrix.md) | replacement status, public contracts, and deletion evidence |
| Runtime | [Goal 03 final acceptance](architecture/goal03_final_acceptance_2026-07-17.md) | final refs, independent environments, WSL gates, and rollback evidence |
| Operations | [WSL test standard](zh_cn/07_wsl_test_standard.md) | repeatable Fedora WSL test environment |
| Operations | [Goal 03 rollback](operations/goal03_reconciliation_and_rollback.md) | immutable refs, database boundary, revert order, and rehearsal |
| Operations | [Goal 04 fixed service](operations/goal04_system_service_runbook.md) | install, prepare, run, resume, and stop the fixed systemd fixture |
| Operations | [Linux session doctor](operations/linux_session_doctor.md) | diagnose GNOME/daemon integration readiness |
| Operations | [GNOME VM acceptance](operations/gnome_vm_acceptance.md) | checks that cannot be validated in WSL or CI |
| Operations | [DeepSeek setup](operations/deepseek_api_setup.md) | optional model-provider configuration |
| Product history | [Personal-agent vision](reference/vibeos_personal_agent_vision.md) | early long-term thinking; use the product charter for current direction |
| Chinese guide | [中文文档总览](zh_cn/README.md) | Chinese-language current documentation |

## Archive policy

[`archive/`](archive/README.md) contains older Codex goals, phase logs, VM
evidence, and research drafts. The files remain readable and retain their
original claims, but their status is historical. Do not execute an archived
goal or treat an archived test count as current without reconciling it against
the documents above.
