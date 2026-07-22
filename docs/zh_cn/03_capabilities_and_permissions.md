# 能力注册表与权限模型

准确能力列表以 `vibe capabilities --json` 为准，当前基线为 19 个。风险等级：

- L0：只读观察，可自动执行；
- L1：受限且可审计的低风险动作；
- L2：必须显式批准并绑定具体计划/步骤；
- L3：未知、禁止或超出边界，fail-closed。

批准对象是已经持久化的 task interaction 与安全绑定，不会重新解析原始自然
语言。批准、拒绝和补充信息都以 CAS 转换更新同一 `TaskRun`。重启后绑定保持
不变；安全上下文发生变化时生成新绑定并要求重新批准。

每个能力只有一条生产执行路线：

```text
DurableTaskEngine
  -> DurableActionExecutor
  -> StepExecutionService
  -> CapabilityRecipeRegistry / ToolRegistry
  -> domain tool / adapter
```

Broker 只做构造与兼容 facade，不直接执行桌面副作用。完整动作所有权见
[能力注册表](../architecture/capability_registry.md)。
