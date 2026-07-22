# Goal 11：验证一个只读扩展并作出独立发行版决策

- 阶段：11 / 11
- 依赖：[Goal 10](10_runtime_release_lifecycle.md)全部完成
- 规模：L
- 风险：中高

## 给 Codex 的命令

你要在 Goal 10 已稳定交付的 Core contract 上实现一个只读 E0 collector/verifier
扩展，证明用户可以显式增加能力，而扩展不能绕过 Task、事实、模型、秘密、动作、
权限和审计边界。随后只使用 Goal 03–10 的真实平台和维护证据，作出是否继续 Runtime、
是否探索定制镜像/发行版的正式 ADR。

固定扩展是 `host.boot_session`：读取 Linux boot ID 与 monotonic uptime，输出本机作用域
哈希后的 boot-session ID、uptime bucket、captured_at 和 source，用来区分 daemon
重启与整机重启。它只改善恢复证据，不得追溯性改写 Goal 07 的旧验收结论；安装后应
重新运行相关恢复场景形成新增证据。

不得建设公共插件市场、远程自动下载、E1/E2 扩展或发行版实现。若 ADR 选择
`prototype-distro`，它只产生待用户批准的新 Goal，不授权创建 ISO、installer 或镜像。

## 项目总体思想

“本地 Agent”与“按需增加能力”并不冲突。扩展只能提供窄 collector/verifier 或未来
受治理 action 的实现；Core 始终拥有 Task Store、Effect Policy、Model Gateway、
Secret Broker、Privilege 边界、Tool/Context Registry、审计和完成判断。扩展不能把
任意 Python 导入 Core 进程，也不能携带自己的任务循环、权限系统或模型客户端。

产品应先在成熟 Linux 发行版上作为稳定 Runtime 存在。只有现有平台结构性阻碍至少
一个核心黄金场景，而且 Runtime、容器/immutable host、定制镜像或窄系统集成都不能
合理解决时，才有理由承担独立发行版的更新、安全、硬件和长期维护成本。

## 预期进入状态与现场核对

预期 Goal 10 已有正式 artifact、稳定 install/upgrade/rollback/uninstall 生命周期、
Fedora 主支持证据和 Ubuntu smoke 结论。开始前现场确认：

- Core 当前真正稳定、适合开放的最小 collector/verifier port；
- ContextPackageRegistry/ObservationService、EvidenceBundle 和 verifier 的版本化合同；
- artifact 如何显式安装、升级、禁用和移除可选组件；
- 可用的进程隔离、资源控制和 IPC 机制，不能凭想象承诺 sandbox；
- Goal 03–10 实际发生的平台限制、失败恢复、维护成本和用户收益；
- Fedora/Ubuntu、GNOME、systemd、Secret Service、portal、polkit 和打包限制的证据
  来源，区分一次缺陷、实现债务和结构性平台限制。

如果没有足够稳定的 extension port，先报告阻塞并修订范围，不把 Core 私有 Python
模块暴露为临时 API。若发行版证据不足，可以完成扩展并在 ADR 选择
`insufficient-evidence`。

## 核心目标

交付以下受限扩展闭环：

```text
trusted local E0 extension artifact
  -> strict manifest / digest / Core compatibility
  -> isolated process with bounded resources
  -> versioned read-only collector port
  -> Core validation
  -> Core-owned MachineFact / Evidence path
  -> enable | disable | quarantine | remove
```

随后比较以下产品路线：

```text
continue-runtime
container or immutable-host integration
custom image prototype
full distro prototype
insufficient-evidence
```

ADR 必须给出结论、证据、反证条件、资源和复审触发点，而不是为了结束路线图强行选择
发行版。

## 必须实施

### 1. 严格 extension manifest

- manifest 版本化声明 identity、version、publisher、artifact digest、Core compatibility、
  entrypoint、port version、fact/evidence schema、数据等级、权限、资源预算和来源。
- 未知字段、未知版本、digest 不符、publisher 不受信、schema 不兼容和能力超声明
  fail-closed；首版只支持用户显式安装的本地受信 artifact。
- 扩展身份与 capability/fact identity 稳定，升级不能静默换 publisher、数据级别或
  resource scope。
- Core 安装记录只包含非敏感 metadata；不得让 manifest 声明 secret、网络、UI、
  action、E2 或任意文件权限。

### 2. 隔离进程与只读 port

- 扩展在独立进程运行，通过窄版本化 IPC 接收一次 collector 请求并返回 strict payload；
  不 import Core 私有模块，不与 Core 同进程执行任意 Python。
- 进程只能获得完成 `host.boot_session` 所需的读取范围、wall-time、CPU、内存、输出和
  并发预算；无网络、secret、UI input、任意文件写入、数据库或 helper 访问。
- Core 验证 schema、大小、时间、source 和数据级别后，才把事实写入 Goal 04/06 已有
  Observation/Evidence 路径。扩展不能直接写 Task DB、outbox、audit 或完成状态。
- crash、timeout、坏 schema、资源超限、IPC 截断和恶意输出使该扩展调用失败/隔离，
  Core 和其他任务继续运行或进入可解释等待。

### 3. 固定 `host.boot_session` 扩展

- 只读取 Linux boot ID 与 monotonic uptime；不输出原始 boot ID、用户名、hostname、
  machine-id、进程 argv、环境变量或其他稳定主机标识。
- 使用 Core 提供或审查过的本机作用域 salt/hash 方案输出 boot-session pseudonym；
  该值标为 D1，默认不进入云模型上下文。
- uptime 只输出有明确恢复价值的 bucket，不提供不必要的精细设备行为画像。
- verifier 用该事实区分 daemon restart 与 host reboot；事实 stale/extension unavailable
  时不得猜测。
- 安装后重跑 Goal 07 的相关 daemon/reboot 恢复子场景，记录新增证据和仍需用户 session
  的边界。

### 4. 扩展生命周期与隔离攻击测试

- 支持 install、compatibility check、enable、disable、upgrade、quarantine、remove；
  操作由 Goal 10 Runtime 生命周期管理且用户可审计。
- disable/remove 后停止调度并清理非必要扩展缓存，保留 Core-owned 历史 evidence 的
  来源可读性；不能让任务悬挂在不可恢复私有状态。
- 连续 crash、timeout、坏 schema 或资源超限自动 quarantine；用户可以查看原因、
  重新启用或移除。
- 测试扩展尝试读 DB/Secret Service、调用 Model Gateway、访问网络、启动 action、
  请求 helper、写用户文件、伪造 evidence/完成状态和加载其他代码，均必须失败。
- 不把测试进程“没有尝试越权”当隔离证明；记录实际 OS/IPC enforcement 和剩余同 UID
  威胁边界。

### 5. 发行版决策证据

- 量化成熟发行版对原子更新/可启动回滚、权限/秘密生命周期、Runtime/桌面一致发布、
  版本化机器事实、灾难恢复和扩展隔离的真实限制。
- 区分 VibeOS 自身缺陷、可修的打包债务、上游版本差异、用户配置和真正平台结构
  限制；单次 VM 故障不能直接成为发行版理由。
- 比较普通 Runtime、容器/immutable host、定制镜像和完整发行版的用户收益、安全
  收益、工程成本、更新责任、硬件兼容、供应链、支持负担和退出成本。
- 使用 Goal 10 Fedora/Ubuntu 证据，不追求新发行版全矩阵；必要的补充探针必须只读、
  可复核，不创建镜像。

### 6. 正式 ADR

- 输出 `continue-runtime`、`prototype-custom-image`、`prototype-distro` 或
  `insufficient-evidence` 之一；说明为什么其他选项当前不成立。
- `prototype-distro` 只有在至少一个核心黄金场景在受支持发行版反复失败，根因是平台
  结构限制且较小方案无法解决时才允许。
- ADR 包含证据表、反证条件、候选基底、最小 prototype 范围、人员/维护成本、更新与
  安全责任、退出条件和复审触发点。
- 选择 prototype 只建议下一份待用户批准的 Goal；不得在本阶段创建 ISO、installer、
  镜像、仓库、品牌或自动更新服务。

## 明确非目标

- 不建设公共插件市场、远程下载、自动安装、任意 Python in-process 插件或 E1/E2
  扩展；
- 不允许扩展自带 Task Store、模型客户端、secret store、Effect Policy、网络执行器、
  UI input 或 root helper；
- 不开放通用 SDK、任意 collector 文件权限或第三方兼容承诺；
- 不在本 Goal 改造 Runtime 发布体系或新增黄金用户场景；
- 不创建独立发行版、ISO、系统 installer、定制镜像、包仓库或自动 OS 更新；
- 不因技术偏好、品牌或打包方便选择发行版。

## 验收条件

- [ ] `host.boot_session` manifest、digest、Core/port compatibility 和数据等级严格校验；
- [ ] 扩展独立进程不能 import Core 私有模块或直读 DB/Secret Service；
- [ ] 扩展没有网络、UI、文件写入、action、Model Gateway 或 helper 能力；
- [ ] Core 验证后才产生 MachineFact/Evidence，扩展不能伪造 Task/完成状态；
- [ ] boot-session pseudonym 和 uptime bucket 满足最小化要求，D1 默认不进入云上下文；
- [ ] install/enable/disable/upgrade/quarantine/remove 和失败恢复通过；
- [ ] crash、timeout、坏 schema、资源超限和越权攻击 fail-closed，不影响 Core；
- [ ] 重新运行的 daemon/reboot 场景证明事实价值且不改写旧验收；
- [ ] 发行版 ADR 使用真实 Goal 03–10 证据并区分实现债务与平台限制；
- [ ] 若证据不足，结论诚实为 `insufficient-evidence`；
- [ ] 仓库没有未经用户授权的发行版或市场实现；共同质量门禁全部通过。

## 必交付物

- versioned extension manifest/port 和生命周期合同；
- `host.boot_session` E0 扩展、Core adapter、fact/evidence 与恢复场景；
- 隔离威胁模型、资源/攻击/故障测试和 quarantine 证据；
- Fedora 主平台下的扩展安装/升级/移除证据；
- Runtime、immutable/container、定制镜像和发行版比较表；
- 独立发行版决策 ADR；若选择 prototype，只包含待用户批准的后续 Goal 建议；
- 更新后的扩展、隐私、支持边界和项目当前状态文档。

只有扩展能够增加一个真实只读能力而无法绕过 Core，并且发行版方向由可复核证据而
不是想象决定时，才结束本 Goal。
