# Goal 07：实现桌面 Action Fabric 与真实 Linux Agent MVP

- 阶段：07 / 09
- 依赖：[Goal 06](06_privileged_control_and_rollback.md)全部完成
- 风险：高
- 完成后进入：[Goal 08](08_proactive_advisor.md)

## 给 Codex 的命令

你要在真实 GNOME Wayland 环境完成 VibeOS 的首个端到端个人 Agent MVP：优先
使用应用 API、D-Bus 和 AT-SPI 语义控件树，只有这些路径不可用时才使用 XDG
RemoteDesktop portal 的输入/截图 fallback。将现有桌面 adapters 接入统一
Action Fabric 和 Durable Task Engine，用少量黄金场景证明澄清、长任务、重启
恢复、秘密、E2/E3 边界、UI fallback 和证据完成判断。

不要用 WSL、fake adapter 或 dry-run 宣布 MVP 完成；不要为绕过 Wayland/portal
授权而默认访问 `/dev/uinput`、屏幕抓取后门或宽泛 GNOME extension。

## 项目总体思想

VibeOS 要像真实用户一样使用电脑，但优先利用比人更可靠的底层接口。桌面不是
独立运行时，而是 Action Fabric 的一组 provider；每次 fallback 都要保留原因、
用户会话授权和证据。Agent 可以自行判断完成，但目标状态必须由应用/API、
AT-SPI 或可复核视觉状态独立验证。

## 当前起点

- 仓库已有 GNOME extension、portal、窗口/应用/browser adapters 和 VM smoke
  脚本，但本轮基线仅在 WSL 通过，未证明真实 GNOME Wayland；
- 当前 19 个能力偏静态桌面动作，缺少统一 AT-SPI provider、fallback policy 和
  跨重启会话语义；
- Goal 01-06 已提供唯一任务内核、秘密、机器事实、模型网关、普通动作和一个
  特权事务 canary；桌面必须复用这些边界。

## 核心目标

在受支持的 Fedora Workstation GNOME Wayland VM（再选择一个 Ubuntu GNOME
版本做兼容 smoke）交付以下路径：

```text
app/system API or D-Bus
  -> AT-SPI semantic tree/action
  -> XDG RemoteDesktop portal input/screenshot fallback
```

完成 MVP 前先做 portal/AT-SPI feasibility spike，记录授权是否可持久、daemon
重启/用户登出后的行为、无障碍覆盖和限制。若 portal 不支持无人值守恢复，MVP
必须把相关任务标为 `awaiting_user_session`，而不是虚假承诺后台继续操作 UI。

## 必须实施

1. **桌面状态与 AT-SPI**
   - 建立 desktop session、application、window 和 accessible node 的短期 observation；
   - AT-SPI provider 支持稳定 role/name/state/action selector，避免依赖坐标；
   - selector 必须处理歧义、窗口变化、stale node 和应用重启；歧义时重新观察或
     询问，不点击“最像”的危险对象；
   - UI 文本按 D0-D4 处理，私密内容不默认持久化到 Machine State。

2. **portal fallback**
   - 使用标准 RemoteDesktop/ScreenCast portal 会话和用户授权；
   - portal handle、session 生命周期、取消、超时、分辨率/缩放/多屏变化可恢复；
   - 视觉/坐标动作必须先有目标定位证据，动作后重新观察；不能把截图发给不允许
     的模型或留在普通 trace；
   - portal 不可用或授权失效时进入明确等待，不降级到未治理设备输入。

3. **窄 GNOME bridge**
   - 盘点现有 extension：只有标准 API/AT-SPI/portal 无法可靠覆盖且有 MVP 价值的
     窄能力才保留；
   - extension 只暴露版本化、类型化、最小动作/观察，不包含 planner、task state、
     secret、模型或特权策略；
   - 不再使用的 bridge 和重复 desktop route 删除。

4. **用户交互**
   - 实质目标歧义在任何 UI 动作前进入 clarification；
   - E3 确认界面展示精确外部后果并绑定 proposal digest；本阶段只可在用户明确
     批准后执行测试账户/沙箱中的外部动作；
   - 任务进展、等待 portal/登录/用户输入、取消和接管在 CLI/D-Bus/UI 可理解；
   - 用户接管后 Agent 停止注入输入，归还时重新观察而非沿用旧 UI 状态。

5. **黄金场景**
   - 在实现前固定至少五个真实、可重复、相互覆盖的场景，包括：
     1. API/D-Bus 完成且不触发 UI；
     2. AT-SPI 完成一个应用内任务并验证结果；
     3. 语义接口缺失后使用 portal fallback；
     4. 任务跨 daemon 重启或用户接管后恢复；
     5. 一个包含 E2 canary 或 E3 测试批准的混合任务；
   - 每个场景写明初始 VM snapshot、用户目标、允许效果、预期路径、注入故障、
     完成条件和证据，不依赖人工主观判断成功。

## 明确非目标

- 不支持所有桌面、所有应用、X11 或 Windows/macOS；
- 不做纯视觉通用 computer-use benchmark，也不追求任意网页自动化；
- 不读取浏览器密码/cookie，不绕过 CAPTCHA、MFA、portal 或应用安全提示；
- 不把鼠标键盘作为默认路径，不默认启用 `/dev/uinput`；
- 不在此阶段扩充更多 E2 操作、主动建议或插件市场。

## 验收条件

- [ ] feasibility 报告明确 AT-SPI/portal 在支持版本的能力、授权和恢复边界；
- [ ] 路径选择证明 API/D-Bus 可用时不使用 AT-SPI，AT-SPI 可用时不注入坐标；
- [ ] AT-SPI selector 对歧义/stale/reorder/应用重启安全，危险歧义不误操作；
- [ ] portal 授权失效、缩放、多屏、取消和 daemon 重启行为符合报告，不越权降级；
- [ ] 用户接管立即停止输入，归还控制后重新观察并安全继续；
- [ ] 五个黄金场景在干净 Fedora GNOME Wayland VM 连续运行至少 10 轮，达到预先
  定义的成功率且没有错误外部副作用；失败均有可诊断 Task/Evidence；
- [ ] Ubuntu GNOME 兼容 smoke 通过或被支持矩阵明确排除并给出原因；
- [ ] mixed scenario 中 E2/E3、Secret Broker、重启恢复和完成证据保持有效；
- [ ] VM 重建脚本、版本、snapshot、日志脱敏和证据包可由另一人复现；
- [ ] fake/dry-run 继续用于 CI，但文档不把它们写成真实 MVP 证据；
- [ ] 共同质量门禁全部通过。

## 必交付物

- AT-SPI provider、portal fallback、桌面 observation/evidence 和窄 bridge；
- feasibility 报告、支持矩阵与明确产品限制；
- 五个黄金场景、重复运行报告、故障证据和可重建 VM 流程；
- 用户接管/等待/批准体验以及更新后的真实当前状态文档。

只有真实 GNOME 黄金场景可复现、fallback 不越权且产品限制被如实记录时结束。
