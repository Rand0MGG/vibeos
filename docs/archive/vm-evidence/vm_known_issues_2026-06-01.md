# VibeOS VM 实测问题记录

记录日期：2026-06-01

测试环境：

- Fedora Workstation 44 VM
- GNOME Shell 50.1
- Wayland session
- VMware Workstation Pro
- 项目路径：`/home/rand0mg/vibeos`
- Python venv：`/home/rand0mg/vibeos/.venv`
- 模型配置：DeepSeek API key 已配置

## 当前已确认跑通

单元测试已通过：

```text
43 passed in 0.06s
```

`vibe doctor` 大部分检查通过：

```text
overall: warn  ok=9 warn=2 fail=0
ok    platform: running on Linux
ok    session_type: GNOME Wayland session detected
ok    gnome_shell: GNOME Shell 50.1
ok    gdbus: gdbus is available
ok    xdg_desktop_portal: xdg-desktop-portal is available
ok    systemd_user: running
ok    app_registry: found 37 desktop applications
ok    action_helpers: notification and clipboard helpers are available
ok    model_config: DeepSeek API key is configured
warn  vibed_service: vibed.service is not active
warn  gnome_extension_bridge: VibeOS GNOME Shell bridge is not responding
```

应用发现能力已跑通：

- Fedora VM 内识别到 37 个 `.desktop` 应用。
- Firefox 被识别为 `org.mozilla.firefox.desktop`。
- Terminal 被识别为 `org.gnome.Ptyxis.desktop`。

模型 intent broker 已跑通：

- `打开浏览器` 能被解析为 `app.open`。
- `关闭浏览器` 能被解析为 `window.close`。
- `删除下载目录` 能被解析为 `unknown` 并被 L3 权限策略拒绝。

权限审查链路已部分跑通：

- `window.close` 被识别为 L2。
- 系统能生成 `review_id`。
- 危险动作会进入拒绝路径，而不是执行 shell 或文件操作。

## 已知问题

### 1. `app.open` 命令返回太慢，并最终报 `gtk-launch timed out`

复现命令：

```bash
/home/rand0mg/vibeos/.venv/bin/vibe ask "打开浏览器" --json
```

现象：

```json
{
  "status": "failed",
  "result": {
    "status": "failed",
    "error": "gtk-launch timed out"
  },
  "selected_target": "org.mozilla.firefox.desktop"
}
```

用户观察：

- 浏览器实际上已经打开。
- CLI 需要等待很久才返回 JSON。

初步判断：

- 当前 `app.open` 实现把 `gtk-launch` 当成同步命令等待退出。
- 某些桌面应用启动后，`gtk-launch` 进程可能不会很快退出，导致 VibeOS 把一次实际成功的启动误判成超时失败。
- 这属于 capability adapter 的执行语义问题，不是模型理解问题。

影响：

- `app.open` 的自然语言 intent 正确。
- 用户体验较差。
- 审计日志会记录为 `failed`，即使用户侧看到应用已经打开。

建议后续处理：

- 把 `app.open` 改成 fire-and-forget 启动语义。
- 或者启动后短时间轮询窗口列表 / app presence 来确认应用是否出现。
- 不应长时间阻塞 CLI。

### 2. `关闭浏览器` 生成了审批，但批准后没有关闭窗口

复现命令：

```bash
/home/rand0mg/vibeos/.venv/bin/vibe ask "关闭浏览器" --json
/home/rand0mg/vibeos/.venv/bin/vibe approve <review_id> --json
```

现象：

```json
{
  "status": "review_required",
  "intent": {
    "action": "window.close",
    "target": {
      "name": "browser",
      "kind": "window"
    }
  }
}
```

审批后：

```json
{
  "status": "failed",
  "message": "no window matched 'browser'"
}
```

初步判断：

- 权限审查层是工作的：`window.close` 正确进入 L2。
- 执行失败发生在窗口解析阶段。
- 当前 `vibe doctor` 显示 `gnome_extension_bridge` 没响应，所以 `WindowRegistry` 很可能无法拿到真实窗口列表。
- 即使 extension bridge 修好，`browser` 也未必能直接匹配 Firefox 窗口标题或 app id，可能还需要 window target alias，例如 `browser -> firefox -> org.mozilla.firefox`。

影响：

- 当前窗口管理能力没有真正闭环。
- `window.list`、`window.focus`、`window.close` 都依赖 GNOME Shell extension bridge。

建议后续处理：

- 先修复 GNOME Shell extension bridge。
- 确认 `vibe windows` 能列出 Firefox 窗口。
- 再改进 window resolver，让 `browser`、`浏览器`、`firefox` 都能匹配 Firefox 窗口。
- 审批执行失败后是否消费 `review_id` 需要重新评估；现在偏安全，但用户体验可能困惑。

### 3. `vibed.service` 没有运行

当前 `doctor` 输出：

```text
warn  vibed_service: vibed.service is not active
```

影响：

- CLI 仍可直接运行本地 broker，所以部分功能不受影响。
- 但 D-Bus 服务 `org.vibeos.Agent` 不一定可用。
- 这意味着系统级 agent runtime 还没有作为用户会话 daemon 常驻起来。

建议后续排查命令：

```bash
systemctl --user status vibed.service --no-pager -l
journalctl --user -u vibed.service -n 100 --no-pager
systemctl --user restart vibed.service
```

需要确认：

- service 文件是否安装到 `~/.config/systemd/user/vibed.service`。
- `ExecStart` 是否指向 `/home/rand0mg/vibeos/.venv/bin/vibed`。
- `VIBEOS_ENV_FILE` 是否指向 `/home/rand0mg/vibeos/.env`。
- `dbus-next` 是否安装在当前 venv 里。

### 4. GNOME Shell extension bridge 没响应

当前 `doctor` 输出：

```text
warn  gnome_extension_bridge: VibeOS GNOME Shell bridge is not responding
```

影响：

- `window.list` 无法可靠列出窗口。
- `window.focus` 无法可靠聚焦窗口。
- `window.minimize` / `window.maximize` / `window.close` 无法可靠执行。

建议后续排查命令：

```bash
gnome-extensions list | grep vibeos
gnome-extensions info vibeos@local
gnome-extensions enable vibeos@local
journalctl --user -n 200 --no-pager | grep -i vibeos
```

操作建议：

- 运行安装脚本后登出 Fedora，再登录一次。
- 如果 GNOME 50 extension API 有兼容问题，需要看 GNOME Shell 日志。
- 当前 extension metadata 虽然声明支持 GNOME 50，但仍需真实日志确认。

### 5. VM 内代码可能与 Windows 工作区不同步

现象：

- Windows 工作区中后续可能已有本地修改。
- Fedora VM 内运行的是之前压缩包解压出来的 `/home/rand0mg/vibeos`。

影响：

- Windows 侧修复不一定已经进入 VM。
- VM 里看到的问题可能来自旧代码。

建议后续同步方式：

- 短期：重新压缩 `E:\codex_project\vibeos`，拖入 VM 覆盖 `/home/rand0mg/vibeos`。
- 中期：把项目放到 Git 仓库，通过 `git pull` 同步。
- 每次同步后在 VM 内重新运行：

```bash
cd /home/rand0mg/vibeos
source /home/rand0mg/vibeos/.venv/bin/activate
pip install -e ".[dev]"
python -m pytest -q /home/rand0mg/vibeos/tests
```

## 当前 API 与能力状态

### CLI 入口

```text
vibe ask
vibe approve
vibe reviews pending
vibe reviews reject
vibe capabilities
vibe apps
vibe windows
vibe doctor
vibe audit tail
```

### D-Bus API

预期服务：

```text
bus name:  org.vibeos.Agent
object:    /org/vibeos/Agent
interface: org.vibeos.Agent
```

方法：

```text
Command(text)
AppsList()
WindowsList()
ApproveReview(review_id)
RejectReview(review_id)
Capabilities()
PendingReviews()
```

当前状态：

- `vibed.service` 未 active，所以 D-Bus 服务需要先修复后再验收。

### Capability 状态

已确认基本可用：

```text
app.list
system.status
capabilities
permission review creation
L3 rejection
audit logging
```

解析正确但执行适配有问题：

```text
app.open
```

依赖 GNOME extension bridge，当前未闭环：

```text
window.list
window.focus
window.minimize
window.maximize
window.close
```

仍需单独实测：

```text
notification.send
portal.open_uri
clipboard.write
```

## 建议下一步优先级

1. 修 `vibed.service`，让 D-Bus agent daemon 真正常驻。
2. 修 GNOME Shell extension bridge，让 `vibe windows` 能列出窗口。
3. 修 `app.open` 的同步等待问题，避免 `gtk-launch timed out`。
4. 改进窗口 target resolver，让 `browser` 能匹配 Firefox 窗口。
5. 重新跑 VM 验收：

```bash
/home/rand0mg/vibeos/.venv/bin/vibe doctor
/home/rand0mg/vibeos/.venv/bin/vibe ask "打开浏览器" --json
/home/rand0mg/vibeos/.venv/bin/vibe ask "列出窗口" --json
/home/rand0mg/vibeos/.venv/bin/vibe ask "切到浏览器" --json
/home/rand0mg/vibeos/.venv/bin/vibe ask "关闭浏览器" --json
```

