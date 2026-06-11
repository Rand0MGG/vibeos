# Linux 会话集成、`vibe` 与 `vibed`

## 1. 两个入口分别是什么

在这个项目里：

- `vibe`
  - CLI 客户端入口
  - 你直接在终端里执行的命令
  - 例如 `vibe ask ...`、`vibe doctor ...`
- `vibed`
  - daemon 服务入口
  - 由 `systemd --user` 常驻运行
  - 对外暴露 D-Bus / HTTP service

如果只运行 `vibe` 而没有 daemon，可退回 `local` transport。
如果 daemon 正常工作，则优先使用 `dbus` 或 `http` transport。

## 2. Linux 会话集成包含什么

当前 Linux session 集成主要包括：

- `vibed.service`
- GNOME Shell extension
- D-Bus 服务
- HTTP daemon status API
- XDG Desktop Portal URI 打开
- window bridge
- `.desktop` 应用注册表

## 3. 安装脚本做了什么

`scripts/install_linux_session.sh` 主要做三件事：

1. 把项目以 editable mode 安装进当前 Python 环境
2. 安装 GNOME Shell extension
3. 写入并启动 `~/.config/systemd/user/vibed.service`

卸载脚本 `scripts/uninstall_linux_session.sh` 主要负责：

- 停掉并 disable `vibed.service`
- 删除 unit 文件
- 禁用并删除 GNOME extension

它不会自动删除你的仓库，也不会自动删除 `.venv`。

## 4. 如何判断现在到底跑的是哪一套

最关键的几个命令：

```bash
which vibe
which vibed
systemctl --user cat vibed.service
systemctl --user status vibed.service --no-pager -l
```

要确认：

- `which vibe` 指向当前仓库的 `.venv`
- `which vibed` 也指向同一个 `.venv`
- `ExecStart=` 指向同一个环境里的 `vibed`
- `vibed.service` 确实 active

## 5. `vibe doctor`

`vibe doctor` 是排障入口，不只是一个展示命令。

它会检查：

- 平台
- session type
- GNOME Shell
- `gdbus`
- portal
- `systemd --user`
- `vibed.service`
- extension bridge
- app registry
- action helpers
- model config

当你怀疑：

- daemon 没起来
- extension 没工作
- portal 行为异常
- VM 缺依赖

先跑：

```bash
vibe doctor --json
./scripts/status_linux_session.sh
```

## 6. 常见错位

最常见的运行错位有三类：

- shell 里的 `vibe` 来自新 `.venv`，但 `vibed.service` 指向旧环境
- service unit 已更新，但 daemon 进程没有重启
- daemon 不可用，CLI 自动回退到 `local`

这三类问题会直接导致：

- 你以为在测 daemon，实际在测 local
- 你以为已经换了新版本，实际还在跑旧进程
- 你以为浏览器行为是 service 的，实际是 local broker 的

## 7. 推荐排障顺序

1. `vibe doctor --json`
2. `systemctl --user status vibed.service --no-pager -l`
3. `journalctl --user -u vibed.service -n 120 --no-pager`
4. `systemctl --user cat vibed.service`
5. `vibe ask ... --json` 里看 `transport`

如果 `transport` 是 `local`，说明你没有走 daemon 主路径。
