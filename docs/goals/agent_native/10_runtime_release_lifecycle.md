# Goal 10：完成 Runtime 安装、升级、失败恢复与卸载生命周期

- 阶段：10 / 11
- 依赖：[Goal 09](09_proactive_service_advisor.md)全部完成
- 规模：XL
- 风险：高
- 完成后进入：[Goal 11](11_readonly_extension_and_distro_gate.md)

## 给 Codex 的命令

你要把 Goal 06 的基础可安装 artifact 和 Goal 07–09 的真实能力收敛成一个可重复安装、
配置、升级、失败恢复、卸载和重装的版本化 Runtime。用户不能依赖源码 checkout、
editable install、仓库 `.env`、开发者 home 或手工修数据库。

本 Goal 只负责 Runtime 生命周期和发布证据，不建设扩展协议，不编写发行版结论，不
创建 ISO、包仓库或自动系统更新。Goal 03 已证明代码整合回退，但 schema 升级后的
产品回退不能靠把旧代码直接指向新数据库；必须把 artifact、数据库兼容副本/恢复点、
SecretRef、可选 helper 和桌面会话边界作为一个发布事务处理。

## 项目总体思想

VibeOS 首先是在成熟 Linux 发行版上交付的本地 Agent Runtime。稳定交付不仅是 wheel
能安装，而是用户数据、未完成任务、秘密引用、可选特权机制和桌面会话在版本变化中
具有明确状态。升级失败必须恢复到一个真实可运行的 artifact/database pair，不能只
回滚二进制后让它读取不兼容 schema。

默认安装保持最小权限。provider secret 由 Secret Broker 管理；E2 helper/polkit 只有
用户已选择时才安装和迁移；portal 授权、UI node、截图和一次性 PrivilegeLease 不得
被当成可跨版本恢复的长期能力。

## 预期进入状态与现场核对

预期已有：Goal 05 唯一 Model Gateway/Secret Broker；Goal 06 非 editable 基础 artifact
和干净 Fedora 安装证据；Goal 07 GNOME 混合任务；Goal 08 可选单一 E2 mechanism；
Goal 09 Finding/Suggestion 聚合与主动建议闭环。

开始前现场确认：

- 当前 artifact 类型、version、digest、依赖锁定和安装入口；
- systemd user service、D-Bus 文件、desktop bridge、SecretRef 和可选 polkit/helper 的
  实际安装位置及 owner；
- Alembic head、Goal 01/02/03 数据升级合同、未完成 Task 和 Finding/Suggestion schema；
- Goal 03 runbook 中“旧代码不得读取升级后数据库”的回退边界；
- Fedora 主支持版本和一个明确 Ubuntu GNOME smoke 版本；
- 当前开发安装、VM 脚本和真实用户调用者，避免无证据删除；
- portal、keyring、用户 session、锁屏、登出和 reboot 对升级流程的影响。

若 Goal 06 artifact 仍依赖 editable/source path，先修复基础 artifact，不并行设计另一套
打包系统。改变 wheel/installer 体系需要 ADR 和用户确认。

## 核心目标

交付以下可审计生命周期：

```text
versioned artifact + manifest + digest
  -> preflight and compatible backup/restore point
  -> clean install or in-place upgrade
  -> schema/data/service switch
  -> post-upgrade verification
  -> commit release
     OR restore prior artifact/database pair
  -> uninstall with explicit state policy
  -> reinstall/import and continue
```

固定用户结果是：一个不了解仓库结构的用户可以安装并配置 VibeOS，完成 Goal 07 主
场景和 Goal 09 建议闭环；带着未完成任务升级；在注入失败后恢复前一可运行版本；
卸载默认保留可导出状态，重装后按支持窗口继续。

## 必须实施

### 1. 正式 Runtime artifact

- 基于 Goal 06 方式生成 versioned、非 editable、依赖可复现的 release candidate；
  记录 artifact digest、来源、许可证、依赖清单/SBOM、Python/OS/desktop 兼容范围。
- installer 幂等配置 systemd user service、D-Bus、desktop integration、数据库迁移、
  非敏感 provider route 和用户选择的可选 E2 mechanism；不得写入 provider key 明文。
- 默认安装不含 root helper、自动扩展下载或宽泛 polkit。可选特权组件必须单独显示
  verb、权限、版本、卸载和用户批准。
- artifact 在无源码目录、无开发依赖、无仓库 `.env` 的干净用户环境运行；systemd
  unit 使用稳定安装路径，不使用当前 checkout 作为 WorkingDirectory。
- manifest 记录 Core contract、schema、Secret Broker、helper 和 desktop component
  版本，未知或不兼容组合在修改系统前 fail-closed。

### 2. 升级预检与发布事务

- 升级前停止接收新动作并 drain；记录运行中/等待中任务、Finding/Suggestion、outbox、
  Alembic revision、artifact、helper、SecretRef 和服务健康。
- 预检磁盘空间、版本路径、schema 支持、keyring 状态、可选组件和 rollback 可用性；
  不满足时在任何破坏性步骤前停止。
- 创建一致数据库备份/恢复点，包括 SQLite WAL/SHM 处理和校验；Secret Service item
  不导出明文，记录可验证引用与人工恢复要求。
- 明确 prepare、install artifact、migrate、switch service、verify 和 commit 阶段；
  每步可重复、可观察，并有超时、取消和 crash recovery。
- 新旧 daemon 不得同时写同一数据库；systemd restart 不能成为数据库一致性的唯一
  保证。

### 3. 数据与未完成工作兼容

- 覆盖空库、Goal 01/02/03 数据、当前 release 前一版本、未完成 Task、awaiting review/
  clarification、timer、outbox、Finding/Suggestion 和 terminal evidence。
- 旧 approval、SecretGrant、PrivilegeLease、portal session、AT-SPI node 和截图坐标按
  各自生命周期重新验证；不得因数据库里存在记录就自动恢复权限或 UI 控制。
- 升级后重新绑定 effect/policy/model route/helper version；绑定变化时安全等待新的
  review/用户输入，而不是沿用过期授权。
- migration 失败必须恢复与旧 artifact 匹配的数据库副本。不得执行“代码降级但继续
  使用已升级数据库”并称为回滚。
- 对不可自动恢复的任务生成只读导出、证据和人工 disposition，不猜测外部副作用。

### 4. 故障注入与回退

- 在预检后、备份后、artifact 安装中、schema migration 中、服务切换中和 post-verify
  前分别注入进程崩溃/断电等价故障。
- 每个边界证明能够继续升级或恢复到完整旧 artifact/database pair；恢复过程自身失败
  时进入明确 `recovery_required`，停止 writer 并保留证据。
- 证明回退后旧版本完成其支持的 status/任务，保留数据库 hash/版本证据；新数据库和
  导出不得被回退过程悄悄修改。
- 发布后若只需撤回兼容 adapter，使用审计可见 revert/repair release，不重写共享历史。
- 为紧急停止、只读导出、恢复点选择、失败 helper 禁用和人工升级提供 runbook。

### 5. 卸载、重装和状态政策

- 卸载默认停止/禁用服务并移除 artifact、D-Bus/desktop 文件和用户选择的 helper，
  保留可导出的用户状态、Secret Service item 和审计，明确显示保留内容。
- 只有用户显式选择 destructive purge 才删除状态/secret；该选择属于 E3 用户确认，
  本 Goal 自动测试只能使用 fixture state。
- 重装识别保留状态、验证 schema/support window 后恢复；不兼容时提供只读导出或
  明确迁移路径，不静默新建空状态掩盖旧数据。
- installer/uninstaller 幂等；重复执行、半安装、服务不存在和组件版本不一致都有
  确定结果。

### 6. 发布验证矩阵

- Fedora GNOME 是主支持环境：clean install -> configure provider -> Goal 07 主场景
  -> Goal 09 建议 -> unfinished task upgrade -> injected failure recovery -> uninstall/
  reinstall。
- Ubuntu GNOME 只验证一个明确版本的安装、daemon/D-Bus、数据库、provider 和 E0/E1
  smoke；不通过时可以暂不支持，但必须记录真实原因。
- 机器可读证据包含 artifact/schema/OS/desktop/component versions、命令、结果、hash
  和未覆盖边界；WSL/mock/source checkout 不能替代发布矩阵。
- 发布候选重跑 Goal 03–09 的核心黄金场景、共同质量门禁、secret/PII 扫描和架构
  守卫；只声明实际通过的组合。

## 明确非目标

- 不实现扩展 manifest/port、插件 SDK、市场、远程下载或第三方代码加载；
- 不编写最终发行版 ADR，不创建 ISO、系统 installer、包仓库或自动 OS 更新；
- 不新增 capability、E2 verb、detector 或桌面场景；
- 不把 Secret Service item 明文打进备份，不把 VM snapshot 当产品回退；
- 不承诺无限向后兼容；使用明确 schema/support window 和导出政策；
- 不删除仍有调用者的开发/兼容路径，除非有 replacement evidence 和用户批准。

## 验收条件

- [ ] 正式 artifact 非 editable、来源/digest/依赖/SBOM/支持范围可查且默认最小权限；
- [ ] 干净环境不依赖源码、`.env`、开发者 home 或手工安装步骤；
- [ ] 升级使用一致恢复点和 artifact/database pair，不把旧代码指向升级数据库；
- [ ] 空库、旧数据、未完成 Task、review/clarification、Finding/Suggestion、SecretRef、
  可选 helper 和桌面 session 边界均有明确处理；
- [ ] 所列升级/断电故障点可以继续或回退；失败回退进入显式安全状态；
- [ ] 卸载默认保留状态，显式 purge 边界清楚，重装可验证恢复；
- [ ] Fedora 完整发布矩阵通过，Ubuntu smoke 的支持/排除有真实证据；
- [ ] Goal 03–09 的任务、模型、秘密、桌面、E2 和建议边界无回归；
- [ ] 发布候选没有 WSL/mock/source checkout 冒充真实安装或现实效果；
- [ ] 安装、升级、恢复、卸载、支持和紧急处置文档可由新用户执行。

## 必交付物

- 正式 Runtime artifact、manifest、digest、依赖锁/SBOM 和支持政策；
- 幂等 installer/uninstaller 与非敏感配置流程；
- 数据/任务/SecretRef/helper/desktop 兼容矩阵和 release transaction；
- 升级故障注入、artifact/database pair 回退证据和运维 runbook；
- Fedora 完整发布证据、Ubuntu smoke 结论和机器可读 evidence bundle；
- 更新后的当前状态、发布、数据迁移、恢复和卸载文档。

只有一个不了解仓库的用户能够重复安装、升级、从失败中恢复、卸载和重装，并且任务、
数据、秘密和权限没有被静默破坏时，才结束本 Goal。
