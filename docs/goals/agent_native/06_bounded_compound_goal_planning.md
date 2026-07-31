# Goal 06：交付有界复合目标规划与完整覆盖门禁

- 阶段：06 / 12
- 依赖：[Goal 05](05_model_gateway_and_secret_broker.md)全部完成
- 规模：XL
- 风险：高
- 完成后进入：[Goal 07](07_unprivileged_tasks_and_installable_runtime.md)

## 给 Codex 的命令

你要在 Goal 05 已统一的 Model Gateway、purpose-specific strict schema 和失败分类上，
为 VibeOS 交付第一版有界复合目标规划。Agent 必须能够把一条包含多个领域、顺序、
条件和数据依赖的自然语言目标，编译成一个覆盖完整用户意图的 Durable Task 计划；
只有 host 已证明计划覆盖所有 required subgoal 时，selector 才能选择并进入执行。

这不是重写 planner 或 Durable Task Engine。复用现有 goal understanding/synthesis、
单领域 route/step builder、TaskPlan、Effect Policy、ToolRegistry、Task Store、deadline、
receipt、verifier、recovery 和 reconciliation。模型只能提出受 schema 和 capability
boundary 限制的 subgoal；host 负责组合、验证、policy、执行与完成判断。

本 Goal 的真实效果范围限制为 E0/E1，并以 planning/controlled-provider 证明为主。
真实 GNOME 浏览器、剪贴板、通知和用户接管由 Goal 08 验收。任何 capability、参数、
数据绑定、policy 或 verifier 无法在第一个 effect 前静态确认时，必须 fail-closed；绝不
执行部分计划后把整个用户目标标记为成功。

## 项目总体思想

VibeOS 的核心价值不是把一句话映射到一个最像的动作，而是忠实完成用户的整个目标。
模型擅长理解自然语言，但不能直接获得执行权。一个复合目标必须保留原文 provenance，
被拆成有界 subgoal，再由 host-owned compiler 映射到已注册能力，并由独立 coverage gate
证明“没有漏掉用户要求”。

歧义与能力缺失不同。对象、现实后果、数据范围、条件含义或完成标准存在实质歧义时，
主动询问用户；技术实现细节由 Agent 在既有 policy 中自行选择。完整目标已经清楚、但
当前能力不足时，应明确返回 missing capability/unsupported，不能要求用户把目标手工拆
成多条命令来掩盖系统缺陷。

条件不成立也不等于失败。若用户说“如果服务健康，则执行后续动作”，Agent 应先用
host-owned policy 解释 typed observation；条件为假时持久化可解释的 skipped 结果，并
以“检查完成、条件未满足、后续未执行”结束，而不是执行、失败或反复追问。

## 预期进入状态与现场核对

预期 Goal 05 已交付：

- 所有生产模型调用经过唯一 Gateway/RoutePolicy/SecretBoundTransport；
- `goal_understanding`、`goal_synthesis`、`route_selection` 和
  `service_diagnosis` 有独立 strict schema、版本和分类失败；
- schema rejection、provider failure 和 host fallback provenance 可区分，完整原始
  provider payload 默认不持久化；
- multi-domain 请求仍由现有 selector guard 在任何 effect 前 fail-closed；
- Goal 03/04 的 Durable Task、E0-E4、O0-O2、19 capability 和公共入口保持有效。

开始前必须以现场代码为准，至少核对：

- `GoalSubgoal`、`TaskSpan`、`TaskPlan`、`TaskStep`、plan persistence/codec 当前 schema；
- 现有 candidate generation、route builders、selector、plan validation 和 replanning
  的真实调用链，哪些已支持多 step，哪些仍假设单领域；
- durable driver 如何选择下一 step、恢复 completed step、保存 receipt 和处理 blocked
  dependency；不要把非耐久 execution helper 误当 production 权威；
- 每个首期 capability 的 typed input/output、EffectPolicy、precondition、verifier 和
  independent observation 是否真实存在；
- 当前数据库中 active v2 plan、已完成历史 plan 和旧 evidence 的兼容边界；
- Fedora GNOME 报告中四领域请求的真实 transcript、provider failure metadata 和
  `awaiting_clarification` 终态；过期任务不得恢复，修复后创建新任务。

如果现场证明某项前置合同没有完成：Gateway/schema 缺口退回 Goal 05 remediation；
Durable Task/effect/receipt 权威缺口退回 Goal 03/04 remediation。不得在本 Goal 建第二套
模型调用、任务循环、数据库或动作执行器绕过前置问题。

## 核心目标

建立以下唯一链路：

```text
utterance + GoalContract
  -> strict goal understanding/synthesis proposal
  -> bounded subgoals with source spans
  -> host-owned compound plan composer
  -> route/step builders from existing registries
  -> dependency + condition + typed data bindings
  -> whole-goal coverage certificate
  -> plan/schema/effect/verifier preflight
  -> candidate selection
  -> existing Durable Task Engine
  -> receipt/evidence/independent verification
  -> complete(with condition_not_met reason) | clarification | blocked | failed
```

首期固定边界：

- 一个复合目标最多 4 个 capability domain、最多 8 个 executable step；
- 只编排 E0/E1；任何 E2/E3/E4 subgoal 在本版本不得进入复合执行；
- 只允许无环依赖图和确定性顺序；E1 不并行；
- 只允许一层 `when` 条件，无 `else`、嵌套、循环、递归、运行时模型扩展或任意表达式；
- 条件只引用已完成 E0 step 的版本化 typed output，首期操作符限制为 `eq`、`in`、
  `exists`；unknown/missing/type mismatch 一律不视为真；
- 输入绑定只允许引用前置 step 的 allowlisted typed field，并通过 host-owned 固定模板或
  formatter 形成目标参数；执行器不得猜字段、解释自然语言或重新调用模型拼接正文；
- model proposal 超出边界、coverage 不完整或 provenance 不可信时必须澄清、blocked 或
  unsupported，不做“尽力执行”。

## 必须实施

### 1. 版本化复合目标合同

- 为 subgoal 增加稳定 ID、原文 source span、goal type、required domain/capability、
  顺序/依赖、条件、所需输入和预期完成语义；span 必须对应真实 utterance 范围，不能
  给所有 subgoal 复用整句伪造 provenance。
- provider 只可在 Goal 05 的 allowed domain/capability/schema 内提出结构；host 对每个
  字段重新校验，不信任 provider 给出的 effect、policy、route、verifier 或完成结论。
- 明确“实质歧义”“缺能力”“schema rejected”“完整可规划”四种结果，不把技术 fallback
  伪装成用户需要澄清。
- 若新增持久字段无法保持 v2 含义，使用显式的新 plan schema/version 和新增 migration；
  不修改历史 migration。已完成历史保持不可变，active old plan 按书面 disposition
  继续旧语义、暂停或迁移，不能由新执行器猜测。

### 2. Host-owned whole-goal composer

- 复用现有单领域 route/step builder，把每个 validated subgoal 编译为已注册 step；不得
  让模型直接提交 executable `TaskPlan`，也不得复制 domain registry。
- composer 负责生成稳定 step ID、DAG、条件和输入绑定，并保留 subgoal/span 到 step 的
  双向映射。一个 subgoal 可以映射多个 step，但每个 required subgoal 必须有清晰处置。
- 不允许通过简单拼接多个独立 `TaskPlan` 绕过冲突：资源、顺序、effect、目标参数、
  verifier、deadline 和数据流必须经过统一 preflight。
- 计划必须在持久化与 dispatch 前验证 step/domain 上限、无环、绑定类型、条件引用、
  capability availability、EffectPolicy、deadline、verifier 和 completion conditions。

### 3. Whole-goal coverage gate 与 selector

- 建立 host-owned coverage certificate，至少记录 required subgoal、source span、compiled
  step、capability、condition、verifier、处置和未覆盖原因；certificate digest 与
  plan revision 绑定。
- 只有 coverage 完整、plan validation 通过、所有静态前置成立的 candidate 才是
  selectable。部分覆盖 candidate 可以用于诊断，但不得成为 executable candidate。
- 修改 selector 的多领域 guard：不是按 domain 数量放行，而是仅在收到有效 coverage
  certificate 时允许选择；没有完整候选时继续在第一个 effect 前澄清或 unsupported。
- 模型 route selection 只能在 host 已批准的完整候选之间排序，不能修改 coverage、
  增删 step 或把 blocked candidate 改为可执行。

### 4. 条件与 typed data binding

- 定义 host-owned `HealthPolicy` v1，把 `system.status` typed result 映射为 `healthy`、
  `degraded`、`unhealthy` 或 `unknown`，并给出 reason codes。模型不得自行定义“运行正常”。
- 首个条件固定为 `system.status.health == healthy`。`degraded`、`unhealthy`、`unknown`、
  timeout 或字段缺失均不执行条件后的 E1 step。
- 条件为假时，为受控后续步骤持久化 `skipped_by_condition` 及引用证据；该分支没有
  receipt，不能伪装成已执行。任务可以以 `condition_not_met` 的可解释 terminal outcome
  reason 结束，不触发无界 replan；它不是第二套 TaskStatus 或新的状态机，规范 TaskRun
  仍使用唯一 Durable Task 终态并通过 TerminalOutcome 表达该理由。
- 固定摘要使用 host-owned template，把检查时间和 status typed fields 绑定到
  `clipboard.write` 输入。所有绑定在 dispatch 前物化并计入 proposal/idempotency digest；
  crash 恢复后同一 step 不得因重新格式化而改变参数。

### 5. Durable execution、失败与恢复

- 复用现有 Durable Task Engine 执行多步骤计划；不得使用 provider 私有 loop、CLI
  编排脚本或仅内存 execution graph 冒充 production 结果。
- proposal-before-effect、canonical receipt、独立 verifier、deadline、取消、用户接管、
  lease、idempotency 和 unknown-outcome reconciliation 对每个实际 step 继续成立。
- 静态缺口必须在第一个 effect 前阻断；运行时失败可能留下真实的部分 receipt，系统
  必须准确报告已完成/未完成/跳过和剩余风险，绝不能把整体目标标为成功。
- 覆盖 plan persisted、condition evaluated、binding materialized、proposal committed、
  effect returned、receipt committed 前后的 crash。恢复不得重复 E1，也不得把旧
  observation 或旧 binding 当成当前事实。

### 6. 固定证明与回归矩阵

- 两领域 planning fixture：检查固定 user service；仅当健康时发送受控通知。证明条件真、
  条件假、unknown 和 missing verifier 四条路径。
- 四领域 planning fixture 固定为报告中的发布前检查请求：系统/VibeOS 状态；条件为真时
  打开已配置项目页面；把含检查时间和状态的摘要写入剪贴板；发送固定通知。
- 固定 fixture 使用 controlled providers/adapters 运行完整 Durable Task 路径，不产生
  真实桌面或公网效果；它必须证明 source-span coverage、顺序、binding、skip、receipt、
  verifier、deadline、restart 和无重复 dispatch。
- 有可用 credential 时，用至少一个真实云 provider 完成一次 planning-only smoke，验证
  purpose schema 和 host composer；没有 credential 时记录 external blocker，不能用
  fixture 冒充真实 provider。
- 保持单领域 Goal 03/04 行为、19 capability、EffectPolicy、公共入口、迁移和恢复合同。

## 明确非目标

- 不支持任意数量领域/步骤、循环、嵌套分支、自由表达式、动态工作流或通用 DAG 平台；
- 不支持 E2/E3/E4 复合执行，不实现 Reviewer、提权、付款、发布、账户变更或不可逆动作；
- 不实现真实 GNOME 浏览器、剪贴板、通知、AT-SPI、portal 或用户接管验收；
- 不建设第二套 planner、Task Store、Effect Policy、ToolRegistry、receipt/evidence 或
  provider loop；
- 不把模型输出直接当 executable plan，不让模型定义健康、权限、完成或 fallback policy；
- 不要求用户把清晰目标手工拆成多条 CLI 命令，不删除 guard 后选择部分候选；
- 不借机批量清理所有 legacy planner 模块；删除仍遵守 replacement matrix 和真实调用者
  门禁。

## 验收条件

- [ ] 新复合合同明确限制 4 domain、8 step、E0/E1、无环、一层条件和 allowlisted typed
  binding；超界输入在任何 effect 前 fail-closed；
- [ ] 两领域与四领域固定请求均生成 source span 完整、依赖明确、覆盖所有 required
  subgoal 的 candidate，不只生成首个动作；
- [ ] 每个 executable candidate 都有与 plan revision 绑定的 host-owned coverage
  certificate；部分覆盖 candidate 不可选择；
- [ ] selector 只在 coverage 和 plan preflight 通过时放行多领域计划，现有安全 guard
  没有被简单删除；
- [ ] `system.status` 健康判断来自 `HealthPolicy` v1；条件为假/unknown 时 E1 step 不执行，
  outcome 和 skipped evidence 可解释；
- [ ] 检查时间和状态通过 typed output binding 与固定 formatter 进入 clipboard 参数，
  执行器和模型不在 dispatch 时自由拼接；
- [ ] 静态 capability/参数/policy/verifier 缺失在第一个 effect 前阻断；运行时部分失败
  不被标记为整体成功；
- [ ] Durable Task crash/restart/deadline/cancel/reconciliation 测试证明 E1 不重复、binding
  不漂移、condition 不使用旧事实；
- [ ] active old plan 和历史证据有明确 version/disposition；没有修改旧 migration 或建立
  双重 live contract；
- [ ] 至少一个真实云 provider 完成 planning-only smoke，或缺少 credential 被准确记录为
  external blocker；
- [ ] Goal 03–05 单领域、Gateway/Secret、19 capability、公共入口和共同质量门禁无非预期
  回归；
- [ ] architecture/current status/用户文档明确区分“复合规划已证明”和“真实 GNOME
  复合效果尚待 Goal 08”。

## 必交付物

- 版本化 subgoal/condition/input-binding/coverage contract 和兼容性说明；
- 复用既有 route builders 的唯一 whole-goal composer、coverage validator 和 selector
  集成；
- `HealthPolicy` v1、condition/skip 语义和固定摘要 formatter；
- plan schema/migration/old-plan disposition 与回退说明；
- 两领域、四领域 fixture，Durable Task crash/recovery 矩阵和 planning-only provider
  smoke 证据；
- 更新后的 architecture baseline、current status、planning 文档和 Goal 08 进入清单。

只有 Agent 能把一个清晰的有限复合目标编译成覆盖完整意图的计划，host 能在 effect 前
证明 coverage/condition/binding/policy/verifier 全部成立，并在 Durable Task 中安全执行、
恢复或解释性跳过时，才结束本 Goal。
