# Goal 03：统一 Model Gateway 并建立 Secret Broker

- 阶段：03 / 09
- 依赖：[Goal 02](02_durable_task_engine.md)全部完成
- 风险：高
- 完成后进入：[Goal 04](04_machine_state_and_context_routing.md)

## 给 Codex 的命令

你要把所有云模型调用收敛到一个严格验证的 Model Gateway，同时让 VibeOS
Core、planner、任务快照和日志不再接触 provider key 等秘密明文。实现基于
Secret Service/GNOME Keyring 的 Secret Broker、短期 grant 和专用 provider
transport；迁移全部现有模型调用并用泄漏/故障测试证明边界。不要先做通用密码
管理 UI，也不要把环境变量换个名字继续传递。

## 项目总体思想

云模型承担高能力推理，但模型只能收到任务允许的最小上下文。Agent 只知道
“存在一个可用于特定 provider 的 secret reference”，不知道 token 本身。确定性
Broker 验证任务、目标、用途、次数和期限，只把值交给专用 transport；模型、
planner、DB、trace 和错误回显不能获得秘密。模型输出在进入核心前必须严格
校验，不能直接授予权限或写入领域状态。

## 当前起点

- 至少 9 个语义模块直接调用 `request_json_object`；provider 配置、重试和错误
  分散在各处；
- 当前使用 `urllib.request` 和环境变量 provider key，没有统一连接池、分阶段
  timeout、取消、调用预算或 secret grant；
- 日志已有基础脱敏，但不能证明 argv/env/snapshot/exception 全链路安全；
- Goal 02 已提供唯一 Task Store、进程生命周期和可审计 Attempt/event。

## 核心目标

建立一条唯一模型路径：

```text
semantic component
  -> typed ModelRequest (不含 secret)
  -> Model Gateway policy/schema/budget
  -> isolated provider transport
  <- scoped grant from Secret Broker
  -> HTTPX provider request
  -> strictly validated ModelResponse
```

Secret Service 只由 Broker 连接。planner/semantic worker 不挂载 session bus，或
使用经过验证的 D-Bus proxy 明确拒绝 Secret Service；同 UID 进程隔离不能只靠
代码约定。provider transport 每次只获得当前 provider/attempt 的短期凭据。

## 必须实施

1. **Model Gateway contract**
   - 定义 provider-neutral request/response/usage/error、调用 purpose、Task/Attempt、
     schema version、timeout、token/cost budget 和数据类别；
   - 每种语义调用注册 Pydantic 2 strict response schema；未知字段、无效 enum、
     prompt 注入式结构和缺字段 fail-closed，修复重试次数有限；
   - 使用 HTTPX async client/连接池，明确 connect/read/write/pool timeout、取消、
     429/5xx/网络错误分类和有限退避；
   - 首期只实现当前 OpenAI-compatible adapter，不引入 LiteLLM 或本地推理服务。

2. **Secret contract 和 policy**
   - 定义 opaque `SecretReference` 与 `SecretGrant`：task/action、target identity、
     purpose、provider/domain、account hint、uses、expiry、injection method；
   - reference 不编码值；序列化、repr、异常和 API 均不能取得明文；
   - grant 由确定性 policy 发放，绑定错误、过期、超次数、重放或目标不匹配全部
     fail-closed，模型不能请求扩大 grant。

3. **存储与进程隔离**
   - 生产使用 freedesktop Secret Service；CI 可用协议级 fake，但生产不能静默
     回退明文文件；
   - Broker 与 planner/semantic worker 分进程；后者不具有 Secret Service D-Bus
     路径。用 sandbox/D-Bus proxy 测试证明直接枚举或读取被拒绝；
   - transport 只通过只读 FD、一次性 AF_UNIX 响应或 systemd credential 获得一项
     凭据，不能枚举 collection；使用完成/取消/超时后关闭通道和撤销 grant；
   - keyring 未解锁时任务进入可解释等待，不能重复弹窗或写入临时明文。

4. **迁移全部模型调用**
   - 所有 planner、understanding、acceptance、replan 等组件只依赖 Gateway port；
   - provider transport 是唯一可联网调用模型且可接收 provider credential 的模块；
   - 删除旧 `provider_client` 生产入口、各模块环境读取和重复 retry/JSON repair；
   - production composition 不保留旧 provider fallback。

5. **provider key 管理**
   - CLI 提供交互式 import/set/status/delete，从 TTY/安全输入读取且不回显；
   - `.env`/环境变量只允许一次显式迁移并提示弃用，不能作为长期 fallback；
   - 迁移操作不把值写入 shell history、日志或任务数据库。

6. **审计、数据和泄漏测试**
   - Gateway 先执行 D0-D4 基础阻断：D3 永不进模型，D4 不发云端；更细的 Machine
     State 上下文裁剪在 Goal 04 完成；
   - 只记录 reference、grant metadata、schema、数据类别、usage/cost、provider
     状态和结果摘要；
   - 用高熵 canary 扫描 DB、event、outbox、日志、trace、异常、argv、
     `/proc/<pid>/environ`、任务导出和 HTTP body；
   - 恶意 schema 输出、adapter 回显、transport 崩溃、超时和取消全部故障注入。

## 明确非目标

- 不构建跨设备密码同步、浏览器自动填充或完整密码管理器；
- 不索引 Machine State，不实现本地模型或复杂多 provider 路由；
- 不从浏览器/应用提取现有 cookie、密码或 MFA；
- 不用加密 SQLite 字段或普通环境变量代替 Secret Service/进程隔离；
- 不在本阶段开放通用动作、root 或 E2/E3 行为。

## 验收条件

- [ ] 全仓 production 只有 Model Gateway/transport 可发模型请求，依赖测试禁止旁路；
- [ ] timeout、429、5xx、断网、坏 JSON、schema 注入、取消和预算耗尽测试通过；
- [ ] planner/semantic worker 无 Secret Service 路径，直接访问/枚举测试被拒绝；
- [ ] provider transport 只能获得当前 grant 的一项凭据，过期/撤销/错配/重放失败；
- [ ] provider 请求真实成功，key 不出现在 argv、普通 env、日志、DB、trace、
  snapshot、错误、导出或 HTTP payload content；
- [ ] D3 canary 从不进入模型请求，D4 云端请求被拒绝；
- [ ] daemon/Broker/transport 在注入前、中、后崩溃，grant 正确回收且任务可恢复；
- [ ] locked keyring 进入明确等待，解锁后继续，不丢任务或泄漏值；
- [ ] 旧环境变量路径已迁移并从 production 删除；
- [ ] 真实 GNOME VM 使用 Secret Service 完成云模型调用，CI fake 只补故障测试；
- [ ] 共同质量门禁全部通过。

## 必交付物

- HTTPX Model Gateway、OpenAI-compatible transport、严格 schemas 和预算/故障策略；
- SecretReference/Grant、隔离 Broker、进程/D-Bus 边界和注入 adapters；
- provider key 安全迁移 CLI、泄漏扫描及崩溃/撤销/keyring 锁定测试；
- 全部旧模型调用删除清单和真实 GNOME 验收记录。

只有模型调用真正收敛、真实 provider 可用、全链路找不到 canary 且旧 fallback
关闭后才结束本 Goal。
