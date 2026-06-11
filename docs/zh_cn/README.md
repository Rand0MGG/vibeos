# VibeOS 中文文档总览

这套中文文档的目标不是替换历史版本文档，而是把目前分散在 `docs/` 里的设计、实现、测试、部署材料重新整理成更适合维护和阅读的模块化说明书。

阅读顺序建议：

1. [01_overview.md](/E:/codex_project/vibeos/docs/zh_cn/01_overview.md)
2. [02_planning_and_execution.md](/E:/codex_project/vibeos/docs/zh_cn/02_planning_and_execution.md)
3. [03_capabilities_and_permissions.md](/E:/codex_project/vibeos/docs/zh_cn/03_capabilities_and_permissions.md)
4. [04_linux_session_and_daemon.md](/E:/codex_project/vibeos/docs/zh_cn/04_linux_session_and_daemon.md)
5. [05_vm_install_upgrade_test_runbook.md](/E:/codex_project/vibeos/docs/zh_cn/05_vm_install_upgrade_test_runbook.md)
6. [06_history_and_source_index.md](/E:/codex_project/vibeos/docs/zh_cn/06_history_and_source_index.md)
7. [07_wsl_test_standard.md](/E:/codex_project/vibeos/docs/zh_cn/07_wsl_test_standard.md)

## 文档结构

- `01_overview.md`
  - 项目目标
  - 当前主架构
  - 主要数据流
- `02_planning_and_execution.md`
  - 自然语言到任务计划的路径
  - `run / attempt / retry / replan`
  - 验收与调试链路
- `03_capabilities_and_permissions.md`
  - 能力注册表
  - 风险等级
  - 审批与审计
- `04_linux_session_and_daemon.md`
  - Linux 桌面集成
  - `vibe` / `vibed`
  - daemon transport、doctor、常见排障
- `05_vm_install_upgrade_test_runbook.md`
  - VM 环境准备
  - 卸载旧版本常驻服务
  - 安装新版本
  - 完整测试与验收命令
- `06_history_and_source_index.md`
  - 旧版按版本文档的归档索引
  - 按模块对应到原始资料
- `07_wsl_test_standard.md`
  - WSL 应承担的测试范围
  - WSL 与 VM 的职责边界
  - WSL 标准命令与通过条件

## 使用原则

- 这套中文文档以当前 `main` 分支实现为准。
- 历史版本文档原文保留，不直接改写或删除。
- 当模块说明与历史草案冲突时，以当前代码和 `docs/current_status.md` 为准。
