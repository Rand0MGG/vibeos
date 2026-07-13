# VibeOS v0：现代 Linux 用户会话级 Agent 方案

## Summary

VibeOS v0 先不做文件检索、记忆系统、微信自动化，也不改 Linux 内核源码。第一版目标是做一个 **现代 Linux GNOME Wayland 用户会话层的 Agent capability runtime**：

用户用自然语言下命令，模型只输出结构化 intent，VibeOS daemon 校验 intent 后，通过 D-Bus、XDG Portal、GNOME Shell Extension、systemd user service 调用真实 Linux 用户会话能力，先完成“打开应用 / 列出窗口 / 聚焦窗口”。

## Key Architecture

```text
User
  ↓ natural language
vibe CLI / future overlay
  ↓
vibed systemd --user daemon
  ↓
Model Intent Broker
  ↓ validated capability request
Capability Broker
  ↓
D-Bus / XDG Portal / GNOME Shell Extension
  ↓
GNOME Wayland user session
```

核心组件：

- `vibed`：用户会话 daemon，通过 `systemd --user` 常驻。
- VibeOS D-Bus Service：在 session bus 暴露 `org.vibeos.Agent` API。
- Model Intent Broker：调用模型 API，只接受结构化 JSON intent。
- Capability Broker：校验 action allowlist、权限、风险等级、审计日志。
- GNOME Shell Extension：提供 Wayland 下窗口列表、窗口聚焦、桌面入口。
- XDG Portal Adapter：接入受授权的桌面能力，例如打开 URI、文件选择、截图授权。
- CLI：第一版调试入口，最终不是核心产品形态。

v0 支持的 capability：

```text
app.list
app.open
clipboard.write
notification.send
portal.open_uri
window.close
window.list
window.focus
window.maximize
window.minimize
system.status
```

v0 明确不支持：

```text
任意 shell
删除文件
安装软件
发送消息
读取任意屏幕内容
模拟键盘鼠标批量操作
跨应用自动填表
```

## Linux Source 与测试环境

第一版 **不需要下载 Linux 内核源码**。

原因：

- 我们做的是 Linux 用户会话层，不是内核模块。
- D-Bus、XDG Portal、GNOME Shell、systemd user service 都属于用户态/桌面会话层。
- 下载 Linux kernel 源码对 v0 没有帮助，反而会增加复杂度。

如果后续要研究源码，建议分开存放，不 vendoring 到 VibeOS 项目里：

```text
~/src/linux-kernel
~/src/gnome-shell
~/src/xdg-desktop-portal
~/src/systemd
```

测试环境建议用 VMware，可以，而且更安全：

- 使用 VMware 创建 Fedora Workstation 或最新 Ubuntu GNOME VM。
- 优先 Fedora Workstation，因为它更贴近现代 GNOME / Wayland 上游。
- 启用 3D acceleration。
- 安装 `open-vm-tools-desktop`。
- 使用 NAT 网络。
- 开发早期关闭共享文件夹或只读共享，避免误改宿主机文件。
- 每个阶段测试前打 VM snapshot。

v0 验收环境：

```text
Modern GNOME
Wayland session
systemd user session
D-Bus session bus
xdg-desktop-portal
GNOME Shell Extension support
```

## Implementation Plan

1. 项目初始化
   - 创建 Python 后端项目结构。
   - 创建 `docs/vibeos_v0_linux_session_agent_plan.md`。
   - 创建 `vibed` daemon、`vibe` CLI、GNOME extension 三个顶层模块。
   - 配置开发环境说明：Fedora/Ubuntu GNOME Wayland + VMware。

2. `vibed` daemon
   - 作为普通用户进程运行。
   - 增加 `systemd --user` service 文件。
   - 启动后注册本地 HTTP 或 Unix socket 管理接口。
   - 后续再注册正式 D-Bus service。

3. D-Bus service
   - 暴露 `org.vibeos.Agent.Command(text)`。
   - 暴露 `org.vibeos.Apps.List()` 和 `org.vibeos.Windows.List()`。
   - CLI 只调用 D-Bus，不直接操作系统。

4. Model Intent Broker
   - 接入 OpenAI-compatible API。
   - prompt 强制模型只输出 JSON intent。
   - schema validation 拒绝未知 action。
   - 模型输出不能包含 shell、脚本、任意 D-Bus 路径或系统命令。

5. Capability Broker
   - 维护固定 allowlist。
   - v0.1 通过 PermissionPolicy 审查所有动作。
   - L0/L1 自动执行并记录 audit。
   - L2 生成持久化 `review_id`，必须通过 `vibe approve <review_id>` 批准。
   - L3 默认拒绝。
   - 所有动作写入 JSONL audit log。
   - 拒绝超出 scope 的自然语言命令。

6. GNOME Shell Extension
   - 提供窗口列表能力。
   - 提供窗口 focus 能力。
   - 后续提供全局快捷键和桌面 overlay。
   - 只适配新版 GNOME extension API，不兼容老版本 GNOME。

7. XDG Portal Adapter
   - v0 先接 `OpenURI` 和基础 portal availability check。
   - 截图、屏幕访问、远程桌面授权放到 v1。
   - 不把 portal 当成万能窗口管理 API。

## Test Plan

- VM 环境测试：
  - GNOME Wayland 会话启动正常。
  - `vibed` 能通过 `systemctl --user start vibed` 启动。
  - `vibed` 崩溃后 systemd user service 能重启。
  - D-Bus service 能在 session bus 上被发现。

- 自然语言测试：
  - `vibe ask "打开浏览器"` → `app.open(browser)`。
  - `vibe ask "打开终端"` → `app.open(terminal)`。
  - `vibe ask "列出窗口"` → `window.list`。
  - `vibe ask "切到浏览器"` → `window.focus(browser)`。
  - `vibe ask "删除下载目录"` → 拒绝执行。

- 安全测试：
  - 模型返回未知 action 时拒绝。
  - 模型返回 shell 命令时拒绝。
  - 模型返回多个候选时不自动执行。
  - audit log 包含 user text、model intent、resolved capability、result、timestamp。

## Assumptions

- 优先适配新版 Linux：GNOME Wayland，而不是 X11。
- 第一参考发行版为 Fedora Workstation，Ubuntu GNOME 作为第二验证环境。
- VMware 是推荐测试环境，先用虚拟机和 snapshot 保护宿主机。
- v0 不下载 Linux kernel 源码，不做内核修改。
- VibeOS 的“OS 级”体现在 Linux session capability layer，而不是第一版改 kernel。
- 第一版目标是打通自然语言到受控系统 API 的链路，不追求大量功能。
