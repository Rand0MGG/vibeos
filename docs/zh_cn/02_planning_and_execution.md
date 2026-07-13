# 规划、执行与验收

## 从输入到任务

支持任务从理解与目标合成开始，受宿主注册的 domain、route 和 capability
约束，形成经过验证的 `TaskPlan`。随后 GoalLoop 对每个步骤执行：

```text
观察 -> 步骤安全审查 -> 注册工具执行 -> 后观察 -> 验证/验收
```

失败不是字符串分支，而是结构化分类后进入有限的重试、修复、重规划、请求
用户补充信息或停止。当前格式的 review 恢复也回到同一个 GoalLoop；不会
另起旧 runtime 或重放已完成步骤。

## 一次任务与多次尝试

- `run` 表示一次用户目标的完整执行生命周期。
- `attempt` 表示某个计划/路线的具体尝试，记录观察、执行、失败分类和恢复
  决策。
- 所有失败尝试都会保留用于审计；最终验收只接收每个已完成步骤的当前已接受
  结果。因此成功重试不会被旧失败回执污染。

## 何时算完成

工具返回成功不等于任务完成。验收服务综合后置观察、verifier 和语义验收，
再产生 `execution_status`、`acceptance_status` 与 `overall_status`。浏览器路
径尤其要区分“已请求导航”与“已观察到目标页面或查询”。

更多责任边界见 [运行时架构](../architecture/runtime_convergence.md)，完整
执行审计见 [最终验收审计](../architecture_completion_final_audit.md)。
