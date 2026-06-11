# VibeOS 总览

## 1. 项目定位

VibeOS 的目标不是做一个固定命令解析器，而是做一个能够通过自然语言操控 Linux 图形桌面会话的 agent runtime。

当前聚焦范围：

- GNOME Wayland 用户会话
- 受限、可审计的能力集合
- 明确的计划、执行、观察、验收链路
- 不允许任意 shell 执行
- 不允许模型直接调用 D-Bus、桌面 API 或脚本

## 2. 当前主架构

当前支持任务的主路径是：

```text
utterance
  -> utterance analysis
  -> goal synthesis
  -> domain routing
  -> observation
  -> capability exposure
  -> candidate plans
  -> validation
  -> review
  -> execution
  -> post-execution observation
  -> acceptance
  -> bounded retry / bounded replan
  -> run trace / debug trace / audit
```

这条路径的几个核心原则：

- 主语义路径以结构化 planning 为中心，不再依赖旧的单 intent 直通主路径。
- 执行成功不等于任务完成，必须区分 `execution_status`、`acceptance_status`、`overall_status`。
- 浏览器、窗口、剪贴板、通知等能力必须通过注册表和风险策略暴露。
- 失败必须结构化分类，不能只靠一条字符串报错。

## 3. 当前模块边界

当前显式 domain pack 包括：

- `apps`
- `window_management`
- `clipboard`
- `notification`
- `system_observation`
- `browser`
- `media`

每个 domain 通过注册的 route、capability、context package、verifier 参与 planning 和 execution。

## 4. 当前公开结果语义

所有主要任务结果都应该公开：

- `execution_status`
- `acceptance_status`
- `overall_status`

任务计划路径还应公开：

- `run`
- `attempts`

这意味着调用方不应该只看一句顶层 `message`，而应该看结构化结果。

## 5. 配置原则

模型是可选增强，不是系统安全边界。

- 配置模型时，使用 OpenAI-compatible broker
- 未配置模型时，可使用本地 deterministic 路径覆盖当前受支持任务面
- provider 失败应显式暴露，不应静默伪装成“模型理解成功”

## 6. 当前阅读优先级

如果你刚接手这个仓库，建议按下面顺序理解：

1. 先看 `02_planning_and_execution.md`
2. 再看 `03_capabilities_and_permissions.md`
3. 然后看 `04_linux_session_and_daemon.md`
4. 最后用 `05_vm_install_upgrade_test_runbook.md` 做真实 VM 验证
