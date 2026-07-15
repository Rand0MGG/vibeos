# Goal 09：稳定扩展与交付，并作出发行版门禁决策

- 阶段：09 / 09
- 依赖：[Goal 08](08_proactive_advisor.md)全部完成
- 风险：中高

## 给 Codex 的命令

你要把已经在真实 GNOME 环境证明的 VibeOS Runtime 变成可重复安装、升级、
卸载和恢复的稳定产品；定义受 Core 治理的最小扩展协议，并用两个真实扩展
证明能力可定制而不绕过任务、模型、秘密、动作和权限边界。最后基于安装、
更新、回滚和桌面限制的实测数据编写 ADR，明确继续做 Runtime 还是启动独立
VibeOS Linux。不要因为最初愿景而预设必须做发行版。

## 项目总体思想

“本地 Agent”和“可按需增加能力”并不冲突：扩展提供知识、collector、action
或 verifier，但 Core 始终拥有状态、权限、秘密、模型和审计。产品应先在成熟
Linux 发行版稳定交付；只有现有平台结构性阻碍关键用户价值，且 Runtime 无法
合理解决时，才承担维护发行版的长期成本。

## 当前起点

- 当前 Python 包和 shell 安装脚本偏开发态，包含 editable install 和手写
  systemd user unit；
- capability 集合主要编译进 Core，没有稳定 manifest、兼容性、来源和隔离模型；
- Goal 07 已有 Fedora/Ubuntu GNOME 支持证据，Goal 08 已有真实用户协作闭环；
- 尚无正式发布产物、升级/卸载矩阵、供应链策略或发行版量化决策。

## 核心目标

交付一个可安装的版本化 Runtime 和最小扩展 SDK/manifest：

```text
signed package/extension
  -> static manifest validation
  -> user-visible requested scope
  -> compatibility and provenance check
  -> Core registration
  -> existing sandbox/broker/task/evidence paths only
```

同时建立 Runtime 与独立发行版的证据比较，输出明确 `continue-runtime`、
`prototype-distro` 或 `insufficient-evidence` 决策及复审条件。

## 必须实施

1. **最小扩展协议**
   - manifest 版本化声明 identity/version/publisher、Core compatibility、entrypoints、
     requested actions/effects、data classes、domains、models、secrets、resources、
     sandbox profile、evidence 和 rollback support；
   - strict schema 校验，未知权限/字段/版本 fail-closed；manifest 不能声明任意
     polkit、root command、Bubblewrap 参数或读取 Secret Service；
   - 扩展只通过版本化 ports 与 Core 通信，不能导入 Core 内部模块或直接写 DB。

2. **来源、安装和隔离**
   - 首版只支持本地受信源/明确用户安装，不先建设公开市场；
   - 校验 artifact digest、publisher/signature（若选择签名方案）、依赖锁和
     reproducible metadata；安装前展示精确范围和兼容性；
   - 扩展执行复用 Action Fabric sandbox、Model Gateway 和 Secret Broker，不能
     获得 Core 进程内任意 Python 执行；
   - 支持 enable/disable/remove/quarantine，禁用后未完成任务安全暂停。

3. **两个真实扩展**
   - 一个只读 collector/verifier 扩展；
   - 一个 E1 action 扩展，具有 sandbox、receipt、独立 verify 和 recovery；
   - 从 manifest 到安装、任务调用、升级、不兼容、撤销和卸载全链路测试；
   - 不为示例扩展修改 Task Engine、Effect Engine 或 Broker 的专用分支。

4. **稳定 Runtime 交付**
   - 选择与 Fedora/Ubuntu 支持矩阵匹配的成熟打包方式，生成非 editable、版本化、
     锁定依赖的 artifact；不保留 shell 脚本作为唯一安装器；
   - 安装正确配置 user service、D-Bus、desktop bridge、polkit/helper（如需要）和
     migration；卸载默认保留/导出用户状态并可显式清理；
   - 升级包含 DB、任务、扩展和协议兼容检查；失败可恢复到前一可运行版本；
   - 建立 SBOM、许可证、漏洞响应、发布说明和支持窗口。

5. **发布矩阵**
   - 从干净 Fedora/Ubuntu VM 测试 install → configure → golden tasks → upgrade →
     rollback/recovery → uninstall/reinstall；
   - 测试 daemon 运行中升级、未完成任务、locked keyring、不兼容扩展、helper
     版本错配和断电；
   - 产出机器可读证据包，发布声明只覆盖实际通过组合。

6. **发行版决策门禁**
   - 量化现有发行版对原子系统更新/可启动回滚、Agent 权限/秘密生命周期、
     Runtime/桌面一致发布、版本化机器状态和恢复的实际阻碍；
   - 比较 Runtime integration、容器/immutable host 集成、定制镜像和完整发行版
     的用户收益、工程/安全/发布维护成本；
   - 只有至少一个核心黄金场景在受支持发行版上反复失败，且根因是平台结构
     限制、Runtime 方案无法合理解决，才可选择 `prototype-distro`；
   - 决策 ADR 包含证据、资源估算、范围、基底发行版候选、退出条件和复审日期。

## 明确非目标

- 不建设公开插件市场、自动下载第三方代码或任意 Python in-process 插件；
- 不允许扩展自带权限判定、Task Store、模型客户端、secret store 或 root helper；
- 不承诺无限向后兼容，采用明确支持窗口和迁移策略；
- 不因打包方便复制整个 Linux 发行版；
- 若门禁不满足，不创建 ISO、installer、包仓库或品牌化发行版。

## 验收条件

- [ ] manifest schema、兼容性和权限差异可审计，恶意/未知声明 fail-closed；
- [ ] 扩展无法直接读 DB/Secret Service、绕过 Model Gateway 或启动未治理动作；
- [ ] 两个真实扩展完成安装、调用、升级、禁用、quarantine 和卸载，无 Core 专用
  分支，E1 扩展的 crash/reconcile/verify 测试通过；
- [ ] Runtime artifact 非 editable，依赖可重复，来源/SBOM/许可证可查询；
- [ ] Fedora 和 Ubuntu 发布矩阵全部达到声明门槛，升级失败和断电可恢复；
- [ ] 旧开发安装路径有明确弃用/删除结果，production 文档不依赖源码 checkout；
- [ ] 未完成任务、DB、secret reference、extension state 在升级/回退中保持一致；
- [ ] 发行版 ADR 使用真实故障和成本数据，结论、反证条件和复审日期明确；
- [ ] 若门禁不满足，仓库没有未经授权的发行版实现工作；
- [ ] 安全、操作、用户和共同质量门禁全部通过。

## 必交付物

- 扩展 manifest/SDK、隔离安装器、两个真实扩展和攻击/兼容测试；
- 正式 Runtime artifact、版本/升级/卸载/恢复流程和 SBOM；
- Fedora/Ubuntu 发布证据、支持矩阵和维护政策；
- 独立发行版决策 ADR；若选择 prototype，还要有单独待批准的后续 Goal，不能
  在本阶段顺带实现发行版。

只有 Runtime 可重复交付、扩展不能绕过 Core、发行版方向有实证决策后才结束。
