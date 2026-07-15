# Goal 06：实现特权控制面与按操作回滚

- 阶段：06 / 09
- 依赖：[Goal 05](05_unprivileged_action_fabric.md)全部完成
- 风险：极高
- 完成后进入：[Goal 07](07_desktop_and_linux_mvp.md)

## 给 Codex 的命令

你要在不向 Agent 暴露 root shell 的前提下，建立 E0-E4 效果评估、独立 Reviewer、
system-bus/polkit 特权边界和按操作 TransactionDriver。只选择一个真实、范围
窄、可验证回滚的 E2 系统操作作为 canary，完成端到端证明后停止扩张。任何
无法可靠捕获前态和恢复的动作都不能标为 E2 自动审核路径。

这是安全关键阶段。先做威胁建模和技术 spike；如果目标平台机制无法满足边界，
应交付证据和修订 ADR，而不是退回 `sudo`、任意 shell 或宽泛 polkit rule。

## 项目总体思想

用户希望可回滚的本地提权由 VibeOS 自己审核，而不是每次都询问用户；但审核
Agent 不是权限边界。执行 Agent 提交精确 proposal，确定性 policy 分类效果，
隔离 Reviewer 只能在既定授权和可回滚范围内批准 E2，最终由 polkit 和极小
allowlist mechanism 强制。E3 仍逐动作请求用户，E4 拒绝。

## 当前起点

- 当前 L0-L3 主要按动作名判断，L2 由用户审批；没有基于参数/资源/可逆性的
  E0-E4 engine；
- 没有自动 Reviewer、PrivilegeLease、system-bus helper 或真实事务回滚；
- Goal 05 已提供统一 ActionProposal、sandbox、receipt 和 evidence；
- Goal 03 已提供 secret 隔离，但本阶段不得让 privileged mechanism 读取无关秘密。

## 核心目标

以一个 canary 证明完整闭环：

```text
ActionProposal
  -> deterministic EffectAssessment
  -> independent ReviewerDecision
  -> scoped PrivilegeLease
  -> polkit/system-bus allowlisted verb
  -> TransactionDriver prepare/execute/verify
  -> commit OR rollback/verify
  -> EvidenceBundle and audit
```

canary 优先选择 systemd 系统 unit 的一个窄状态变更，因为有结构化 D-Bus API、
明确前态和可验证恢复。最终选择必须通过代码/平台 spike 记录理由；不得选择
包管理、bootloader、磁盘分区、账户、安全策略或任意文件编辑作为首个 canary。

## 必须实施

1. **Effect Engine**
   - 根据操作、参数、目标资源、数据外流、权限、可逆性和 blast radius 输出
     E0-E4，不仅根据 capability 名称；
   - E3 至少包含付款、外部通信/发布、私人数据外传、不可逆删除、账户/协议和
     重大安全变化；长期授权不能降级 E3；
   - 规则是版本化确定性代码，未知/冲突一律提升到 needs_user 或拒绝。

2. **独立 Reviewer**
   - 与 executing planner 使用隔离 prompt/context、独立调用记录和只读输入；
   - 只能输出 approve/deny/needs_user、scope、lease、checks 和 rationale；
   - 不能修改 proposal、执行动作、扩大 scope 或发放超过 policy 上限的 lease；
   - 模型不可用、输出无效或不确定时 fail-closed；确定性 policy 可否决批准。

3. **Privilege mechanism**
   - 优先调用现有系统 D-Bus API；确需自有 helper 时使用 system bus + polkit；
   - helper 优先用 Rust，实现固定 typed verb 和严格资源 canonicalization；不加载
     Python、模型、shell、插件或任意命令，不接收未约束路径；
   - 安装/启用机制由用户明确授权；每个调用绑定 task/action/reviewer decision、
     nonce、deadline、次数和目标资源；重放与跨用户调用被拒绝；
   - polkit 声明精确 action，不安装通用 JavaScript `.rules` 或隐式 always-yes。

4. **操作专属 TransactionDriver**
   - canary driver 实现 probe/precondition、capture pre-state、prepare、forward、
     health check、rollback、rollback check、commit；
   - 前态和证据持久化后才能执行；forward 或 verify 失败自动 rollback；
   - `rollback_failed` 是高优先终态，停止扩大影响并通知用户；
   - 断电/daemon 崩溃后根据 mechanism 状态和前态 reconcile，不能盲目重放。

5. **用户批准 E3**
   - 复用 Durable Task 的等待机制，展示精确对象、后果、不可逆性和有效期；
   - 批准绑定 proposal digest，只能使用一次；参数变化需要重新批准；
   - 本阶段只实现安全审批协议和无害测试 fixture，不真实执行付款/外发等 E3。

6. **威胁模型与审计**
   - 覆盖 prompt injection、proposal tamper、TOCTOU、symlink/path swap、lease replay、
     reviewer collusion、helper spoofing、DB rollback、crash 和 audit deletion；
   - 特权 audit 追加记录 proposal digest、policy/reviewer 版本、polkit identity、
     pre/post state 和 rollback evidence，不含 secret。

## 明确非目标

- 不提供任意 root command、`sudo -S`、交互 root shell 或模型生成 helper 参数；
- 不实现包管理、内核、boot、磁盘、账户或防火墙的通用自动修改；
- 不承诺任意系统动作可回滚，也不把备份命令文本当作已验证回滚；
- 不允许 Reviewer 绕过 deterministic policy 或把 E3 自动降为 E2；
- 不把用户一次安装授权解释为未来所有特权操作授权。

## 验收条件

- [ ] Effect Engine 的 E0-E4 决策矩阵覆盖参数/资源变化，未知情况 fail-closed；
- [ ] Reviewer 与执行 Agent 隔离，恶意 proposal/prompt 不能扩大 lease；
- [ ] 无有效 policy、decision、nonce、deadline 或资源绑定时 mechanism 均拒绝；
- [ ] helper/API 不能运行任意命令、访问未声明资源或接受路径/identity 替换；
- [ ] canary 在真实 VM 成功 forward、独立健康验证和 commit；
- [ ] 在 forward、verify、commit 前后注入失败/kill/reboot，系统能 rollback 或进入
  有证据的 `rollback_failed`，不会假报成功；
- [ ] rollback 后前态和健康检查恢复；重复调用、receipt 丢失和 lease replay 安全；
- [ ] E3 测试 proposal 必须逐次用户批准，digest/参数变化使旧批准失效；
- [ ] polkit、system-bus 和 helper 经静态检查、最小权限检查及独立攻击测试；
- [ ] WSL 不作为特权完成证据；支持 GNOME/systemd VM 的完整记录可复核；
- [ ] 共同质量门禁全部通过，且未开放第二个 E2 动作。

## 必交付物

- Effect Engine、Reviewer protocol/隔离、PrivilegeLease 和 E3 approval binding；
- 最小 polkit/system-bus mechanism（需要时含 Rust helper）；
- 一个 canary TransactionDriver、崩溃/回滚/重放矩阵；
- 威胁模型、安全审计与真实 VM 证据；
- 后续 E2 操作准入模板，明确每项都需独立 driver 和验收。

只有 canary 在攻击、崩溃和回滚矩阵中成立，且不存在任意特权旁路时才结束。
