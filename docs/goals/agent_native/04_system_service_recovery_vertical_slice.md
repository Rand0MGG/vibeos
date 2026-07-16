# Goal 04：交付第一个 API/CLI 优先的真实系统任务

- 阶段：04 / 09
- 依赖：[Goal 03](03_reconcile_goal01_goal02.md)全部完成并形成干净基线
- 风险：中高
- 完成后进入：[Goal 05](05_unprivileged_tasks_and_installable_runtime.md)

## 给 Codex 的命令

你要让 VibeOS 第一次完成一个对用户有明确价值的真实纵向任务：**诊断一个失败
或异常的 `systemd --user` 服务，在安全范围内恢复它，并用独立证据确认结果**。
只实现这个场景需要的最小 Model Gateway、provider 密钥存取、机器事实和 E0/E1
动作。不要先建设通用 Secret Broker 平台、全机索引、通用 Context Router、任意
shell，或迁移所有历史模型调用。

开始前阅读总 README、Goal 03 交付、产品章程、ADR 和当前源码。参考 fixture 固定为
`vibeos-goal04-fixture.service`：它只读写测试专用临时/Agent-owned 状态，第一次启动
按显式 failure flag 失败并写入可识别 journal 事件，flag 被测试控制器安全解除后再次
启动进入稳定健康状态。fixture 的安装、flag 和状态目录必须与用户真实 service/data
隔离。若目标 VM 无法按此语义实现，先报告具体平台限制，不能私自换成真实用户服务。
若当前代码已经具备部分能力，复用并收窄，不要因为文件名或原计划再次重写内核。

## 项目总体思想

VibeOS 的优势不是模仿人点击，而是能比用户更直接地读取和操作 Linux：优先使用
稳定 API、D-Bus、结构化 CLI 和系统服务；只有这些路径不存在时才进入 UI。
Agent 可以调用高能力云模型，但模型只接收任务所需的最小上下文，不能接触 provider
key，也不能直接决定权限、执行命令或宣布任务完成。

机器知识必须来自可追溯观察。服务状态、journal、进程和版本事实带来源、采集时间、
TTL 与敏感级别；过期时重新观察。动作结果不是完成证据，必须在动作后重新查询服务
状态、健康条件和必要日志。

## 预期进入状态与现场核对

预期 Goal 03 已留下：唯一 Durable Task Engine、统一数据库、19 个现有 capability、
兼容性矩阵和干净提交。Codex 必须现场确认：

- 当前 provider 调用入口、配置方式和实际启用 provider；
- 哪些 planning/understanding 组件参与本场景，不能按旧文档假定“至少 9 个模块”；
- Secret Service/GNOME Keyring 在目标 VM 的可用性和锁定行为；
- systemd user D-Bus 与结构化 `systemctl --user` 的实际能力及权限边界；
- 当前 Task Engine 的 wait/retry/replan/restart 接口；
- 所选 fixture 不接触用户真实服务和数据。

若现场与预期不同，更新任务内的进入状态记录；不得顺手迁移无关 provider、collector
或 capability。

## 核心目标

交付一条可复核纵向链路：

```text
用户目标
  -> 歧义检查与 GoalContract
  -> 最小云模型请求（无 secret）
  -> 按需采集 user service / journal / process 事实
  -> 诊断与结构化 E0/E1 ActionProposal
  -> systemd D-Bus 或固定 argv 执行
  -> 重新观察、验证、必要时安全重试/replan
  -> EvidenceBundle 与 TerminalOutcome
```

成功标准是用户能够理解“为什么失败、Agent 做了什么、服务现在是否健康”；不是
完成若干抽象类或让命令返回零。

## 必须实施

1. **固定真实场景**
   - 创建上述 `vibeos-goal04-fixture.service`，可重复进入已知 `failed` 和健康状态；
   - 写明可操作 unit、允许动作、最大重试、禁止影响、成功与停止条件；
   - 参考用户命令固定为“诊断并恢复 VibeOS 测试用户服务，确认恢复完成”；
   - unit 名称或用户意图有实质歧义时进入 `awaiting_clarification`，不得猜测最像对象；
   - 不对真实未知服务执行 restart/enable/disable。

2. **最小 Model Gateway**
   - 为本场景参与的语义调用定义 provider-neutral request/response、purpose、Task/
     Attempt、timeout、预算和 strict response schema；
   - 首期只支持当前实际 provider 的 OpenAI-compatible adapter，统一有限重试、错误
     分类、取消和 JSON/schema 校验；
   - 只迁移本场景真实调用链。其他历史模型入口保留为有 owner 的兼容债务，不删除
     `provider_client` 或全局替换所有调用；
   - 模型输出只能提出诊断或 typed proposal，确定性代码验证 unit、参数和允许效果。

3. **provider secret 最小闭环**
   - provider key 存在 freedesktop Secret Service/GNOME Keyring；核心状态只保存 opaque
     reference 和非敏感 metadata；
   - 由窄 provider transport 在发请求时读取并使用 key，planner、模型、Task Store、
     trace、argv、普通 env 和错误回显不得获得明文；
   - CLI 提供 TTY 安全导入/status/delete，环境变量只允许一次显式迁移，不作为长期
     fallback；
   - keyring 锁定时任务进入可解释等待，解锁后继续；首期不建立面向任意应用的通用
     SecretGrant 或密码管理 UI。

4. **按需机器事实**
   - 只定义本场景需要的 typed facts：unit load/active/sub state、关键属性、受限 journal
     摘要、相关进程状态和采集时间；
   - 每项事实包含 source、captured_at、TTL、sensitivity、evidence reference；
   - journal 只采集与明确 unit 和时间窗口相关的最小片段，进入模型前确定性裁剪和
     脱敏；不遍历 home、整盘日志或建立通用索引；
   - stale 事实必须重采，派生诊断不能覆盖原始观察。

5. **结构化 E0/E1 动作**
   - 优先调用 systemd user D-Bus；只有实际缺口才使用固定可执行文件和 argv 数组；
   - 首期只允许观察及对 fixture 的 restart/start 等明确 E1 动作，不允许 shell
     字符串、管道、重定向、root、system unit 或任意路径；
   - proposal 在执行前持久化，动作有 timeout、receipt、幂等/对账策略；
   - worker 在动作后崩溃时先查询 unit 当前状态，不盲目重复 restart。

6. **完成判断与恢复**
   - verifier 独立重新查询 unit 状态、健康属性和必要日志，不能复用执行返回值；
   - 一次安全恢复无效时，根据证据 replan、询问或明确失败，不无限重试；
   - daemon 在采集、模型调用、执行、等待和 verify 各阶段重启后能继续或安全暂停；
   - TerminalOutcome 包含诊断、动作、当前状态、证据 ID 和未解决风险。

## 明确非目标

- 不迁移全部模型调用，不实现多 provider 智能路由或本地模型 runtime；
- 不建立通用 Machine State Index、向量库、知识图谱或全盘 collector；
- 不开放任意 CLI、shell、Bubblewrap、root、system-bus 或 polkit；
- 不管理系统级 unit，不修改 unit 文件、软件包、账户或网络配置；
- 不删除现有 Registry、provider 路径或 desktop adapter；
- 不把 CI fake、WSL dry-run 或仅调用成功当作真实场景验收。

## 验收条件

- [ ] 受控 user service 从已知失败状态进入已定义健康状态，独立 verify 通过；
- [ ] unit 歧义、无权限、unit 不存在、journal 不可用和恢复无效均有安全终态；
- [ ] 本场景所有云模型调用只经最小 Gateway，strict schema、timeout、取消、429/5xx
  和坏 JSON 测试通过；
- [ ] provider key 不出现在 Task DB、event、outbox、trace、日志、argv、普通 env、
  exception 或导出；高熵 canary 扫描通过；
- [ ] 真实 GNOME VM 的 Secret Service 完成一次 provider 调用，locked keyring 等待/
  解锁恢复符合设计；
- [ ] 机器事实有来源、TTL 和敏感级别，stale 重采与 journal 最小化可证明；
- [ ] 动作只针对 allowlisted user fixture，无 shell/root/system unit 旁路；
- [ ] 模型调用前、动作后提交前、verify 前和等待中崩溃恢复不重复副作用；
- [ ] 用户得到可解释诊断、动作、完成证据和剩余风险；
- [ ] Goal 03 兼容矩阵和共同质量门禁继续通过，没有删除无关生产路径。

## 必交付物

- 一个可重复失败/恢复的 systemd user service fixture 和黄金场景；
- 本场景最小 Model Gateway、provider transport 和 strict contracts；
- Keyring provider key 导入/使用/锁定恢复及泄漏测试；
- user service/journal/process facts、E0/E1 provider、verifier 和 EvidenceBundle；
- 真实 WSL 非桌面测试与 GNOME Secret Service 验收记录，明确区分二者。

只有 Agent 真正完成这个用户任务，并且秘密、动作、恢复和完成证据边界可证明时，
才结束本 Goal。
