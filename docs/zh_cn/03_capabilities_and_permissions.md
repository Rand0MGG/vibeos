# 能力注册表与权限模型

能力注册表位于 `src/vibeos/capabilities.py`，运行时可用的准确列表应以以下
命令为准：

```bash
vibe capabilities --json
```

当前注册动作按风险分层：

- L0 自动观察：`app.list`、`system.status`、`window.list`；
- L1 有审计的受限动作：应用、窗口、浏览器、通知、应用内搜索和媒体动作；
- L2 必须存储审批：`clipboard.write`、`portal.open_uri`、`window.close`；
- L3 拒绝：未知、未注册或不符合目标约束的请求。

用户批准的是存储的、已验证绑定的 review，而不是重新解析自然语言。SQLite
状态机使用显式转换：审批后先原子 claim 为 `executing`；成功完成才 consumed，
失败需要显式 release；补充输入只能从 `pending -> provided -> consumed`。

每个动作都只有一条生产执行路线：GoalLoop 经 `StepExecutionService`、宿主
配方和 `ToolRegistry` 到 domain tool。Broker 不直接调用 adapter。完整动作
表和实现所有权见 [能力注册表](../architecture/capability_registry.md)。
