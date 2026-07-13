# VibeOS 总览

## 项目边界

VibeOS 是面向 Linux 用户会话的、受限且可审计的任务运行时。它接受自然
语言目标，但模型不能直接调用 shell、原始 D-Bus、任意键鼠控制或未注册的
桌面 API。所有实际动作都必须经过能力注册、计划、风险审查和验收。

## 当前主路径

```text
CLI / HTTP / D-Bus
  -> CommandService
  -> TaskApplicationService
  -> GoalLoop
  -> StepExecutionService
  -> CapabilityRecipeRegistry / ToolRegistry
  -> domain tool / existing adapter
```

`GoalLoop` 是所有支持任务的唯一生产状态机，负责观察、审查、执行、验
证、重试、修复、重规划、暂停和恢复。`CapabilityBroker` 只是构造与兼容门
面，不拥有第二条任务或执行路径。

## 重要状态语义

调用方应同时查看：

- `execution_status`：动作是否执行；
- `acceptance_status`：证据是否证明任务达成；
- `overall_status`：任务对外最终状态；
- `run` 与 `attempts`：计划执行过程与历史尝试。

SQLite 是 review 当前状态的唯一权威；JSONL 只可用作一次性迁移输入。旧
`review_kind=plan` 审批只有在计划、步骤、目标、安全审查和策略绑定均可
验证时才会迁移，否则会失败关闭并要求重新下达命令。

## 当前验证边界

Fedora WSL 已验证静态检查、263 项测试和离线 dry-run。真实 GNOME Wayland
会话仍需单独验证 daemon、扩展、窗口控制、portal、剪贴板、通知和浏览器
观测。详细结果见 [当前状态](../architecture/current_status.md)。
