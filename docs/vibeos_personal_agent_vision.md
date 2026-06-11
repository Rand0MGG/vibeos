# VibeOS 项目中心思想

Last updated: 2026-06-10

## 一句话定义

**VibeOS 不是一个把自然语言翻译成几个桌面命令的 broker。**

**VibeOS 要做的是一个本地优先、可审计、把 agent 抽象成用户代理的系统级个人助理 runtime。**

更直白一点说：

```text
VibeOS = 让 agent 像用户本人一样观察、理解、操作电脑，
但整个过程仍然受系统边界、权限策略、验收逻辑和审计记录约束。
```

## 它不是什么

VibeOS 不是下面这些东西：

- 不是固定命令解析器
- 不是只会调用少数桌面 capability 的 task broker
- 不是只靠 app 开发者预留 API 的 Siri 式 app-intent orchestration
- 不是无限制、不可控、默认高权限的 always-on agent
- 不是“为了安全所以先装傻”的保守命令机

## 它要成为什么

VibeOS 要成为一个这样的系统：

- 用户用自然语言表达目标
- agent 先理解“用户想完成什么”
- runtime 再根据当前环境选择“怎么做”
- 如果应用没有开放 API，agent 仍然可以像用户一样操作电脑完成任务
- 执行成功不等于任务完成，系统必须继续观察并验证结果
- 安全控制主要发生在执行边界，而不是在语义理解前把任务压扁

## 与 Siri 的关系

VibeOS 和 Siri 有相似点，但不应被理解成同一种产品。

相似点：

- 都是面向用户目标而不是面向低层命令
- 都要理解上下文
- 都要跨多个工具或应用完成事情
- 都不能把“动作发出去了”误判成“用户目标完成了”

关键区别：

- Siri 主要依赖系统和开发者预留的能力面
- VibeOS 不能把 app cooperation 当作前提
- 当应用没有开放接口时，VibeOS 仍然要能继续工作

因此，VibeOS 的核心差异点不是“会不会调用 app API”，而是：

```text
agent 可以被抽象成用户本人，
通过系统 API、accessibility、UI 自动化、视觉观察、键鼠动作等多层手段独立操控电脑。
```

## 产品中心

VibeOS 的产品中心不是“browser route”或“desktop action”。

真正的中心是：

### 1. 用户目标

系统首先要保留稳定的用户目标，而不是过早压缩成某条 route。

例如：

- “打开百度官网”
- “在微信里搜和某个人的聊天记录”
- “把刚才浏览器里看到的地址记下来”

这些首先是用户目标，不是某个 capability 名称。

### 2. 用户代理

agent 在运行时应当被建模为“受控的用户代理”，而不是一个只能触发几个 adapter 的命令调度器。

它必须在必要时具备：

- 找窗口
- 聚焦窗口
- 读界面
- 找输入框
- 输入文字
- 点击按钮
- 滚动
- 观察结果
- 判断是否完成目标

### 3. 环境感知

系统不能只看一条字符串输入，还必须感知环境。

最重要的环境包括：

- 当前前台应用
- 当前窗口与标题
- 当前页面或控件状态
- 可用 capability 和 tool family
- 上一步动作产生的真实结果
- 必要时的屏幕内容与视觉证据

### 4. 分层执行

agent 不应该默认用最脆弱的方式做事。

同一个目标可以有多种执行层次：

1. 原生系统接口
2. 应用或系统 accessibility
3. 结构化 UI 自动化
4. 键盘鼠标动作
5. 截图、OCR、视觉定位

VibeOS 应当优先使用更结构化、更可靠、更可验证的层次；
只有上层不可用时，才退化到更脆弱的 computer-use 手段。

### 5. 结果验收

系统不能把“命令已派发”当成“任务已完成”。

真正要回答的问题是：

- 用户目标是否完成了
- 是否只完成了某条中间动作
- 是否需要继续观察
- 是否只是当前策略失败，但目标仍应保留

## 核心设计原则

### 1. 用户目标高于动作

goal 不能因为某条 route 失败就消失。

### 2. 策略高于 route

route 只是某个执行面的具体实现。
对于同一个目标，系统必须允许多条策略竞争。

### 3. 安全后置到执行边界

安全应该主要通过这些层来治理：

- 权限策略
- review / approval
- capability allowlist
- tool family exposure
- 可回滚和可观察的执行记录
- 明确的 blocked / unsupported / rejected 结果

不应该靠“先把语义理解得很笨”来换安全。

### 4. 可感知、可验证、可审计

系统必须公开：

- 当前 goal
- 当前 strategy
- 当前 environment
- 每一步 evidence
- execution_status
- acceptance_status
- overall_status
- stop reason

### 5. 应用配合是加速项，不是前提项

如果未来某些应用愿意开放更结构化接口，那是更优路径；
但产品设计不能建立在“应用必须配合”这个前提上。

## 能力分层

为了符合上述目标，VibeOS 的能力面应当被理解成四层，而不是单一 capability broker。

### 1. Native action layer

系统已经拥有或正在拥有的能力：

- app
- window
- portal
- clipboard
- notification
- browser launch / open

这些能力结构化程度高，可靠性好，应当优先使用。

### 2. Accessibility layer

未来必须成为一等公民的能力：

- 读取控件树
- 定位按钮、输入框、列表、文本
- 对控件执行 focus / click / set text / invoke

这是“像用户一样操作电脑”但又不完全退化成像素点击的关键层。

### 3. Computer-use layer

当结构化手段不足时，系统仍要有退路：

- 键盘输入
- 快捷键
- 鼠标移动与点击
- 滚动
- 截图
- OCR
- 视觉定位

这层不是第一选择，但必须存在。

### 4. Observation and verification layer

这层不是附属品，而是产品可信性的核心：

- post-action observation
- UI state check
- browser page identity check
- app result list check
- semantic acceptance check

## 典型能力示例

### 示例 1：打开百度官网

系统应优先尝试：

1. 站点解析
2. 直接 URL 打开
3. 页面身份验证

如果站点无法解析，再退到：

1. 搜索策略
2. 搜索结果观察
3. 结果页到官网页的继续动作

关键点：

- “官网”不是默认等于 `search_web`
- 搜索只是候选策略，不是语义结论

### 示例 2：在微信里搜索聊天记录

系统不应把这理解成“微信没开放 API，所以做不了”。

正确思路应当是策略分层：

1. accessibility 搜索策略
2. 快捷键 + 输入框定位策略
3. computer-use + OCR fallback 策略

真正的产品价值就在这里：

- app 不配合时，agent 仍然可以继续工作
- 但整个过程仍然需要被审计、验证和约束

## 当前架构需要纠正的偏差

从这个中心思想出发，当前系统最需要纠正的偏差包括：

### 1. 过早语义压缩

自然语言请求过早被压缩成单一动作或单一路线。

后果：

- 策略错误被误当成执行错误
- 目标在 route 失败时消失
- 运行时没有足够空间做真正的重规划

### 2. 动作中心而不是目标中心

当前系统虽然已经有 goal / run / attempt / acceptance，但浏览器等路径仍然过于动作中心。

### 3. 原生 capability 仍被当成产品本体

实际上它们应该只是执行层的一部分，而不是产品定义本身。

### 4. 安全位置放错

当前部分规则是在语义层做保守压缩，而不是在执行层做治理。

## 项目路线

未来版本的路线不应再被描述成：

- 再加几个 browser heuristic
- 再加几个桌面命令
- 再修一个具体 route 的 fallback

而应当被描述成：

```text
把 VibeOS 从受限 capability executor
升级成以用户目标、环境感知、策略竞争、分层动作和目标级验收为中心的个人助理 runtime。
```

## 简短结论

VibeOS 的核心不是“自然语言转系统调用”。

VibeOS 的核心是：

**让一个受控、可审计、可验证的 agent，能够被抽象成用户本人，独立地观察和操控电脑，为用户完成真实目标。**
