# Goal 06：用一个 E2 金丝雀证明受控提权与完整回滚

- 阶段：06 / 09
- 依赖：[Goal 05](05_unprivileged_tasks_and_installable_runtime.md)全部完成
- 风险：极高
- 完成后进入：[Goal 07](07_gnome_mixed_task_mvp.md)

## 给 Codex 的命令

你要只选择**一个**范围严格、所有副作用可枚举且可验证回滚的本地 E2 操作，证明
Agent 能自行提出提权方案、由隔离 Reviewer 审核、经确定性 policy 限制、获得一次性
最小权限、执行、验证，并在失败或崩溃后恢复原状态。不得顺便开放第二个 E2 动作，
不得增加通用 sudo/shell，也不得把“恢复服务 active/inactive 状态”误当成已回滚
服务运行期间产生的其他副作用。

先做代码和目标 Fedora 平台 feasibility spike。优先使用现有 typed system D-Bus API；
只有它无法满足已固定 canary，且用户明确批准安装特权组件后，才设计最小 system-bus/
polkit helper。Rust 不是默认交付物，不能为了技术偏好引入新的构建链。

## 项目总体思想

VibeOS 可以比用户更直接地控制系统，但模型 Reviewer 不是权限根。最终授权来自
版本化确定性 effect policy、固定 typed verb、精确资源和操作系统权限机制。Reviewer
只能在 policy 上限内输出 approve/deny/needs_user，不能修改 proposal、扩大 scope、
执行动作或发放宽泛权限。

E2 的核心不是“命令可逆”，而是完整事务：执行前保存足够前态，执行后独立验证，
失败时运行操作专属 compensator，再验证所有声明副作用都恢复。无法证明回滚的操作
必须提升为需要用户决定或拒绝，不能标为 E2 自动提权。

## 预期进入状态与现场核对

预期 Goal 05 已有可安装 Runtime、唯一 Durable Task Engine、少量 E0/E1 provider、
typed facts、ActionProposal/Receipt/Evidence 和独立 verifier。开始前必须核对：

- 目标 Fedora 版本、polkit/system-bus 行为和可用 typed 系统 API；
- 当前用户提出的提权偏好：Agent 自审 E2，但安装/启用特权机制需用户批准；
- 候选操作的全部直接/间接副作用、持久化位置、并发参与者和恢复边界；
- 是否存在不需要自有 helper 的成熟系统 API；
- Goal 05 安装包如何在不默认提权的前提下可选安装机制。

## 核心目标

先提交一份只包含一个候选的 canary 选择记录，向用户展示 typed verb、目标 fixture、
全部副作用、权限机制、回滚和失败处置；**获得用户对这个实现范围的明确确认后**再
冻结并实现。该确认是高风险设计范围确认，不代替运行时 deterministic policy。

```text
typed ActionProposal
  -> deterministic EffectAssessment(E2, exact resource/effects)
  -> isolated ReviewerDecision
  -> policy intersection
  -> one-shot PrivilegeLease
  -> typed system API or allowlisted helper verb
  -> prepare / execute / verify
  -> commit OR compensate / rollback verify
  -> EvidenceBundle and audit
```

候选 canary 必须作用于专用测试资源或明确可安全恢复的真实资源。不得选择包管理、
bootloader、磁盘分区、账户、安全策略、网络主连接、任意文件编辑或未知 systemd
系统服务。若 spike 找不到满足全部条件的操作，停止实现、提交 `no-safe-canary` 证据
并请求用户决定；不得降低回滚标准使 Goal 看似完成。

## 必须实施

1. **canary 选择与副作用账本**
   - 记录用户收益、目标资源、前置条件、正常效果、间接效果、blast radius、并发风险、
     回滚动作、回滚验证和不可恢复失败；
   - 对 systemd system unit 候选，必须分析服务运行期间文件、socket、网络、子进程和
     外部状态；只有恢复全部声明效果才可视为可回滚；
   - 建立专用 fixture/snapshot，禁止对用户真实关键服务试验。
   - 在用户确认前只允许 spike、文档和无副作用探针，不实现或安装 production helper。

2. **最小 EffectAssessment**
   - 根据 typed verb、参数、资源、数据、权限、可逆性和 blast radius 评估，不只按
     capability 名称；
   - 未知参数、资源不匹配、回滚缺失、外流或不可逆结果一律 `needs_user` 或拒绝；
   - 本阶段只完整支持 canary 的 E2 规则，并保留 E0/E1、E3/E4 的边界测试；不建设
     覆盖未来所有动作的通用规则语言。

3. **隔离 Reviewer**
   - 与 executing planner 使用独立调用、最小只读上下文和 strict decision schema；
   - 只能输出 approve/deny/needs_user、scope、检查项、有效期和理由；
   - 模型不可用、输出无效、不确定或与 policy 冲突时 fail-closed；
   - approval 绑定 proposal digest、精确资源、次数、deadline 和 policy/reviewer 版本。

4. **最小特权机制**
   - 优先调用现有系统 D-Bus typed method；调用身份和 polkit action 必须精确；
   - 自有 helper 只有经 spike 证明必要并由用户批准安装时才允许；只接受一个版本化
     typed verb，不加载 Python、模型、shell、插件或任意路径/命令；
   - lease 使用 nonce/fencing、一次消费和短期限；跨用户、重放、参数替换、资源替换
     和过期调用全部拒绝；
   - 不安装通用 `sudoers`、通用 polkit JavaScript always-yes rule 或长期 root daemon。

5. **操作专属事务与回滚**
   - 实现 probe、precondition、capture pre-state、prepare、forward、health check、
     rollback、rollback check、commit；
   - 前态和 rollback plan 持久化后才能执行；forward/verify 失败自动 rollback；
   - 在外部动作成功但 receipt/commit 前崩溃时，根据真实系统状态 reconcile；
   - `rollback_failed` 是显式高优先状态：停止进一步动作，保留证据并通知用户。

6. **用户边界、审计和安装**
   - 安装/启用 helper 或 polkit 定义必须明确向用户展示并获得批准；
   - E3 仅保留协议 fixture：付款、外部通信/发布、私人数据外传、不可逆删除、账户和
     重大安全变化不能因 Reviewer 批准降为 E2；
   - audit 记录 proposal digest、assessment、decision、lease、OS identity、receipt、
     rollback 和验证证据，但不记录 secret 或私人正文；
   - Runtime 未安装可选特权机制时，E0/E1 功能照常可用，E2 明确 unavailable。

## 明确非目标

- 不开放第二个 E2 capability、任意 sudo、root shell 或任意文件写入；
- 不真实执行付款、外发、账户变更或其他 E3；
- 不构建通用事务 DSL、通用 root 插件系统或后台永久授权；
- 不让 Reviewer 取代 deterministic policy、polkit 或操作专属 verifier；
- 不把 VM snapshot 回滚当成产品级单操作 rollback；
- 不因 Rust、systemd 或 polkit 看似成熟而跳过 canary 可逆性证明。

## 验收条件

- [ ] canary 选择记录列出全部声明副作用、风险、回滚和不可恢复条件，并保存用户对
  该实现范围的确认引用；
- [ ] 只有一个 typed E2 verb 可达，参数/资源/重放/过期/跨用户攻击均 fail-closed；
- [ ] Reviewer 与执行模型隔离，policy 可以否决其批准，模型故障不会获得权限；
- [ ] 正常路径完成 prepare/execute/verify/commit 并产生完整 EvidenceBundle；
- [ ] forward、verify、rollback 各阶段故障和 daemon/OS 进程崩溃均经过恢复矩阵；
- [ ] rollback 后所有声明副作用与前态一致，不只比较一个状态字段；
- [ ] rollback 失败进入显式状态并停止扩大影响，不被报告为成功；
- [ ] 安装/启用特权机制经过用户批准，普通安装默认没有宽泛权限；
- [ ] Goal 03–05 的兼容、用户态任务和安装路径没有回归；
- [ ] 真实 Fedora VM 完成攻击、崩溃和回滚验收，WSL/mock 不替代该证据；
- [ ] 共同质量门禁全部通过，仓库中不存在第二个 E2 实现。

## 必交付物

- canary feasibility/选择记录和副作用账本；
- 最小 EffectAssessment、独立 Reviewer contract 和一次性 PrivilegeLease；
- 一个系统 typed API adapter，或经批准的最小 helper/polkit 定义；
- canary TransactionDriver、故障/攻击/回滚矩阵和真实 Fedora 证据；
- 特权机制安装、禁用、卸载、审计和紧急处置文档。

只有一个 E2 动作在最小权限下可执行、可审计、可崩溃恢复并能证明完整回滚时，
才结束本 Goal。
