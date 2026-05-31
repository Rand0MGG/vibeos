# AAFS v1：交互生长式 Agent 文件检索系统设计草案

## 1. 背景与问题定义

在 Agent-First 操作系统或 Agent-Native Userland 的构想中，Agent 需要频繁访问、理解和操作用户系统中的文件。传统操作系统已经具备成熟的文件存储、权限、路径管理和命令行工具，但这些能力主要是面向人类用户和传统程序设计的。

现有 Coding Agent，例如 Claude Code、Codex 等，通常依赖 `ls`、`glob`、`grep`、`read`、`bash`、LSP、测试命令等工具在项目中“边找边读”。这种方式有效，但存在几个问题：

1. 每次任务都需要重新探索项目或文件系统。
2. Agent 对用户文件的长期语义记忆很弱。
3. 传统搜索结果往往只是孤立文件路径，缺乏“为什么这个文件是用户要找的文件”的解释。
4. 用户心中对文件的称呼，往往不同于真实文件名。
5. 文件的实际意义经常来自使用场景，而不是文件内容本身。

因此，AAFS v1 的目标不是替代 Linux 文件系统，也不是重建完整代码关系图，而是在传统文件系统之上提供一层面向 Agent 的文件检索、语义标签和交互记忆系统。

---

## 2. 核心定位

AAFS v1 的完整定位是：

> AAFS v1 是运行在传统文件系统之上的交互生长式 Agent 文件检索层。它在冷启动时仅依赖文件名、路径、扩展名、基础元数据等非语义信息进行检索；随着用户与 Agent 的交互，系统在合适时机为文件追加语义标签、任务标签和用户确认标签，使文件检索从传统路径搜索逐步成长为面向用户语境的语义路由表。

AAFS v1 不试图直接理解系统中的所有文件，也不试图在第一版建立完整文件关系图。它首先解决一个更基础、更高频的问题：

> 当用户用模糊语言描述一个特定文件时，Agent 如何在整个可访问文件空间中找到最可能相关的候选文件？

---

## 3. 设计原则

### 3.1 保留传统文件系统与 GNU/Linux 用户态

AAFS 不替代 ext4、APFS、NTFS、Btrfs 等真实文件系统，也不替代 GNU 工具链、bash、systemd 或已有桌面搜索能力。它应作为现有系统之上的增强层存在。

推荐架构：

```text
Linux / Windows / macOS 原生文件系统
        ↓
传统 shell / GNU 工具 / 系统 API
        ↓
AAFS 文件检索与语义标签层
        ↓
Agent Runtime / Semantic Shell / Local Executor
```

### 3.2 先找入口文件，不急于理解完整关系

AAFS v1 的第一目标不是“理解整个项目”，而是“找到可能的入口文件”。文件之间的逻辑关系由 Agent 在读取候选文件后继续探索。

换句话说：

```text
AAFS 负责：找入口、排序、解释、记录标签
Agent 负责：读取文件、理解逻辑、继续搜索、验证修改
```

### 3.3 语义应从交互中生长，而不是冷启动时全盘生成

冷启动时，AAFS 只需要知道：

- 文件名
- 路径
- 扩展名
- 文件大小
- 修改时间
- 基础文件类型
- 部分可低成本提取的元数据

随着用户和 Agent 的互动，AAFS 再逐渐学习：

- 这个文件在用户语境里叫什么
- 它曾经被用于什么任务
- 用户是否确认过它的身份
- 它和哪些主题、项目、材料或工作流有关

### 3.4 不使用伪精确置信度，使用分层词条

AAFS v1 不建议为每个语义判断打 `confidence = 0.82` 这样的伪精确分数。更好的方式是将语义信息拆成不同来源的词条，并根据来源分配检索权重。

例如：

```text
用户确认词条       最高权重
文件名词条         高权重
内容证据词条       高权重
路径词条           中高权重
任务历史词条       中高权重
元数据词条         中权重
上下文推测词条     低权重
用户习惯推测词条   低权重
```

### 3.5 搜索结果必须可解释

AAFS 不应只返回路径和分数，而应返回命中证据：

```json
{
  "path": "/Documents/CityU/visa/bank/deposit_proof_2026.pdf",
  "matched_evidence": {
    "filename": ["deposit", "proof"],
    "path": ["CityU", "visa", "bank"],
    "content": ["Bank of China"],
    "user_confirmed": ["港城大签证存款证明"]
  },
  "why_ranked": "文件名、路径、内容和用户确认标签均命中"
}
```

---

## 4. AAFS v1 的非目标

为了保持第一版可落地，AAFS v1 明确不做以下事情：

1. 不重写操作系统内核。
2. 不替代真实文件系统。
3. 不替代 bash、GNU coreutils、systemd 等传统用户态组件。
4. 不构建完整代码调用图。
5. 不构建完整项目依赖图。
6. 不强依赖 LSP 或复杂静态分析。
7. 不对全盘文件做大模型摘要。
8. 不把向量数据库作为主检索路径。
9. 不假设 Agent 一开始就理解所有文件。
10. 不试图在第一版解决所有跨文件逻辑理解问题。

这些能力可以作为后续版本演进方向，但不应进入 v1 的核心路径。

---

## 5. 系统总体架构

AAFS v1 可以拆成五个核心模块：

```text
AAFS v1
├── 1. File Scanner / Watcher
│   ├── 初次扫描
│   ├── 文件新增监听
│   ├── 文件删除监听
│   ├── 文件修改监听
│   └── 文件移动/重命名检测
│
├── 2. Basic Indexer
│   ├── 文件名索引
│   ├── 路径索引
│   ├── 扩展名索引
│   ├── 文件类型索引
│   ├── 修改时间索引
│   └── 基础元数据索引
│
├── 3. Term Layer
│   ├── filename_terms
│   ├── path_terms
│   ├── metadata_terms
│   ├── content_terms
│   ├── task_history_terms
│   ├── user_confirmed_terms
│   ├── habit_inferred_terms
│   └── temporary_context_terms
│
├── 4. Search & Ranking
│   ├── 多字段检索
│   ├── 字段加权
│   ├── 候选合并
│   ├── Top-K 排序
│   └── 命中证据解释
│
└── 5. Agent Feedback Loop
    ├── 记录 Agent 使用过的文件
    ├── 记录任务与文件关系
    ├── 接收用户确认
    ├── 写入强语义标签
    └── 过期或降权弱推测标签
```

---

## 6. 冷启动阶段：基础检索能力

在用户尚未与 Agent 进行任何交互时，AAFS 不能依赖语义标签。此时系统应退化为一个高质量本地文件搜索器。

冷启动阶段可用字段包括：

```text
文件名
完整路径
目录名
扩展名
文件大小
修改时间
创建时间
MIME 类型
文件来源目录
低成本元数据
```

例如，用户说：

> 找一下我的简历照片。

冷启动时，AAFS 可能只能根据以下线索召回：

```text
photo
照片
头像
resume
简历
assets/photo
.png
.jpg
.jpeg
```

如果文件名是 `照片2.png`，路径是：

```text
E:/codex_project/简历/assets/photo/照片2.png
```

即使没有语义标签，仅凭路径和文件名也可以进入候选列表。

---

## 7. 交互生长阶段：语义标签如何产生

AAFS 的关键价值在于：它不是静态索引，而是会随着使用逐渐成长。

### 7.1 用户明确确认时

当用户说：

> 对，这个就是我简历里用的头像。

AAFS 应写入用户确认词条：

```text
简历头像
简历照片
resume headshot
profile photo
```

这类标签权重最高，长期保留。

### 7.2 Agent 成功完成任务时

如果 Agent 使用某个文件完成了任务，例如修改 `build-agent.mjs` 来生成 Agent 岗简历版本，AAFS 可以写入任务历史词条：

```text
Agent 简历构建脚本
RepoLens 简历版本
build script
resume generator
```

这类标签来自实际任务行为，权重较高，但低于用户明确确认。

### 7.3 文件位于强语义目录时

例如：

```text
/Documents/CityU/visa/bank/deposit_proof_2026.pdf
```

AAFS 可以从路径生成路径词条：

```text
CityU
visa
bank
deposit proof
港城大
签证
银行材料
存款证明
```

这些词条是路径证据，不等同于用户确认，但检索价值很高。

### 7.4 上下文推测时

如果用户最近一直在处理港城大签证材料，Agent 打开某个 PDF 后，可以添加低权重临时词条：

```text
可能与港城大签证有关
可能是留学材料
```

这类标签应有过期机制，避免长期污染索引。

---

## 8. 词条分层设计

AAFS v1 的核心数据不是单一摘要，而是分层词条。

推荐词条类型如下：

| 词条类型 | 来源 | 权重 | 是否长期保留 | 说明 |
| --- | --- | --- | --- | --- |
| user_confirmed_terms | 用户明确确认 | 最高 | 是 | 最可靠的语义来源 |
| filename_terms | 文件名 | 高 | 是 | 强物理证据 |
| path_terms | 目录路径 | 高 | 是 | 反映用户组织习惯 |
| content_terms | 可提取文本内容 | 高 | 是 | 来自文件内部证据 |
| metadata_terms | 文件元数据 | 中 | 是 | 包括类型、时间、作者、标题等 |
| task_history_terms | Agent 任务记录 | 中高 | 是 | 表示文件曾被用于某任务 |
| habit_inferred_terms | 用户存储习惯推断 | 低 | 可长期但低权重 | 作为弱排序信号 |
| temporary_context_terms | 最近上下文推断 | 最低 | 否 | 需要 TTL 过期 |

---

## 9. 搜索与排序机制

AAFS v1 的检索不应是单字段搜索，而应是多字段、多来源、多权重的候选生成与排序。

### 9.1 查询理解

用户查询：

> 找一下之前那个港城大签证用的存款证明。

Agent 或 AAFS Query Parser 可拆解为：

```json
{
  "entities": ["港城大", "CityU", "签证", "visa", "存款证明", "deposit proof"],
  "file_type_hint": ["pdf", "docx", "image"],
  "time_hint": "previous_or_recent",
  "domain_hint": ["留学", "银行材料", "签证材料"],
  "action": "locate_file"
}
```

### 9.2 多通道召回

AAFS 应并行搜索：

```text
文件名字段
路径字段
用户确认标签字段
任务历史字段
内容字段
元数据字段
上下文推测字段
```

### 9.3 字段加权

示例权重：

```text
user_confirmed_terms    10
filename_terms           8
content_terms            7
path_terms               6
task_history_terms       5
metadata_terms           3
habit_inferred_terms     1
temporary_context_terms  0.5
```

这些权重不是置信度，而是排序策略。

### 9.4 返回结果

AAFS 返回 Top-K 候选，而不是只返回一个结果。

示例：

```json
{
  "query": "港城大签证用的存款证明",
  "candidates": [
    {
      "path": "/Documents/CityU/visa/bank/deposit_proof_2026.pdf",
      "matched_evidence": {
        "filename": ["deposit", "proof"],
        "path": ["CityU", "visa", "bank"],
        "content": ["Bank of China", "deposit certificate"],
        "user_confirmed": ["港城大签证存款证明"]
      },
      "rank_reason": "文件名、路径、内容和用户确认词条均命中"
    },
    {
      "path": "/Downloads/proof.pdf",
      "matched_evidence": {
        "filename": ["proof"]
      },
      "rank_reason": "仅文件名弱命中"
    }
  ]
}
```

---

## 10. Agent 与 AAFS 的协作流程

AAFS v1 不负责完整代码逻辑理解。Agent 应通过逐步阅读和再次检索完成探索。

标准流程：

```text
用户任务
  ↓
Agent 将任务拆成搜索词
  ↓
AAFS 检索文件名 / 路径 / 标签 / 内容
  ↓
AAFS 返回 Top-K 候选文件和命中证据
  ↓
Agent 读取最相关文件
  ↓
Agent 从文件内容中提取新的关键词、函数名、配置名或路径线索
  ↓
Agent 再次调用 AAFS 搜索
  ↓
Agent 完成任务
  ↓
AAFS 记录任务使用过的文件和语义标签
```

例如：

用户说：

> 修复登录失败没有错误提示的问题。

AAFS 初次可能返回：

```text
src/pages/Login.tsx
src/api/auth.ts
src/components/Toast.tsx
tests/Login.test.tsx
```

Agent 读取 `Login.tsx` 后发现 `authApi.login`，再调用：

```text
AAFS.search("authApi login")
```

读取 `auth.ts` 后发现错误处理在 `request.ts`，再调用：

```text
AAFS.search("request interceptor error message")
```

这说明：AAFS 只负责高质量入口检索；跨文件逻辑关系由 Agent 在阅读中逐步展开。

---

## 11. 数据模型草案

### 11.1 files 表

```text
files
- file_id
- path
- normalized_path
- basename
- extension
- mime_type
- size
- created_at
- modified_at
- inode_or_file_key
- content_hash
- partial_hash
- workspace_id
- indexed_at
- deleted_at
```

### 11.2 file_terms 表

```text
file_terms
- term_id
- file_id
- term
- normalized_term
- source_type
- weight_class
- source_detail
- created_at
- expires_at
```

`source_type` 可选值：

```text
filename
path
metadata
content
user_confirmed
task_history
habit_inferred
temporary_context
```

`weight_class` 可选值：

```text
confirmed
strong_evidence
normal_evidence
weak_inference
temporary_inference
```

### 11.3 file_usage_events 表

```text
file_usage_events
- event_id
- file_id
- agent_id
- task_id
- action
- timestamp
- user_confirmed
- description
```

`action` 示例：

```text
searched
opened
read
edited
exported
sent
used_in_task
confirmed_by_user
```

### 11.4 tasks 表

```text
tasks
- task_id
- user_query
- normalized_query
- created_at
- completed_at
- summary
```

---

## 12. 文件身份追踪

AAFS v1 必须处理文件移动、重命名和修改。不能只用 path 作为文件身份。

应组合使用：

```text
路径 path
文件名 basename
inode 或平台文件 ID
文件大小 size
修改时间 mtime
完整 hash
partial hash
父目录上下文
```

典型处理：

```text
文件只移动或重命名，但内容 hash 不变：继承原有语义标签。
文件内容小幅修改：保留文件身份，更新内容索引。
文件内容大幅变化：保留部分历史，但降低内容相关标签权重。
文件复制：新文件继承弱标签，标记为 copy-derived。
文件删除：保留 tombstone 记录，避免历史任务断链。
```

---

## 13. 标签写入策略

AAFS 不应随意为文件添加大量语义标签。标签写入应发生在高价值时机。

### 13.1 强写入场景

```text
用户明确说“这就是某某文件”
用户从候选结果中选择了正确文件
Agent 成功使用文件完成任务
用户要求“记住这个文件”
```

### 13.2 弱写入场景

```text
文件多次在同一任务语境中被打开
文件位于强语义目录中
文件与最近上下文高度相关
Agent 读取后发现某些关键词
```

### 13.3 不应写入场景

```text
仅因为一次低相关搜索命中
仅因为文件名中有模糊词
仅因为模型主观猜测
无法解释来源的标签
涉及敏感内容且未获授权
```

---

## 14. 标签过期与晋升机制

AAFS 的语义标签应允许成长、过期和晋升。

```text
temporary_context_terms
    ↓ 多次使用
habit_inferred_terms
    ↓ 用户确认
user_confirmed_terms
```

例如：

1. Agent 推测某文件可能是签证材料，写入临时标签。
2. 用户之后多次在签证任务中打开该文件，标签晋升为习惯推断词条。
3. 用户明确说“对，这就是签证存款证明”，标签晋升为用户确认词条。

临时词条应设置 TTL，例如：

```text
1 天
7 天
30 天
```

长期标签则保留，除非用户删除或文件内容明显改变。

---

## 15. 与普通搜索引擎的关系

AAFS v1 的底层可以大量借鉴搜索引擎技术：

```text
倒排索引
BM25
字段权重
分词器
增量索引
snippet/highlight
Top-K 排序
```

但是 AAFS 不等同于普通搜索引擎。

普通搜索引擎目标：

```text
返回相关文档
```

AAFS 目标：

```text
返回 Agent 可使用的候选文件，并解释为什么这些文件可能是用户要找的文件。
```

更准确的定义：

```text
AAFS = 本地搜索引擎 + 交互语义标签 + 用户确认记忆 + Agent 检索接口
```

---

## 16. 推荐技术选型

### 16.1 MVP 推荐

```text
SQLite + FTS5
```

原因：

```text
轻量
嵌入式
单文件数据库
支持全文检索
支持 BM25
方便快速原型
适合单机低能耗系统
```

### 16.2 后续可选

```text
Tantivy
```

原因：

```text
Rust 生态
Lucene 风格
性能强
适合本地搜索引擎内核
```

### 16.3 暂不推荐第一版使用

```text
Elasticsearch
OpenSearch
大型向量数据库
全盘 Embedding 系统
```

原因：

```text
过重
资源消耗高
部署复杂
不符合低能耗单机目标
```

---

## 17. 示例：从冷启动到成长

### 17.1 初始状态

文件：

```text
E:/codex_project/简历/assets/photo/照片2.png
```

AAFS 初始知道：

```text
文件名：照片2.png
路径：E:/codex_project/简历/assets/photo/
类型：png
修改时间：...
```

此时用户搜索：

> 找我的简历照片。

AAFS 可能通过路径和文件名召回它，但排名不一定极高。

### 17.2 交互后

用户或 Agent 使用该文件处理简历头像问题，AAFS 记录任务词条：

```text
简历照片
头像
resume photo
assets/photo
```

如果用户确认：

> 对，这就是我要放进简历里的照片。

AAFS 写入用户确认词条：

```text
简历头像
正式简历照片
resume headshot
```

### 17.3 未来检索

用户之后说：

> 打开上次那个简历头像。

AAFS 可高权重命中该文件，并返回解释：

```text
命中用户确认词条：简历头像
命中路径：简历/assets/photo
命中历史任务：曾用于简历照片处理
```

---

## 18. 示例：代码项目中的使用方式

用户说：

> 帮我找 RepoLens 那个构建简历的文件。

AAFS 搜索：

```text
RepoLens
build
简历
agent
mjs
```

候选结果：

```text
E:/codex_project/简历/build-agent.mjs
```

命中证据：

```text
文件名命中：build-agent.mjs
路径命中：简历
任务历史命中：曾用于 Agent 岗简历版本构建
用户上下文命中：RepoLens 简历项目
```

AAFS 不需要知道这个文件和其他文件的完整关系。Agent 读取它之后，如果发现它引用了其他数据文件，再继续调用 AAFS 搜索即可。

---

## 19. 版本规划

### v1：交互生长式文件检索

核心能力：

```text
基础文件索引
多字段搜索
分层词条
用户确认标签
任务历史标签
检索结果解释
文件移动/重命名追踪
```

不做：

```text
完整关系图
复杂静态分析
全盘 AI 摘要
向量库主检索
```

### v2：内容提取增强

加入：

```text
PDF/docx 文本提取
图片 Exif
音频 ID3
压缩包目录读取
代码符号粗提取
```

### v3：项目模式

加入：

```text
项目 manifest
AGENTS.md 解析
README 摘要
Git 状态
简单 import 关系
测试文件匹配
```

### v4：关系扩展与上下文包

加入：

```text
轻量关系索引
入口文件邻域
上下文包生成
Agent 修改前影响范围提示
```

### v5：高级语义与个性化

加入：

```text
可选 embedding
长期用户文件习惯模型
多设备同步
跨应用文件来源记忆
```

---

## 20. 最终结论

AAFS v1 的关键不是“让系统一开始就理解所有文件”，而是建立一个可以随着用户交互逐渐成长的文件语义层。

它的核心闭环是：

```text
基础索引 → 搜索候选 → Agent 使用 → 用户确认 → 写入语义标签 → 未来更准检索
```

第一版不需要关系索引，也不需要复杂静态分析。它只需要稳定解决一个问题：

> 用户用模糊语言描述一个文件时，Agent 能否通过文件名、路径、元数据、历史任务和语义标签，把最可能的候选文件排到前几名，并解释为什么。

这就是 AAFS v1 最小但有价值的形态。

一句话总结：

> AAFS v1 是一个会成长的 Agent 文件检索层。它冷启动时只是本地搜索，使用过程中逐步学习用户文件语义，最终形成覆盖用户文件空间的、可解释的语义路由表。

