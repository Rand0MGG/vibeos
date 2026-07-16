# Goal 03：整合 Goal 01 与 Goal 02，建立可信可合并基线

- 阶段：03 / 09
- 依赖：[Goal 01](01_core_foundation_replacement.md)与
  [Goal 02](02_durable_task_engine.md)均已实际执行
- 规模：XL
- 风险：高
- 完成后进入：[Goal 04](04_system_service_recovery_vertical_slice.md)

## 给 Codex 的命令

你要把 Goal 01 的稳定提交与当前未提交的 Goal 02 Durable Task Engine 候选整理为
一个可审查、可回退、可以继续开发的唯一基线。先完整保存当前工作树，再从 Goal 01
提交建立独立整合分支，按逻辑组取回 Goal 02 代码；不要在当前脏工作树中直接继续
修补，也不要把 Goal 01 与 Goal 02 当成两个无关分支执行普通 `git merge`。

Goal 02 本来就是基于 Goal 01 工作树产生的未提交差异。正确做法是：把当前差异保存
为不可丢失的候选快照，以 Goal 01 为干净祖先，在另一 worktree 中选择性重放持久
内核、兼容层、迁移和测试；被删除旧代码只有在等价证据成立后才从整合分支删除。

本阶段只处理代码整合、公共 contract、迁移、HTTP 兼容、提交和回退。不实现 Goal 04
的 Model Gateway、Secret、Machine State 或新系统能力。

## 项目总体思想

VibeOS 是本地 Linux Agent Runtime：Agent 接收用户目标，实质歧义先询问，优先
使用 API、CLI、D-Bus 和系统服务，必要时才使用 UI；任务可持续数小时并在重启后
恢复；完成必须由独立观察和证据判断。E2 是严格限定且可回滚的本地提权，E3 外部
承诺和不可逆后果逐动作由用户批准；秘密不进入 Agent、模型、日志或快照。

Goal 02 的 Durable Task Engine 方向继续保留：纯 transition、Task Store、lease/
fencing、timer、outbox、ActionProposal/Receipt/Evidence、reconciliation 和崩溃恢复
是后续产品的正确内核。但“新代码能运行”不等于“旧行为已安全替代”。本 Goal 以
Goal 01 的公共行为和数据为基线，以 Goal 02 的持久内核为目标实现，通过黑盒 contract
和真实调用者证据决定兼容、迁移和删除。

## 已确定的项目决策

以下决定已经由项目经理作出，Codex 不应再次停在无结论的二选一讨论；只有现场出现
与决定冲突的新事实时才请求用户：

1. **保留新内核，不恢复双内核**
   - Durable Task Engine 是唯一目标 Task Store；
   - 可以恢复薄 adapter、兼容 payload 或黑盒测试，不能恢复第二套生产 GoalLoop、
     ReviewStore、snapshot 或 shadow state。

2. **受控修正历史迁移**
   - 当前仓库为 `0.1.0`、无发布 tag，按未正式发布阶段处理；
   - 接受修改 `0001_core_foundation.py`，把 Goal 01 当时的 schema 冻结在迁移文件中；
   - 必须记录原提交、修改前后 SHA-256、理由和 ADR，并证明原 Goal 01 数据库升级与
     空数据库新建最终等价；
   - `0002`、`0003` 当前也引用可变 runtime `metadata`，必须在首次正式提交前改为
     自包含迁移。审计所有 revision，任何历史迁移都不得依赖会继续变化的应用 metadata；
   - Goal 03 合并后，已提交迁移不可再静默修改，只能增加后续 revision。

3. **保留薄 HTTP 兼容面**
   - Goal 01 的运维/VM 脚本、`VIBEOS_RUNTIME=http` 和 `/v1/*` contract 是已证明的
     仓库调用者，因此“无 HTTP 调用者”不成立；
   - D-Bus 仍是 Linux 本地主要控制面；HTTP 只绑定 loopback，调用同一 application
     service 和唯一 Task Store，无独立业务逻辑、线程式状态或审批存储；
   - 兼容范围至少核对 `/v1/status`、`/v1/command`、`/v1/apps`、`/v1/windows`、
     `/v1/capabilities`、`/v1/reviews/pending` 和 `/v1/audit/tail`；
   - 仓库脚本逐步迁移到 D-Bus，但 HTTP 标记 deprecated 后至少保留到 Goal 09 的
     正式交付决策。Goal 03 不获授权删除它。

4. **Goal 03 分为三个检查点**
   - 03A 冻结现状与清单；
   - 03B 在干净分支完成兼容和迁移整合；
   - 03C 整理提交、演练回退并在用户批准后合入 `main`。

## 预期进入状态与现场核对

2026-07-17 的审计快照只作为线索，执行时必须复核：

- `main`、`HEAD`、`origin/main` 指向 Goal 01 提交
  `a6d809ffb60a61c29380c04eebbbb134c7ddef9c`；
- 当前工作树约有 107 个状态项，73 个受跟踪文件差异，约 `+2591/-11505`；
- Goal 02 新增 Durable Task Engine，同时删除 GoalLoop、ReviewStore、legacy runtime、
  HTTP adapter 和大量旧测试；
- 当前完整测试为 `901 passed`，19 个 capability 可发现，但这只证明当前实现内部
  一致和路径接入，不证明 Goal 01 公共行为全部等价；
- `0001` 已被修改，`0002`、`0003` 仍引用可变共享 metadata；
- Goal 01 存在明确 HTTP 仓库调用者。

开始任何写操作前必须：

1. 输出当前分支、HEAD、远端基线、`git status --short`、diff stat 和未跟踪文件；
2. 检查是否有其他 Codex/进程修改同一工作树；有并发写入时停止并报告；
3. 区分 Goal 02、Goal 03 文档和可能的用户无关改动；归属不确定时先询问；
4. 重新运行完整测试和无副作用 smoke，记录真实结果，不引用旧文档数字代替；
5. 确认下面计划使用的分支名和临时 worktree 路径不存在；存在时不得覆盖、删除或
   强制复用，先报告并改用用户确认的名称。

## 核心目标

把当前未提交的 Goal 02 候选完整保全下来，以 Goal 01 的稳定提交
`a6d809f` 为干净基线，在独立 worktree 中按逻辑组重新整合，并用公共契约、黑盒行为、
迁移兼容性和回退演练证明新耐久内核可以安全承接旧内核。

本 Goal 的结果不是“把两个目录混在一起”，而是一条可审计的整合链：

```text
当前 Goal 02 脏工作树
  -> 不可变的未整合 checkpoint 分支

Goal 01 稳定提交 a6d809f
  -> 干净的 reconciliation worktree

checkpoint 中经过审查的差异
  -> 分组应用与独立提交
  -> 兼容性证据与回退演练
  -> 用户批准后 fast-forward 合入 main
```

完成后，仓库必须只保留一套 Durable Task Engine 作为任务、审批、澄清、恢复和证据的
权威状态机；Goal 01 的对外能力必须被等价承接，或有经过记录和验证的兼容/弃用决策。

## 分支与代码存放结构

整合期间保持以下拓扑：

```text
main / origin/main
  -> a6d809f Goal 01 基线，整合完成前不移动

codex/goal02-unreconciled
  -> 当前 Goal 02 + 当前规划文档的完整检查点，只用于保全和对照

codex/goal03-reconciliation
  -> 从 a6d809f 新建，在独立 worktree 中按逻辑组重新整合

detached Goal 01 worktree
  -> a6d809f 的只读运行/黑盒对照环境
```

所有临时 worktree、patch 和测试数据库放在系统临时目录或用户明确允许的工作目录，
不能放进源码树后误提交。Goal 01 已存在本地 Git 对象中，不需要 `git pull`；不得把
远端内容直接拉进脏工作树。只有本地缺少指定对象时，才在说明远端和精确 ref 后请求
网络批准执行 `git fetch`。

参考 Git 顺序如下；尖括号是执行时根据 03A manifest 和安全临时目录填入的参数，
不能直接原样运行：

```text
# 在当前主工作树中保存未提交候选；分支必须事先不存在
git switch -c codex/goal02-unreconciled
git add -- <manifest 中逐项审查通过的路径>
git diff --cached --name-status
git commit -m "checkpoint: preserve unreconciled Goal 02 candidate"

# 在源码树外创建两个隔离 worktree；a6d809f 已在本地，无需 pull
git worktree add --detach <safe-temp>/vibeos-goal01 a6d809f
git worktree add -b codex/goal03-reconciliation <safe-temp>/vibeos-goal03 a6d809f

# 每个逻辑组单独生成、审查、应用和提交；不要省略 -- <reviewed-paths>
git diff --binary --output=<safe-temp>/<group>.patch a6d809f codex/goal02-unreconciled -- <reviewed-paths>
git -C <safe-temp>/vibeos-goal03 apply --index <safe-temp>/<group>.patch
git -C <safe-temp>/vibeos-goal03 diff --cached --name-status
git -C <safe-temp>/vibeos-goal03 commit -m "<该逻辑组的准确说明>"
```

任何命令失败时先检查当前分支、worktree 和 index，不使用 force/reset/clean 让命令
“通过”。临时 patch 只用于搬运已审查差异，不是交付物；checkpoint branch 才是完整
Goal 02 候选的权威保全引用。

## 03A：冻结当前 Goal 02 候选

1. **从当前脏工作树创建保全分支**
   - 在不改变文件内容的情况下，从当前 `main` 创建 `codex/goal02-unreconciled`；
   - 不使用 `git reset --hard`、`git checkout --`、`git clean`、stash 后覆盖或删除文件；
   - 建立路径 manifest，逐项标记 modified/deleted/untracked、来源、用途和 owner；
   - 对未跟踪源码、迁移、测试和文档计算 SHA-256，防止遗漏。

2. **建立检查点提交**
   - 只按已审查 manifest 显式 stage，不用未经核对的 `git add -A`；
   - stage 后复核 cached name-status/stat，确认没有 secret、数据库、日志、缓存、构建
     产物或用户无关文件；
   - 创建一个明确标为“未整合候选、不可发布”的 checkpoint commit；
   - 记录 checkpoint commit ID。该分支在 Goal 03 完成后仍保留，直到用户明确删除。

3. **生成 03A 清单**
   - 公共入口：CLI、D-Bus、HTTP、Python、systemd、脚本；
   - 数据：Goal 01 schema、0001–0004、旧 pending review/clarification fixture；
   - 能力：19 个 capability 的输入、风险、结果、错误、receipt/evidence；
   - 删除：每个旧生产模块、测试和 adapter 的替代候选；
   - 输出 `equivalent / intentionally_changed / compatibility_missing /
     obsolete_with_evidence / unknown`，unknown 不得进入删除阶段。

03A 只完成保全和审计，不在检查点分支继续修代码。

## 从 Goal 01 取出旧代码

Goal 01 的权威来源是提交 `a6d809f`，不是网络上的最新 `main`，也不是从当前工作树
反向恢复文件。使用独立 detached worktree：

```text
git worktree add --detach <temporary-goal01-path> a6d809f
```

在该 worktree 中使用独立虚拟环境、临时数据库和测试目录运行 Goal 01 黑盒行为；
不得让它读写 Goal 03 数据库、Secret Service collection 或运行时状态。需要查看单个
旧文件时优先使用 `git show a6d809f:<path>`；不要把旧目录整体复制回新分支。

随后从 `a6d809f` 新建 `codex/goal03-reconciliation` 到另一独立 worktree：

```text
git worktree add -b codex/goal03-reconciliation <temporary-goal03-path> a6d809f
```

如果 `main` 或 `origin/main` 已不再是该祖先，停止并重新审计，不自动 rebase、force
push 或把新远端历史混入本 Goal。

## 03B：按逻辑组整合新旧代码

这不是一次普通分支 merge。以 `codex/goal02-unreconciled` 为来源，以 Goal 01 整合
worktree 为目标，按路径组生成和应用 patch；每组单独测试和提交。可以使用限定路径的
`git diff --binary a6d809f codex/goal02-unreconciled -- <reviewed-paths>` 生成临时 patch，
但应用前必须人工检查 name-status 和删除项。禁止一次应用整个 +2591/-11505 diff。

### 03B-1：持久内核和 schema（不删除旧代码）

- 取回 Durable Task domain、repository、worker/timer/outbox、lease、receipt/evidence、
  reconciliation 和必要依赖；
- 先保持 Goal 01 旧生产模块存在，确保新内核可单独测试；
- 根据已确定决策冻结 `0001` 的 Goal 01 schema，记录修改前后 hash 和 ADR；
- 把 `0002`、`0003` 及其他 revision 改为自包含定义，不 import runtime metadata；
- 验证三条数据库路径：空库到 head、真实 Goal 01 数据库到 head、故障中断后恢复；
- 比较最终表、列、索引、外键、约束、数据和 Alembic revision，不只比较命令退出码。

完成后提交独立的“durable kernel and frozen migrations”提交。

### 03B-2：公共兼容与薄 HTTP adapter

- 把 CLI、D-Bus 和 Python 公共请求接到同一个 Durable application service；
- 恢复 loopback-only HTTP compatibility adapter，所有 endpoint 只转换请求/响应并调用
  同一 service；禁止恢复独立 HTTP Task/Review 状态；
- `VIBEOS_RUNTIME=http`、auto/dBus/local 选择和历史错误行为进入 contract 测试；
- 迁移仓库内运维/VM 脚本到 D-Bus 时保留 HTTP 兼容测试和弃用说明；
- 对外字段或错误有意改变时，必须在矩阵中说明用户收益、迁移方式和批准记录。

完成后提交独立的“public compatibility adapters”提交。

### 03B-3：19 能力、审批、澄清和恢复等价

- 不能只测试“19 个 capability 进入新 task path”；
- 为每项能力固定有效输入、参数规范化、风险、dry-run、真实/不可用结果、错误、
  receipt、evidence 和兼容 projection；
- 至少真实运行一个 E0、一个 E1 和一个需要 review 的代表；环境不支持真实 adapter
  时明确记录，不以 fake 替代真实声明；
- review、clarification、approve/deny、补充输入、暂停、恢复、取消、接管、daemon
  重启和旧 pending 数据升级必须使用黑盒 contract 验证；
- Goal 01 有价值的测试迁移为新公共 contract 测试，不以测试函数总数作为目标。

完成后提交独立的“behavior compatibility and recovery”提交。

### 03B-4：逐项删除旧实现

- 只有 replacement matrix 为 `equivalent` 或经用户确认的 `intentionally_changed`，
  且新 contract 测试已通过的旧模块才能删除；
- 删除一个逻辑组后立即运行对应黑盒测试和依赖扫描；
- planner、observer、verifier、adapter 中仍有价值的逻辑通过 typed port 接入新内核，
  不因文件属于旧目录就删除；
- HTTP compatibility adapter、Goal 02 检查点分支和回退证据不在本阶段删除；
- unknown 或 compatibility_missing 项保留为有 owner 的薄兼容路径，不伪装完成。

完成后提交一个或多个范围清晰的“remove proven legacy path”提交，禁止巨大无说明删除。

## 03C：验证、回退和合入 main

1. **完整验证**
   - 完整 pytest、Ruff、format、strict mypy、architecture guard；
   - 19 capability contract、CLI/D-Bus/HTTP、旧数据升级和八个崩溃边界；
   - doctor、capabilities、provider/offline plan、dry-run、task controls、真实 session
     D-Bus，以及环境允许的代表性真实动作；
   - Goal 01 detached worktree 与整合 worktree 使用相同黑盒输入，对比规范化输出。

2. **回退演练**
   - `main` 仍保持 `a6d809f`，`codex/goal02-unreconciled` 保留原候选；
   - 记录每个逻辑提交的回退顺序、数据库兼容范围和不可直接 downgrade 的情况；
   - 在临时 clone/worktree 中演练从整合版本回到 Goal 01 代码并安全读取/导出数据；
   - 不使用强制移动共享分支作为产品回退方案；已经合入后的代码回退采用审计可见的
     revert 提交或部署切回已知 artifact。

3. **准备合入**
   - 整合分支工作树干净，提交按“内核/迁移、兼容、行为、删除、文档”可独立审查；
   - 更新当前架构、迁移、HTTP 弃用、运行和回退文档；
   - 输出最终 diff、commit list、验收证据和剩余兼容债务；
   - 若 `main` 自 03A 后发生变化，停止并请求重新整合，不自动 rebase/force push。

4. **最终合入**
   - 在全部硬验收满足后请求用户批准合入；
   - 批准后，从主工作树将 `main` fast-forward 到 `codex/goal03-reconciliation`；
   - 禁止 squash 掩盖逻辑提交，禁止 force push；
   - 合入后再次运行快速 smoke，保留 `codex/goal02-unreconciled` 和 Goal 01 基线引用，
     直到 Goal 04 稳定完成或用户明确清理。

最终命令只在用户批准、主工作树干净、`main` 仍指向已审计祖先且整合 worktree 已
通过全部验收时执行：

```text
git switch main
git merge --ff-only codex/goal03-reconciliation
```

若 `--ff-only` 拒绝，说明基线已变化；停止并重新审计，不能改用普通 merge、rebase
或 force 规避门禁。

## 明确非目标

- 不实现 Model Gateway、Secret Broker、Machine State、通用 Action Fabric 或新插件；
- 不恢复整个旧 GoalLoop/ReviewStore/AgentRuntime 作为生产 fallback；
- 不在当前脏 `main` 上继续堆修复后一次性提交；
- 不执行普通 `git merge` 把巨大 checkpoint 原样合入整合分支；
- 不从远端最新分支覆盖 Goal 01 基线，不用 reset/clean/force 丢弃工作；
- 不为了达到旧测试数量搬回私有实现测试；
- 不删除 HTTP compatibility、未知调用者路径或未经证明等价的旧行为。

## 验收条件

- [ ] `codex/goal02-unreconciled` 完整保存 03A 前所有已确认项目改动、未跟踪源码和文档；
- [ ] `main` 在整合期间保持 Goal 01 基线，Goal 01 detached worktree 可重复建立；
- [ ] `codex/goal03-reconciliation` 从 `a6d809f` 开始，Goal 02 代码按逻辑组而非整体
  巨型 patch 整合；
- [ ] replacement/compatibility matrix 覆盖所有删除模块、公共入口和 19 个能力；
- [ ] Durable Task Engine 是唯一 Task Store，没有第二任务内核或双写；
- [ ] `0001` 冻结决策有 ADR/hash，`0002`、`0003` 等迁移不再导入可变 runtime metadata；
- [ ] 空库、Goal 01 数据库和故障恢复升级路径最终 schema/data 等价；
- [ ] CLI、D-Bus、薄 HTTP adapter 和 `VIBEOS_RUNTIME=http` 通过公共 contract 测试；
- [ ] 19 capability 行为、review、clarification、用户控制和崩溃恢复通过；
- [ ] 所有删除项都有替代实现、黑盒证据和可回退提交，不存在“先删再证明”；
- [ ] 完整质量门禁通过，文档不夸大 WSL、mock、dry-run 或历史测试结果；
- [ ] 整合分支工作树干净、逻辑提交可审查、回退已演练；
- [ ] 只有用户批准后才 fast-forward 合入 `main`，没有 squash、force push 或历史丢失。

## 必交付物

- `codex/goal02-unreconciled` 检查点、路径/hash manifest 和 commit ID；
- Goal 01 detached worktree 与 `codex/goal03-reconciliation` 分支记录；
- replacement/compatibility matrix 和公共黑盒 contract 测试；
- 迁移冻结 ADR、0001 前后 hash、0002/0003 自包含实现和三路径数据库证据；
- loopback-only HTTP compatibility adapter、D-Bus 主路径和弃用计划；
- 19 能力、review/clarification、崩溃恢复及控制面验收证据；
- 分组提交列表、删除依据、回退演练和最终合入记录。

只有当前 Goal 02 候选被完整保存，Goal 01 行为与 Goal 02 持久内核在一个从干净基线
构建的可回退分支上同时成立，并经用户批准安全合入 `main` 后，才结束本 Goal。
