# Goal 01：替换核心技术底座并建立可迁移骨架

- 阶段：01 / 09
- 依赖：无
- 风险：中
- 完成后进入：[Goal 02](02_durable_task_engine.md)

## 给 Codex 的命令

你要为 VibeOS 建立新的可维护核心底座，并用两个真实垂直切片证明它能接管
当前生产路径。不要在 `GoalLoop`、`ReviewStore` 或线程式 daemon 上继续添加
未来能力，也不要一次重写全部业务。完成 schema、分层、生命周期和切片迁移，
达到退出门禁后删除被替代的局部旧路径。

开始前完整阅读本目录 [README](README.md)、[基线审计](00_alignment_and_code_baseline.md)、
[实施技术决策](../../product/decisions/0002-implementation-foundation.md)、当前架构和
源代码。先复测基线并找出真实调用者，不能机械相信文档快照。

## 项目总体思想

VibeOS 是本地 Agent-native Linux 计算层：Agent 优先通过 API/CLI/系统服务
完成用户目标，必要时才使用 UI；实质歧义先询问，完成由证据判断；E2 可回滚
提权由独立 Reviewer 审核，E3 逐动作由用户批准；秘密不进入 Agent 或模型。

本阶段只建设能承载这些能力的简洁本地模块化单体，不实现长期任务、通用
Shell、提权、完整 Machine Model 或主动建议。

## 当前起点

- `src/vibeos` 目前是较平的模块集合，领域对象会直接接触 provider、SQLite
  或 runtime 细节；
- `reviews.py` 自己管理 SQLite schema/migration；其他状态分散在 trace、ledger
  和 snapshot 中；
- daemon 同时运行 `ThreadingHTTPServer` 和单独的 asyncio D-Bus 线程/循环；
- 当前 19 个 capability 和 263 个测试是必须保留的行为基线；
- `GoalLoop` 仍是当前生产内核，本阶段不能提前删除它。

## 核心目标

建立以下唯一技术底座：

```text
domain        纯状态、值对象、规则、事件
application   用例、事务协调、端口调用
ports         repository、clock、id、event、transport 等协议
adapters      SQLite、D-Bus、systemd、provider、desktop 实现
composition   唯一生产装配点
```

使用 SQLAlchemy 2 Core + Alembic + SQLite 建立权威数据库；使用一个 asyncio
supervisor 统一服务生命周期；迁移一个只读 E0 能力和一个当前可逆 E1 能力，
证明 CLI/D-Bus 到执行、证据和结果投影的完整链路。

## 必须实施

1. **依赖边界**
   - 根据仓库实际情况逐步建立新 package；不要只为满足目录名搬文件。
   - domain 不导入 adapter/framework；application 只依赖 ports/domain。
   - 加入自动依赖边界测试，禁止新代码回流到旧巨型模块。
   - 建立可机器检查的 legacy debt 清单和 complexity/import-cycle 基线；现有例外
     只能逐步减少，新模块不得用新例外替代旧巨型模块。

2. **严格 contract**
   - 外部请求、数据库 payload、模型/扩展动态对象使用 Pydantic 2 strict model。
   - 内部状态用 dataclass/enum，禁止 `dict[str, Any]` 穿过核心层。
   - contract 有明确版本、未知字段和无效 enum 的 fail-closed 测试。

3. **统一数据库**
   - 增加 SQLAlchemy 2 Core metadata、transaction manager 和 repository 基础。
   - 用 Alembic 管理 schema；从现有 ReviewStore 数据提供可重复迁移和回退说明。
   - 配置 foreign keys、WAL、busy timeout；数据库必须位于本地文件系统。
   - 当前态、domain event 和 outbox 有独立表及同事务写入测试，但不实现完整
     event sourcing。

4. **统一生命周期**
   - 新建单 asyncio supervisor，具备 start/ready/drain/stop 和结构化健康状态。
   - D-Bus 接入同一 event loop。HTTP 先统计真实调用者：没有调用者就移除；有
     调用者则仅保留薄兼容 adapter，并记录最迟删除阶段。
   - SIGTERM、启动失败、DB 不可用和重复启动必须可预测退出。

5. **两个垂直切片**
   - 从当前 19 个 capability 中选择一个真实 E0 只读和一个真实 E1 可逆能力；
   - 从 CLI 与 D-Bus 请求开始，经新 application/port/repository 路径执行，生成
     action receipt、observation/evidence 和兼容结果；
   - 与旧路径做表驱动等价测试，生产 composition 只为这两个能力选择新路径；
   - 迁移完成后删除这两个能力在旧路径中的重复业务逻辑，不能永久双写。

## 明确非目标

- 不实现新的通用任务调度器、Machine State Index、Model Gateway 或 Secret Broker；
- 不开放任意 shell、root、polkit、Bubblewrap 或 UI 输入；
- 不把所有旧模型一次改写，不为未来可能需求提前设计插件框架；
- 不用 FastAPI、Redis、消息队列、图数据库或 ORM session 模型替代既定路线；
- 不以增加 facade 包裹旧模块作为“完成替换”。

## 验收条件

- [ ] 新增代码遵守分层依赖，自动测试能故意捕获一次反向依赖 fixture；
- [ ] complexity/import-cycle/legacy 例外清单可机器检查，本阶段没有新增例外；
- [ ] 新数据库可从空库创建、从真实旧 fixture 升级，失败迁移不会留下半状态；
- [ ] current state、event、outbox 在一次事务中原子提交，故障注入证明全部回滚；
- [ ] 并发 writer、busy timeout、进程崩溃和 WAL 恢复测试通过；
- [ ] daemon 只有一个 asyncio 生命周期；ready 前不接请求，drain 后不接新任务；
- [ ] HTTP 的保留/删除有真实调用者证据和记录，不再存在独立业务实现；
- [ ] E0、E1 两个切片通过 CLI 和 D-Bus 真实执行，与旧行为/错误/证据等价；
- [ ] 这两个切片的生产入口不再调用旧业务逻辑，无双写、影子状态或开关遗留；
- [ ] 当前 19 capability 均仍可发现，既有测试与共同质量门禁全部通过；
- [ ] 架构文档准确标出新旧边界、剩余迁移和删除计划。

## 必交付物

- 新模块边界、依赖/复杂度 ratchet 测试和唯一 production composition；
- SQLAlchemy/Alembic schema、迁移、repository 和事务测试；
- 单 asyncio supervisor 与 D-Bus/可选 HTTP adapter；
- 两个真实迁移切片及等价/故障测试；
- 一份迁移清单，逐项列出 `GoalLoop`、`ReviewStore`、legacy runtime 和 HTTP
  兼容层的真实调用者、owner、删除门禁。

只有所有硬验收满足、被迁移切片不再依赖旧业务路径时才结束本 Goal。
