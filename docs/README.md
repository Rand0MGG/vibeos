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
8. [Runtime architecture](architecture/runtime_convergence.md) — production
   ownership and the GoalLoop execution path.
9. [Core foundation replacement](architecture/core_foundation.md) — Goal 01
   module boundaries, database, lifecycle, migrated slices, and deletion gates.
10. [Capability registry](architecture/capability_registry.md) — current
   capability surface and review levels.
11. [WSL test standard](zh_cn/07_wsl_test_standard.md) — the required Fedora
   WSL workflow. This is the primary local verification guide.

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
| Architecture completion | [Final audit](architecture_completion_final_audit.md) | refactor evidence, state machine, execution audit, CI, and deferred GNOME checks |
| Architecture completion | [Master contract](architecture_completion_master_goal.md) | immutable historical acceptance contract; do not edit as a status document |
| Runtime | [Current status](architecture/current_status.md) | present scope and local verification result |
| Runtime | [Runtime convergence](architecture/runtime_convergence.md) | dependency boundaries and production paths |
| Runtime foundation | [Core foundation replacement](architecture/core_foundation.md) | Goal 01 layers, schema, lifecycle, slices, compatibility callers, and migration gates |
| Runtime | [Capability registry](architecture/capability_registry.md) | action ownership, risk, and review policy |
| Operations | [WSL test standard](zh_cn/07_wsl_test_standard.md) | repeatable Fedora WSL test environment |
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
