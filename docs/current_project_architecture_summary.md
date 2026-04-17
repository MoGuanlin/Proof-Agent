# 当前项目架构总结

本文总结的是**当前仓库中已经落地的实现架构**，不是只复述 `docs/agent_architecture.md` 中的理想化设计。阅读源码后，可以把本项目理解为一个围绕 Delaunay 三角剖分 stretch factor 上界改进而构建的、**候选势函数驱动的自治研究系统**。

---

## 1. 项目目标与当前定位

### 1.1 数学目标

项目聚焦的问题是：

- 在 Xia 2013 的原始几何框架、记号系统和证明骨架下，寻找新的势函数；
- 尝试把 Delaunay triangulation stretch factor 的上界继续压低；
- 重点不是复现论文，而是围绕六个关键性质 `N1/N2/N3/D4/Q5/Q6` 持续提出、筛选、修补候选势函数。

这一点在以下文档中是主线：

- `docs/potential_function_requirements.md`
- `docs/agent_architecture.md`

### 1.2 当前实现不是“通用数学 Agent 平台”，而是“为这个问题定制的研究流水线”

从 [`src/proof_agent/cli/main.py`](/remote-home/MoGuanlin/proof_agent/src/proof_agent/cli/main.py) 可以看出，当前主入口已经把目标问题写死为：

- 主论文默认是 `data/papers/primary/1103.4361v2.pdf`
- 目标不是停在 `1.98`
- 要求系统尽量往更小上界推进，并明确说明证明支撑与缺口

也就是说，当前项目更像一个**针对特定开放问题的实验性自治研究系统**，而不是接受任意数学任务的通用框架。

---

## 2. 仓库分层

根据 [`README.md`](/remote-home/MoGuanlin/proof_agent/README.md) 与 [`src/proof_agent/paths.py`](/remote-home/MoGuanlin/proof_agent/src/proof_agent/paths.py)，仓库按职责分成以下几层：

| 层 | 目录/模块 | 作用 |
|---|---|---|
| 核心源码层 | `src/proof_agent/` | 研究主流程、Agent 抽象、记忆库、RAG、验证工具 |
| 文档层 | `docs/` | 数学约束分析与目标架构设计 |
| 数据层 | `data/papers/` | 主论文与参考文献 PDF |
| 产物层 | `artifacts/` | 报告输出、prompt 快照 |
| 运行时层 | `runtime/` | 日志、PID、nohup 相关文件 |
| 缓存层 | `.cache/` | PDF 转 Markdown 缓存、RAG 向量库、候选记忆库 |

`paths.py` 在导入时就会调用 `ensure_project_dirs()`，自动确保这些目录存在。这意味着项目把“路径初始化”内置成了基础设施的一部分。

---

## 3. 当前系统的真实架构：Candidate-Centric 单进程自治流水线

### 3.1 一句话概括

当前实现的核心不是多进程协作，而是：

**一个 Python 进程 + 一组带不同 system prompt 的 LLM 角色 + 一个候选势函数为中心的串行探索流水线 + SQLite 记忆库 + RAG/验证工具。**

### 3.2 主体结构图

```text
CLI/main.py
    |
    v
AutonomousResearchSystem
    |
    +-- PDF -> Markdown 解析
    +-- 文献摘要 + LiteratureRAG 初始化
    +-- Agent 角色加载/固化
    |
    v
CandidateExplorationPipeline
    |
    +-- Orchestrator 生成方向(direction)
    +-- Potential Designer 设计候选势函数
    +-- Proof Planner 生成候选级证明计划
    +-- 对每个性质 N1..Q6:
    |      +-- Proof Planner 分解 proposition
    |      +-- Proof Writer 写局部证明草稿
    |      +-- 如有需要，生成 tool request
    |      +-- verification_tools 本地执行
    |      +-- 3 个 reviewer 审稿并触发修订
    |
    +-- 候选通过/剪枝
    +-- Orchestrator 决定继续探索 / 进入 proof refinement / 停止
    |
    v
最终报告综合输出
```

### 3.3 这不是“真正独立的多个 agent 进程”

当前代码中的各个“agent”都定义在 [`src/proof_agent/agents.py`](/remote-home/MoGuanlin/proof_agent/src/proof_agent/agents.py) 中，本质上是：

- 共享同一个 LLM provider 适配层；
- 通过不同 `system_role` 模拟不同角色；
- 串行调用，不是并发自治体；
- 中间状态都由主流程 Python 代码显式推进。

所以，项目的“多 agent”更准确地说是**多角色 prompt 编排**，而不是操作系统层面的多 Agent 执行框架。

---

## 4. 端到端执行流程

### 4.1 启动层

主入口是 [`src/proof_agent/cli/main.py`](/remote-home/MoGuanlin/proof_agent/src/proof_agent/cli/main.py)：

1. 读取环境配置与 API key。
2. 检查主论文 PDF 是否存在。
3. 创建 `AutonomousResearchSystem()`。
4. 调用 `system.execute(PDF_FILE, TARGET_GOAL)`。
5. 将最终报告写入 `artifacts/reports/`。

后台运行由 [`scripts/start_main.sh`](/remote-home/MoGuanlin/proof_agent/scripts/start_main.sh) 负责：

- 加载 `.env`
- 设置 `PYTHONPATH`
- 生成日志文件
- 写入 `runtime/pids/main.pid`
- 后台启动 `python -m proof_agent.cli.main`

### 4.2 文献准备层

`AutonomousResearchSystem.execute()` 会先进入文献准备阶段：

1. 使用 marker 解析 PDF 为 Markdown。
2. 将解析结果写入 `.cache/markitdown/`。
3. 从主论文 Markdown 构造摘要型 `literature_context`。
4. 加载额外 Markdown 文献并初始化 `LiteratureRAG`。

这里有两个重要特点：

- **摘要上下文**用于直接塞进 prompt；
- **RAG 检索片段**用于按 query 拉取更相关的文献段落。

### 4.3 角色加载层

`_customize_team()` 会加载并固化角色提示词：

- `PI Brain`：总控/方向决策
- `Potential Function Designer`
- `Proof Strategy Planner`
- `Proof Writer`
- `Correctness Checker`
- `Logic Auditor`
- `Global Adversary`

角色 prompt 会被保存到：

- `artifacts/prompt_snapshots/customized_prompts.snapshot.json`

这让每次运行时的角色配置可审计、可复现。

### 4.4 Candidate-centric 探索层

`CandidateExplorationPipeline` 是当前系统最核心的执行器。

它做的事情是：

1. 从记忆库恢复最近的 active 候选，支持中断续跑。
2. 若没有方向，就先让 orchestrator 给出当前搜索方向。
3. 在候选预算 `CANDIDATE_MAX_COUNT` 内循环：
   - 设计一个新候选；
   - 为该候选做候选级 proof planning；
   - 按顺序验证六个性质；
   - 候选通过后进入终态决策；
   - 若失败则剪枝，并更新下一轮方向。

### 4.5 性质验证层

对单个候选的单个性质，系统流程已经细化成：

1. `Proof Planner` 把该性质拆成 1 到 4 个 proposition。
2. `Proof Writer` 为每个 proposition 写六段式证明草稿：
   - `Assumptions`
   - `Claim`
   - `Derivation`
   - `Boundary Cases`
   - `Verification Needs`
   - `Conclusion`
3. 如果 `Verification Needs` 未闭合，系统要求生成可执行 tool request。
4. 本地 Python 工具执行验证。
5. `Correctness Checker`、`Logic Auditor`、`Global Adversary` 三轮审稿。
6. 若 reviewer 拒绝，则让 `Proof Writer` 基于反馈修订。
7. 达到最大轮数仍不通过，则该 proposition 失败，候选被剪枝。

这是当前实现相较于 `docs/agent_architecture.md` 最重要的工程化增强之一：**把“证明一个性质”细化成 proposition 级可审查闭环。**

### 4.6 候选终态与 proof refinement

当候选到达终态后，系统会：

- 生成 terminal report；
- 让 orchestrator 决定：
  - `continue_exploring`
  - `proof_refinement`
  - `stop`

如果候选适合继续深化，则进入 `_run_proof_refinement()`：

- 将已通过性质整合成更完整的候选证明包；
- 再次经过 reviewer 审核；
- 输出一个更适合人工接管的 proof package。

---

## 5. 核心模块职责

### 5.1 `research_system.py`：总导演

[`src/proof_agent/research_system.py`](/remote-home/MoGuanlin/proof_agent/src/proof_agent/research_system.py) 是绝对核心模块，负责：

- 运行阶段编排；
- prompt 上下文拼装；
- 候选探索循环；
- proposition 级 proof/review/tool 闭环；
- 终态决策与 proof refinement；
- 最终报告综合输出。

它实际上承担了“应用服务层 + 编排器 + 状态机”的职责。

### 5.2 `agents.py`：LLM 角色与输出约束

[`src/proof_agent/agents.py`](/remote-home/MoGuanlin/proof_agent/src/proof_agent/agents.py) 提供：

- `BaseAgent`
- `ReviewerAgent`
- 不同数学角色的预设实例

它做了三件关键事情：

1. 统一 Google / SiliconFlow / AI Hub Mixed 等 provider 调用方式。
2. 强制使用 XML-like tag 包裹结构化输出，例如：
   - `<CANDIDATE_JSON>`
   - `<PROOF_PLAN_JSON>`
   - `<REVIEW_RESULT>`
3. 在模型格式跑偏时做 fallback 修复：
   - JSON substring 恢复
   - reviewer verdict 恢复
   - 二次格式修复 prompt

换句话说，`agents.py` 是当前系统避免“LLM 输出失控”的第一层保险。

### 5.3 `candidate_memory.py`：结构化长期记忆

[`src/proof_agent/candidate_memory.py`](/remote-home/MoGuanlin/proof_agent/src/proof_agent/candidate_memory.py) 定义了：

- `CandidateRecord`
- `MemoryManager`

`CandidateRecord` 保存的不是简单字符串，而是一个完整研究对象，包括：

- 候选形式 `form`
- 派生来源 `derived_from`
- 六个性质的状态
- proposition 级状态
- tool request 状态
- artifact 文本
- exploration log
- terminal decision

`MemoryManager` 则负责：

- SQLite schema 初始化
- 从旧 jsonl 导入
- 最新快照与全量快照管理
- 候选、性质、proposition、tool request 的查询
- 相似失败模式、可复用 proposition 模板、tool request 模板总结

这使得系统具备了“从历史候选中学习”的基本能力。

### 5.4 `literature_rag.py`：文献检索层

[`src/proof_agent/literature_rag.py`](/remote-home/MoGuanlin/proof_agent/src/proof_agent/literature_rag.py) 负责：

- 将 Markdown 文献分块；
- 用 Voyage 的 contextualized embedding 编码；
- 写入 Qdrant；
- 检索后再用 Voyage rerank；
- 渲染成可直接塞进 prompt 的 snippets。

这里的检索逻辑是**向量检索 + rerank**，而不是简单关键词搜索。

### 5.5 `verification_tools.py`：本地验证引擎

[`src/proof_agent/verification_tools.py`](/remote-home/MoGuanlin/proof_agent/src/proof_agent/verification_tools.py) 提供两类工具：

- `numeric_1d`
- `symbolic_multivar`

其中：

- `numeric_1d` 支持 grid 和 Piyavskii/branch-bound 型验证；
- `symbolic_multivar` 基于 SymPy，支持化简、偏导、符号不等式与代入检查；
- 所有表达式都先经过 AST 安全校验，避免任意执行。

这部分是项目里真正执行“证明辅助计算”的本地算子层。

### 5.6 `app_config.py`：环境与超参数中心

[`src/proof_agent/app_config.py`](/remote-home/MoGuanlin/proof_agent/src/proof_agent/app_config.py) 管理：

- `.env` 加载
- provider/model 配置
- timeout
- RAG 参数
- marker 参数
- 候选预算
- review 轮数
- reviewer role overrides

可以把它理解为项目的**运行时控制面板**。

### 5.7 `reporting.py` 与 `paths.py`：工程基础设施

- [`src/proof_agent/reporting.py`](/remote-home/MoGuanlin/proof_agent/src/proof_agent/reporting.py)：负责报告文件命名，文件名里编码了时间、goal hash、模型、temperature、streaming、timeout 等运行元信息。
- [`src/proof_agent/paths.py`](/remote-home/MoGuanlin/proof_agent/src/proof_agent/paths.py)：统一所有路径，避免依赖 shell 当前目录。

---

## 6. 当前记忆库的数据模型

当前记忆库不是简单 KV，而是分层结构化 SQLite。核心表包括：

| 表 | 作用 |
|---|---|
| `candidate_snapshots` | 候选的全量历史快照 |
| `candidate_latest` | 每个候选的最新快照索引 |
| `property_states` | 六个性质的状态 |
| `proposition_states` | proposition 级状态 |
| `artifacts` | 各阶段产出的文本材料 |
| `tool_request_states` | 工具请求与验证结果 |

这个设计的关键价值是：

- 允许断点恢复；
- 允许查看 candidate 演化历史；
- 允许抽取“历史通过模板”和“历史失败模式”；
- 允许 web/CLI 管理工具直接查询。

---

## 7. Agent 角色分工

### 7.1 当前代码里的角色

| 角色 | 主要职责 |
|---|---|
| `PI Brain` | 给出搜索方向、终态决策、passed candidate 排名 |
| `Potential Function Designer` | 设计新的势函数候选 |
| `Proof Strategy Planner` | 做候选级和性质级计划 |
| `Proof Writer` | 写 proposition 证明草稿与修订稿 |
| `Correctness Checker` | 第一层局部正确性审查 |
| `Logic Auditor` | 第二层逻辑审查 |
| `Global Adversary` | 第三层全局一致性与过度宣称审查 |

### 7.2 相比概念稿的变化

与 `docs/agent_architecture.md` 相比，当前实现：

- 保留了 orchestrator / designer / planner / writer / checker 的主线；
- 但把单一 checker 扩展成了三重 reviewer；
- 把“证明一个性质”进一步落成了 proposition 级工作单元；
- 把 verifier 从“按需调用工具”具体化为可执行 JSON spec 协议。

---

## 8. 工具层与触发条件

### 8.1 当前支持的工具类型

当前主流程只允许一种 tool_name：

- `verification`

但它内部又分成两种 mode：

- `numeric_1d`
- `symbolic_multivar`

### 8.2 触发逻辑

触发逻辑不是 planner 直接执行，而是：

1. `Proof Writer` 先写出 proposition 草稿；
2. 草稿中的 `Verification Needs` 若不是 `None`；
3. 系统要求 `Proof Writer` 再输出 `TOOL_REQUESTS`；
4. 主流程本地执行工具；
5. 验证报告插回 proposition 草稿；
6. 再进入 reviewer 审查。

### 8.3 Q5 的特殊地位

当前代码里，`Q5` 是唯一被明确要求可以携带 proposition 级数值证书的性质：

- proposition planning 时，只有 `Q5` 可以 `requires_tool=true`
- 若 `Q5` 没有获得 `verified_pass` 的工具证书，候选会直接失败

这与文档中的判断一致：Q5 是最接近“需要程序证书”的环节。

---

## 9. 当前实现与设计文档的关系

### 9.1 `docs/agent_architecture.md` 更像目标架构

该文档描述的是理想中的三层：

- 元控制层
- 自主探索层
- 验证工具层

### 9.2 代码实现是这个目标架构的一个“工程化收缩版”

当前代码中的落地方式是：

| 设计文档概念 | 当前代码中的对应物 |
|---|---|
| Orchestrator | `PI Brain` prompt 角色 + `research_system.py` 控制逻辑 |
| Memory Manager | `MemoryManager` + SQLite |
| 势函数设计师 | `potential_designer` |
| 证明规划师 | `proof_planner` |
| 证明撰写者 | `proof_writer` |
| 正确性检查员 | `correctness_checker + logic_rev + global_rev` |
| 数值验证器/符号工具 | `verification_tools.py` |
| 领域文献 RAG | `literature_rag.py` |

### 9.3 最大差异

最大的实现差异有三点：

1. **当前是串行单进程，不是独立自治体并行协作。**
2. **当前增加了 proposition 级状态机，这比概念稿更细。**
3. **当前大量依赖 Python 侧的流程控制来约束 LLM，而不是把控制权放给 Agent 自主协商。**

---

## 10. 当前项目的工程特点

### 10.1 优点

- 研究状态可持久化，支持断点恢复。
- 每个候选的失败原因被结构化记录，而不是只留日志。
- proposition 级 review loop 比“整篇证明一次性审”更稳。
- Q5 的工具证书机制比较清晰，便于压制空泛推理。
- 有单独的 memory admin CLI 和 web UI，便于观察搜索树、日志与清理脏状态。

### 10.2 当前限制

- 角色虽多，但本质还是同一类 LLM prompt 切换，独立性有限。
- 主任务和主论文路径仍然硬编码，泛化能力弱。
- Memory 的“相似失败检索”主要靠 token overlap，不是向量化数学结构检索。
- proof correctness 仍主要依赖 LLM reviewer，而不是真正形式化证明系统。
- Q6 虽然加了“防止过度宣称”的约束，但本质上仍是语言模型辅助判断，不是机器证明。

---

## 11. 对当前项目架构的最终判断

当前项目已经不只是“调用几个 prompt 的脚本”，而是一个相对完整的研究型系统，具有以下鲜明特征：

- **问题导向**：专门围绕 Delaunay stretch-factor 上界改进。
- **候选驱动**：以 potential-function candidate 为核心状态对象。
- **分层清晰**：主流程、记忆、RAG、验证、运维工具基本分离。
- **工程化较强**：有缓存、日志、报告、SQLite、Web 管理面。
- **数学闭环明确**：设计候选 -> 分解性质 -> 写 proposition -> 工具验证 -> reviewer 审查 -> 剪枝或通过。

因此，如果用一句话概括当前架构：

> 这是一个以候选势函数为中心、由单进程编排的多角色 LLM 研究流水线，配合 SQLite 记忆库、文献 RAG 和本地数值/符号验证工具，专门用于探索并筛选 Delaunay 三角剖分 stretch factor 更低上界的证明路径。
