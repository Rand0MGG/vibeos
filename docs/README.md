# VibeOS Documentation

This directory separates current operating facts from completed work and
historical proposals. When documents disagree, prefer current source code, the
current verification record, and the documents in **Current documentation**.
Archived material is retained for traceability; it is not an implementation
instruction.

## Start here

1. [Current status](architecture/current_status.md) — supported scope, exact
   verification evidence, and remaining environment limits.
2. [Runtime architecture](architecture/runtime_convergence.md) — production
   ownership and the GoalLoop execution path.
3. [Capability registry](architecture/capability_registry.md) — current
   capability surface and review levels.
4. [WSL test standard](zh_cn/07_wsl_test_standard.md) — the required Fedora
   WSL workflow. This is the primary local verification guide.

## Current documentation

| Area | Document | Use it for |
| --- | --- | --- |
| Architecture completion | [Final audit](architecture_completion_final_audit.md) | refactor evidence, state machine, execution audit, CI, and deferred GNOME checks |
| Architecture completion | [Master contract](architecture_completion_master_goal.md) | immutable historical acceptance contract; do not edit as a status document |
| Runtime | [Current status](architecture/current_status.md) | present scope and local verification result |
| Runtime | [Runtime convergence](architecture/runtime_convergence.md) | dependency boundaries and production paths |
| Runtime | [Capability registry](architecture/capability_registry.md) | action ownership, risk, and review policy |
| Operations | [WSL test standard](zh_cn/07_wsl_test_standard.md) | repeatable Fedora WSL test environment |
| Operations | [Linux session doctor](operations/linux_session_doctor.md) | diagnose GNOME/daemon integration readiness |
| Operations | [GNOME VM acceptance](operations/gnome_vm_acceptance.md) | checks that cannot be validated in WSL or CI |
| Operations | [DeepSeek setup](operations/deepseek_api_setup.md) | optional model-provider configuration |
| Product intent | [Personal-agent vision](reference/vibeos_personal_agent_vision.md) | long-term direction, not current capability scope |
| Chinese guide | [中文文档总览](zh_cn/README.md) | Chinese-language current documentation |

## Archive policy

[`archive/`](archive/README.md) contains older Codex goals, phase logs, VM
evidence, and research drafts. The files remain readable and retain their
original claims, but their status is historical. Do not execute an archived
goal or treat an archived test count as current without reconciling it against
the documents above.
