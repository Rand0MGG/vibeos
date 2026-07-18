# WSL 测试标准

本项目的标准本地 Linux 环境是 `FedoraLinux-44` WSL：

- 仓库：`/mnt/e/codex_project/vibeos`
- 虚拟环境：`/home/rand0mg/.venvs/vibeos`

WSL 负责 Linux 用户态、数据库并发、进程恢复、CLI/D-Bus 与静态质量预验证；
不替代 Fedora GNOME Wayland VM 的真实桌面验收。

## 1. 环境

```bash
cd /mnt/e/codex_project/vibeos
source /home/rand0mg/.venvs/vibeos/bin/activate
which python
which vibe
which vibed
```

首次初始化：

```bash
python3 -m venv /home/rand0mg/.venvs/vibeos
source /home/rand0mg/.venvs/vibeos/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

## 2. 每次必须执行的质量门禁

```bash
python -m ruff check src tests
python -m ruff format --check src tests
python -m mypy --strict
python -m pytest -q
python scripts/architecture_guard.py
```

要求：无 lint、格式、类型、测试或架构违规。`docs/goals/agent_native/`
中的 Goal 合同不可由执行任务修改。

2026-07-19 Goal 03 dry-run 恢复修复后完整结果：`958 passed in 52.72s`；
Ruff lint/format、48 个声明源码文件的 strict mypy 和 architecture guard
全部通过。包含三项 dry-run 真实子进程崩溃回归的持久化任务专项为
`708 passed in 24.04s`。

## 3. Goal 02 持久化任务专项

```bash
python -m pytest \
  tests/test_durable_task_domain.py \
  tests/test_durable_task_repository.py \
  tests/test_durable_task_controls.py \
  tests/test_durable_task_crash_matrix.py -q

python scripts/benchmark_durable_tasks.py
```

专项必须证明：

- 全状态/事件矩阵 fail-closed，终态不可复活；
- 一小时 fake-clock wait 在 repository/daemon 重启后仍可扫描；
- 双 worker 竞争只有一个有效 lease owner，过期 token 被 fencing；
- proposal-before-I/O、receipt-after-I/O 和 unknown 对账暂停；
- review/clarification 重启恢复；
- pause/resume/cancel/takeover/release 使用 revision CAS 并留下 domain event；
- outbox 至少一次投递与 consumer 幂等；
- 64 task / 8 worker 基准零错误，p95 不超过 2500 ms，wall 不超过 20 s。

实测报告见 `docs/architecture/durable_task_benchmark.json`。

2026-07-19 最新基准：p95 `66.22 ms`、wall `0.196 s`、吞吐
`327.17 tasks/s`、lock/commit error 为 0。

## 4. CLI 与本地路径

```bash
vibe doctor --json
vibe capabilities --json
vibe plan "open browser" --json
vibe ask "status" --json --offline
vibe ask "search web for hello" --json --offline --dry-run
vibe tasks list --json
```

WSL 的 `vibe doctor` 可以因无 GNOME/portal/桌面 service 返回 `warn`；这不是
失败。命令不得出现 traceback，支持任务结果应包含 task、run、attempts、
execution/acceptance/overall status。

dry-run 必须投影为明确的 `dry_run` 终态并绑定模拟证据，不得投影为
`succeeded` 或真实桌面效果。

## 5. 真实 session D-Bus 预验证

有 user session bus 时：

```bash
python scripts/verify_foundation_dbus.py
```

没有现成 bus 时：

```bash
dbus-run-session -- python scripts/verify_foundation_dbus.py
```

若 `dbus-run-session` 未安装但已有可用的 session bus，直接命令仍是有效的
预验证路径；应记录包装器缺失，但不得把它误报为代码失败。

验证器临时启动 `vibed --offline`，检查 daemon ready、19 capability、严格请求
合同、E0/E1 路径，以及 `TasksList`/`TaskShow` 暴露的持久任务状态。WSL 中通知
adapter 返回 unavailable 是准确环境证据，不能宣称桌面通知已显示。

## 6. WSL 明确不负责

- GNOME Shell extension 是否加载；
- Wayland 窗口 focus/minimize/maximize/close 的真实效果；
- portal 是否真正打开桌面应用；
- 通知是否可见、剪贴板是否真的改变；
- `collect_vm_evidence.py --real` 的最终通过。

这些项目必须回到 Fedora GNOME VM。可选 WSLg 真实动作测试只能作为预验证：

```bash
python scripts/verify_wsl_real_actions.py
```

## 7. 推荐顺序

1. Ruff、format、mypy；
2. durable task 专项与全量 pytest；
3. architecture guard 与并发基准；
4. doctor、capabilities、plan、offline ask；
5. `dbus-run-session -- python scripts/verify_foundation_dbus.py`；
6. 最后在 GNOME VM 执行真实桌面验收。
