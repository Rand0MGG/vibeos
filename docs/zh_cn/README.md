# VibeOS 中文文档总览

本目录只保留与当前实现相符的中文说明。历史版本目标、阶段报告和旧 VM
证据已移入 [`../archive/`](../archive/README.md)，保留供追溯但不应作为
当前操作指令。

建议阅读顺序：

1. [产品章程](../product/product_charter.md) — 为什么做、为谁做和产品边界
2. [战略目标](../product/strategic_goals.md) — 未来必须取得的结果和优先顺序
3. [Agent 总体系统框架](../product/agent_system_framework.md) — 目标架构、动作层、
   提权审核、事务回滚、Secret Broker、长期任务和模型路由
4. [Agent-native 方向决策](../product/decisions/0001-agent-native-direction.md) —
   已确认的产品取舍和待验证问题
5. [实施技术底座决策](../product/decisions/0002-implementation-foundation.md) —
   模块化单体、持久化、任务、sandbox、提权、秘密和桌面技术路线
6. [Agent-native 实施计划](../goals/agent_native/README.md) — 九份可直接交给
   Codex 的阶段 Goal、依赖顺序和验收门禁
7. [项目总览](01_overview.md)
8. [规划、执行与验收](02_planning_and_execution.md)
9. [能力与权限](03_capabilities_and_permissions.md)
10. [Linux 会话与 daemon](04_linux_session_and_daemon.md)
11. [WSL 测试标准](07_wsl_test_standard.md) — 当前本地验证的首选入口
12. [历史索引](06_history_and_source_index.md)
13. [Goal 01 核心底座实现](../architecture/core_foundation.md) — 新分层、统一
    数据库、daemon 生命周期、两个迁移切片与旧边界删除门禁

当前代码与验收结论的英文权威入口：

- [当前状态](../architecture/current_status.md)
- [运行时架构](../architecture/runtime_convergence.md)
- [最终验收审计](../architecture_completion_final_audit.md)
- [完整文档导航](../README.md)
