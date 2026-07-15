# VibeOS Agent-native 方向、代码与计划可行性审计

- 状态：已完成
- 审计日期：2026-07-15
- 用途：所有阶段 Goal 的共同事实基线；它不是待实现 Goal

## 1. 总结论

产品方向与用户已经确认的意图一致，不需要改变使命：VibeOS 要成为运行在
个人 Linux 设备上的 Agent-native 计算层，能比用户更深入地理解和操作机器，
优先使用 API/CLI/系统服务，必要时使用 UI，在权限和现实世界边界内自主完成
持续任务。

原八阶段计划不能直接执行。主要问题不是目标错误，而是实施切分继承了当前
代码形状：它把 Machine Model 提前做大、要求扩写同步 `GoalLoop`，过早建设
完整模型路由，又把 Effect、Reviewer、提权、秘密和通用回滚塞进一个巨大
阶段。这会形成新一轮巨型模块，也无法用真实垂直场景逐段验收。

修订后的九阶段计划采用“替换旧底座 → 建立持久任务 → 单独封闭秘密 → 建立
最小机器状态和模型网关 → 开放普通动作 → 证明一个特权事务 → 完成桌面 MVP
→ 主动协作 → 扩展与发行版决策”的顺序。每阶段都有删除门禁或真实证据，不
把接口、fake adapter 或文档视为完成。

## 2. 产品一致性复核

| 已确认意图 | 当前产品文档 | 结论 |
| --- | --- | --- |
| Agent 像真实用户使用电脑，但能触及更底层能力 | Machine Model + Action Fabric，UI 不是上限 | 一致 |
| API 优先，失败后才使用鼠标键盘 | 执行路径按语义 API、CLI、服务、Accessibility、UI 降级 | 一致 |
| 实质歧义必须先询问 | GoalContract 在执行前澄清目标、对象、范围和现实后果 | 一致 |
| Agent 根据证据自行判断完成 | EvidenceBundle 与 completion conditions 是终态依据 | 一致 |
| 支持持续数小时和重启恢复 | Durable Task Kernel 是权威状态层 | 一致 |
| 可回滚提权由系统内部 Agent 审核 | E2 由独立 Reviewer 审核，确定性 policy/helper 强制 | 一致 |
| 外部、不可逆、支付和重大安全影响由用户批准 | E3 逐动作批准，长期授权不能降级 E3 | 一致 |
| Agent 不看到密码明文 | Secret Broker 只暴露引用并向目标程序注入 | 一致 |
| 云模型为主，本地模型按证据参与 | Model Gateway 先支持云端，本地路线经基准触发 | 一致 |
| Agent 主动发现问题，但由用户决定是否解决 | Advisor 默认只建议，不自动扩大授权 | 一致 |
| 本地 Agent 可按需扩展 | 扩展接入统一任务、权限、数据和动作协议 | 一致 |
| 对独立发行版保留疑问 | 先交付现有 Linux Runtime，最后以数据决策 | 一致 |

## 3. 当前代码事实

当前唯一生产路径是：

```text
CLI / HTTP / D-Bus
  -> CommandService
  -> TaskApplicationService
  -> GoalLoop
  -> StepExecutionService
  -> CapabilityRecipeRegistry
  -> ToolRegistry
  -> domain tool
  -> adapter
```

2026-07-15 在项目配置的 Fedora 44 WSL 环境实际执行：

```text
python -m ruff check src tests -> passed
python -m mypy --strict        -> 0 issues in 16 source files
python -m pytest -q            -> 263 passed in 11.64s
vibe capabilities --json      -> 19 registered capabilities
vibe doctor --json            -> 4 ok / 8 warn / 0 fail
offline dry-run               -> passed
```

WSL 没有证明 GNOME Wayland、portal、AT-SPI、system bus 或真实提权路径；这些
能力必须在受支持的 GNOME VM 上重新验收。

## 4. 代码结构风险

| 位置 | 当前规模/事实 | 计划影响 |
| --- | --- | --- |
| `planner.py` | 1515 行 | 不再继续聚合规划、模型、schema 和 fallback |
| `goal_loop.py` | 1173 行，同步单命令循环 | 由新 transition engine 垂直替换，不扩写为工作流平台 |
| `reviews.py` | 845 行，内联 SQLite schema/migration | 迁入统一数据库和 repository，不再增加第二种迁移机制 |
| `understanding.py` | 754 行 | 模型边界与领域理解拆开 |
| `agent_runtime.py` | 749 行，非当前生产主干 | 不复活为第二运行时；迁移后删除或归档 |
| `runtime.py` | 587 行 | daemon 收敛为单 asyncio supervisor |
| `task_trace.py` | 573 行，诊断记录 | 不能充当权威 Task Store |
| 模型调用 | 至少 9 个模块直接调用 `request_json_object` | 统一进入 Model Gateway |
| 依赖 | Linux 生产依赖目前仅 `dbus-next` | 新依赖逐阶段引入并记录理由 |

这些行数不是单独的失败标准，但它们显示职责已经过度集中。修订计划以模块
依赖、状态权威和删除旧入口作为验收，而不是仅要求“再拆几个文件”。

## 5. 可复用资产与替换边界

可复用的是行为和测试，不是所有类的形状：

- 保留现有 19 个 capability 的用户语义、确定性 adapter、观察器、verifier、
  acceptance、recovery 和回归测试；
- 保留 `ReviewStore` 的 CAS/claim、WAL、FULL synchronous、busy timeout 等并发
  经验，将其迁入统一 repository；
- 保留 GoalLoop 的 observe/review/execute/verify/retry/replan 语义作为等价测试；
- 保留 Tool/Capability Registry 的注册思想，但动作必须增加 effect、sandbox、
  evidence 和 secret 声明；
- 保留 D-Bus、GNOME extension、portal 和 systemd 集成中经真实验证的窄适配器。

必须替换或收敛：

- 同步 `GoalLoop` 及其只在暂停点持久化的状态模型；
- Store 各自建表和内联迁移；
- ThreadingHTTPServer 与独立 D-Bus loop 的双生命周期；
- 分散的 provider 环境变量读取和 HTTP 调用；
- 只按 capability 名称静态判断的 L0-L3 风险；
- 任何准备复活 legacy runtime 或长期维护双生产路径的方案。

## 6. 技术路线与可行性

共同技术决策见
[决策 0002](../../product/decisions/0002-implementation-foundation.md)。核心判断：

| 主题 | 选择 | 可行性判断 | 主要风险与控制 |
| --- | --- | --- | --- |
| 核心形态 | Python 本地模块化单体 | 高 | 用依赖测试阻止层级回流 |
| 持久化 | SQLite + SQLAlchemy Core + Alembic | 高 | 单机足够；压测锁竞争，禁止网络盘 |
| 长任务 | 自有 asyncio engine + systemd user service | 中高 | at-least-once、lease、幂等和崩溃矩阵必须先证明 |
| Machine Model | 最小关系型 State Index | 高 | 按需采集、TTL；禁止提前做全量图谱 |
| 模型 | HTTPX + 严格 schema 的集中网关 | 高 | provider 故障、超时、成本和数据裁剪测试 |
| E0/E1 命令 | 结构化 argv + systemd transient + Bubblewrap | 中高 | sandbox profile 需真实对抗测试 |
| 秘密 | Secret Service + systemd credential/FD/socket | 中 | 需要进程隔离和全链路泄漏测试 |
| E2 提权 | system bus + polkit + 极小 allowlist helper | 中 | 首先只做一个 canary；helper 不接受任意命令 |
| 回滚 | 按操作 TransactionDriver | 中 | 不承诺通用回滚；每种动作独立证明 |
| 桌面 | AT-SPI，portal 作为最后 fallback | 中 | portal 授权/恢复先 spike，不可行就缩减承诺 |
| 独立发行版 | 最后证据门禁 | 当前不实施 | 避免分散 Runtime MVP 资源 |

## 7. 为什么按九阶段执行

1. **基础替换**先解决依赖、schema 和 daemon 生命周期，否则长任务会把旧债
   固化成公共 API。
2. **持久任务引擎**先迁移现有能力，证明可恢复的唯一权威状态并删除旧内核。
3. **模型网关与 Secret Broker**同时收敛模型调用和秘密，避免旧分散调用破坏
   进程隔离。
4. **机器状态与上下文路由**只实现规划所需事实和裁剪，避免平台先行。
5. **普通 Action Fabric**先证明 E0/E1 sandbox、receipt 和 API 优先选择。
6. **特权控制与回滚**只增加一个 E2 canary，验证 Reviewer/polkit/事务全链路。
7. **桌面与 Linux MVP**在内核和控制边界稳定后完成真实 GNOME 用户闭环。
8. **主动建议**复用已经可信的状态和任务系统，不让建议成为旁路执行器。
9. **扩展、交付和发行版门禁**最后稳定公共面，并用证据决定是否做发行版。

## 8. 全计划约束

- 每个阶段先核对当前代码，文档中的行数和测试数只作为本次审计快照；
- 一项状态只能有一个权威来源，一类副作用只能有一条生产执行路径；
- 兼容层必须写明 owner、调用者、删除门禁和最迟删除阶段；
- 先迁移一个真实垂直切片，再扩大覆盖；不以全面抽象开局；
- 模型不能控制权限、sandbox、secret 或数据库事务边界；
- E3 永远逐动作向用户批准；E2 Reviewer 不能绕过确定性 policy；
- 完成声明必须有真实状态证据，不能只依靠模型自述、mock 或 dry-run；
- Linux 集成必须在支持矩阵中的真实 GNOME VM 验收；
- 任一技术假设被推翻时新增 ADR，不在代码中暗自采用第二路线。

## 9. 计划完备性检查

修订后的总计划只有同时满足以下条件才可视为可执行：

- 九个 Goal 都可单独作为 Codex goal，包含总体思想、当前起点、目标、非目标、
  技术约束、迁移方式、验收条件和交付物；
- 每个目标都有唯一依赖，不存在“先开放权限再补安全”的倒序；
- 每个高风险边界先用一个 canary 证明，再扩大能力面；
- 每个旧生产模块都有迁移或删除去向；
- 每个阶段的验收能由自动测试、数据库状态、系统状态或真实 VM 证据判定；
- 不依赖尚未证明的独立发行版、本地大模型、通用回滚或无人值守 UI 假设。

满足以上条件后，具体执行以[实施计划总览](README.md)和九份阶段 Goal 为准。
