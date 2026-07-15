# 决策 0002：采用可替换的本地模块化单体技术底座

- 状态：已接受
- 日期：2026-07-15

## 背景

VibeOS 当前已经形成一条可运行的生产路径，并有 263 个测试和 19 个注册能力。
但 `planner.py`、`goal_loop.py`、`reviews.py` 等核心文件已经承担过多职责；任务只
在审批或澄清时保存快照，daemon 同时维护线程式 HTTP 和独立 asyncio D-Bus
循环，模型调用则分散在多个语义组件中。

下一阶段要支持小时级任务、受治理的命令执行、秘密注入、可回滚提权和真实
桌面操作。继续在现有大类上增加分支会放大恢复、并发和权限错误；直接引入
分布式工作流、图数据库或微服务，又会给单机个人 Agent 带来不必要的运维面。

本决策定义未来九个实施阶段共同使用的技术底座和迁移规则。

## 决策

### 1. 采用本地模块化单体

- VibeOS Core 继续使用 Python 3.11+，部署为本机服务，不拆成微服务。
- 代码按 `domain`、`application`、`ports`、`adapters` 分层；领域层不导入
  D-Bus、HTTP、SQLite、systemd、模型 SDK 或桌面实现。
- 模块只能通过类型化端口协作。动态输入、持久化记录、模型输出和扩展清单
  使用 Pydantic 2 严格校验；内部稳定领域对象优先使用普通 dataclass/enum。
- 不建立第二套长期生产运行时。新内核按垂直切片接管能力；达到等价门禁后
  删除旧实现和兼容开关。

### 2. 统一持久化和迁移

- 权威状态使用一个位于本地文件系统的 SQLite 数据库，启用 WAL、外键和
  合理的 busy timeout；不支持把数据库放在网络文件系统。
- 使用 SQLAlchemy 2 Core 明确表达 SQL 和事务；使用 Alembic 管理前向迁移、
  数据回填和兼容窗口。停止在各 Store 内追加内联建表/迁移代码。
- 数据模型采用“规范化当前状态 + 追加式领域事件 + transactional outbox”。
  事件用于审计、恢复和通知，不把整个系统改造成完整 event sourcing。
- 调度语义明确为 at-least-once。通过 lease、幂等键、action receipt 和执行后
  reconciliation 避免重复副作用；不得宣称无法证明的 exactly-once。

### 3. 新建持久任务引擎，不扩写旧 GoalLoop

- 新任务引擎以纯 transition/reducer 作为状态机核心，I/O 由 application
  service 在事务边界外执行，再把结果作为事件提交。
- daemon 使用单一 asyncio supervisor 管理 D-Bus、调度器、worker、timer 和
  生命周期。D-Bus 是 Linux 本地控制面的首选接口。
- 现有 HTTP API 只作为有真实调用者时的临时兼容面；不得继续投资线程式
  HTTP daemon，也不因“以后可能需要”引入 FastAPI。
- 不引入 Temporal、Celery、Redis 或消息队列；只有单机 SQLite 方案经压测
  无法满足已记录需求时才重新决策。

### 4. Machine Model 首版实现为 Machine State Index

- 产品概念仍称 Machine Model；首版只实现带类型、来源、采集时间、TTL、
  置信度和敏感级别的关系型 Machine State Index。
- 只按任务需要采集事实，不默认遍历整盘、保存文件内容或建立用户行为画像。
- 不引入图数据库、向量数据库或通用知识图谱。只有关系查询或语义检索的真实
  基准证明 SQLite 不足时才扩展。

### 5. 同时集中模型网关与 provider 秘密边界

- 所有模型调用收敛到一个 Model Gateway，使用 HTTPX 的连接池、分阶段超时、
  取消和可观测性；响应在进入领域层前通过严格 schema 校验。该迁移与 provider
  Secret Broker 同阶段完成，避免旧分散调用继续读取明文 key。
- 首期支持 OpenAI-compatible 云端提供商，并保留 provider adapter；不在证据
  不足时引入 LiteLLM 或强制部署本地大模型。
- 确定性程序负责 D0-D4 数据分类、裁剪和禁止规则。模型只能在被允许的候选
  集合中做语义决策，不能给自己授权。
- 本地模型只有在固定数据集上同时满足质量、延迟、资源和隐私目标时才进入
  默认路由，否则保持可选实验。

### 6. 分层执行普通动作

- 命令能力接收结构化 `argv`、cwd、环境白名单、资源上限和预期证据，默认
  `shell=False`；不向模型暴露交互式 shell。
- E0/E1 子进程优先由 systemd transient unit 提供生命周期、cgroup、资源和
  sandbox 属性；需要文件系统/namespace 隔离时使用 Bubblewrap。
- Bubblewrap 只是低层构件，VibeOS 必须拥有少量、版本化、经过对抗测试的
  sandbox profile，不能把参数拼装责任交给模型或扩展。

### 7. 独立实施特权边界和按操作回滚

- 优先调用现有受治理的系统 D-Bus API。确需自有特权操作时，使用 system-bus
  机制配合 polkit action；安装时由用户授权建立该边界。
- 自有特权 helper 必须很小，优先使用 Rust，实现类型化 allowlist verb；它
  不加载模型、不解析 shell、不接受任意路径或命令，也不拥有任务规划逻辑。
- 独立 Reviewer Agent 只审核 E2 proposal，确定性 policy 和 helper 仍是最终
  强制边界。E3 始终逐次请求用户批准。
- 回滚由每类操作的 `TransactionDriver` 实现：捕获前态、准备、执行、健康
  检查、提交或恢复。不存在“任意 shell 通用回滚”；先证明一个真实 E2 canary，
  再增加操作类型。

### 8. 秘密由 Broker 注入，不进入 Agent 上下文

- Secret Service/GNOME Keyring 保存用户秘密，Core 只持有引用和短期 grant。
- 优先通过 systemd credentials、受控文件描述符或 AF_UNIX broker 注入目标
  进程；秘密不得进入 argv、普通环境变量、日志、trace、任务快照或模型请求。
- 模型语义/planner worker 与 Secret Broker 分进程；前者不挂载 session bus，
  或使用明确拒绝 Secret Service 的 D-Bus proxy。专用 provider transport 只能
  获得当前 grant 的单项凭据，避免把同 UID 的“代码约定不读”当作安全边界。

### 9. 桌面路径以语义接口优先

- GNOME/Wayland 首选 AT-SPI 可访问性树；XDG Desktop Portal RemoteDesktop
  只作为需要用户会话授权的最后输入 fallback。
- GNOME extension 只弥补稳定接口无法覆盖的窄缺口，不演变成第二任务运行时。
- 对 portal 的持久授权、重启恢复和无人值守能力先做 spike；不满足时必须明确
  降级产品承诺，而不是用不安全的 `/dev/uinput` 绕过桌面安全模型。

## 成熟技术依据

- [SQLite](https://www.sqlite.org/docs.html)、
  [SQLAlchemy 2.0](https://docs.sqlalchemy.org/en/20/) 和
  [Alembic](https://alembic.sqlalchemy.org/en/latest/) 提供成熟的本地事务、显式
  SQL 与版本化迁移能力；Alembic 也记录了
  [SQLite batch migration](https://alembic.sqlalchemy.org/en/latest/batch.html)。
- [Pydantic strict mode](https://docs.pydantic.dev/latest/concepts/strict_mode/)
  提供动态边界的严格类型校验；
  [HTTPX timeout](https://www.python-httpx.org/advanced/timeouts/)区分连接、读取、
  写入和连接池等待，适合集中 provider 故障治理。
- [systemd transient unit 属性](https://systemd.io/TRANSIENT-SETTINGS/)覆盖资源
  控制、执行环境和 sandbox 设置；
  [systemd credentials](https://systemd.io/CREDENTIALS/)提供比普通环境变量更
  合适的服务秘密传递边界。
- [polkit](https://polkit.pages.freedesktop.org/polkit/polkit.8.html)用于非特权
  调用者请求特权机制授权；应用声明精确 action，而不是安装任意通用规则。
- [Bubblewrap](https://github.com/containers/bubblewrap/blob/main/README.md)是低层
  非特权 sandbox 构件，并明确要求调用方自行提供完整安全策略。
- [Secret Service API](https://specifications.freedesktop.org/secret-service/latest/)、
  [AT-SPI](https://gnome.pages.gitlab.gnome.org/at-spi2-core/devel-docs/index.html)
  和 [XDG RemoteDesktop Portal](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.RemoteDesktop.html)
  是目标 Linux 桌面已有的系统接口。

## 明确拒绝的路线

- 在 `GoalLoop`、`ReviewStore` 或线程式 daemon 上继续堆长期任务分支；
- 在单机 MVP 阶段引入微服务、Redis、Kafka、Celery 或通用分布式工作流；
- 先建设全量 Machine Model、知识图谱或向量库，再寻找用户场景；
- 把任意 shell、任意 root shell 或模型生成的 sandbox/polkit 规则当作能力；
- 把 Reviewer 的自然语言判断当成内核级授权；
- 承诺所有系统修改都能通用回滚；
- 将密码、token 或 cookie 放进环境变量、argv、截图或模型上下文；
- 在未通过基准前把本地模型或多 provider 框架变成首期硬依赖；
- 长期保留新旧两套生产状态机以“降低迁移风险”。

## 后果

该路线需要主动重写任务内核和持久化边界，并在迁移期为每个垂直切片做新旧
等价测试；短期代码改动大于继续打补丁。但它限制了依赖数量，把特权和秘密
缩到可审计边界，并为删除现有巨型模块设置了可验证门禁。

如果后续实测推翻任何关键假设，必须新增 ADR，写明证据、替代方案、迁移和
回退方法；不得在实现中静默偏离本决策。
