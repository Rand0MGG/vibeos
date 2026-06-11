# Linux VM 安装、升级、卸载与完整测试手册

这份手册面向 Fedora Workstation 或 Ubuntu GNOME Wayland VM。

目标：

- 配置 VM 环境
- 创建并激活项目 `venv`
- 卸载旧版本，让旧 `vibed` 不再常驻
- 安装当前仓库版本
- 确认 `vibe` / `vibed` / `vibed.service` 指向一致
- 跑完整 smoke test 和 evidence test

## 1. 安装基础依赖

Fedora：

```bash
sudo dnf install python3 python3-pip python3-venv glib2 wl-clipboard libnotify curl
```

Ubuntu：

```bash
sudo apt install python3 python3-venv python3-pip libglib2.0-bin wl-clipboard libnotify-bin curl
```

## 2. 创建并激活 `venv`

```bash
cd ~/vibeos
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

确认当前命令入口：

```bash
which python
which vibe
which vibed
```

预期都在：

```text
~/vibeos/.venv/bin/
```

## 3. 配置 `.env`

如果你使用模型：

```bash
cd ~/vibeos
cp .env.example .env
```

示例：

```env
VIBEOS_MODEL_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-your-key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
```

如果你在中国大陆环境里做浏览器搜索验证，建议同时设置：

```env
VIBEOS_DEFAULT_SEARCH_ENGINE=baidu
VIBEOS_SEARCH_ENGINE_URL_TEMPLATE=https://www.baidu.com/s?wd={query}
```

如果你明确要求测试 daemon 主路径：

```env
VIBEOS_REQUIRE_DAEMON=1
```

## 4. 卸载旧版本并停止常驻

先激活当前仓库的环境：

```bash
cd ~/vibeos
source .venv/bin/activate
```

先跑正常卸载：

```bash
./scripts/uninstall_linux_session.sh || true
```

然后确认旧服务已经不再常驻：

```bash
systemctl --user daemon-reload
systemctl --user cat vibed.service
systemctl --user status vibed.service --no-pager -l
```

如果你想强制清理：

```bash
systemctl --user stop vibed.service || true
systemctl --user disable vibed.service || true
rm -f ~/.config/systemd/user/vibed.service
rm -f ~/.config/systemd/user/default.target.wants/vibed.service
rm -rf ~/.local/share/gnome-shell/extensions/vibeos@local
systemctl --user daemon-reload
hash -r
```

如果你还要连 `.venv` 一起重建：

```bash
rm -rf .venv
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

## 5. 安装当前版本

```bash
cd ~/vibeos
source .venv/bin/activate
chmod +x scripts/*.sh
./scripts/install_linux_session.sh
```

这一步会：

- editable 安装 Python 包
- 安装 GNOME extension
- 写入 `~/.config/systemd/user/vibed.service`
- 启动 `vibed.service`
- 运行 `vibe doctor`

## 6. 验证当前运行的是哪一版

```bash
which vibe
which vibed
systemctl --user cat vibed.service
systemctl --user status vibed.service --no-pager -l
```

你要确认：

- `which vibe` 指向 `~/vibeos/.venv/bin/vibe`
- `which vibed` 指向 `~/vibeos/.venv/bin/vibed`
- `ExecStart=` 指向同一个 `.venv`
- service 已经 active

如果 unit 已变更但进程没有刷新：

```bash
systemctl --user daemon-reload
systemctl --user restart vibed.service
systemctl --user status vibed.service --no-pager -l
```

## 7. 基线诊断

```bash
cd ~/vibeos
source .venv/bin/activate
vibe doctor --json
vibe capabilities --json
./scripts/status_linux_session.sh
```

重点看：

- `vibed_service`
- `gnome_extension_bridge`
- `runtime_entry`
- `action_helpers`

## 8. 代码回归测试

```bash
cd ~/vibeos
source .venv/bin/activate
python -m pytest -q
```

## 9. 功能 smoke test

建议按顺序跑：

```bash
cd ~/vibeos
source .venv/bin/activate

vibe plan "open browser" --json
vibe plan "open https://example.com" --json
vibe plan "clipboard hello" --json
vibe ask "search web for hello" --json --offline --dry-run

vibe ask "open browser" --json
vibe ask "list windows" --json
vibe ask "notify hello" --json
vibe ask "open https://example.com" --json
vibe ask "search web for hello" --json
vibe ask "copy hello to clipboard" --json
vibe reviews pending --json
```

如果产生 `review_id`，继续：

```bash
vibe approve <review_id> --json
```

## 10. 完整证据采集

安全模式：

```bash
cd ~/vibeos
source .venv/bin/activate
python scripts/collect_vm_evidence.py
```

真实动作模式：

```bash
cd ~/vibeos
source .venv/bin/activate
python scripts/collect_vm_evidence.py --real
```

目标结果：

```json
{
  "overall": "ok",
  "mode": "real"
}
```

## 11. 最短定位命令

当你怀疑“跑的不是当前版本”或“没有走 daemon”时，直接跑：

```bash
cd ~/vibeos
source .venv/bin/activate
which vibe
which vibed
systemctl --user cat vibed.service
systemctl --user status vibed.service --no-pager -l
vibe ask "search web for hello" --json
```

判断方式：

- `vibed.service` 不存在：说明没安装或已卸载 daemon
- 返回里 `transport = local`：说明没有走 daemon
- 返回里有 `run` 和 `attempts`：说明已经在新版结构化任务路径
- `ExecStart=` 与 `which vibed` 不一致：说明 service 和 shell 不在同一个环境

## 12. 一键升级流程

如果只是把已安装版本升级到当前仓库代码：

```bash
cd ~/vibeos
source .venv/bin/activate
./scripts/uninstall_linux_session.sh || true
pip install -e ".[dev]"
./scripts/install_linux_session.sh
vibe doctor --json
systemctl --user status vibed.service --no-pager -l
```

## 13. `python scripts/collect_vm_evidence.py --real` 没跑通时怎么查

不要只看最后是不是 `overall != ok`，要按层排查。

建议按下面顺序跑：

```bash
cd ~/vibeos
source .venv/bin/activate

vibe doctor --json
./scripts/status_linux_session.sh

systemctl --user status vibed.service --no-pager -l
journalctl --user -u vibed.service -n 200 --no-pager

python scripts/collect_vm_evidence.py --real
ls -lt .vibeos-vm-evidence/
```

然后看最新生成的报告：

```bash
python -m json.tool .vibeos-vm-evidence/<最新报告文件>.json | less
```

如果你只想快速看几个关键字段：

```bash
grep -n '"overall"' .vibeos-vm-evidence/<最新报告文件>.json
grep -n '"doctor"' .vibeos-vm-evidence/<最新报告文件>.json
grep -n '"runtime_entry"' .vibeos-vm-evidence/<最新报告文件>.json
grep -n '"vibed_service"' .vibeos-vm-evidence/<最新报告文件>.json
grep -n '"gnome_extension_bridge"' .vibeos-vm-evidence/<最新报告文件>.json
grep -n '"transport"' .vibeos-vm-evidence/<最新报告文件>.json
```

各层分别代表什么：

- `vibe doctor --json`
  - 先回答“这台 VM 有没有能力通过真实验收”
  - 如果 `vibed_service`、`gnome_extension_bridge`、`xdg_desktop_portal`、`action_helpers` 本身就 `fail`，先修环境，不要先怀疑 planner
- `./scripts/status_linux_session.sh`
  - 一次性把 unit、journal、D-Bus、HTTP、GNOME extension 状态都打出来
- `systemctl --user status vibed.service`
  - 看 daemon 是不是 active、是不是在反复重启、是不是启动失败
- `journalctl --user -u vibed.service`
  - 看 daemon 为什么失败
- `.vibeos-vm-evidence/*.json`
  - 看脚本化验收到底是哪一项失败

### 常见故障 1：`vibed.service` 不存在或没启动

表现：

- `systemctl --user status vibed.service` 提示 not found 或 inactive
- evidence 报告里 daemon 相关检查失败

处理：

```bash
source ~/vibeos/.venv/bin/activate
./scripts/install_linux_session.sh
systemctl --user daemon-reload
systemctl --user restart vibed.service
```

### 常见故障 2：结果里 `transport = local`

表现：

- `vibe ask ... --json` 结果显示 `"transport": "local"`
- 这说明 CLI 没有走 daemon，而是回退到了本地运行时

排查：

```bash
systemctl --user status vibed.service --no-pager -l
systemctl --user cat vibed.service
vibe doctor --json
```

结论：

- `--real` 场景下，`local` 不能替代 daemon 验证
- 先把 daemon 修通，再重新跑 evidence

### 常见故障 3：GNOME Shell bridge 没响应

表现：

- `vibe doctor --json` 里 `gnome_extension_bridge` 是 `warn` 或 `fail`
- 窗口类能力失败

排查：

```bash
gnome-extensions list | grep vibeos
gnome-extensions info vibeos@local
gdbus call --session --dest org.vibeos.Shell --object-path /org/vibeos/Shell --method org.vibeos.Shell.ListWindows
```

处理：

- 重跑 `./scripts/install_linux_session.sh`
- 退出图形会话再重新登录
- 再次跑 `vibe doctor --json`

### 常见故障 4：浏览器 / portal 类动作失败

表现：

- evidence 报告里 browser open/search 相关项失败
- journal 里有 portal、URI opener、browser integration 错误

排查：

```bash
vibe ask "open https://example.com" --json
vibe ask "search web for hello" --json
journalctl --user -u vibed.service -n 200 --no-pager
```

重点看这些字段：

- `transport`
- `execution_status`
- `acceptance_status`
- `overall_status`
- `run`
- `attempts`

### 常见故障 5：剪贴板或通知 helper 缺失

表现：

- `vibe doctor --json` 里 `action_helpers` 是 `warn` 或 `fail`
- evidence 里的 clipboard / notification 步骤失败

处理：

Fedora：

```bash
sudo dnf install wl-clipboard libnotify
```

Ubuntu：

```bash
sudo apt install wl-clipboard libnotify-bin
```

### 常见故障 6：service 跑的不是你当前 `.venv`

表现：

- `which vibe` 和 `ExecStart=` 指向不同路径
- shell 里行为和 daemon 里行为不一致

排查：

```bash
which vibe
which vibed
systemctl --user cat vibed.service
```

处理：

```bash
source ~/vibeos/.venv/bin/activate
pip install -e ".[dev]"
./scripts/install_linux_session.sh
systemctl --user daemon-reload
systemctl --user restart vibed.service
```

### 常见故障 7：需要一套完整 bug 报告

把下面这些输出一起收集出来：

```bash
cd ~/vibeos
source .venv/bin/activate
vibe doctor --json
systemctl --user status vibed.service --no-pager -l
journalctl --user -u vibed.service -n 200 --no-pager
vibe ask "search web for hello" --json
python scripts/collect_vm_evidence.py --real
```

这组信息通常足够判断问题属于哪一层：

- 环境配置
- daemon transport
- GNOME bridge
- portal / browser integration
- review 流程
- acceptance 逻辑
