# Goal 05：收敛 Model Gateway、模型路由与 Secret Broker

- 阶段：05 / 11
- 依赖：[Goal 04](04_core_execution_foundation_and_system_service_slice.md)全部完成
- 规模：XL
- 风险：高
- 完成后进入：[Goal 06](06_unprivileged_tasks_and_installable_runtime.md)

## 给 Codex 的命令

你要在 Goal 04 已交付的 Model Gateway v1、SecretRef v1 和独立 provider transport
进程边界上继续扩展，把它们收敛为所有生产模型调用和 secret 使用的唯一入口，并用
两个固定证明完成模型路由和秘密使用边界：

1. 同一 Gateway 能按确定性策略调用 OpenAI 与 DeepSeek 的云端 adapter，完成固定的
   goal-understanding 和 service-diagnosis purpose；
2. 一个受控 authenticated loopback fixture 只能通过 Secret Broker 执行窄请求，
   Core、planner、模型、Task Store 和日志始终只看到 `SecretRef`，没有读取 secret
   明文的 API。

本 Goal 不是建设任意密码管理器、模型市场或复杂调度平台。模型迁移必须通过兼容
facade 收敛，不得重写 Durable Task Engine、planning pipeline 或所有语义模块。秘密
使用必须绑定固定操作，而不是提供 `get_secret()` 给 Agent。

Goal 04 的 Gateway request/response、预算与失败分类、SecretRef、transport redacted
receipt 和进程/D-Bus 隔离是本 Goal 的生产进入合同，不是临时 scaffold。不得复制、
平行实现或先删除后重建。若现场证明其中任一合同没有真正交付，应停止 Goal 05 的
扩展并把缺口作为 Goal 04 remediation 处理，而不是在 Goal 05 造第二套基础设施。

## 项目总体思想

高能力云模型是 VibeOS 的主要推理能力；本地模型只有在具体 purpose、数据边界、质量、
延迟和资源基准全部通过后才能被路由。模型只能理解目标、生成受约束提案或解释证据，
不能决定 effect、权限、secret scope 或现实完成状态。

Secret Broker 的目标不是让秘密消失于本机内存，而是让 Agent 核心、模型上下文、任务
持久化、日志和扩展没有“读取秘密明文”的接口。秘密只能由窄 transport 在执行已批准、
已绑定的操作时使用。必须明确同一 Unix 用户下的信任边界：首期防止模型、普通 Core
代码、日志和意外导出接触秘密；若要抵抗已攻陷的同 UID 进程，需要新的 OS identity/
sandbox 设计和用户批准，不能在文档中虚构已经解决。

## 预期进入状态与现场核对

预期 Goal 04 已交付：

- 唯一 Durable Task Engine、E0-E4 Effect Policy、O0-O2 Observation 路径、ToolRegistry
  和 canonical action receipt/evidence 权威；
- 一个版本化、provider-neutral 的 Gateway v1 request/response、预算/失败分类和一个
  真实云 adapter；
- provider key 的 SecretRef v1、TTY import/status/delete、locked-keyring 恢复和窄
  provider transport/Broker 进程；
- semantic/planner worker 与 secret-capable transport 的进程/D-Bus 隔离证明；
- systemd user-service 场景、机器事实、E0/E1 action、receipt 和独立 verifier；
- 现有直接 `provider_client` 调用点、兼容 owner 和删除门禁清单。

开始前现场复核所有生产模型调用者，不依赖旧模块数量。至少检查 intent、clarification、
goal synthesis、candidate selection、understanding、strategy、replan 和 semantic acceptance
的实际调用链；确认是否有模块直接读取 provider key、构造 HTTP、绕过 command 总预算
或把完整私人上下文发送给 provider。

同时确认：目标 OpenAI/DeepSeek API 的当前合同、用户实际提供的 credential、预算和
允许的数据范围；Secret Service 的锁定行为；本地模型 runtime 是否已存在；Goal 04
留下的 architecture debt owner 是否准确。没有用户提供的真实 provider credential 时，
可以完成离线合同和故障测试，但不能把对应 provider 标为真实验收通过。

## 核心目标

形成两条唯一受治理链路：

```text
ModelPurpose + ContextManifest + Budget
  -> deterministic ModelRoutePolicy
  -> selected ProviderAdapter
  -> SecretBoundTransport
  -> strict response validation
  -> typed result / classified failure / bounded fallback

SecretRef + one-shot SecretGrantRequest + exact operation
  -> deterministic secret policy
  -> broker-issued opaque grant
  -> narrow transport performs operation
  -> redacted receipt
```

所有生产模型请求最终都必须经过 Gateway。所有 production secret 使用只能通过没有
“返回明文”方法的 Broker/transport contract。兼容 facade 可以暂时保留旧函数签名，
但不得保留旧网络、预算或 secret 权威。Goal 05 可以版本化扩展 v1，却不能更换 owner、
建立不兼容平行类型或让 Goal 04 的 systemd 场景继续依赖旧路径。

## 必须实施

### 1. 扩展并统一 Model Gateway 合同

- 复用 Goal 04 的 `ModelRequest`/`ModelResponse` v1 和 `service_diagnosis` purpose；通过
  additive/versioned evolution 增加调用者所需字段，不得另建第二组 Gateway domain types。
- 扩展版本化 `ModelRequest`：purpose、task/attempt、schema、最小 context manifest、
  data classification、deadline、token/cost budget、cancellation 和 idempotency metadata。
- 扩展严格 `ModelResponse` 与既有 failure taxonomy：unconfigured、locked_secret、timeout、
  cancelled、rate_limited、provider_unavailable、budget_exhausted、invalid_json、
  schema_rejected、policy_denied 和 unknown_delivery。
- Gateway 负责总预算、timeout、有限重试、响应大小、JSON/schema 校验、审计摘要和
  redaction。caller 不得各自实现无限重试、宽松 parse 或隐式 provider fallback。
- 模型响应只进入 typed domain boundary；原始 provider payload 默认不持久化，必要的
  调试证据必须脱敏、限长、限期并由用户显式启用。

### 2. 收敛现有调用路径

- 对每个生产模型调用记录 purpose、输入数据级别、schema、预算、当前 provider、失败
  行为和 owner。
- 允许旧模块暂时保留，但其网络调用必须经过 Gateway 或无独立逻辑的兼容 facade；
  生产源码中不再允许直接读取 API key、直接构造 provider HTTP 或创建私有重试器。
- 为迁移前后的 clarification、compound goal、replan、acceptance 和离线/fail-closed
  行为建立兼容矩阵。不能为了统一接口降低 Goal 03/04 的行为合同。
- 更新 architecture baseline：已完成 Goal 03/04 的 legacy debt 不得继续挂在过期 owner
  名下；仍保留的模块必须有新 owner、边界和删除条件。

### 3. 两个云 provider adapter 与确定性路由

- 实现 OpenAI 与 DeepSeek 的独立配置/能力声明，即使底层都兼容 OpenAI 协议，也不能
  用一个含糊 provider name 隐藏模型、base URL、能力和数据策略差异。
- `ModelRoutePolicy` 只根据 purpose、用户配置、数据等级、provider 能力、健康、预算、
  latency/cost 上限和允许区域选择；模型不能选择自己或改变 policy。
- 固定验证 purpose 为 `goal_understanding` 与 `service_diagnosis`。每个 purpose 写明
  首选、允许 fallback、禁止 provider、最大上下文、完成 schema 和失败终态。
- fallback 只能在请求尚未产生未知计费/处理结果且数据 policy 允许时发生；每次选择
  和 fallback 都有可解释记录，不静默把数据发送到另一个 provider。
- 用户可通过 CLI/D-Bus 查看 provider status、purpose route 和选择理由，修改 route
  必须经过严格配置校验。

### 4. 本地模型准入边界

- 建立 purpose-specific admission benchmark，而不是先安装本地 runtime。至少衡量 strict
  schema 合格率、关键事实保持、危险 proposal 拒绝、歧义识别、延迟、内存和失败模式。
- 本地模型默认 `not_admitted`。只有某个明确 purpose 达到预先记录的门槛，且硬件和
  数据边界合适时，才能成为该 purpose 的候选；不能因离线或便宜自动获得权限。
- 本 Goal 可以诚实交付“没有本地模型通过”。不得降低门槛、用云模型结果冒充本地
  结果或让本地模型参与 Effect Policy、Secret Policy 和后续 E2 Reviewer。
- 不建设模型下载器、训练平台、量化流水线或全硬件矩阵。

### 5. Secret Broker 核心合同

- 复用 Goal 04 的 `SecretRef`、窄 provider transport、redacted receipt 和进程/D-Bus
  隔离；增加通用 grant 前先证明不会改变既有 provider smoke 和 systemd 场景合同。
- 扩展 opaque `SecretRef` 的非敏感 metadata、secret kind、owner、用途和 lifecycle；
  Task DB 只能保存引用和策略版本。
- Broker API 只允许 `import/status/delete` 和“为精确操作申请使用”；禁止向 Core、CLI、
  D-Bus、HTTP、模型或扩展返回 secret value。
- 一次性 grant 绑定 task、attempt、typed operation、provider/endpoint、resource、次数、
  deadline、policy version 和 nonce；参数替换、endpoint 替换、重放、过期和跨用户拒绝。
- transport 在最后责任点解析引用并执行请求；明文不得通过 argv、普通 env、临时文件、
  exception、trace、receipt 或 crash dump 传播。内存生命周期尽量短并记录现实限制。
- semantic/planner worker 继续与 Secret Broker/transport 分进程，并保持无 Secret Service
  session-bus 权限或等价的显式拒绝代理；不得因收敛调用者而退化为同进程模块约定。
- keyring locked、item missing、permission denied、broker restart 和 transport crash 都有
  可恢复或明确失败状态；不得回退到 `.env`、命令行 key 或模型询问用户明文。

### 6. 固定 Secret Broker 证明

- 建立无外部网络、无用户数据的 authenticated loopback fixture。它只接受一个固定
  typed read-only request，并用秘密验证调用；返回非敏感 challenge result。
- Core 只提交 operation 与 `SecretRef`；Broker/transport 完成认证请求并返回 redacted
  receipt。fixture、Core、DB、日志和导出分别运行高熵 canary 检查。
- 覆盖错误 secret、锁定、grant 重放、endpoint 替换、transport 崩溃、请求 timeout、
  unknown delivery 和 Broker 重启。该 fixture 只证明边界，不冒充真实用户价值。
- provider transport 与 fixture transport 共享 Secret Broker 合同，但不能共享含糊的
  任意 HTTP 执行入口。

### 7. 真实 provider 与隐私验收

- 使用用户明确提供的 OpenAI 和 DeepSeek credential 分别完成固定 purpose 的最小真实
  smoke；缺少某个 credential 时明确记录 external blocker，不伪造成功。
- 构造 D0/D1/D2/D3 数据样本验证 route policy；D2/D3 未获明确授权不得出站，秘密和
  无关机器事实永远不进入模型上下文。
- 记录 provider、model、purpose、预算、延迟、schema 结果和 redacted request digest，
  不记录 prompt 中的私人正文或秘密。
- 断网、429/5xx、慢响应、坏 schema、provider 切换和取消必须保持 Durable Task 的
  可解释等待/失败/重试边界。

## 明确非目标

- 不建设公共模型市场、自动购买额度、模型训练/微调或任意远程 provider 插件；
- 不承诺本地模型一定进入 production，不让模型决定路由、权限或数据等级；
- 不建立浏览器密码自动填写、网站登录、任意进程秘密注入或完整密码管理 UI；
- 不支持 secret 明文读取、复制、显示、日志或导出；
- 不新增 Task Store、网络执行平台、风险引擎或 provider 私有任务循环；
- 不批量重写 planning/understanding 业务逻辑，不因文件旧而删除仍有调用者的模块；
- 不实现 E2 Reviewer、桌面输入、主动建议、扩展市场或正式发行。

## 验收条件

- [ ] 所有生产模型网络请求经过唯一 Gateway 或无独立权威的兼容 facade；
- [ ] Goal 04 Gateway/SecretRef/transport v1 被原位复用和版本化扩展，没有第二套
  request/response、secret owner、网络预算或进程边界；
- [ ] 生产源码不存在新的直接 API key 读取、provider HTTP、私有重试/预算或宽松 parse；
- [ ] OpenAI/DeepSeek adapter、两个固定 purpose 和确定性 route/fallback 规则有严格合同；
- [ ] provider 选择、拒绝和 fallback 可解释，数据不会静默发送给另一个 provider；
- [ ] 本地模型准入按预设门槛执行，未通过时保持 `not_admitted`；
- [ ] Core/模型/Task DB/CLI/D-Bus/HTTP/扩展没有读取 secret 明文的 API；
- [ ] grant 与 task/operation/endpoint/次数/deadline 绑定，重放和替换攻击 fail-closed；
- [ ] provider 和 loopback fixture 的高熵 canary 泄漏扫描通过；
- [ ] locked/missing secret、Broker/transport crash、timeout 和 unknown delivery 有安全终态；
- [ ] 有 credential 的 provider 完成真实 smoke；缺失 credential 被准确标为外部阻塞；
- [ ] Goal 03/04 systemd 场景、19 capability 的发现/参数/基础功能、迁移和共同质量门禁
  无非预期回归；effect/批准行为只允许按已批准矩阵变化；
- [ ] architecture baseline、当前状态、模型路由、秘密威胁模型和运维文档与代码一致。

## 必交付物

- 模型调用者/purpose/数据/预算迁移清单和兼容矩阵；
- 从 Goal 04 v1 原位扩展的唯一 Model Gateway、OpenAI/DeepSeek adapters、RoutePolicy
  和 failure taxonomy，以及无平行实现的架构证明；
- 本地模型准入基准、门槛和 admission 结论；
- 从 Goal 04 v1 原位扩展的 SecretRef、一次性 grant、Broker/transport contract、
  进程/D-Bus 隔离和真实威胁边界说明；
- authenticated loopback fixture、攻击/崩溃矩阵和泄漏报告；
- provider status/route/secret 管理 CLI/D-Bus 合同与真实 smoke 证据；
- 更新后的 architecture baseline、状态、配置、隐私和故障处理文档。

只有模型和秘密都只有一个受治理入口，云端路由可解释、本地模型有明确准入边界，且
Agent 核心没有读取 secret 明文的能力时，才结束本 Goal。
