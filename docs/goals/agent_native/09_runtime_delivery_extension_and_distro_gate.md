# Goal 09：稳定交付 Runtime，验证一个只读扩展并作出发行版决策

- 阶段：09 / 09
- 依赖：[Goal 08](08_proactive_service_advisor.md)全部完成
- 风险：中高

## 给 Codex 的命令

你要把 Goal 05 的基础可安装 artifact 和 Goal 07–08 的真实 GNOME 能力收敛为可
重复安装、升级、失败恢复和卸载的版本化 Runtime；在已经稳定的 Core contract 上
实现一个只读 E0 collector/verifier 扩展，证明用户可以按需增加能力而扩展不能绕过
Task、模型、秘密、动作和权限边界；最后使用本项目实际证据编写独立 Linux 发行版
ADR。不得在本 Goal 顺带创建 ISO、installer、包仓库或品牌化发行版。

本阶段不建设公共插件市场，不实现 E1/E2 扩展，不追求两个发行版全矩阵。Goal 05
已经完成基础打包，本阶段只做真实升级/回退/卸载收敛，避免再次把三个新平台从零
塞进一个 Goal。

## 项目总体思想

“本地 Agent”与“可按需增加能力”并不冲突。扩展可以提供窄 collector、verifier
或未来受治理的 action，但 Core 始终拥有 Task Store、Effect Policy、Model Gateway、
Secret/Privilege 边界、审计和完成判断。扩展不能把任意 Python 导入核心进程，也
不能携带自己的权限系统。

产品应先在成熟 Linux 发行版上成为稳定 Runtime。只有现有平台结构性阻碍至少一个
核心用户场景，且 Runtime、容器/immutable host 或窄系统集成无法合理解决时，才有
理由承担独立发行版的更新、安全、硬件和长期维护成本。

## 预期进入状态与现场核对

预期 Goal 05 已有非 editable 基础 artifact，Goal 07 已有真实 Fedora GNOME 混合
场景，Goal 08 已有一个主动建议闭环。开始前现场确认：

- 当前 artifact 类型、依赖锁定、systemd/D-Bus/desktop/可选 polkit 安装内容；
- 数据库、Task、secret reference、特权机制和桌面 session 的升级兼容要求；
- Core 当前真正稳定并适合开放的最小 extension ports；
- Fedora 主支持版本和一个合理的 Ubuntu GNOME smoke 版本；
- 过去 Goal 中实际出现的平台限制、恢复失败、维护成本和用户收益证据。

## 核心目标

用户结果固定为：一个不了解仓库结构的用户可以从正式 artifact 安装 VibeOS，配置
provider 后完成 Goal 07 主场景和 Goal 08 建议闭环；升级失败时恢复到前一可运行版本，
卸载后可保留并重新导入状态；用户还可以显式安装/禁用/移除一个只读扩展。用户不
需要源码 checkout、editable install 或开发者 home 中的隐式文件。

交付以下产品化闭环：

```text
versioned Runtime artifact
  -> clean install and configure
  -> golden task + proactive suggestion
  -> in-place upgrade with unfinished task/data
  -> failure recovery or rollback
  -> uninstall/reinstall

trusted local E0 extension
  -> strict manifest and compatibility check
  -> isolated process and read-only port
  -> Core-owned fact/evidence path
  -> disable/quarantine/remove
```

最后输出 `continue-runtime`、`prototype-distro` 或 `insufficient-evidence` ADR。ADR
是决策，不授权实现发行版；若选择 prototype，必须由用户另行批准新的 Goal。

## 必须实施

1. **稳定 Runtime artifact**
   - 在 Goal 05 打包方式上生成版本化、非 editable、依赖可复现的正式候选 artifact；
   - 安装幂等配置 systemd user service、D-Bus、desktop bridge、数据库迁移以及经用户
     选择的 provider secret/可选 E2 mechanism；
   - 默认安装仍是最小权限，特权组件和扩展显式选择，不从源码 checkout 运行；
   - 记录 artifact digest、来源、许可证、依赖清单/SBOM 和支持窗口。

2. **升级、恢复和卸载**
   - 覆盖空库、旧 Goal 01/02 数据、未完成 Task、awaiting review/clarification、locked
     keyring、extension state 和可选 helper 版本；
   - 升级前做兼容检查和备份/恢复点；迁移或服务启动失败时恢复到前一可运行版本；
   - 在升级前、schema 中、服务切换和升级后验证阶段注入失败/断电；
   - 卸载默认保留可导出用户状态，明确选择才清理；重装可继续兼容数据；
   - 旧开发安装路径只记录弃用和真实调用者，不在没有独立批准时批量删除。

3. **一个最小只读扩展**
   - 固定实现 `host.boot_session` E0 collector：只读取 Linux boot ID 与 monotonic uptime，
     输出本机作用域哈希后的 boot session ID、uptime bucket、captured_at 和 source，
     用于区分 daemon 重启与整机重启并改善 Goal 07 恢复证据；该事实标为 D1，默认
     不进入云模型上下文；
   - collector 不需要 secret、网络、UI 输入、文件写入或提权，不输出用户名、进程
     argv、环境变量或其他主机标识；
   - manifest 严格声明 identity/version/publisher、Core compatibility、entrypoint、事实/
     evidence schema、数据等级、资源预算和来源；未知字段/版本 fail-closed；
   - 首版只支持用户明确安装的本地受信来源和 digest 校验，不建设市场或自动下载；
   - 扩展在隔离进程中通过版本化 port 与 Core 通信，不能 import Core 私有模块、直读
     DB/Secret Service、调用 Model Gateway、启动任意 action 或获得 helper；
   - 支持 install/enable/disable/quarantine/remove；崩溃、超时、坏 schema 和资源超限
     不影响 Core，未完成任务安全等待或降级。

4. **发布验证矩阵**
   - Fedora GNOME 作为主支持环境，测试 clean install -> configure -> Goal 07 主场景
     -> Goal 08 建议 -> upgrade -> recovery/rollback -> uninstall/reinstall；
   - Ubuntu GNOME 只做明确版本 smoke；不通过时可以暂不支持，但必须记录事实原因，
     不能用 Fedora 结果推断；
   - 产出机器可读证据包，声明只覆盖实际通过的版本和组合；
   - 发布候选不能依赖 WSL、mock、源码目录或开发者 home 中的隐式状态。

5. **发行版决策门禁**
   - 量化成熟发行版对原子更新/可启动回滚、权限/秘密生命周期、Runtime/桌面一致发布、
     版本化 Machine State 和灾难恢复的真实限制；
   - 比较普通 Runtime、容器/immutable host 集成、定制镜像和完整发行版的用户收益、
     安全收益、工程成本、更新责任、硬件兼容和退出成本；
   - `prototype-distro` 只有在至少一个核心黄金场景在受支持发行版反复失败，根因是
     平台结构限制且较小方案无法解决时才允许；
   - 证据不足时明确选择 `insufficient-evidence` 并给出复审触发条件，而不是为了结束
     Goal 强行作永久决定；
   - ADR 包含结论、反证条件、资源估算、候选基底、退出条件和复审日期。

## 明确非目标

- 不建设公共插件市场、远程自动下载、任意 Python in-process 插件或 E1/E2 扩展；
- 不允许扩展自带 Task Store、模型客户端、secret store、effect policy 或 root helper；
- 不承诺无限向后兼容，采用明确 schema/support 窗口和迁移策略；
- 不在本 Goal 创建 ISO、系统 installer、包仓库、品牌化发行版或自动系统更新；
- 不因打包方便复制整个 Linux 发行版；
- 不删除未经 caller/兼容证据和用户批准的开发或生产路径。

## 验收条件

- [ ] 正式候选 artifact 非 editable、来源/依赖/SBOM 可查且默认最小权限；
- [ ] Fedora clean install、配置、真实黄金场景、主动建议、升级、失败回退和卸载/
  重装矩阵通过；
- [ ] 未完成 Task、review/clarification、数据库、secret reference、extension state 和
  可选 helper 在升级/回退中保持一致；
- [ ] `host.boot_session` E0 扩展完成安装、运行、升级兼容检查、禁用、quarantine
  和卸载，并能区分 daemon restart 与 host reboot；
- [ ] 扩展无法直读 DB/Secret Service、绕过 Model Gateway/Task/Action 路径或获得提权；
- [ ] 扩展 crash、timeout、坏 schema、资源超限和恶意 manifest 测试 fail-closed；
- [ ] Ubuntu smoke 的支持或排除结论有真实证据；
- [ ] 旧开发路径只有证据驱动弃用，没有未经批准的大面积删除；
- [ ] 发行版 ADR 使用真实故障和成本数据；若证据不足则诚实输出 insufficient-evidence；
- [ ] 仓库没有未经用户授权的发行版实现；Goal 03–08 和共同质量门禁全部通过。

## 必交付物

- 正式 Runtime artifact、版本/依赖/SBOM 和支持政策；
- install/upgrade/failure-recovery/rollback/uninstall/reinstall 流程与 Fedora 证据；
- 最小 E0 extension manifest/port、一个真实扩展和隔离/攻击测试；
- Fedora 主支持矩阵与 Ubuntu smoke 结论；
- 独立发行版决策 ADR；若选择 prototype，只记录待用户批准的后续 Goal。

只有 Runtime 可以稳定重复交付、扩展不能绕过 Core、升级失败可恢复，并且发行版方向
有诚实的实证决策时，才结束本 Goal。
