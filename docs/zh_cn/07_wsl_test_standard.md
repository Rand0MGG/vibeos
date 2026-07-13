# WSL 测试标准

这份文档定义 VibeOS 在 WSL 中应该完成什么测试、不能把什么当成 WSL 的职责，以及推荐的标准命令与通过条件。

当前已确认的 WSL 环境：

- 发行版：`FedoraLinux-44`
- 系统标识：`Fedora Linux 44 (WSL)`
- 当前用户：`rand0mg`

## 1. WSL 的角色定位

WSL 在这个项目里的定位不是“替代 Fedora GNOME VM”，而是“承担 Linux 用户态开发与预验证”。

WSL 适合做的事情：

- Linux 用户态依赖安装
- Python 虚拟环境维护
- `pytest` 全量与定向回归
- CLI 本地路径验证
- `--offline`、`--dry-run`、本地 broker 路径验证
- Linux 路径、权限、换行、脚本可执行位等问题排查
- `vibe doctor --json` 的 Linux 用户态部分验证

WSL 不适合替代的事情：

- GNOME Shell extension 验证
- Wayland 窗口管理真实行为
- `systemd --user` 在图形桌面会话中的完整语义
- `xdg-desktop-portal` 的最终桌面动作验收
- 通知、剪贴板、窗口焦点、窗口关闭等真实桌面副作用
- `python scripts/collect_vm_evidence.py --real` 的最终验收

一句话：

- `WSL` 负责“Linux 开发与预验证”
- `VM` 负责“真实桌面会话验收”

## 2. WSL 标准工作目录与虚拟环境

推荐约定：

- 仓库工作目录：
  - `/mnt/e/codex_project/vibeos`
- 持久虚拟环境：
  - `/home/rand0mg/.venvs/vibeos`

推荐原因：

- 仓库继续放在 Windows 磁盘，和当前开发环境一致
- 虚拟环境放在 WSL 的 Linux 用户目录，避免直接把 `.venv` 放在 `/mnt/e` 下造成的兼容和性能问题

## 3. WSL 环境初始化标准命令

在 Windows PowerShell 中可通过：

```powershell
wsl -d FedoraLinux-44
```

进入 WSL 后，标准初始化命令：

```bash
cd /mnt/e/codex_project/vibeos
mkdir -p /home/rand0mg/.venvs
python3 -m venv /home/rand0mg/.venvs/vibeos
source /home/rand0mg/.venvs/vibeos/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

验证入口：

```bash
which python
which vibe
which vibed
```

预期：

- `python` 指向 `/home/rand0mg/.venvs/vibeos/bin/python`
- `vibe` 指向 `/home/rand0mg/.venvs/vibeos/bin/vibe`
- `vibed` 指向 `/home/rand0mg/.venvs/vibeos/bin/vibed`

## 4. WSL 必跑测试

### 4.1 Python 回归测试

这是 WSL 里必须跑的第一层标准测试：

```bash
cd /mnt/e/codex_project/vibeos
source /home/rand0mg/.venvs/vibeos/bin/activate
python -m pytest -q
```

通过标准：

- 全量测试通过
- 不接受 import 失败、路径失败、editable install 失败

当前基线：

- 已在 Fedora 44 WSL 中实际跑通
- 最近一次完整验证（2026-07-12）：`263 passed in 11.90s`

### 4.1.1 静态质量检查

在全量回归前或提交前，运行当前 CI 同样执行的静态门禁：

```bash
cd /mnt/e/codex_project/vibeos
source /home/rand0mg/.venvs/vibeos/bin/activate
python -m ruff check src tests
python -m ruff format --check src tests
python -m mypy --strict
```

通过标准：Ruff 无诊断、格式化无需修改、严格类型检查在已声明的核心范围内
无问题。当前范围与结果见 `docs/architecture_completion_final_audit.md`。

### 4.2 基础诊断

```bash
cd /mnt/e/codex_project/vibeos
source /home/rand0mg/.venvs/vibeos/bin/activate
vibe doctor --json
```

WSL 中的预期不是 `overall: ok`，而是“符合 WSL 角色的 `warn`”。

WSL 下通常合理的结果是：

- `platform`: `ok`
- `gdbus`: `ok`
- `systemd_user`: `ok` 或至少不是 `fail`
- `model_config`: `ok`（若 `.env` 已配置）

WSL 下通常预期会是 `warn` 的项目：

- `session_type`
- `gnome_shell`
- `xdg_desktop_portal`
- `vibed_service`
- `runtime_entry`
- `gnome_extension_bridge`
- `app_registry`
- `action_helpers`

这些 `warn` 在 WSL 中是可接受的，不应该被误判成“项目坏了”。

### 4.3 CLI 结构化路径检查

推荐在 WSL 中跑这些命令：

```bash
cd /mnt/e/codex_project/vibeos
source /home/rand0mg/.venvs/vibeos/bin/activate

vibe capabilities --json
vibe plan "open browser" --json
vibe plan "open https://example.com" --json
vibe ask "search web for hello" --json --offline --dry-run
vibe ask "copy hello to clipboard" --json --dry-run
```

通过标准：

- 命令可以正常返回 JSON
- 支持任务路径能生成结构化 `plan`
- 不出现原始 Python traceback
- 对 review-required 类动作，至少 dry-run 路径要正常工作

### 4.4 结构化结果字段检查

在 WSL 中，以下字段应该能正常观察到：

- `execution_status`
- `acceptance_status`
- `overall_status`

对支持任务执行路径，还应能观察到：

- `run`
- `attempts`

这一步的目的不是验证真实桌面效果，而是验证结构化运行链没有退化。

## 5. WSL 可以承担的验证任务

下面这些属于“应该在 WSL 里先做掉”的内容：

### 5.1 安装与依赖验证

- `pip install -e ".[dev]"`
- Python 依赖是否完整
- Linux 下脚本是否能执行
- shebang、权限位、换行问题

### 5.2 Linux 用户态行为验证

- `pytest`
- `vibe` CLI 的结构化输出
- 本地 broker / offline 模式
- review 流程的非桌面部分
- 配置项加载，例如 `.env`、搜索引擎模板、provider timeout

### 5.3 代码回归前置筛查

每次改完代码，建议先在 WSL 跑：

```bash
python -m pytest -q
vibe doctor --json
vibe ask "search web for hello" --json --offline --dry-run
```

只有这三步过了，再回 VM 跑桌面验收。

## 6. WSL 明确不承担的任务

下面这些不能用 WSL 替代 VM：

- `./scripts/install_linux_session.sh` 的最终桌面有效性验收
- `vibed.service` 在真实 GNOME 图形会话中的行为
- GNOME Shell extension 是否真正加载
- `window.list` / `window.focus` / `window.close` 的真实桌面行为
- `notification.send` 的真实通知展示
- `clipboard.write` 的真实系统剪贴板行为
- `portal.open_uri` 打开真实浏览器后的最终桌面结果
- `python scripts/collect_vm_evidence.py --real`

如果这些也在 WSL 里“看起来能跑”，也不能当成 VM 验收通过。

## 7. WSL 与 VM 的标准分工

建议固定采用下面的分工：

### WSL 负责

- 每次改代码后的第一轮回归
- Linux 用户态脚本和依赖问题
- CLI 结构化结果检查
- provider / planning / run loop / acceptance 的非桌面逻辑验证

### VM 负责

- Fedora GNOME Wayland 真实桌面行为
- daemon 常驻与真实 transport
- portal、通知、剪贴板、窗口管理真实副作用
- `collect_vm_evidence.py --real`

## 8. 推荐测试顺序

每次改动后的标准顺序：

1. Windows 本机快速编辑与静态检查
2. WSL 跑：

```bash
python -m pytest -q
vibe doctor --json
vibe ask "search web for hello" --json --offline --dry-run
```

3. 如果 WSL 通过，再去 VM 跑：

```bash
vibe doctor --json
./scripts/status_linux_session.sh
python scripts/collect_vm_evidence.py --real
```

## 9. 当前 WSL 已知合理现象

在当前 Fedora 44 WSL 中，下面这些现象不应被视为 bug：

- `XDG_SESSION_TYPE` 未设置
- `gnome-shell` 不存在
- `xdg_desktop_portal` 不可用
- `vibed.service` 未激活
- `runtime_entry` 提示会回退到 `local`
- `app_registry` 为 0
- `notify-send`、`wl-copy/xclip/xsel` 缺失

这些都是 WSL 不是 GNOME 桌面会话带来的正常结果。

## 10. 当前 WSL 不应出现的问题

以下问题如果在 WSL 里出现，应该优先修：

- `pip install -e ".[dev]"` 失败
- `pytest` 失败
- `vibe` / `vibed` 入口找不到
- `vibe plan` / `vibe ask --offline --dry-run` 抛 traceback
- 支持任务结果缺失 `execution_status` / `acceptance_status` / `overall_status`
- 结构化支持任务结果缺失 `run` / `attempts`

这些问题说明不是桌面集成限制，而是用户态开发面本身有问题。

## 11. 标准命令清单

### 进入 WSL

```powershell
wsl -d FedoraLinux-44
```

### 激活项目环境

```bash
cd /mnt/e/codex_project/vibeos
source /home/rand0mg/.venvs/vibeos/bin/activate
```

### 必跑命令

```bash
python -m pytest -q
vibe doctor --json
vibe capabilities --json
vibe plan "open browser" --json
vibe ask "search web for hello" --json --offline --dry-run
```

### 需要时补跑

```bash
vibe ask "copy hello to clipboard" --json --dry-run
vibe ask "open https://example.com" --json --offline --dry-run
```

## 12. 结论

WSL 的标准不是“模拟完整 Linux 桌面”，而是：

- 提前把 Linux 用户态问题清掉
- 降低每次都进 VM 的成本
- 把必须回到 VM 的范围压缩到真正的桌面集成和最终验收

只要这个边界守住，WSL 就是高价值开发环境，而不是一个失败的 VM 替代品。
