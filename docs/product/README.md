# VibeOS 产品文档

本目录定义 VibeOS 的产品方向和目标体系。它回答“为什么做、为谁做、最终
要取得什么结果”，但不把尚未实现的愿景描述成当前能力。

## 阅读顺序

1. [产品章程](product_charter.md)——项目使命、目标用户、产品边界、核心
   原则和北极星结果。
2. [战略目标](strategic_goals.md)——从当前原型走向可信 Linux 个人代理所
   必须完成的结果，以及目标间的依赖和优先顺序。
3. [Agent 总体系统框架](agent_system_framework.md)——目标契约、长期任务内核、
   机器模型、动作层、独立提权审核、事务回滚、Secret Broker 和模型路由。
4. [Agent-native 方向决策](decisions/0001-agent-native-direction.md)——本轮已经
   确认的产品取舍、约束、影响和仍待验证的问题。
5. [实施技术底座决策](decisions/0002-implementation-foundation.md)——模块化
   单体、持久化、任务引擎、sandbox、提权、秘密和桌面路径的技术取舍。
6. [Agent-native 实施计划](../goals/agent_native/README.md)——按风险和代码依赖
   拆分的九份可直接交给 Codex 的阶段 Goal 及统一验收门禁。

## 文档权威层级

当不同文档出现冲突时，按以下规则判断：

1. **当前实现事实**：源代码、自动化测试和
   [当前状态](../architecture/current_status.md)。
2. **产品方向与取舍**：本目录中的产品章程、战略目标、总体系统框架和已
   接受的决策记录。
3. **已确认实施计划**：[Agent-native 实施计划](../goals/agent_native/README.md)
   及其中的阶段 Goal。
4. **当前技术设计**：[运行时架构](../architecture/runtime_convergence.md)和
   [能力注册表](../architecture/capability_registry.md)。
5. **历史背景**：[历史归档](../archive/README.md)和
   [早期个人代理愿景](../reference/vibeos_personal_agent_vision.md)。

产品方向说明“应该走向哪里”；当前状态说明“现在实际到达哪里”。产品文
档不能用来声明功能已经实现，当前实现也不应在没有产品决策的情况下反向
定义长期使命。

## 后续版本文档

以下文档应在战略目标确认后逐步补齐：

- 版本路线图：为近期发布选择阶段目标、时间、负责人和资源；
- MVP 定义：首个真实 GNOME 用户闭环及明确非目标；
- 用户场景与任务目录：目标用户、关键任务和黄金验收集；
- 产品需求文档：按版本拆分的范围、交互和验收标准；
- 发布与质量门禁：安装、升级、兼容性、安全和回滚要求；
- 后续决策记录：新增重大产品取舍、替代方案和复审日期。

这些文档必须从产品章程和战略目标派生，避免重新形成互相冲突的目标集合。
