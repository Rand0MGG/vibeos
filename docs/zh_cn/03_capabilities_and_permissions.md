# 能力注册表与权限模型

## 1. 基本原则

VibeOS 不允许模型直接输出任意桌面操作。所有可执行动作都必须先出现在 capability registry 中。

注册表是单一事实源，定义：

- action 名称
- 风险等级
- 是否需要审批
- 是否允许执行
- 执行效果说明
- 可逆性
- target 约束

## 2. 当前能力面

当前主要能力包括：

- `app.list`
- `app.open`
- `window.list`
- `window.focus`
- `window.minimize`
- `window.maximize`
- `window.close`
- `notification.send`
- `clipboard.write`
- `portal.open_uri`
- `system.status`
- `browser.open_url`
- `browser.search_web`
- `browser.open_site_search`

更详细的动作表见原文档：

- [capability_registry.md](/E:/codex_project/vibeos/docs/capability_registry.md:1)

## 3. 风险等级

当前权限模型按 L0-L3 工作：

- `L0`
  - 只读观察
  - 自动执行
- `L1`
  - 低风险
  - 自动执行并审计
- `L2`
  - 中风险
  - 生成 `review_id`
  - 必须显式批准
- `L3`
  - 高风险
  - 直接拒绝

这层设计的意义是：

- 用户批准的是“已审查的结构化动作”
- 不是重新让模型再理解一次原始命令

## 4. 目标约束

权限审查不只看 action，还看 target。

例如：

- URI 只能是允许的 scheme
- 剪贴板内容不能空，也不能过长
- 通知标题与正文有长度限制
- `app.open` 必须有可解析的应用名
- 窗口类动作必须有可解析的窗口目标

## 5. 审批流

标准 L2 流程：

1. 用户发出自然语言请求
2. 系统完成 planning
3. review 层判断这是 L2
4. 系统返回 `review_required` 和 `review_id`
5. 用户执行 `vibe approve <review_id>`
6. 系统执行存储的结构化动作

关键点：

- 批准的是 review 记录，不是原始文本
- 真实批准通常是一性消费
- 过期 review 不可再批准
- 已拒绝 review 不可再批准

## 6. 为什么这层很重要

如果没有这层，系统就会退化成：

- 自然语言 -> 直接系统动作

这既不安全，也不利于审计和回放。

VibeOS 现在的目标是：

- 限制能力面
- 限制目标形状
- 留下审计链
- 保持自然语言 agent 的上层灵活性
