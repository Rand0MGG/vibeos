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
