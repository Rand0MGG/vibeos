# Goal 07：交付真实 GNOME 混合任务与桌面 fallback MVP

- 阶段：07 / 09
- 依赖：[Goal 06](06_privileged_canary_and_rollback.md)全部完成
- 风险：高
- 完成后进入：[Goal 08](08_proactive_service_advisor.md)

## 给 Codex 的命令

你要在真实 Fedora Workstation GNOME Wayland VM 上交付一个同时包含系统步骤、
等待/重启恢复和桌面步骤的用户任务。路径必须严格遵循：应用/系统 API 或 D-Bus
优先，缺失时才使用 AT-SPI，AT-SPI 也无法完成时才在用户授权的 XDG RemoteDesktop/
ScreenCast portal 会话中使用鼠标键盘和视觉定位。

先单独完成 portal/AT-SPI feasibility gate，再实现下文固定的一个主黄金场景和一个
受控 portal fallback 场景。不要承诺任意应用自动化，不要一次建设五个场景、运行十轮
矩阵或清理所有旧 desktop bridge。若 portal 授权无法跨 daemon 重启或用户登出
恢复，真实产品状态必须是 `awaiting_user_session`，不能伪装成后台持续控制 UI。

## 项目总体思想

VibeOS 是“像真实用户一样完成任务”的 Agent，但比人类多出 API、CLI、D-Bus 和
系统服务这些更可靠路径。UI 是能力补全层，不是默认执行层。每次 UI 动作前需要
当前可见状态和目标定位证据，动作后重新观察；危险歧义先询问，绝不点击“最像”的
对象。

用户始终拥有接管权。接管发生时 Agent 立即停止注入输入；归还后丢弃旧 UI node、
坐标和截图，重新观察再继续。桌面文本和截图按数据等级处理，不默认持久化或发送
给云模型。

## 预期进入状态与现场核对

预期 Goal 05 已提供可安装 Runtime 和少量 API/CLI 任务，Goal 06 已证明一个 E2
canary，但桌面 MVP 不应依赖新增更多 E2。开始前现场核对：

- 目标 Fedora GNOME、Wayland、AT-SPI、RemoteDesktop/ScreenCast portal 版本；
- portal 授权是否可持久、daemon 重启/用户登出/锁屏后的行为；
- 现有 GNOME extension、window/app/browser adapter 的真实可用能力和调用者；
- AT-SPI 对选定真实应用的 role/name/state/action 覆盖与稳定性；
- 当前安装 artifact 能否在干净 GNOME VM 启动 daemon、D-Bus 和用户交互面；
- 截图、可访问文本和模型上下文的数据边界。

## 核心目标

主黄金场景固定为：

> 用户要求“诊断并恢复 VibeOS 测试用户服务，并打开恢复报告”。Agent 诊断和恢复
> `vibeos-goal04-fixture.service`，在 receipt 已提交、verify 前注入一次 daemon 重启，
> 恢复后验证服务健康；Core 在自己的 state/report 目录生成 task-scoped 纯文本报告，
> 通过应用/portal API 打开支持 VM 中固定安装的 GNOME Text Editor，再用 AT-SPI
> 验证窗口和报告中的 task ID、unit 与健康结论。测试过程中用户接管一次并归还。

报告生成是 Core-owned task artifact，不开放任意用户路径写入。支持 VM 必须在镜像
定义中固定 GNOME Text Editor 版本；若目标版本确实不可用，Codex 先报告并由用户
确认替代应用，不能自行改变场景。

```text
user goal -> clarification if needed
  -> system API/D-Bus/CLI steps
  -> durable wait/restart recovery
  -> app API/D-Bus if available
  -> AT-SPI semantic action
  -> portal visual/input only when semantic path is unavailable
  -> independent system + desktop evidence
  -> terminal outcome
```

portal fallback 场景固定为一个运行在真实 GNOME 会话中的受控测试窗口：窗口故意不
暴露可执行 AT-SPI action，仅提供一个视觉目标；用户批准 portal 后单击目标会切换
可见的随机 challenge marker。该窗口无网络、不读写用户文件、不访问剪贴板。测试
必须用动作前生成的 challenge 和动作后 marker 证明没有点击错误对象；它只验证
fallback 安全性，不能冒充真实应用产品价值。

## 必须实施

1. **先完成 feasibility gate**
   - 记录支持版本、AT-SPI 覆盖、portal 授权、会话生命周期、缩放、多屏、锁屏、
     daemon 重启和用户登出行为；
   - 明确哪些任务可无人值守继续、哪些必须等待用户 session/授权；
   - 若标准接口无法满足主场景，先修改场景或提交受限结论，不用 `/dev/uinput`、X11
     或绕过 portal 解决；
   - spike 只产生决策和最小探针，不能在未通过门禁前铺设通用桌面框架。

2. **桌面状态与 AT-SPI provider**
   - 建立短期 desktop session、application、window 和 accessible node observation；
   - selector 使用 role/name/state/action、父子关系和应用身份，处理歧义、stale node、
     reorder、窗口变化和应用重启；
   - AT-SPI action 前后均重新观察，不能长期缓存 node 或用坐标替代语义；
   - 私密 UI 文本默认不进入长期 Machine State，证据只保存必要摘要/哈希/引用。

3. **portal fallback**
   - 只使用标准 RemoteDesktop/ScreenCast portal 和明确用户授权；
   - handle/session、取消、timeout、缩放、多屏和 daemon 重启按 feasibility 结果实现；
   - 视觉动作需要当前截图、目标候选、置信门槛和歧义处理；动作后必须新截图/观察；
   - portal 不可用或授权失效时进入等待，不降级到未治理输入或谎报完成。

4. **现有 bridge 兼容策略**
   - 盘点 extension/desktop adapters，只为主场景复用已有窄 typed 能力；
   - bridge 不得包含 planner、Task Store、secret、模型、effect policy 或提权决策；
   - 本阶段不删除旧 bridge/route。记录真实调用者、重复能力和未来删除门禁，待稳定
     场景证明后另行收敛。

5. **用户交互与接管**
   - 目标、应用、外部后果或 UI 对象有实质歧义时在动作前询问；
   - 进度、等待登录/portal/用户输入、取消和失败原因在 CLI/D-Bus/可用 UI 可理解；
   - 用户接管立即停止输入并持久化安全状态；归还后重新观察和重新验证计划；
   - 本阶段不真实执行付款、发布消息、账户变更或私人数据外传。

6. **主黄金场景与恢复**
   - 按上述固定场景记录 VM snapshot、系统/桌面路径、允许效果、故障点和完成证据；
   - 注入 daemon 重启、应用重启、stale AT-SPI node、portal 取消和用户接管；
   - 系统步骤由系统事实验证，桌面步骤由独立 accessible/visual 状态验证；
   - 至少连续重复三轮主场景和三轮 portal 受控场景；先获得稳定证据，再在未来扩大
     场景数，不以高轮次数掩盖单一场景设计问题。

## 明确非目标

- 不支持所有桌面、所有应用、X11、Windows 或 macOS；
- 不追求任意网页/游戏/画布自动化或纯视觉通用 benchmark；
- 不读取浏览器密码/cookie，不绕过 CAPTCHA、MFA、portal 或应用安全提示；
- 不把鼠标键盘作为默认路径，不启用 `/dev/uinput`；
- 不开放更多 E2，不构建主动建议、插件市场或完整桌面 SDK；
- 不删除现有 desktop bridge，不把 fake/dry-run 写成真实 GNOME 证据。

## 验收条件

- [ ] feasibility 报告明确 AT-SPI/portal 在目标版本的授权、恢复和不可承诺边界；
- [ ] 路径选择证明 API/D-Bus 可用时不使用 AT-SPI，AT-SPI 可用时不注入坐标；
- [ ] selector 对歧义/stale/reorder/应用重启安全，危险对象不误操作；
- [ ] portal 授权失效、缩放、多屏、取消和 daemon 重启符合报告且不越权降级；
- [ ] 用户接管立即停止输入，归还后丢弃旧状态并安全继续；
- [ ] 主黄金场景跨 daemon 重启完成系统和真实桌面步骤，独立证据支持 TerminalOutcome；
- [ ] 主场景和受控 portal 场景分别连续通过至少三轮，无错误外部副作用；
- [ ] 干净 Fedora GNOME VM 从 Goal 05 artifact 安装后可复现；
- [ ] Goal 03–06 兼容、秘密、用户态任务和 E2 边界没有回归；
- [ ] 共同质量门禁通过，文档准确区分真实 GNOME、WSL、fixture 和 mock。

## 必交付物

- AT-SPI/portal feasibility 报告和支持边界；
- 窄 AT-SPI provider、portal fallback、桌面 observation/evidence；
- 上述主混合黄金场景和受控 portal 场景及故障矩阵；
- 用户接管/归还、等待 session/授权和数据处理说明；
- 可重建 Fedora GNOME VM、安装步骤和重复运行证据。

只有 Agent 在真实 GNOME 上遵循接口优先级完成一个混合用户任务，并能在重启、
授权失效和用户接管下安全继续或等待时，才结束本 Goal。
