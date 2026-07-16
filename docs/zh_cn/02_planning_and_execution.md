# 规划、执行与验收

新命令先创建 `GoalContract` 和 `TaskRun`，再调用规划服务。规划产生版本化
`PlanRevision` 与步骤；存在实质歧义时进入 `awaiting_clarification`，不会猜测
目标、范围或现实后果。

每个步骤的固定顺序是：

1. 预观察；
2. 安全审查，必要时持久化等待用户批准；
3. 获取带 fencing token 的短 lease；
4. 在 I/O 前提交稳定幂等键和 `ActionProposal`；
5. 通过注册工具执行；
6. 提交 `ActionReceipt` 与 `EvidenceBundle`；
7. 验证、接受或进入 retry/replan/clarification；
8. 只有证据充分时提交明确终态。

调度是 at-least-once，不宣称 exactly-once。进程在外部动作成功、receipt 提交
前崩溃时，恢复 worker 先对账；无法证明结果的副作用会暂停为 unknown，不会
盲目重放。L0 只读动作可使用同一 proposal/attempt 安全重试。

等待时间是数据库中的到期条件，不使用进程 sleep。daemon 重启后扫描到期和
可恢复任务继续执行。任务结果仍保留公开的 `execution_status`、
`acceptance_status`、`overall_status`、`run` 和 `attempts` 字段。
