# VibeOS v0.2 Goal

记录日期：2026-06-01

## 一句话定义

VibeOS v0.2 的目标不是继续堆自然语言命令，而是把当前原型收敛成一个：

- 单用户
- 单会话
- 常驻
- 可审计
- 统一入口

的 Linux desktop capability service。

用一句更工程化的话说：

```text
VibeOS v0.2 = 把单用户 Linux session runtime 跑通，并做成可信、常驻、统一入口的 capability service。
```

## 这版为什么要这样收

当前仓库已经不再是纯 demo：

- 已有 capability broker
- 已有 app/window/portal/notification/clipboard adapters
- 已有 HTTP daemon
- 已有 D-Bus service
- 已有 systemd user service 安装脚本
- 已有 GNOME Shell extension bridge
- 已有 transport / audit / evidence 这条主线的本地实现

但系统还没有在目标环境里完成最终闭环。当前最重要的问题不是“再多支持几个命令”，而是：

- daemon 是否真的在 Fedora 用户会话里稳定常驻
- GNOME bridge 是否真的在目标 VM 里工作
- CLI 是否真实优先走 daemon，而不是退回本地 broker
- audit、review、transport 是否和真实执行结果一致
- VM evidence 是否能稳定证明这些结论

所以 v0.2 要解决的是“把已有能力变成可信系统”，不是“继续扩充能力表”。

## 当前阶段判断

截至 2026-06-01，可以把当前项目状态分成两层：

### 1. 本地代码层

本地代码已经明显往正确方向推进：

- 已有统一 runtime 抽象
- CLI 已能优先走 daemon，再回退到本地
- D-Bus 和 HTTP 已对齐到同一套 capability contract
- transport 已进入 result、audit、evidence
- review / audit 语义比早期版本更一致
- 本地测试和本地验证脚本已经成为主线证据

### 2. 目标环境层

Fedora GNOME Wayland VM 仍然是 v0.2 的真正验收环境。当前剩余风险主要集中在这里：

- `vibed.service`
- `org.vibeos.Agent`
- GNOME Shell extension bridge
- 真实窗口能力
- `collect_vm_evidence.py --real`

因此，v0.2 不应再被描述成“从零开始设计 capability runtime”，而应被描述成：

```text
本地架构已基本成形，接下来要完成 Fedora VM 上的真实运行时闭环。
```

## v0.2 的产品边界

长期方向不变：

```text
Codex = agent 使用操作系统
VibeOS = 操作系统向 agent 提供原生、受控、可审计的能力接口
```

v0.2 只做用户会话层，不做以下事情：

- 不改 Linux kernel
- 不开放任意 shell 执行
- 不做文件删除/移动能力
- 不做大规模 GUI 自动化
- 不做键鼠批量模拟平台
- 不做完整多用户权限系统
- 不做完整多 agent delegation / revocation
- 不做第三方 app 平台生态

## v0.2 范围

目标环境：

- Fedora Workstation 44+
- GNOME Wayland
- systemd user session
- D-Bus session bus
- xdg-desktop-portal
- GNOME Shell Extension
- VMware VM

v0.2 只要求把少量 capability 做成可信闭环：

```text
app.list
app.open
window.list
window.focus
window.close
notification.send
clipboard.write
portal.open_uri
system.status
```

## v0.2 的核心目标

### 1. 常驻运行时成立

VibeOS 必须以用户会话 daemon 的形式成立，而不是主要依赖一次次 CLI 进程临时执行。

至少应满足：

- `vibed.service` 在 Fedora VM 中可启动并保持 active
- `org.vibeos.Agent` 可发现
- daemon 启动依赖、环境变量、`.env` 路径明确
- daemon 故障时有可诊断输出

### 2. 统一入口成立

VibeOS 必须形成清晰的服务边界：

- daemon 是权威 runtime
- broker 是 daemon 内部执行核心
- CLI 是客户端
- D-Bus 和 HTTP 面向同一 capability contract
- transport 可观测，可进入返回结果和审计

v0.2 允许保留本地 fallback，但 fallback 只能是过渡手段，不能继续作为系统长期主路径。

### 3. 桌面 bridge 闭环成立

GNOME Shell extension bridge 必须在 Fedora VM 里真实工作，因为窗口能力是当前桌面 runtime 的关键事实来源。

至少应满足：

- `vibe windows` 能列出真实窗口
- `window.focus` 能聚焦真实窗口
- `window.close` 能关闭真实窗口
- bridge 返回状态被正确解释
- 不允许把“D-Bus 调用成功”误记成“窗口动作成功”

### 4. capability 语义可信

当前少量 capability 必须具备稳定且可解释的语义：

- `app.open` 快速返回，不再误报 `gtk-launch timed out`
- `window.*` 以真实 bridge 结果为准
- resolver 对 `browser` / `firefox` / `terminal` 等目标映射稳定
- 多个候选合理时返回 `ambiguous`，而不是静默误操作

### 5. 审计与审批一致

review、execution、audit 必须和真实结果一致。

至少应满足：

- L2 action 会创建 review
- `approve --dry-run` 不消费审批
- 真实 approve 只有在实际执行成功时才消费 review
- 执行失败不能被记成成功
- 审批、拒绝、失败、执行结果都能进入 audit
- audit 至少能区分 transport

### 6. evidence 可重复

v0.2 必须让“这次结论来自哪台环境、哪条入口、哪份代码”可验证。

至少应满足：

- 有稳定的 VM 同步方式
- 每次 VM 验收都产出 evidence
- evidence 至少包含 doctor、capabilities、代表性命令结果、audit tail
- 本地验证和 VM 验证分层清晰，不混淆

## 当前对 v0.2 的重新分阶段

### Phase A：本地 capability service 基线

这部分已经基本完成或正在收尾：

- runtime 抽象成立
- CLI 优先使用 daemon
- D-Bus / HTTP / local 的 transport 路线明确
- audit / evidence 开始体现 transport
- review / execution 语义更接近真实结果
- 本地测试与本地验证脚本可作为回归基线

### Phase B：Fedora VM 真实运行时闭环

这是当前 v0.2 的主战场：

- `vibed.service` 真正 active
- `org.vibeos.Agent` 真正可发现
- GNOME bridge 真正可响应
- 真实 Firefox / Ptyxis 窗口可被列出与操作
- `collect_vm_evidence.py --real` 转绿

v0.2 是否完成，最终以 Phase B 为准，而不是只看 Windows 工作区下的本地测试。

## 当前版本最重要的未完成项

优先级按下面排序：

1. 修复 Fedora VM 中的 `vibed.service` 启动与常驻问题
2. 修复 GNOME Shell extension bridge 的真实响应
3. 让窗口能力在 VM 中形成真实闭环
4. 让 doctor / status / evidence 明确显示 CLI 实际走的是哪条 transport
5. 收紧 VM 代码同步与 evidence 流程

## Success Criteria

v0.2 完成时，以下检查应在 Fedora GNOME Wayland VM 中通过。

### A. Doctor

```bash
/home/rand0mg/vibeos/.venv/bin/vibe doctor
```

期望：

```text
overall: ok
vibed_service: ok
gnome_extension_bridge: ok
runtime_entry: ok
```

并且 `runtime_entry` 明确显示 CLI 主路径是 daemon，而不是长期落回 local broker。

### B. Daemon / D-Bus / HTTP

```bash
systemctl --user status vibed.service --no-pager
gdbus introspect --session --dest org.vibeos.Agent --object-path /org/vibeos/Agent
curl -s http://127.0.0.1:8765/v1/status
```

期望：

- `vibed.service` active
- D-Bus name 可发现
- `Status()` 可调用
- HTTP `/v1/status` 可返回
- transport 信息与实际运行形态一致

### C. App Capability

```bash
/home/rand0mg/vibeos/.venv/bin/vibe ask "打开浏览器" --json
```

期望：

- 快速返回
- 状态为 `executed`
- transport 明确
- Firefox 实际打开
- audit 记录 `app.open`

### D. Window Capability

```bash
/home/rand0mg/vibeos/.venv/bin/vibe ask "列出窗口" --json
/home/rand0mg/vibeos/.venv/bin/vibe ask "切到浏览器" --json
```

期望：

- 能列出 Firefox / Ptyxis 等真实窗口
- 能聚焦 Firefox 窗口
- `browser` / `浏览器` / `firefox` 解析稳定
- 返回结果和真实用户观察一致

### E. Review / Audit

```bash
/home/rand0mg/vibeos/.venv/bin/vibe ask "关闭浏览器" --json
/home/rand0mg/vibeos/.venv/bin/vibe reviews pending --json
/home/rand0mg/vibeos/.venv/bin/vibe approve <review_id> --dry-run --json
/home/rand0mg/vibeos/.venv/bin/vibe approve <review_id> --json
```

期望：

- `window.close` 进入 L2 review
- dry-run 不消费 review
- 执行失败不应伪装成成功
- transport、审批、执行结果进入 audit

### F. Dangerous Actions

```bash
/home/rand0mg/vibeos/.venv/bin/vibe ask "删除下载目录" --json
/home/rand0mg/vibeos/.venv/bin/vibe ask "执行 sudo rm -rf /" --json
```

期望：

- 返回 `rejected`
- action 为 `unknown`
- risk level 为 `L3`
- 不执行 shell

### G. Evidence

```bash
python /home/rand0mg/vibeos/scripts/collect_vm_evidence.py --real
```

期望：

- evidence 完整生成
- evidence 与当次 VM 实际状态一致
- doctor、transport、capabilities、代表性命令、audit tail 全部可追踪

## 明确不属于 v0.2 的内容

以下内容仍然重要，但不应阻塞 v0.2：

- 完整多用户权限系统
- 完整多 agent delegation / revocation
- per-app policy
- 远程 agent 身份体系
- 第三方 app 平台化接入
- 大规模 UI 自动化

## Multi-user / Multi-agent Baseline

这条线仍然要保留，但在 v0.2 中只做“预留边界”，不做完整实现。

v0.2 对这条线的最低要求是：

- 文档里明确 `actor / subject / capability / target / approval / audit` 这些概念
- schema 不把未来扩展路径写死
- 审计与 review 结构允许未来加入更明确的 requester / actor 信息

换句话说：

```text
v0.2 先把 runtime 跑通；
v0.3+ 再把多用户 / 多 agent 做成真正的平台能力。
```

## Immediate Next Steps

下一轮工作应按下面顺序推进：

1. 在 Fedora VM 中修复 `vibed.service`
2. 在 Fedora VM 中修复 GNOME Shell extension bridge
3. 让 `window.list / focus / close` 在 VM 中形成真实闭环
4. 用 doctor / status / evidence 明确 transport 主路径
5. 收紧 VM 同步与验收流程
6. 以 `collect_vm_evidence.py --real` 作为 v0.2 最终验收入口

## 结论

当前的 VibeOS v0.2 已经不该再被定义为“继续完善一个桌面命令 demo”，而应该被定义为：

```text
把已有 capability runtime 收敛成一个真实可运行、可审计、可验证的 Linux session service。
```

如果这一步完成，VibeOS 才真正具备承接后续 app integration、多 agent、更多 capability 的基础。没有这个基础，后续功能只会继续堆在不稳定的运行时之上。
