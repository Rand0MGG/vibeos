# 规划、执行与验收

## 1. 输入如何变成任务计划

支持任务的核心流程不是“先解析一个动作名再执行”，而是：

1. 分析 utterance 是否属于支持任务面
2. 合成 typed goal
3. 在显式 domain 集合中做 route competition
4. 产出结构化 `TaskPlan`
5. 通过验证、风险审查后执行

这里最重要的约束是：

- planner 只能使用注册过的 domain、route、capability
- 自然语言不能直接映射成任意系统调用
- 风险控制在计划之后、执行之前发生

## 2. `run` 与 `attempt`

当前任务执行不是单次线性调用，而是 bounded run loop。

关键对象：

- `run`
  - 代表一次完整用户请求
  - 包含 `run_id`
  - 包含最终状态与尝试链
- `attempt`
  - 代表一次具体计划执行
  - 绑定 `attempt_id`
  - 记录 route、execution、failure、replan 决策

这样设计的意义是：

- 可以区分“第一次选错路线”和“第二次重规划后的结果”
- 可以把 transport failure、adapter failure、acceptance failure 都收进同一个账本
- 可以避免无界重试

## 3. 失败分类

当前系统把失败分成几类：

- `transport_timeout`
- `tool_timeout`
- `provider_timeout`
- `provider_transient`
- `environment_unreachable`
- `semantic_mismatch`
- `acceptance_unverified`
- `acceptance_failed`
- `permission_blocked`
- `unsupported_request`

这些分类不是为了漂亮，而是为了决定下一步行为：

- retry same attempt
- replan with constraints
- ask user
- stop

## 4. 重试与重规划

这里必须严格区分：

- `retry`
  - 语义不变
  - 主要处理瞬时错误
  - 例如 transport timeout、tool timeout
- `replan`
  - 语义目标不变，但当前 route 不成立
  - 主要处理 `semantic_mismatch`
  - 例如把“本地应用”误选成 `app.open`，失败后改走浏览器语义

系统目标不是“失败后乱试”，而是“基于结构化证据做有界重规划”。

## 5. 验收层

执行完成后，还会做 postcondition observation 和 acceptance。

验收链的目标是回答：

- 是否真的完成了用户目标
- 是否只有执行成功、但没有足够证据证明任务完成
- 是否观察到了明确失败页或错误态

因此需要分开看：

- `execution_status`
- `acceptance_status`
- `overall_status`

## 6. 浏览器路径的特别注意

浏览器任务容易出现“请求发出”和“页面真正成功加载”混淆的问题。

因此在浏览器任务上要重点观察：

- `requested_url`
- `active_url`
- `query`
- `page_title`
- `error_state`
- `adapter`

如果只有“发起了 URL 打开请求”，没有真实页面证据，就不能轻易把任务当作真正完成。

## 7. 调试与审计

当前结果链路里有三类重要信息：

- `audit`
  - 面向运行记录和外部追踪
- `run trace`
  - 面向一次任务的结构化执行链
- `debug_trace`
  - 面向调试 provider / planning / execution 细节

阅读建议：

- 看用户结果先看 `overall_status`
- 看为什么失败先看 `attempts[*].failure`
- 看为什么走到这条 route 再看 `goal_synthesis`、`domain_routing`、`route_competition`
