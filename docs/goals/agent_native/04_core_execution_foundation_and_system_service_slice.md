# Goal 04：建立最小受治理执行地基，并用 systemd user service 完成纵向验收

- 阶段：04 / 11
- 依赖：[Goal 03](03_reconcile_goal01_goal02.md)全部完成，补充修复、真实 VM 证据和资产归属已形成干净提交
- 规模：XL
- 风险：高
- 完成后进入：[Goal 05](05_model_gateway_and_secret_broker.md)

## 给 Codex 的命令

你要先收敛 VibeOS 当前最关键的执行地基，再用一个范围固定的真实 Linux 用户任务
证明这套地基可以工作。固定验收场景是：**诊断一个失败或异常的 `systemd --user`
测试服务，在允许范围内恢复它，并用独立证据确认结果**。

systemd 场景不是本 Goal 抢先交付的孤立功能，而是地基的验收试件。必须严格按
04A -> 04B -> 04C 执行，并让每一段形成可独立审查、可回退的逻辑提交：04A 没有
形成唯一 effect、动作结果和数据合同权威时，不得开始 04B；04B 的 Gateway/SecretRef
合同和进程边界没有独立验收时，不得开始 04C；04C 没有经过崩溃和真实环境验证时，
不得结束本 Goal。不要重写 Goal 03 的 Durable Task Engine，不要新增平行 Registry、
Task Store、effect policy、动作结果存储或 daemon 生命周期。

参考 fixture 固定为 `vibeos-goal04-fixture.service`。它只读写测试专用、
Agent-owned 状态。测试控制器只能在任务开始前重置 fixture 并预先触发一次确定性
失败；任务开始后不得替 Agent 修改状态。fixture 的首次启动失败并写入唯一合成日志，
随后由 Agent 执行的下一次 allowlisted `start`/`restart` 才进入定义健康状态。不得操作
用户真实服务或数据。若目标 Fedora 无法实现该 fixture，先报告平台限制和最小替代
方案，不得自行改成真实未知服务。

## 项目总体思想

VibeOS 是个人 Linux 设备上的 Agent-native 执行层。用户给出目标和现实边界，Agent
优先使用 API、D-Bus、结构化 CLI 和系统服务，自主处理技术细节；只有语义接口不足
时才使用 UI。所有动作都必须从 GoalContract、机器事实和确定性 effect policy 出发，
经过唯一 Durable Task Engine，产生 receipt，并由独立观察判断是否完成。

模型负责理解、诊断和提出 typed proposal，但不能决定权限、直接执行命令、接触
秘密明文或宣布现实任务完成。机器事实必须有来源、采集时间、TTL、敏感级别和证据
引用。动作返回成功不等于用户目标完成。

## 预期进入状态与现场核对

Goal 03 已建立唯一 Durable Task Engine、`SqliteTaskRepository`、CLI/D-Bus/HTTP/Python
统一入口、19 个兼容 capability、迁移链和回退证据。开始本次规划修订前，`main` 与
`origin/main` 都位于 Goal 03 remediation 合并提交 `c9b7ca6`，跟踪工作树干净；本次
Goal/状态文档修改必须先形成规划提交，Goal 04 的真实起点因此可能晚于 `c9b7ca6`。
`.codex_vm_artifacts` 中的 13 项 VM 证据已经由该提交跟踪并归属 Goal 03，不是 Goal 04
可以改写或清理的临时文件。该提交只是记录过的预期起点，执行者仍必须现场复核。

当前代码还存在本 Goal 必须正视的过渡事实：

- capability、持久化和审批路径主要使用 `L0-L3`/`risk_level`，而产品章程已经确定
  `E0-E4`/`effect_level`；本项目尚未正式发布，这是一次性根治该债务的窗口；
- 独立的 observation depth 也使用 `ObservationLevel = L0/L1/L2`；它不是 effect，
  但继续共用 `L` 命名会造成策略和架构守卫歧义，本 Goal 将其迁移为 `O0/O1/O2`；
- Core receipt 已使用部分 `E0/E1`，不能再增加第三套风险语义；
- Goal 01 `FoundationSliceService` 会持久化自己的 receipt/evidence，Durable Task 层又
  生成外层 receipt/evidence；唯一任务状态已经成立，但 canonical 动作结果仍需收敛；
- `ContextPackageRegistry`、`ObservationService`、`ToolRegistry` 和
  `CapabilityRecipeRegistry` 已经存在；
- 多个语义模块仍直接经过 `provider_client`，provider key 仍可来自环境变量或 `.env`；
- 还没有 Model Gateway、Secret Broker/transport 进程边界或受治理的 user-service
  fact/action provider。

执行前必须记录当前分支、HEAD、`origin/main`、工作树、并发 worktree、Goal 03 证据
资产清单和 Alembic head。只有现场仍然能够证明 Goal 03 已合并、证据资产归属清楚，
且没有来源不明的脏改动时，才能冻结 Goal 04 基线。不得 reset、clean、覆盖、改写
Goal 03 证据或顺手提交其他任务的修改。若基线与上述预期不同，先报告差异并收窄调整。

同时现场确认：

- 当前 Alembic head、完整质量门禁和 19 capability 合同；
- 所有 provider 调用点、模型预算、配置和实际启用 provider；
- Secret Service/GNOME Keyring 在目标 VM 的可用、锁定和会话行为；
- systemd user D-Bus 与结构化 `systemctl --user` 的能力和权限边界；
- 当前 Task Engine 在 observation、planning、action、verify、wait 和 restart 的恢复点；
- fixture 与用户真实 service/data 完全隔离。

## 核心目标

建立并证明一条唯一受治理链路：

```text
用户目标
  -> GoalContract 与实质歧义检查
  -> 同一 Observation 路径采集并裁剪 typed service facts
  -> 最小、版本化的 context manifest
  -> Model Gateway 诊断与 typed proposal
  -> 唯一 EffectAssessment 与 typed ActionProposal
  -> 现有 ToolRegistry 中的 allowlisted provider
  -> Durable Task execution / canonical receipt / reconciliation
  -> 独立重新观察与 TerminalOutcome
```

成功不是多写几个抽象类，也不是 `systemctl` 返回零；成功是核心边界只有一套，并且
用户能理解服务为什么失败、Agent 做了什么、当前是否健康、证据在哪里以及还有什么
风险。

## 04A：收敛 effect、数据合同与动作结果权威

### 1. 冻结唯一状态与注册边界

- `DurableTaskEngine` 与 `SqliteTaskRepository` 继续是任务、审批、澄清、恢复、receipt
  和 evidence 的唯一权威；不得复制或旁路。
- Goal 01 slice 和所有 provider/adapter 只能返回严格 `AdapterResult`、外部引用与待规范化
  的证据材料；不得再生成或持久化第二套任务级 `ActionReceipt`/`EvidenceBundle`。
  canonical receipt/evidence 只由 Durable Task 执行边界生成并一次性提交。
- provider 为幂等与 unknown-outcome reconciliation 保留的内部操作状态可以存在，但必须
  明确是 provider-local state，通过 external reference 关联，不得成为任务状态或完成
  判断权威。
- `ToolRegistry` 是唯一可执行工具注册表；`CapabilityRecipeRegistry` 只负责把已验证
  task step 映射到工具调用，不得另建 production `ActionRegistry`。
- `ContextPackageRegistry` 与 `ObservationService` 是事实采集/上下文路径。需要的
  `MachineFact` 应成为该路径中的严格数据合同或规范化记录，不得另建第二个通用
  Machine State 权威。
- 写一份 convergence matrix，列出旧类型、权威 owner、兼容 adapter、调用者和未来
  删除门禁。兼容层不得持有独立状态。

### 2. 一次性迁移 effect 与 observation 命名

- 以产品框架的 `E0-E4` 为规范 effect classification：E0 观察；E1 任务范围内可逆
  本地动作；E2 可回滚本地提权；E3 外部承诺、不可逆破坏或重大安全影响；E4 拒绝。
- 项目仍是未发布 `0.1.0`，不为 `L0-L3` 建长期兼容层。先建立 19 capability、所有
  persisted task/review fixture 和公共入口的逐项重分类矩阵，再完成一次性迁移和删除。
- 不得按数字机械映射：现有 L2 同时包含剪贴板、URI、关窗等不同效果，必须结合 typed
  verb、参数、资源、数据流、可逆性和外部影响分别归入 E0-E4；无法确定的旧 pending
  work 一律迁移为安全等待/人工 disposition 或 E4，不猜测批准。
- `ObservationLevel` 不是 effect。将当前 observation depth 的 `L0/L1/L2` 按原语义
  一次性改名为 `O0/O1/O2`，并迁移当前非终态 observation payload；不得用 effect
  重分类矩阵解释 observation depth，也不得在 production 保留 L/O alias。
- 从现场 Alembic head 新增 additive revision。若 head 仍是
  `0005_persist_dry_run_intent`，下一 revision 应为 `0006`；不得修改 Goal 03 已冻结的
  `0001`-`0005`。迁移必须盘点普通列和 JSON payload，至少覆盖
  `plan_revisions.payload_json`、`task_steps.payload_json`、当前 task/review/capability
  snapshot 以及仍可能恢复执行的 observation 数据，而不是只重命名数据库列。
- 将 Task、Plan、Step、Review、Capability 和 Observation 的 live contract 升到 v2。
  CLI、D-Bus、loopback HTTP 与 Python 的新 live payload 只返回 `effect_level`/E0-E4
  和 `O0-O2`；不得同时返回 `risk_level`、effect L 值或 observation L 值。
- 对每类 v1 持久状态写出 disposition：能够由批准矩阵确定转换的非终态任务迁入 v2；
  无法确定 effect、批准绑定或 observation 语义的非终态任务进入带原因的安全暂停/人工
  disposition，不得猜测为 E 值后执行。v2 runtime 不得继续执行未处置的 v1 step。
- 已完成的 v1 event、receipt、evidence 和 plan revision 保持不可变；只允许通过明确的
  v1 历史 decoder/只读投影查看，不能重新进入 live execution。历史旧数据 fixture 继续
  用 L 值验证升级，但不能形成 production alias 或双 policy。
- 回退合同固定为“Goal 04 前 artifact + 迁移前数据库快照”；不得把旧 artifact 指向
  v2 数据库。验证空库、Goal 03 旧库、混合终态/非终态数据、迁移中断、重复 upgrade
  和 artifact/database pair 恢复。
- 现有 `PermissionPolicy` 必须被直接演进/替换为唯一确定性 `EffectPolicy`，迁移调用者
  后删除旧类、旧 summary、旧 rank helper 和旧测试。禁止 adapter、alias 或双 policy。
- 本 Goal 只完整实现固定场景所需 E0/E1。E2/E3/E4 保留 fail-closed 合同和测试，
  不实现提权或外部承诺。
- 更新 Goal 03 compatibility matrix/当前状态，明确这是用户批准的未发布契约替换；
  旧行为证据保留在归档，不在 production runtime 继续提供。
- architecture guard 必须拒绝 production 源码、当前 contract/schema 和非迁移测试重新
  引入 effect `L0-L3`、`risk_level`、`PermissionPolicy`，也必须拒绝 `ObservationLevel`
  使用 L 值。允许清单仅限冻结历史 migration、v1 decoder、旧数据 fixture、归档/ADR
  和迁移说明；允许项不得被 live execution 导入。

### 3. 固定 system-service 的 typed 合同

- 定义本场景需要的 typed facts：unit load/active/sub state、关键属性、受限 journal
  摘要、相关进程状态、source、captured_at、TTL、sensitivity 和 evidence reference。
- 定义严格 ActionSpec：只允许观察以及对固定 fixture 执行 start/restart 等 E1 动作；
  写清 resource scope、precondition、timeout、idempotency、adapter result、canonical
  receipt、verify 和 reconciliation。
- 首选 systemd user D-Bus；仅在已证明缺口时使用固定绝对 executable 和 argv 数组。
  禁止 shell 字符串、管道、重定向、root、system unit、任意 unit 和任意路径。

04A 必须通过 convergence matrix、v2 合同/迁移测试、canonical receipt 守卫和完整质量
门禁，形成独立提交并演练与迁移前数据库快照配对回退。未通过时不得开始 04B。

## 04B：建立可继承的最小 Gateway 与 SecretRef 边界

### 1. 建立稳定的 Model Gateway v1

- 为 `service_diagnosis` 定义可由 Goal 05 直接扩展的 provider-neutral、版本化
  `ModelRequest`/`ModelResponse`：Task/Attempt、最小 context manifest、数据级别、
  timeout、总预算、取消和 strict response schema。它是 production v1 合同，不是
  Goal 05 可以丢弃的场景 scaffold。
- 先使用完全合成、无用户数据的 D0 service facts 验证 request/response 和失败分类；
  首期只要求一个实际可用的 OpenAI-compatible cloud adapter。模型只能返回诊断或
  typed proposal，确定性代码验证 unit、参数、事实新鲜度和 effect。
- 本 Goal 新增的所有模型调用必须经 Gateway。盘点现有直接 `provider_client` 调用，
  可用兼容 facade 把本场景导入 Gateway；未迁移调用必须记录 owner 和后续 Goal 05
  门禁，不得在本 Goal 大爆炸删除。
- 429、5xx、timeout、坏 JSON、schema 不匹配、预算耗尽、取消和 unknown delivery 必须
  分类并 fail-closed；
  不允许静默回退到更宽松提示或未经记录的 provider。
- Goal 05 只能在该合同上增加 provider、purpose、RoutePolicy、grant 和迁移剩余调用者；
  不得另建第二套 Gateway 或替换本 Goal 的 request/response、预算和失败权威。

### 2. 建立 SecretRef 与最小进程隔离

- provider key 存入 freedesktop Secret Service/GNOME Keyring，Core 只持久化 opaque
  `SecretRef` 和非敏感 metadata。
- 只有窄 provider transport/Broker 进程在发请求时解析引用并短暂使用秘密。planner、模型输入、
  GoalContract、Task DB、event、outbox、trace、argv、普通环境变量和错误回显不得获得
  明文。
- 落实 ADR 0002 的最小真实隔离：semantic/planner worker 与 secret-capable transport
  分进程；前者不挂载 session bus，或通过可验证的 D-Bus proxy 明确拒绝 Secret Service。
  Core/Gateway 只发送 `SecretRef`、绑定后的 typed operation 和非敏感 context manifest，
  transport 只返回 strict model result、classified failure 和 redacted receipt。
- 明确首期威胁声明：该边界防止模型、semantic worker、普通 Core 路径、持久化和日志
  获得 secret；它不宣称能够抵抗已经攻陷的同 UID 任意进程。不得把普通 Python 模块
  封装写成进程或 OS 级隔离。
- CLI 提供 TTY 安全 import/status/delete；环境变量只允许一次显式迁移并立即清除
  长期配置依赖，不得继续作为隐式 fallback。
- keyring 锁定时任务进入可解释等待，解锁后从 Durable Task 状态继续。本阶段不建立
  任意应用密码管理 UI、通用 SecretGrant 或自动登录系统；完整 Secret Broker 在
  Goal 05 收敛。

04B 必须用 D0 fixture、locked/unlock、泄漏 canary、进程/D-Bus 隔离检查和一次受控
真实 provider smoke 验收，形成独立提交。没有用户提供的 credential 时可以完成离线
合同，但不得把真实 smoke 标为通过，也不得开始依赖该证明的 04C 真实 provider 验收。

## 04C：实现 systemd 纵向场景并完成真实证据验收

1. 创建可重复失败/恢复的 `vibeos-goal04-fixture.service`、Agent-owned 状态和测试
   控制器。控制器在任务开始前重置一次性 failure token 并预先启动服务；该次启动必须
   确定性失败并写入唯一合成日志。任务开始后控制器不得再写状态，Agent 的下一次
   allowlisted start/restart 才能进入健康状态。写明允许效果、最大重试、禁止影响、
   成功和停止条件。
2. 固定用户目标为“诊断并恢复 VibeOS 测试用户服务，确认恢复完成”。unit、对象或
   目标有实质歧义时进入 `awaiting_clarification`，不得猜测最像服务。
3. 按需采集 unit、journal 和 process facts。journal 只读取明确 unit 与时间窗口，
   进入模型前确定性裁剪和脱敏；不遍历 home、整盘日志或用户其他服务。
4. 从裁剪后的 facts 生成最小 context manifest，再调用 04B Gateway 诊断并提出 typed
   proposal；不得在事实采集前调用 `service_diagnosis`。
5. 持久化 typed proposal 后才执行；worker 在动作后崩溃时先查询真实 unit 状态，
   不盲目重复 restart。
6. provider 只返回 adapter result；Durable Task 边界生成唯一 canonical receipt/evidence。
   verifier 独立重新查询状态、健康条件和必要日志，不复用执行返回值或 fixture 控制器
   内部状态。一次安全恢复无效时根据证据 replan、询问或明确失败，不无限重试。
7. TerminalOutcome 包含诊断、动作、当前状态、证据 ID、完成判断和未解决风险。

- 在事实采集、context manifest、模型调用、proposal 提交、外部动作前、外部动作后
  canonical receipt 前、verify 前和等待期间分别注入 daemon/worker 崩溃；恢复不得
  重复未知副作用。
- 覆盖 unit 不存在、无权限、journal 不可用、keyring locked、provider timeout、坏
  schema、恢复无效、stale fact 和并发 dispatch。
- 使用高熵 canary 扫描 DB、event、outbox、trace、日志、导出、argv、环境和 exception，
  证明 provider key 不泄漏。
- WSL 只验证非桌面和持久内核；真实 Fedora GNOME VM 验证 Secret Service、systemd
  user session、一次真实 provider 调用和 fixture 恢复。分别记录环境、命令、结果和
  未覆盖边界。
- 最终重新运行 Goal 03 公共兼容、迁移、19 capability、完整质量和架构门禁，并形成
  独立 04C 提交。04A/04B/04C 每个提交都必须记录验证范围和回退点。

## 明确非目标

- 不迁移全部模型调用，不实现多 provider 智能路由或本地模型 runtime；
- 不建立通用密码管理器、任意应用 SecretGrant 或登录自动化；
- 不建立全机 Machine State Index、向量库、知识图谱或通用 Action 平台；
- 不开放任意 shell、root、system-bus、polkit、Bubblewrap 或 system unit 管理；
- 不实现 AT-SPI、RemoteDesktop、视觉输入、主动建议或正式发行打包；
- 不删除现有 Registry、provider 或 desktop 路径，除非有独立 replacement matrix、
  等价证据、回退提交和用户批准。

## 验收条件

- [ ] Goal 03 remediation 与本 Goal 修改边界清楚，Goal 04 从记录过的干净提交开始；
- [ ] Task Store、Effect Policy、工具注册、observation/context 和 canonical action result
  各只有一个权威 owner；Foundation/provider 不再持久化第二套任务 receipt/evidence；
- [ ] 19 capability、旧 pending work 和公共入口有逐项重分类矩阵，不能机械映射的旧
  状态 fail-closed；
- [ ] v2 live contract 使用 `effect_level`/E0-E4 和 observation `O0-O2`；additive migration
  覆盖 JSON payload、非终态 disposition 和历史只读边界，冻结历史 migration 未修改，
  空库/旧库/中断/重复升级/artifact-database pair 恢复通过；
- [ ] production 源码、当前 schema/contract、CLI/D-Bus/HTTP/Python payload 和普通测试
  不再包含 effect `L0-L3`、observation L 值、`risk_level` 或 `PermissionPolicy`，
  architecture guard 强制该规则；
- [ ] 04A convergence matrix、v2 迁移、canonical receipt、架构守卫和合同门禁通过并形成
  可回退提交后才开始 04B；04B 独立验收并提交后才开始 04C；
- [ ] 本场景所有模型调用只经 Gateway，strict schema、总预算、timeout、取消和错误
  分类可证明，且没有新增直接 provider 调用；
- [ ] Goal 04 Gateway/SecretRef/transport 是 Goal 05 必须继承的 production v1 合同，
  semantic/planner 与 secret-capable transport 的进程/D-Bus 隔离可验证；
- [ ] provider key 仅由窄 transport 解析，不出现在持久状态、模型上下文、日志、argv、
  普通 env、异常或导出；locked/unlock 恢复通过；
- [ ] fixture 的失败由任务前一次性 token 确定触发；任务期间控制器不介入，Agent 的
  allowlisted 动作使其进入定义健康状态，独立 verify 支持完成判断；
- [ ] unit 歧义、不存在、无权限、journal 不可用和恢复无效均有安全终态；
- [ ] 只对 allowlisted user fixture 执行 E1，无 shell/root/system unit/任意路径旁路；
- [ ] 所列崩溃边界、重复 dispatch 和未知结果均安全 reconciliation；
- [ ] 真实 Fedora VM 的 Secret Service、provider 和 systemd 场景证据完整，WSL/mock
  没有被当成真实环境证明；
- [ ] 19 capability 仍可发现，参数与基础功能不无故丢失；effect/批准行为只按用户批准
  的重分类矩阵变化，Goal 03 其余公共合同和共同质量门禁无非预期回归。

## 必交付物

- Goal 04 基线记录和执行地基 convergence matrix；
- effect L0-L3 -> E0-E4 重分类矩阵、observation L0-L2 -> O0-O2 命名矩阵、v2 live
  contract、JSON-aware additive migration、历史/非终态 disposition 和唯一 EffectPolicy；
- Foundation/provider -> Durable Task canonical action result 收敛矩阵和机器守卫；
- 现有 Context/Observation/Tool Registry 的权威边界和机器可检查守卫；
- 可由 Goal 05 继承的 Model Gateway v1、独立 provider transport/SecretRef v1、进程与
  D-Bus 隔离证明、CLI 和泄漏测试；
- systemd user fixture、service facts、typed E0/E1 provider、verifier 和黄金场景；
- 崩溃/故障矩阵、WSL 非桌面证据和真实 Fedora GNOME 验收记录；
- 更新后的当前状态、架构、秘密、效果治理和运维文档。

只有最小地基先收敛为唯一权威，并被固定 systemd 用户任务在真实环境、崩溃恢复、
秘密隔离和独立完成判断上证明后，才结束本 Goal。
