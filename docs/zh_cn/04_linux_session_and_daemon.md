# Linux 会话、`vibe` 与 `vibed`

- `vibe` 是 CLI 入口；无 daemon 时可以走 local transport。
- `vibed` 是 `systemd --user` 管理的 daemon，可提供 D-Bus/HTTP transport。
- GNOME Shell extension、portal、窗口桥、`.desktop` 注册表、通知和剪贴板
  helper 都属于真实 Linux 会话集成，不应由 WSL dry-run 代替。

在真实 GNOME 主机上，先确认 CLI 与服务没有指向不同虚拟环境：

```bash
which vibe
which vibed
systemctl --user status vibed.service --no-pager -l
systemctl --user cat vibed.service
vibe doctor --json
```

`vibe doctor --json` 在 WSL 里出现 GNOME、portal、daemon 或扩展警告是预期
现象；这说明环境没有桌面集成，不是这些能力已验证可用。WSL 用于确定性代码
验证，请遵守 [WSL 测试标准](07_wsl_test_standard.md)。真实桌面验证的边界与
最小 checklist 见 [GNOME VM 验收](../operations/gnome_vm_acceptance.md)。

## 单一 daemon 生命周期

`vibed` 现在由一个 asyncio supervisor 统一管理数据库、D-Bus 和薄 HTTP
兼容 adapter，生命周期为 `start -> ready -> drain -> stop`。ready 前和 drain
后都拒绝新请求；SIGTERM 会先 drain，再按逆序停止组件。D-Bus 与 HTTP 不再
各自拥有线程式服务生命周期。

数据库组件会在任何 transport 启动前执行 Alembic，并同时验证当前 revision、
全部权威表、WAL、外键和 busy timeout；随后重新绑定 ReviewStore 兼容连接。
迁移、schema 或重绑定任一失败都会阻止 daemon 进入 `ready`。

HTTP 暂时保留，是因为 CLI runtime fallback、`status_linux_session.sh` 和
`collect_vm_evidence.py` 仍是真实调用者；它不包含独立业务逻辑，最迟在 Goal 02
迁移这些调用者后删除。D-Bus 是首选本地 daemon transport。

WSL 或诊断环境可使用 `vibed --dbus --offline`，它只把意图理解切换为确定性
本地规则，不改变 capability、权限、数据库、核心切片或真实 adapter 装配。
