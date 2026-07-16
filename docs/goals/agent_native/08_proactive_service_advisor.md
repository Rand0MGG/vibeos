# Goal 08：用一个确定性 Detector 交付主动建议闭环

- 阶段：08 / 09
- 依赖：[Goal 07](07_gnome_mixed_task_mvp.md)全部完成
- 风险：中
- 完成后进入：[Goal 09](09_runtime_delivery_extension_and_distro_gate.md)

## 给 Codex 的命令

你要让 VibeOS 主动发现一种已经被前序 Goal 真实证明的问题，并向用户提出有证据、
可忽略、可稍后、可抑制的建议。首期只实现一个确定性、只读、低隐私风险 detector；
优先选择“受管 `systemd --user` 服务在限定窗口内反复失败”，因为它直接复用 Goal 04
的事实和修复路径。建议绝不自动执行；只有用户明确接受，才创建一个普通的新
GoalContract，并重新检查事实、歧义、权限和完成条件。

不要同时实现磁盘、任务失败、更新提醒等多个 detector，不要让模型自由生成检测
规则或紧急程度，也不要把受控测试中的点击率写成真实用户价值。

## 项目总体思想

Agent 可以比用户更早发现问题，但“发现”不等于“获得解决授权”。主动建议是用户
协作对象，不是 Action Fabric 的旁路。用户决定是否解决；一次接受不建立永久授权，
也不能携带旧 approval、PrivilegeLease、SecretGrant 或 UI session。

检测规则、去重、频率和数据范围由确定性代码控制。模型可以把已验证 Finding 转换
为清晰解释，但不能扩大证据、扫描额外私人内容或创造新动作。

## 预期进入状态与现场核对

预期 Goal 07 已有真实 GNOME 通知/用户交互、Durable Task wait/timer、service facts、
user service 修复路径和用户接管。开始前现场确认：

- 受管 unit 的范围和用户可见身份；
- service fact 的 TTL、失败事件、journal evidence 和数据等级；
- 当前 notification adapter 在真实 GNOME 的状态与 quiet hours 能力；
- 用户接受建议后可复用的 Goal 04 修复入口，而不是私有快捷执行函数；
- 可用于受控评估的数据量，不能虚构真实接受率。

## 核心目标

实现唯一生命周期：

```text
service facts/events
  -> deterministic detector
  -> Finding + evidence
  -> dedupe/rate/privacy policy
  -> Suggestion
  -> presented
  -> accepted | snoozed | dismissed | suppressed | expired
  -> accepted creates a normal GoalContract
```

用户必须能看到：发现了什么、证据是什么、为什么现在提示、建议做什么、可能影响
什么，以及“不处理”会怎样。

## 必须实施

1. **Finding/Suggestion contract**
   - Finding 包含 type、resource、severity、evidence IDs、first/last seen、confidence、
     sensitivity、detector version 和 stable dedupe key；
   - Suggestion 包含用户收益、候选方案、预计效果/成本、需要的 effect 等级、有效期；
   - 状态转换持久化在现有 Task Store/领域事件路径，不新增 notification 状态权威。

2. **一个确定性 detector**
   - 只读取允许的 user service facts/events，按变化事件或低频 timer 触发；
   - production 默认阈值固定为同一受管 unit 在滚动 30 分钟内出现 3 次失败；事实
     TTL 沿用 Goal 04，stale 时先重采；连续健康 10 分钟后关闭 Finding；
   - 同一 dedupe key 默认 24 小时最多主动展示一次，除非 severity 确定性升级；这些
     默认值可由用户配置，但模型和 detector 运行时不能自行改变；
   - 事实 stale 时先重采，证据不足不生成高置信建议；
   - detector 有 CPU/I/O/频率预算和总开关，不读取无关 journal、home、邮件或文档。

3. **去重、噪声和用户控制**
   - 同一 unit/根因只维护一个活跃 Finding/Suggestion，跨 daemon 重启不重复轰炸；
   - 支持 accept、snooze 到时间/条件、dismiss 当前实例、suppress type/resource；
   - suppression 可查看和撤销；notification 有 quiet hours、批量和速率限制；
   - 低风险问题不使用夸大紧急措辞；真正紧急但无法安全处理时明确说明限制。

4. **接受后的安全路径**
   - accept 创建普通 GoalContract，引用 Finding evidence 但重新检查 freshness；
   - unit、目标或完成条件有歧义时仍先询问；
   - 后续动作按现有 E0/E1/E2/E3 路径重新评估，不能复用旧 lease/grant/approval；
   - 完成、失败或用户取消回写 Finding，但不自动扩大 detector 范围。

5. **真实交互与诚实指标**
   - 在真实 GNOME 展示通知，并通过 CLI/D-Bus 提供可访问的列表与控制；
   - 用合成故障和受控试用测量 precision、重复率、通知上限、状态正确率；
   - 真实接受率只有在真实用户观察窗口后才能记录；本 Goal 可交付“尚无足够数据”，
     不得把开发者操作或 fixture 当作市场/用户价值证明；
   - detector 未达预设精度或噪声门槛时默认关闭，不能靠增加更多规则掩盖。

6. **停止条件**
   - 若 Goal 04 service facts 无法稳定区分真实失败、重启恢复和 stale observation，
     停止 notification 实现并报告事实模型阻塞，不扫描更多数据源补猜；
   - 若受控试用无法满足预设 precision/重复率上限，保留可审计的 disabled detector
     和证据，不启用 production，也不增加第二个 detector。

## 明确非目标

- 不自动执行建议，不实现后台 self-improvement 或自装能力；
- 不增加第二个 detector，不扫描磁盘内容、邮件、聊天、浏览历史或剪贴板；
- 不让模型生成 detector 条件、severity、通知频率或权限决定；
- 不把一次接受变成长期授权，不做外部营销、推荐或未经请求的消息；
- 不修改 Goal 04 修复路径或绕过 Task Engine、Effect Policy、Secret/Privilege 边界。

## 验收条件

- [ ] 仓库只有一个 production detector，且只使用允许的 service facts/events；
- [ ] 3 次/30 分钟触发、健康 10 分钟关闭、stale、24 小时展示上限、过期、去重和
  重启行为可重复测试；
- [ ] 同一问题不会跨重启重复提示，quiet hours、rate limit、snooze 和 suppression
  持久有效；
- [ ] dismiss/suppress 不改变机器状态，用户可审计和撤销；
- [ ] accept 创建普通新任务并重新评估事实/效果，无法携带旧权限或 secret grant；
- [ ] 真实 GNOME 完成发现、通知、接受、稍后、忽略、抑制和任务回写闭环；
- [ ] 受控精度/噪声门槛有数据，未达标时 detector 默认关闭；
- [ ] 文档明确哪些指标来自合成、开发试用和真实用户，不虚构接受率；
- [ ] 没有新的动作、模型、通知状态或 Task Store 旁路；
- [ ] Goal 03–07 和共同质量门禁全部通过。

## 必交付物

- Finding/Suggestion schema、状态转换和持久化；
- 一个 user service 重复失败 detector；
- dedupe、quiet hours、rate limit、snooze、dismiss、suppression 和用户控制；
- 真实 GNOME 协作闭环与受控精度/噪声报告；
- 主动行为的隐私、授权和停用说明。

只有一个建议真正有证据、噪声受控、用户拥有决定权，且接受后仍走统一安全路径时，
才结束本 Goal。
