# Goal 06：扩展 API/CLI 用户态能力并建立可安装 Runtime

- 阶段：06 / 11
- 依赖：[Goal 05](05_model_gateway_and_secret_broker.md)全部完成
- 规模：XL
- 风险：中高
- 完成后进入：[Goal 07](07_gnome_mixed_task_mvp.md)

## 给 Codex 的命令

你要把 Goal 04 的单一 systemd user service 场景扩展为一个小而真实的用户态
Linux 操作集合，并让 VibeOS 能从干净 Fedora 环境重复安装运行。只从下面固定的
四个用户任务中抽象最小 Machine State、Context Router 和 ActionProvider；保留
现有 19 个 capability 与 Registry，通过 adapter 接入而不是一次迁移、改写或删除。

Goal 05 已经收敛 Model Gateway、provider route 和 Secret Broker；本 Goal 必须直接
复用，不能为新任务重新读取环境变量、创建 provider client、secret store 或模型
路由。现有 `ContextPackageRegistry`/`ObservationService` 和
`ToolRegistry`/`CapabilityRecipeRegistry` 仍分别是事实与动作的唯一 production
注册路径。`MachineFact` 和 ActionSpec 是这些路径的严格合同演进，不是第二套平台。

API/D-Bus/结构化 CLI 仍是默认路径。不要开放任意 shell，不要因为想象中的未来
插件而设计通用执行平台，也不要强制引入 systemd transient unit 或 Bubblewrap；
只有固定场景的威胁模型和测试证明需要时才增加最小 profile。

## 项目总体思想

Agent 应比用户更了解电脑，但这种了解来自可验证机器事实和稳定系统接口，不是把
整盘内容交给模型。每个动作必须有 typed input、真实效果、资源范围、timeout、
receipt、verify 和恢复策略。E0 观察和 E1 用户态动作可以自主执行，但仍受当前
GoalContract、数据范围和资源预算约束。

平台抽象只能从真实重复需求中长出来。本阶段的成功是多个有用任务共用一套窄
contract，而不是完成一个覆盖所有命令、所有文件和所有应用的 Action Fabric。

## 预期进入状态与现场核对

预期 Goal 04 已证明 user service 诊断/恢复纵向链路，Goal 05 已交付唯一 Model
Gateway、云端路由、Secret Broker、本地模型准入结论和规范 E0-E4 Effect Policy。
开始前现场确认：

- Goal 03 的兼容矩阵、19 个 capability 和所有外部入口仍通过；
- Goal 04/05 哪些 schema、provider、collector、route 和 secret contract 真正被两个
  以上场景复用；
- Goal 04 删除 `L0-L3`/`risk_level` 后，production 只剩 E0-E4 `EffectPolicy`；架构
  守卫继续禁止旧语义回流；
- 当前 Context/Observation 与 Tool/Recipe Registry 的调用者和扩展点；
- 当前 Fedora/Ubuntu 支持声明、Python 构建方式、systemd user unit 和安装脚本；
- 目标机器具备哪些稳定 D-Bus/API，哪些任务确实只能用结构化 CLI；
- 用户态动作能否建立独立 verify；无法验证的动作不得加入首批集合。

## 核心目标

实现以下四个固定任务组成的最小用户态操作面：

1. 诊断并恢复 `vibeos-goal04-fixture.service`（E0 观察＋E1 user-service 动作）；
2. 报告指定挂载点的容量、可用空间和 inode 压力（E0，只使用文件系统 metadata，
   不递归扫描文件内容）；
3. 报告当前用户进程的 CPU/内存资源排序及证据（E0，不停止进程、不读取 argv 中的
   secret 或进程环境）；
4. 通过现有 `app.open` 路径打开支持 VM 镜像中固定安装的标准文本编辑器并验证应用
   已出现（E1；VM 定义固定应用包，不由 Codex 临时替换为其他任务）。

如果目标 Fedora 版本缺少完成其中一项所需的稳定接口，Codex 必须先报告阻塞及最小
替代方案，由用户确认后才能改变任务集合；不能自行换成更容易但价值不同的 demo。

```text
GoalContract
  -> typed fact query and freshness check
  -> deterministic context manifest
  -> path selection: system API -> app API -> D-Bus -> structured CLI
  -> ActionProposal -> provider -> receipt
  -> independent observation/verify -> Task transition
```

同时交付一个非 editable、版本化的基础 Runtime artifact，在干净 Fedora VM 安装
后能够运行 daemon、CLI、数据库迁移和上述任务。完整升级回滚留到 Goal 10，扩展交付
留到 Goal 11。

## 必须实施

1. **固定任务与效果边界**
   - 每个任务写明用户收益、初态、允许资源、E0/E1、首选接口、fallback、禁止效果、
     完成条件和证据；
   - 只接受固定 schema 中的目标和参数；实质歧义先询问；
   - 不包含删除用户文件、停止未知进程、发送外部消息、安装软件或系统级修改。

2. **从任务抽象最小 Machine State**
   - 只新增场景实际需要的 `MachineFact` 类型、collector、TTL、source、confidence、
     sensitivity 和 evidence reference，并通过现有 `ContextPackageRegistry`/
     `ObservationService` 持有和查询；
   - 支持按 type/subject/task 查询、stale 标记、失效和最近变化，不保存任意文件正文；
   - Context Router 是现有 observation/context 路径上的确定性选择器，只根据固定
     fact query 和
     [系统框架的数据等级](../../product/agent_system_framework.md)生成最小 manifest；
   - 禁止另建通用 Machine State 数据库、第二套 context registry 或绕过 evidence 的
     collector cache；若需要持久化最近事实，必须进入同一数据库并明确唯一 owner；
   - 不建立“三类事实必须齐全”等人为平台指标，不做本地模型基准或长期用户画像。

3. **最小 ActionSpec/Provider**
   - 为固定任务扩展现有 `ToolSpec`/adapter 元数据，定义 strict parameters、effect、
     resource scope、precondition、timeout、idempotency、receipt、verify 和
     reconciliation；不得创建平行 `ActionRegistry`；
   - 路径选择由确定性 capability/path policy 完成，模型只能在允许候选中选择；
   - 结构化 CLI 使用固定 executable 和 argv list，拒绝 shell metacharacter、环境扩张、
     相对 executable、未约束路径和模型生成整段命令；
   - systemd transient user unit 只用于确实需要独立生命周期的子进程；Bubblewrap 只在
     threat model 证明文件/namespace 隔离必要时引入少量版本化 profile。

4. **兼容接入而不是全量迁移**
   - 当前 19 个 capability 继续由既有 Registry 发现；为首批任务增加窄 adapter，
     不创建第二套 Effect Policy、Tool Registry 或平行 capability 名单；
   - 不删除 `ToolRegistry`、`CapabilityRecipeRegistry` 或其他旧路径；只有某一条路径
     经过 Goal 03 式逐项等价并获得独立删除批准后才可收敛；
   - 新 contract 不应迫使未参与场景的 capability 改写。

5. **资源、安全和故障处理**
   - 每个 provider 有 wall-time、输出、CPU/内存/并发和网络策略；默认无额外网络；
   - 日志和 receipt 记录 argv 摘要、provider、exit/signal、duration 和 bounded output，
     不记录 secret 或无关私人内容；
   - timeout、cancel、崩溃和未知结果进入 reconciliation，不把“进程已启动”当完成；
   - verifier 从独立事实源重新观察；无法建立 verifier 的动作不得成为 production。

6. **基础可安装 Runtime**
   - 默认生成版本化 Python wheel、锁定依赖清单和幂等安装工具；若现场证明 wheel
     无法满足当前项目，必须先提交 ADR 并获得用户确认，不能自行切换打包体系；
   - 提供幂等安装/配置流程，安装 systemd user service、必要 D-Bus 文件、数据库迁移
     和 CLI，不要求源码 checkout，不依赖仓库 `.env`、开发者 home 或 editable path；
   - provider 配置只保存 Goal 05 的非敏感 route/SecretRef；秘密仍由 Secret Broker
     管理，安装器不得把 key 写入 EnvironmentFile、命令行或普通配置；
   - 默认安装不包含 root helper、polkit、桌面输入或第三方扩展；
   - 在干净 Fedora VM 执行 install -> configure provider key -> daemon ready ->
     固定任务 -> uninstall；卸载默认保留可导出的用户状态。

## 明确非目标

- 不迁移或删除全部 19 个 capability 的 Registry/执行逻辑；
- 不开放任意 shell、任意文件操作、root、polkit 或 system-bus；
- 不构建通用容器平台、插件 SDK、公共市场或复杂 sandbox 编排器；
- 不实现完整安装升级回滚、跨发行版包仓库或独立 Linux 发行版；
- 不扩大 Goal 05 的 provider/secret 范围，不让本地模型、云模型或扩展决定数据级别
  和效果等级；
- 不把可发现但环境不可用的 adapter 伪装成已执行成功。

## 验收条件

- [ ] 上述四个固定任务各有真实用户目标、效果边界、路径和独立完成证据；
- [ ] API/D-Bus 可用时不使用 CLI，结构化 CLI 没有 shell 注入或路径逃逸；
- [ ] 新 MachineFact 只服务固定任务，TTL/stale/来源/敏感级别和最小 context 可证明，
  且没有第二个事实/context 权威；
- [ ] 每个 E1 动作在执行后崩溃、timeout、cancel 和重复 dispatch 下安全对账；
- [ ] 现有 19 个 capability、Goal 03 兼容矩阵及 Goal 04 场景没有回归；
- [ ] 旧 Registry 未被批量删除，Tool/Context 接点、owner 和未来删除门禁有记录；
- [ ] 所有模型和 secret 使用继续通过 Goal 05 的唯一 Gateway/Broker，没有 `.env` 或
  私有 provider client 回退；
- [ ] 如果使用 transient unit/Bubblewrap，有真实需求和对抗测试；未使用时没有新增依赖；
- [ ] 非 editable artifact 可在干净 Fedora VM 安装、运行固定任务和卸载；
- [ ] WSL 测试、真实 Fedora 证据和受限环境不可用结果被准确区分；
- [ ] 共同质量门禁、安装 smoke 和文档链接全部通过。

## 必交付物

- 首批任务清单与黄金场景；
- 现有 Context/Observation 与 Tool/Recipe 路径上的最小 MachineFact、Context manifest
  和 ActionSpec/Provider contracts；
- 固定 API/D-Bus/CLI adapters、receipt、reconciliation 和 verifier；
- Registry 兼容接入说明，不含全量迁移或删除；
- 版本化基础 Runtime artifact、幂等安装/卸载流程和干净 Fedora 证据。

只有多个真实用户任务在同一受治理路径上成立，且 Runtime 能从干净环境重复安装
运行时，才结束本 Goal。
