# Goal 08：实现主动建议与长期用户协作

- 阶段：08 / 09
- 依赖：[Goal 07](07_desktop_and_linux_mvp.md)全部完成
- 风险：中
- 完成后进入：[Goal 09](09_extensions_delivery_and_distro_gate.md)

## 给 Codex 的命令

你要让 VibeOS 能基于 Machine State 和任务历史主动发现少量高价值问题，并以
有证据、可解释、可抑制的建议交给用户决定。默认只建议，不直接执行；用户
接受后必须创建普通 GoalContract，重新经过歧义、Effect、Reviewer、E3 和
Secret 边界。不要建设会自行扩大范围的后台“自治 Agent”。

## 项目总体思想

Agent 可以比用户更早发现磁盘紧张、服务故障或任务反复失败，但“发现”不等于
“获得解决授权”。建议是用户协作对象，不是动作旁路。系统要控制通知频率、
保存依据、支持忽略/稍后/永久抑制，并从反馈调整规则而不是暗自改变权限。

## 当前起点

- Goal 04 有带 TTL/来源的 Machine State，Goal 02 有长期 Task/history；
- Goal 07 已证明真实用户任务、通知和接管闭环；
- 当前没有权威 finding/suggestion lifecycle、去重、抑制或价值评估；
- 现有 notification 能力可作为输出 adapter，但不能承担建议状态权威。

## 核心目标

实现以下唯一生命周期：

```text
detector -> Finding -> evidence/policy -> Suggestion
  -> presented -> accepted | snoozed | dismissed | expired
  -> accepted creates a normal GoalContract
```

首期只选择最多三个确定性、低隐私风险 detector。候选优先是：磁盘空间阈值、
受管 systemd user service 反复失败、VibeOS 任务重复出现同类可恢复错误。最终
选择应由真实机器数据和用户价值说明确定。

## 必须实施

1. **Finding/Suggestion contract**
   - Finding 有 type、resource、severity、evidence、first/last seen、confidence、
     sensitivity 和 detector version；
   - Suggestion 有用户收益、候选方案、预计效果/成本、是否需要 E2/E3、有效期；
   - 相同资源/根因使用稳定 dedupe key；状态转换是持久任务引擎的一部分。

2. **检测与调度**
   - detector 是确定性、只读 E0 collector/rule，按 Machine State 变化或低频 timer
     触发，不持续轮询；
   - 事实 stale 时先重新观察；证据不足不生成高置信建议；
   - detector 有 CPU/I/O/频率预算和可关闭开关，不扫描无关私人内容。

3. **用户控制**
   - 展示“发现了什么、证据是什么、为什么现在、建议做什么、可能影响什么”；
   - 支持 accept、snooze 到时间/条件、dismiss 当前实例、suppress type/resource；
   - 用户可查看/撤销 suppression，notification 有 quiet hours、批量和速率限制；
   - 不用紧急措辞放大低风险问题；真正紧急且无法自动安全处理时清楚说明。

4. **接受后的安全路径**
   - accept 生成新的 GoalContract，引用 Finding 证据但重新检查 freshness；
   - 任何歧义仍先询问；E2 经 Reviewer，E3 逐次用户批准；
   - 建议本身不能携带 pre-approved action、PrivilegeLease 或 SecretGrant；
   - 完成/失败回写 Finding，但不能因一次接受建立隐式永久授权。

5. **价值和噪声指标**
   - 记录 presented/accepted/snoozed/dismissed/suppressed/expired，不保存私人内容；
   - 为首批 detector 预先定义 precision、重复率、通知上限和用户接受率观察窗口；
   - 未达精度/噪声门槛的 detector 默认关闭或调整，不继续扩大 detector 数量。

## 明确非目标

- 不自动执行建议，不开放后台 self-improvement 或自装能力；
- 不分析用户情绪、建立行为画像或扫描邮件/聊天/文档寻找建议；
- 不做营销、推荐、广告或未经请求的外部消息；
- 不让模型自由生成 detector 条件或 notification urgency；
- 不在首批三个 detector 之外扩张，直到价值门禁有数据。

## 验收条件

- [ ] 最多三个 detector 只使用允许的 Machine State，触发/失效/去重可重复测试；
- [ ] stale/低置信事实不会产生确定性高风险建议；
- [ ] 同一问题不会跨重启重复轰炸，quiet hours、rate limit、snooze 和 suppression
  持久有效；
- [ ] accept 创建普通新任务且重新评估事实/效果，无法携带旧 lease/grant/approval；
- [ ] E2/E3 测试建议仍分别经过 Reviewer/逐次用户批准；
- [ ] dismiss/suppress 不会改变机器状态，用户可审计和撤销；
- [ ] 在真实 GNOME VM 完成发现、通知、接受、稍后、忽略、抑制和任务回写闭环；
- [ ] 使用合成/受控试用数据评估预先门槛，未达标 detector 默认关闭；
- [ ] 没有新的动作、模型、通知或 Task Store 旁路；
- [ ] 共同质量门禁全部通过。

## 必交付物

- Finding/Suggestion 状态、detector scheduler、dedupe/suppression 和用户控制；
- 最多三个 detector、真实 VM 协作闭环和安全回归测试；
- 精度/噪声/接受指标报告以及启用或关闭决定；
- 主动行为隐私、通知和权限文档。

只有建议真正有证据、噪声受控且接受后仍走统一安全路径时才结束本 Goal。
