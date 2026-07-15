# Goal 04：建立最小 Machine State Index 与上下文路由

- 阶段：04 / 09
- 依赖：[Goal 03](03_model_gateway_and_secret_broker.md)全部完成
- 风险：中高
- 完成后进入：[Goal 05](05_unprivileged_action_fabric.md)

## 给 Codex 的命令

你要让 VibeOS 在规划前获得可追溯的新鲜机器事实，并通过统一 Context Router
把最小必要上下文交给 Goal 03 的 Model Gateway。只实现能改进当前真实任务的
Machine State Index，不做全盘索引、图数据库、向量库或长期用户画像；本地
模型只有在固定基准达到门槛后才可进入 production。

## 项目总体思想

“Agent 比用户更了解电脑”依赖可验证的机器事实，不是把更多原始文件塞给模型。
事实必须带来源、时间、TTL、置信度和敏感级别；过期时重新观察。确定性代码
查询、分类、裁剪和阻断数据，高能力云模型只收到任务允许的最小 manifest，
不能自行扩大上下文范围。

## 当前起点

- observation 主要服务单次规划，没有跨任务的最小权威事实索引；
- planner 不能稳定查询软件、服务、资源和最近变更之间的关系；
- Goal 03 已提供唯一 Model Gateway、D0-D4 硬阻断、预算和 Secret Broker；
- 尚无 collector、TTL、provenance、变化查询或路由收益基准。

## 核心目标

建立 SQLite 中的类型化 Machine State Index 和确定性 Context Router。先选择三类
能直接服务现有黄金任务的机器事实，例如 OS/软件版本、systemd user service
状态、磁盘/进程资源；用固定任务集证明事实减少错误规划或重复探测。最终三类
以代码和用户价值审计决定并记录。

## 必须实施

1. **Machine State contract**
   - `MachineFact` 至少包含 subject/type/value、source、captured_at、expires_at、
     confidence、sensitivity、evidence_ref、schema_version；
   - 区分 observation、derived fact 和历史 event；推断不能覆盖原始事实；
   - repository 支持按 type/subject/task 查询、TTL 过滤、失效和最近变化；
   - 值采用小型结构化 schema，不保存任意文件内容或模型长文本。

2. **按需 collectors**
   - 每个 collector 声明成本、权限、数据等级、timeout、freshness 和保留期；
   - planning 缺关键事实或事实过期时触发 collector，再继续任务；
   - collector 失败可保留最后事实但必须标 stale，不伪装成当前状态；
   - 采集内容/频率/保留期可由用户查看，不默认遍历 home 或整盘。

3. **Context Router**
   - 从 Goal/Attempt 需要生成类型化 fact query，只取最小字段和时间范围；
   - 按 purpose、D0-D4、provider policy、用户策略和预算裁剪/脱敏；D3 永不进
     模型，D4 只走确定性程序或经批准的本地模型；
   - D2 默认只有任务必要且用户策略允许时才发云端，不得静默扩大范围；
   - 生成 context manifest：事实引用、freshness、类别、裁剪和发送目标，不保存
     被禁止的原文。

4. **路由与降级**
   - 根据任务能力、数据政策、provider 健康、质量、成本和延迟预算选择云端模型
     或确定性 fallback；模型只在 policy 给出的候选中选择；
   - provider 不可用时进入等待、显式降级或询问用户，不能用低能力路径静默
     改变目标、完成条件或数据范围；
   - 路由决定、输入事实版本、usage 和结果 schema 绑定 Task/Attempt。

5. **本地模型证据门**
   - 建立固定脱敏任务集和质量/延迟/峰值内存/能耗基准；
   - 先写门槛再测试候选；只有同时达标且确有隐私/成本价值才增加本地 runtime；
   - 未达标时交付明确“不启用”结论，确定性程序仍可承担分类/裁剪等任务。

## 明确非目标

- 不索引用户所有文件内容、浏览历史、聊天、邮件或剪贴板；
- 不引入 Neo4j、向量数据库、RAG 平台或完整知识图谱；
- 不在 Machine State 保存 secret、原始 D2 内容或完整模型 prompt；
- 不实现主动建议、通用动作或桌面观察；
- 不用模型推断替代系统 API 能确定获得的事实。

## 验收条件

- [ ] 三类事实有 strict schema、collector、TTL、来源、失效和变化查询；
- [ ] 过期关键事实会在规划前重采，失败明确标 stale 并影响决策；
- [ ] 固定黄金任务集达到预先记录的规划正确率或探测次数改进阈值；
- [ ] Context Router 对 D0-D4、purpose、provider 和用户策略有完整表驱动测试；
- [ ] D3 canary 与 D4 数据从不进入云请求，D2 只在显式策略允许时最小化发送；
- [ ] context manifest 可解释每项事实为何被使用，但不泄漏原始私密内容；
- [ ] provider 故障、预算耗尽和 stale fact 的等待/降级不改变用户目标；
- [ ] 调用、路由和 fact versions 可绑定 Task/Attempt 重放审计；
- [ ] 本地模型只有基准达标才进入 production，否则没有新增 runtime 依赖；
- [ ] 共同质量门禁全部通过。

## 必交付物

- MachineFact schema/repository、三类 collectors 和变化/TTL 查询；
- Context Router、D0-D4/purpose policy、context manifest 和路由审计；
- 黄金任务收益报告、故障/降级矩阵和本地模型基准/否决记录；
- 机器数据范围、保留期和用户控制文档。

只有 State Index 产生可测用户价值、上下文边界可证明且没有平台过度建设时结束。
