# Goal 05：实现受治理的 E0/E1 Action Fabric

- 阶段：05 / 09
- 依赖：[Goal 04](04_machine_state_and_context_routing.md)全部完成
- 风险：高
- 完成后进入：[Goal 06](06_privileged_control_and_rollback.md)

## 给 Codex 的命令

你要把现有 capability/tool 注册路径升级为唯一的普通动作执行面，让 Agent 能
优先使用 API、CLI、D-Bus 和系统服务，并在明确 sandbox 中运行 E0/E1 结构化
命令。每个动作必须先声明效果、权限、数据、资源、receipt、验证和幂等策略。
不要开放任意 shell，不要实现 root，也不要把模型生成字符串直接交给命令行。

## 项目总体思想

Agent 相比真人的优势是能直接调用稳定接口和底层工具；UI 只是最后 fallback。
但“能运行命令”不等于给 Agent 一个无限 shell。计划产生类型化 ActionProposal，
确定性 policy 选择执行 profile，执行器返回 receipt，再由独立 observation 验证
目标。E0/E1 可自动执行，但仍受任务范围、资源、数据和网络边界约束。

## 当前起点

- 现有 19 个静态桌面 capability 已由 Tool/Capability Registry 执行；
- 风险主要按 capability 名称映射 L0-L3，缺少对参数、目标资源和实际效果评估；
- 尚无通用但受治理的结构化命令 runner、systemd transient unit 或 Bubblewrap
  profile；
- 当前 adapter、observer、verifier 和 recovery 测试可迁移复用；
- Goal 02-04 已提供 durable task、model/secret、machine facts、context 和证据链。

## 核心目标

建立唯一 `ActionProvider` 协议和 effect-aware registry，覆盖：

```text
system_api -> app_api -> D-Bus/portal -> structured CLI
```

按可靠性、语义强度、侵入性和成本选择路径；只有前一条不可用或证据失败才
降级。首期只开放 E0 观察和 E1 可逆的用户态操作。

## 必须实施

1. **类型化动作**
   - `ActionSpec` 声明输入 schema、effect candidates、required permissions、
     data classes、network domains、resource limits、idempotency、receipt、verify、
     compensation 和 sandbox profile；
   - `ActionProposal` 绑定 Task/Attempt、解析后的精确参数、目标资源和前置状态；
   - 动态注册和模型参数先 strict validate；未知 provider/effect/profile 被拒绝。

2. **路径选择**
   - 用确定性 metadata 排序 API/CLI/service 路径，模型只能在允许候选中选择；
   - 保存选择理由、不可用证据和 fallback 次序；
   - 失败不能静默切换到影响更大的路径，effect 提升需重新评估。

3. **结构化命令 runner**
   - 只接受 argv list、固定 executable identity、cwd policy、env allowlist、stdin
     mode、timeout 和输出上限，使用 `shell=False`；
   - 禁止 shell expansion、重定向、管道、命令替换、交互 TTY 和任意脚本字符串；
   - 记录 executable digest/path、argv 的脱敏形式、exit/signal、资源和截断信息。

4. **隔离与资源控制**
   - 默认通过 systemd transient user unit 管理进程、cgroup、timeout、kill 和日志；
   - 对需要更强文件/namespace 隔离的 profile 使用 Bubblewrap；profile 是少量
     版本化代码/配置，由测试维护，不由模型或扩展拼参数；
   - 默认只读根、最小 bind、私有临时目录、网络按 action allowlist 决定；
   - 目标平台缺少所需隔离时 fail-closed 或选择更窄 API，不裸跑同一动作。

5. **receipt、验证和恢复**
   - 每个 provider 返回稳定 receipt，区分未启动、执行中、已退出、状态未知；
   - verifier 使用 API/机器事实/资源 diff 独立检查，不只相信 exit code；
   - E1 动作具备幂等或明确 compensation/reconciliation；不可安全重试则暂停；
   - 输出和 observation 按 D0-D4 处理，秘密继续由 Broker 直接注入目标。

6. **迁移当前能力**
   - 将 19 个 capability 的 metadata、执行和验证接入统一 registry；
   - 删除旧 ToolRegistry/CapabilityRecipeRegistry 中重复的路由、风险和执行逻辑；
   - 保留兼容名称时只做 input adapter，不能建立第二执行路径。

## 明确非目标

- 不开放交互 shell、任意 bash/python 脚本、root、sudo 或系统包修改；
- 不实现鼠标键盘、视觉点击或浏览器 cookie 操作；
- 不让 Bubblewrap 替代 effect policy、Secret Broker 或 verifier；
- 不把所有 Linux 命令包装成 capability；只增加黄金场景需要且可治理的动作；
- 不声称 E1 compensation 等同特权事务回滚。

## 验收条件

- [ ] 所有 production 动作只经统一 registry/provider 路径，旧重复执行逻辑删除；
- [ ] 19 个现有 capability 均有完整 ActionSpec，行为和用户可见错误保持兼容；
- [ ] 至少实现三个真实路径样例：system/app API、D-Bus/service、structured CLI；
- [ ] 路径选择在 API 可用时不调用 CLI，API 故障后按声明顺序降级并保留证据；
- [ ] shell metacharacter、命令替换、路径逃逸、恶意 env/stdin 和输出洪泛被阻止；
- [ ] systemd unit 具备资源/timeout/kill 证据；Bubblewrap profile 通过挂载、网络、
  `/proc`、home 和 socket 越界测试；
- [ ] worker 在命令成功后崩溃可通过 receipt/reconciliation 避免重复副作用；
- [ ] verifier 能识别“exit 0 但目标未达成”和“超时但副作用已发生”；
- [ ] secret canary 不进入 argv/env/log/output，D2-D4 输出政策通过；
- [ ] 在目标 Linux VM 完成 systemd transient 与 Bubblewrap 集成测试；
- [ ] 共同质量门禁全部通过。

## 必交付物

- ActionSpec/Proposal/Provider/Receipt 协议、统一 registry 和 path selector；
- systemd transient structured runner、版本化 Bubblewrap profiles；
- 19 个 capability 迁移与旧 registry 执行逻辑删除；
- adversarial sandbox、崩溃恢复、fallback 和独立验证报告。

只有 E0/E1 真实动作能够受控执行、验证、恢复，且不存在旁路 runner 时才结束。
