# 项目总览

VibeOS 当前是一个面向 Linux 桌面能力的模块化单体 Agent 原型。19 个已注册
能力统一经过持久化任务引擎：

```text
CLI / D-Bus / loopback HTTP / 本地开发入口
  -> CommandService
  -> TaskApplicationService
  -> DurableTaskEngine（纯状态转换）
  -> SQLite Task Store
  -> 注册工具 -> 桌面 adapter
```

任务从创建开始即持久化。目标合同、计划版本、步骤、尝试、等待条件、审批、
澄清、动作 proposal、receipt、证据、终态和 lease 都属于同一个 Task Store。
审计和 trace 只用于诊断，不承担恢复权威。

旧同步任务循环、独立 ReviewStore、loop snapshot、legacy runtime 和 run ledger
实现已删除。D-Bus 是主要控制面；HTTP 作为 loopback-only 薄兼容层保留到 Goal 10，
与 D-Bus 使用同一应用服务和 Task Store，不拥有独立状态。

当前实现详情见：

- [持久化任务引擎](../architecture/durable_task_engine.md)
- [当前状态](../architecture/current_status.md)
- [运行时架构](../architecture/runtime_convergence.md)
- [Goal 03 替代矩阵](../architecture/goal03_replacement_compatibility_matrix.md)
