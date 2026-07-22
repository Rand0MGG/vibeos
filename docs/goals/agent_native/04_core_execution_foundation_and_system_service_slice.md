# Goal 04：建立最小受治理执行地基，并用 systemd user service 完成纵向验收

- 阶段：04 / 11
- 依赖：[Goal 03](03_reconcile_goal01_goal02.md)全部完成，补充修复与真实 VM 证据形成干净提交
- 规模：XL
- 风险：高
- 完成后进入：[Goal 05](05_model_gateway_and_secret_broker.md)

## 给 Codex 的命令

你要先收敛 VibeOS 当前最关键的执行地基，再用一个范围固定的真实 Linux 用户任务
证明这套地基可以工作。固定验收场景是：**诊断一个失败或异常的 `systemd --user`
测试服务，在允许范围内恢复它，并用独立证据确认结果**。

systemd 场景不是本 Goal 抢先交付的孤立功能，而是地基的验收试件。必须严格按
04A -> 04B -> 04C 执行：04A 没有形成唯一风险、动作、事实、模型和秘密边界时，
不得用场景专用捷径开始 04B；04B 没有经过崩溃和真实环境验证时，不得宣称 04C
完成。不要重写 Goal 03 的 Durable Task Engine，不要新增平行 Registry、Task Store、
风险引擎或 daemon 生命周期。

参考 fixture 固定为 `vibeos-goal04-fixture.service`。它只读写测试专用、
Agent-owned 状态；测试控制器可以让它进入确定性失败和健康状态。不得操作用户真实
服务或数据。若目标 Fedora 无法实现该 fixture，先报告平台限制和最小替代方案，
不得自行改成真实未知服务。

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
统一入口、19 个兼容 capability、迁移链和回退证据。当前代码还存在本 Goal 必须正视
的过渡事实：

- capability、持久化和审批路径主要使用 `L0-L3`/`risk_level`，而产品章程已经确定
  `E0-E4`/`effect_level`；本项目尚未正式发布，这是一次性根治该债务的窗口；
- Core receipt 已使用部分 `E0/E1`，不能再增加第三套风险语义；
- `ContextPackageRegistry`、`ObservationService`、`ToolRegistry` 和
  `CapabilityRecipeRegistry` 已经存在；
- 多个语义模块仍直接经过 `provider_client`，provider key 仍可来自环境变量或 `.env`；
- 还没有受治理的 user-service fact/action provider；
- 制定本 Goal 时，`main` 已到 `d792b06`，但 Goal 03 的 Fedora GNOME remediation
  仍有未提交生产代码、测试和证据。该快照不是 Goal 04 的授权起点。

执行前必须记录当前分支、HEAD、`origin/main`、工作树和并发 worktree。只有 Goal 03
补充修改已被明确归属并形成可回退提交，且本 Goal 文档之外没有来源不明的脏改动时，
才能冻结 Goal 04 基线。不得把未提交 remediation 当作自己的代码，不得 reset、clean、
覆盖或顺手提交它。若基线仍不清楚，停止生产修改并向用户报告。

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
  -> 最小 Model Gateway 请求（无 secret）
  -> 同一 Observation 路径中的 typed service facts
  -> 唯一 EffectAssessment 与 typed ActionProposal
  -> 现有 ToolRegistry 中的 allowlisted provider
  -> Durable Task execution / receipt / reconciliation
  -> 独立重新观察与 TerminalOutcome
```

成功不是多写几个抽象类，也不是 `systemctl` 返回零；成功是核心边界只有一套，并且
用户能理解服务为什么失败、Agent 做了什么、当前是否健康、证据在哪里以及还有什么
风险。

## 04A：先收敛最小执行地基

### 1. 冻结唯一状态与注册边界

- `DurableTaskEngine` 与 `SqliteTaskRepository` 继续是任务、审批、澄清、恢复、receipt
  和 evidence 的唯一权威；不得复制或旁路。
- `ToolRegistry` 是唯一可执行工具注册表；`CapabilityRecipeRegistry` 只负责把已验证
  task step 映射到工具调用，不得另建 production `ActionRegistry`。
- `ContextPackageRegistry` 与 `ObservationService` 是事实采集/上下文路径。需要的
  `MachineFact` 应成为该路径中的严格数据合同或规范化记录，不得另建第二个通用
  Machine State 权威。
- 写一份 convergence matrix，列出旧类型、权威 owner、兼容 adapter、调用者和未来
  删除门禁。兼容层不得持有独立状态。

### 2. 一次性迁移并删除 L0-L3，只保留 E0-E4

- 以产品框架的 `E0-E4` 为规范 effect classification：E0 观察；E1 任务范围内可逆
  本地动作；E2 可回滚本地提权；E3 外部承诺、不可逆破坏或重大安全影响；E4 拒绝。
- 项目仍是未发布 `0.1.0`，不为 `L0-L3` 建长期兼容层。先建立 19 capability、所有
  persisted task/review fixture 和公共入口的逐项重分类矩阵，再完成一次性迁移和删除。
- 不得按数字机械映射：现有 L2 同时包含剪贴板、URI、关窗等不同效果，必须结合 typed
  verb、参数、资源、数据流、可逆性和外部影响分别归入 E0-E4；无法确定的旧 pending
  work 一律迁移为安全等待/人工 disposition 或 E4，不猜测批准。
- 新增 additive Alembic revision，把当前 schema 中的 `risk_level` 字段/值迁移为
  `effect_level`。不得修改 Goal 03 已冻结的历史 migration；历史旧数据 fixture 继续
  用 L 值验证升级，迁移完成后的规范 schema 只含 E 值。
- 版本化修改 CLI、D-Bus、loopback HTTP、Python、Task/Plan/Review 和 capability contract，
  删除当前 payload 的 `risk_level` 以及 L 枚举。项目尚未发布，因此允许有记录的
  pre-release breaking change；不得同时返回 `risk_level` 与 `effect_level`。
- 现有 `PermissionPolicy` 必须被直接演进/替换为唯一确定性 `EffectPolicy`，迁移调用者
  后删除旧类、旧 summary、旧 rank helper 和旧测试。禁止 adapter、alias 或双 policy。
- 本 Goal 只完整实现固定场景所需 E0/E1。E2/E3/E4 保留 fail-closed 合同和测试，
  不实现提权或外部承诺。
- 更新 Goal 03 compatibility matrix/当前状态，明确这是用户批准的未发布契约替换；
  旧行为证据保留在归档，不在 production runtime 继续提供。
- architecture guard 必须拒绝 production 源码、当前 contract/schema 和非迁移测试重新
  引入 `L0-L3`、`risk_level` 或 `PermissionPolicy`。允许清单仅限冻结历史 migration、
  旧数据 fixture、归档/ADR 和迁移说明。

### 3. 建立最小 Model Gateway

- 为本场景实际使用的语义调用定义 provider-neutral request/response、purpose、
  Task/Attempt、context manifest、timeout、总预算、取消和 strict response schema。
- 首期只要求一个实际可用的 OpenAI-compatible cloud adapter；模型只能返回诊断或
  typed proposal，确定性代码验证 unit、参数、事实新鲜度和 effect。
- 本 Goal 新增的所有模型调用必须经 Gateway。盘点现有直接 `provider_client` 调用，
  可用兼容 facade 把本场景导入 Gateway；未迁移调用必须记录 owner 和后续 Goal 05
  门禁，不得在本 Goal 大爆炸删除。
- 429、5xx、timeout、坏 JSON、schema 不匹配、预算耗尽和取消必须分类并 fail-closed；
  不允许静默回退到更宽松提示或未经记录的 provider。

### 4. 建立 provider secret 最小闭环

- provider key 存入 freedesktop Secret Service/GNOME Keyring，Core 只持久化 opaque
  `SecretRef` 和非敏感 metadata。
- 只有窄 provider transport 在发请求时解析引用并短暂使用秘密。planner、模型输入、
  GoalContract、Task DB、event、outbox、trace、argv、普通环境变量和错误回显不得获得
  明文。
- CLI 提供 TTY 安全 import/status/delete；环境变量只允许一次显式迁移并立即清除
  长期配置依赖，不得继续作为隐式 fallback。
- keyring 锁定时任务进入可解释等待，解锁后从 Durable Task 状态继续。本阶段不建立
  任意应用密码管理 UI、通用 SecretGrant 或自动登录系统；完整 Secret Broker 在
  Goal 05 收敛。

### 5. 固定 system-service 合同

- 定义本场景需要的 typed facts：unit load/active/sub state、关键属性、受限 journal
  摘要、相关进程状态、source、captured_at、TTL、sensitivity 和 evidence reference。
- 定义严格 ActionSpec：只允许观察以及对固定 fixture 执行 start/restart 等 E1 动作；
  写清 resource scope、precondition、timeout、idempotency、receipt、verify 和
  reconciliation。
- 首选 systemd user D-Bus；仅在已证明缺口时使用固定绝对 executable 和 argv 数组。
  禁止 shell 字符串、管道、重定向、root、system unit、任意 unit 和任意路径。

04A 必须先通过架构守卫、合同测试和 convergence matrix 审查；不能仅凭接口存在进入
04B。

## 04B：实现固定 systemd 纵向场景

1. 创建可重复失败/恢复的 `vibeos-goal04-fixture.service`、Agent-owned 状态和测试
   控制器，写明初态、允许效果、最大重试、禁止影响、成功和停止条件。
2. 固定用户目标为“诊断并恢复 VibeOS 测试用户服务，确认恢复完成”。unit、对象或
   目标有实质歧义时进入 `awaiting_clarification`，不得猜测最像服务。
3. 按需采集 unit、journal 和 process facts。journal 只读取明确 unit 与时间窗口，
   进入模型前确定性裁剪和脱敏；不遍历 home、整盘日志或用户其他服务。
4. 持久化 typed proposal 后才执行；worker 在动作后崩溃时先查询真实 unit 状态，
   不盲目重复 restart。
5. verifier 独立重新查询状态、健康条件和必要日志，不复用执行返回值。一次安全恢复
   无效时根据证据 replan、询问或明确失败，不无限重试。
6. TerminalOutcome 包含诊断、动作、当前状态、证据 ID、完成判断和未解决风险。

## 04C：崩溃、秘密和真实环境验收

- 在事实采集、模型调用、proposal 提交、外部动作前、外部动作后 receipt 前、verify
  前和等待期间分别注入 daemon/worker 崩溃；恢复不得重复未知副作用。
- 覆盖 unit 不存在、无权限、journal 不可用、keyring locked、provider timeout、坏
  schema、恢复无效、stale fact 和并发 dispatch。
- 使用高熵 canary 扫描 DB、event、outbox、trace、日志、导出、argv、环境和 exception，
  证明 provider key 不泄漏。
- WSL 只验证非桌面和持久内核；真实 Fedora GNOME VM 验证 Secret Service、systemd
  user session、一次真实 provider 调用和 fixture 恢复。分别记录环境、命令、结果和
  未覆盖边界。
- 最终重新运行 Goal 03 公共兼容、迁移、19 capability、完整质量和架构门禁。

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
- [ ] Task Store、Effect Policy、工具注册和 observation/context 各只有一个权威 owner；
- [ ] 19 capability、旧 pending work 和公共入口有逐项重分类矩阵，不能机械映射的旧
  状态 fail-closed；
- [ ] additive migration 将规范 schema/数据转为 `effect_level`/E0-E4，冻结历史 migration
  未修改，空库/旧库/失败恢复验证通过；
- [ ] production 源码、当前 schema/contract、CLI/D-Bus/HTTP/Python payload 和普通测试
  不再包含 `L0-L3`、`risk_level` 或 `PermissionPolicy`，architecture guard 强制该规则；
- [ ] 04A convergence matrix、架构守卫和合同门禁通过后才开始场景实现；
- [ ] 本场景所有模型调用只经 Gateway，strict schema、总预算、timeout、取消和错误
  分类可证明，且没有新增直接 provider 调用；
- [ ] provider key 仅由窄 transport 解析，不出现在持久状态、模型上下文、日志、argv、
  普通 env、异常或导出；locked/unlock 恢复通过；
- [ ] fixture 从已知失败状态进入定义健康状态，独立 verify 支持完成判断；
- [ ] unit 歧义、不存在、无权限、journal 不可用和恢复无效均有安全终态；
- [ ] 只对 allowlisted user fixture 执行 E1，无 shell/root/system unit/任意路径旁路；
- [ ] 所列崩溃边界、重复 dispatch 和未知结果均安全 reconciliation；
- [ ] 真实 Fedora VM 的 Secret Service、provider 和 systemd 场景证据完整，WSL/mock
  没有被当成真实环境证明；
- [ ] Goal 03 兼容矩阵、19 capability、迁移和共同质量门禁无回归。

## 必交付物

- Goal 04 基线记录和执行地基 convergence matrix；
- L0-L3 -> E0-E4 重分类矩阵、additive migration、公共 contract 版本变更和唯一
  EffectPolicy；
- 现有 Context/Observation/Tool Registry 的权威边界和机器可检查守卫；
- 最小 Model Gateway、provider transport、SecretRef CLI 和泄漏测试；
- systemd user fixture、service facts、typed E0/E1 provider、verifier 和黄金场景；
- 崩溃/故障矩阵、WSL 非桌面证据和真实 Fedora GNOME 验收记录；
- 更新后的当前状态、架构、秘密、效果治理和运维文档。

只有最小地基先收敛为唯一权威，并被固定 systemd 用户任务在真实环境、崩溃恢复、
秘密隔离和独立完成判断上证明后，才结束本 Goal。
