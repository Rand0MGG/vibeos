# 历史版本与原始文档索引

这份索引的作用是把原来按时间、按版本散落的文档重新映射回“按模块阅读”的路径。

原则：

- 历史文档原文保留
- 新文档只负责组织阅读，不伪造历史
- 当历史草案与当前实现冲突时，以当前代码和 `docs/current_status.md` 为准

## 1. 总体状态与现行范围

优先阅读：

- [current_status.md](/E:/codex_project/vibeos/docs/current_status.md:1)
- [README.md](/E:/codex_project/vibeos/README.md:1)

对应模块：

- 总体架构
- 当前已实现能力
- 本地验证与 VM 验证范围

## 2. 规划与架构演进

历史来源：

- [vibeos_v0_linux_session_agent_plan.md](/E:/codex_project/vibeos/docs/vibeos_v0_linux_session_agent_plan.md:1)
- [vibeos_v0_2_goal.md](/E:/codex_project/vibeos/docs/vibeos_v0_2_goal.md:1)
- [v0.3_structured_task_agent_plan.md](/E:/codex_project/vibeos/docs/v0.3_structured_task_agent_plan.md:1)
- [v0.4_goal_for_codex.md](/E:/codex_project/vibeos/docs/v0.4_goal_for_codex.md:1)
- [v0.5_goal_for_codex.md](/E:/codex_project/vibeos/docs/v0.5_goal_for_codex.md:1)
- [v0.6_goal_for_codex.md](/E:/codex_project/vibeos/docs/v0.6_goal_for_codex.md:1)

建议理解顺序：

1. v0 Linux session 初始计划
2. v0.2 范围与目标
3. v0.3 structured task plan
4. v0.4 目标
5. v0.5 目标
6. v0.6 目标

对应当前模块：

- [01_overview.md](/E:/codex_project/vibeos/docs/zh_cn/01_overview.md:1)
- [02_planning_and_execution.md](/E:/codex_project/vibeos/docs/zh_cn/02_planning_and_execution.md:1)

## 3. 权限与能力

历史来源：

- [capability_registry.md](/E:/codex_project/vibeos/docs/capability_registry.md:1)
- [permission_review_layer.md](/E:/codex_project/vibeos/docs/permission_review_layer.md:1)

对应当前模块：

- [03_capabilities_and_permissions.md](/E:/codex_project/vibeos/docs/zh_cn/03_capabilities_and_permissions.md:1)

## 4. Linux 会话与部署

历史来源：

- [linux_session_doctor.md](/E:/codex_project/vibeos/docs/linux_session_doctor.md:1)
- [deepseek_api_setup.md](/E:/codex_project/vibeos/docs/deepseek_api_setup.md:1)
- [linux_vm_permission_test_checklist.md](/E:/codex_project/vibeos/docs/linux_vm_permission_test_checklist.md:1)
- [vm_acceptance_evidence.md](/E:/codex_project/vibeos/docs/vm_acceptance_evidence.md:1)

对应当前模块：

- [04_linux_session_and_daemon.md](/E:/codex_project/vibeos/docs/zh_cn/04_linux_session_and_daemon.md:1)
- [05_vm_install_upgrade_test_runbook.md](/E:/codex_project/vibeos/docs/zh_cn/05_vm_install_upgrade_test_runbook.md:1)

## 5. 版本阶段性状态记录

阶段性记录文档：

- [v0.3_acceptance_status_2026-06-02.md](/E:/codex_project/vibeos/docs/v0.3_acceptance_status_2026-06-02.md:1)
- [v0.3_completion_audit_2026-06-02.md](/E:/codex_project/vibeos/docs/v0.3_completion_audit_2026-06-02.md:1)
- [v0.3_vm_readiness_2026-06-02.md](/E:/codex_project/vibeos/docs/v0.3_vm_readiness_2026-06-02.md:1)
- [v0.3_vm_test_plan_2026-06-02.md](/E:/codex_project/vibeos/docs/v0.3_vm_test_plan_2026-06-02.md:1)
- [v0.4_implementation_status_2026-06-02.md](/E:/codex_project/vibeos/docs/v0.4_implementation_status_2026-06-02.md:1)
- [vm_known_issues_2026-06-01.md](/E:/codex_project/vibeos/docs/vm_known_issues_2026-06-01.md:1)

用途：

- 追踪某个阶段的具体验收标准
- 找历史问题与判断依据
- 对照当前实现确认哪些问题已经解决，哪些仍需真实 VM 再验证

## 6. 建议维护方式

以后新增文档建议遵守下面的规则：

- 当前有效说明优先放进 `docs/zh_cn/` 的模块文档
- 一次性阶段总结或实验记录继续按日期单独保留
- 目标文档如 `v0.x_goal_for_codex.md` 保留为版本里程碑，不直接充当最终用户手册
- README 只保留高层入口，不再堆积大量重复操作说明
