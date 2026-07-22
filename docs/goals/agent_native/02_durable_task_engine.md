# Goal 02：实现持久任务引擎并删除旧生产内核

- 阶段：02 / 09
- 依赖：[Goal 01](01_core_foundation_replacement.md)全部完成
- 风险：高
- 完成后进入：[Goal 03](03_reconcile_goal01_goal02.md)

## 给 Codex 的命令

你要新建 VibeOS 唯一的 Durable Task Engine，迁移当前所有生产能力、审批与
澄清恢复路径，然后删除同步 `GoalLoop`、独立 `ReviewStore` 和非生产 legacy
runtime。不要把 1173 行旧循环扩写成通用工作流，也不要引入分布式工作流
平台。用纯状态转换、SQLite 事务、lease、timer 和 reconciliation 证明任务可
持续数小时、跨进程崩溃和重启恢复而不重复副作用。

先阅读总计划、Goal 01 交付和当前代码；重新确认真实入口与数据，旧名称可能
已经变化。

## 项目总体思想

Agent 可以自主处理技术细节并判断完成，但长期任务的权威不是某次模型会话。
每一次决定、动作、等待、用户交互和证据都必须绑定到可恢复 TaskRun。系统
采用 at-least-once 调度，不宣称 exactly-once；副作用安全来自幂等、receipt
和执行后对账。

## 当前起点

- Goal 01 已提供统一数据库、outbox、单 supervisor 和两个新路径切片；
- 其余能力仍通过同步 `GoalLoop.run()`，普通运行不会持续保存完整权威状态；
- ReviewStore 只在 review/clarification suspend 场景保存 loop snapshot；
- trace/ledger 是诊断或结果记录，不能继续承担 Task Store；
- 当前 observe/review/execute/verify/retry/replan 行为和 19 个能力必须迁移保留。

## 核心目标

实现唯一 Task Store 和纯 transition engine：

```text
command/event + current Task state
  -> pure transition
  -> state mutations + effects to dispatch + domain events
  -> atomic commit/outbox
  -> leased worker performs I/O
  -> receipt/observation returns as next event
```

支持创建、规划、运行、等待时间/事件、审批、澄清、暂停、恢复、取消、重试、
replan、用户接管、超时和明确终态。系统重启后扫描并安全继续未完成任务。

## 必须实施

1. **领域状态机**
   - 定义版本化 `GoalContract`、`TaskRun`、`PlanRevision`、`Step`、`Attempt`、
     `WaitCondition`、`ActionReceipt`、`EvidenceBundle` 和 `TerminalOutcome`。
   - 所有转移是纯函数，非法转移 fail-closed；终态不可被后台 worker 复活。
   - 目标、范围、完成条件或现实后果有实质歧义时进入 `awaiting_clarification`。

2. **调度与所有权**
   - SQLite repository 用短事务 claim 可运行任务；lease 有 owner、期限和 fencing
     token，过期后可安全回收。
   - timer/event wait 不靠进程 sleep；重启时由索引扫描到期项。
   - outbox dispatcher 至少一次投递；consumer 使用幂等键去重。
   - 同一 Task 同时只有一个有效 transition owner，但多个任务可并发。

3. **副作用恢复**
   - action proposal 在执行前持久化；执行者返回稳定 receipt；
   - worker 在“外部动作成功但 DB 提交前”崩溃时，先 reconcile 外部状态，不能
     盲目重放；不能对账的动作必须暂停并请求安全处置；
   - 重试区分瞬态、永久、需要新计划、需要用户和状态未知。

4. **用户控制**
   - CLI/D-Bus 提供任务 list/show/pause/resume/cancel/takeover/release/control；
   - 控制命令有 expected revision/CAS，解决用户和 Agent 同时操作；
   - cancel 是协作式并可观察，不能把仍运行的外部进程直接报告为 cancelled；
   - 长时间任务提供节流后的阶段进展和下一唤醒条件。

5. **迁移与删除**
   - 先迁移 Goal 01 两切片，再迁移 review、clarification 和其余 17 个 capability；
   - 为每类旧路径建立固定场景等价测试和旧数据库迁移；
   - 切换 CLI、D-Bus 及仍保留的 HTTP 到新 application service；
   - 迁移完成后删除旧 `GoalLoop`、旧 loop snapshot/review store schema、legacy
     runtime、重复 route/ledger 以及仅为双路径存在的 adapter；保留有价值的
     planner、observer、verifier 需通过端口接入新内核。

## 明确非目标

- 不引入 Temporal、Celery、Redis、Kafka、cron 驱动的第二调度器或远程 worker；
- 不实现 Secret Broker、Machine State Index、通用 shell、root 或桌面输入；
- 不做完整 event sourcing，不从事件重算所有读取模型；
- 不通过单个全局锁串行全部任务；
- 不保留“新版默认、旧版 fallback”的永久双内核。

## 必测崩溃矩阵

至少覆盖进程在以下时点被强制终止并重启：

1. proposal 提交前；
2. proposal/outbox 提交后、dispatch 前；
3. 外部动作发出前；
4. 外部动作成功后、receipt 提交前；
5. receipt 提交后、verify 前；
6. verify 后、终态提交前；
7. review/clarification 等待中；
8. cancel、takeover 和 lease expiry 竞争中。

## 验收条件

- [ ] 状态转移表覆盖所有状态/事件组合，非法转移和终态复活测试通过；
- [ ] 任务可等待至少一小时（测试可用 fake clock），daemon 重启后按期恢复；
- [ ] 两个 daemon/worker 竞争时每个 lease 只有一个有效 owner，fencing 生效；
- [ ] 崩溃矩阵通过，已提交的 E1 副作用不会重复，未知结果会暂停而非猜测成功；
- [ ] review 和 clarification 可跨重启恢复，旧 pending 数据可迁移且不会重复批准；
- [ ] pause/resume/cancel/takeover/release 具有 CAS 和审计证据；
- [ ] 当前 19 个 capability 全部只经过新 Task Engine，CLI/D-Bus 结果保持兼容；
- [ ] 源代码和 production composition 不再引用旧 GoalLoop、旧 ReviewStore、
  loop snapshot 或 legacy runtime；删除清单通过 `rg` 和依赖测试证明；
- [ ] 在目标并发/任务数量基准下 SQLite 无不可接受的锁等待，指标和阈值入库；
- [ ] 共同质量门禁、迁移测试和 daemon 重启集成测试全部通过。

## 必交付物

- 纯 task transition engine、Task Store、scheduler/worker/timer/outbox；
- 用户控制与长期进展接口；
- 19 个 capability、review 和 clarification 的迁移；
- 崩溃矩阵、并发/性能报告和旧数据迁移 fixture；
- 旧内核删除提交以及更新后的当前架构/运维文档。

只有新内核成为唯一生产状态权威、旧实现真实删除后才结束本 Goal。
