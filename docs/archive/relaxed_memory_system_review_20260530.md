# Relaxed Agent 记忆系统评审：现状与对照参考论文的差距

> 日期：2026-05-30
> 范围：relaxed agent（`src/proof_agent/relaxed/`）当前记忆系统的通俗讲解，以及从"利于完整数学推导"角度、对照 PKU Anderson 证伪（Rethlas/Archon）、AlphaGeometry2、FunSearch/PatternBoost 的记忆设计做的差距评估。
> 说明：对 Rethlas/Archon（arXiv 2604.03789, 2026）的描述均来自仓库内 `docs/Rethlas_Archon_final.pptx` + reference 笔记 + 设计文档，非独立文献核实，细节以原文为准。

## 一、现在的记忆系统（通俗版）

把它想成一个科研小组的共享资源。relaxed agent 实际在用 4 个"记忆"，外加一个被锁起来没用的旧柜子：

| 记忆 | 通俗说 | 存什么 | 局限 |
|---|---|---|---|
| **Lemma Ledger**（`ledger.py`） | 「已证小结论白板」，谁证出一条小引理就贴上去，别的方案/别的轮次可直接引用 | 只贴**成功**的闭合引理 | 判"同不同"靠**逐字指纹**，措辞一变就当新的，认不出"其实是同一条" |
| **Plan board**（`plans.jsonl`） | 「项目看板」，每条攻击路线的状态 | 活/成/死、卡在哪些 subgoal、停滞几轮；死路留一句障碍 | 只留"死在哪几个 subgoal"，不留"为什么" |
| **Escalations**（`escalations.jsonl`） | 「求助信箱」，全死才写"契约得改/求外部引理" | 契约修订建议 | 至今从未触发（没全死过） |
| **Literature RAG**（`literature_rag.py`） | 「小型文献检索」，按语义找相关段落塞进 prompt | 只装了十几篇本地 PDF | 读一次就塞，与推导脱节 |
| ~~candidate_memory~~（`candidate_memory.py`） | strict 时代的「大记忆柜」：候选快照/命题状态/工具结果/相似失败/启发式/推导树 | 71KB 代码、45MB SQLite | relaxed **完全没接它**——搬新家时把旧书柜锁了没用 |

一句话：现在 relaxed 真正在用的记忆，本质是**「一块只记成功的引理白板 + 一个粗看板 + 十几篇 PDF 检索」**。

## 二、够好了吗？——不够

从"利于完整数学推导"看，对照参考论文的记忆设计，差距很具体：

| 维度 | 现在 | 论文怎么做 | 缺了它，长推导会怎样 |
|---|---|---|---|
| **成功事实复用** | Ledger 共享（有，但逐字指纹） | AlphaGeometry2 SKEST 共享 proven facts（符号引擎演绎闭包，语义级） | 换写法就漏复用 → 长链反复重证同一引理 |
| **失败记忆** | 几乎没有（只轮内修订 + 死方案一句话） | Rethlas「identify key failures」；Archon Review Agent 跨 session 测停滞 | 反复撞同一堵墙，学不到教训（最致命） |
| **外部知识** | 十几篇本地 PDF | Rethlas Matlas：1360 万条 arXiv 语义检索 | 想不起该用哪条已知定理/技巧 |
| **推理↔记忆交错** | 读一次、塞 prompt 前面 | AG2：符号引擎算出新事实 → 回喂 LM → 出下一步 | 不能"推一步、记一步、再推" |
| **可信事实层** | 无（只过结构门 / 单个 LLM reviewer） | Archon 在 Lean4 攒 verified lemmas | 记进白板的"事实"本身可能是错的 |
| **进化/构造记忆** | 无 | FunSearch / PatternBoost 的程序/构造数据库 | S6 找常数 B / 极端链时没"种群"可演化 |

设计文档（`agent_architecture_relaxed_v2.md` §6）自己也承认：跨会话 Review/停滞检测（Rethlas 记忆系统那条）= 第二期未做，S6 FunSearch 式进化 = 第三期。

**结论**：现在的记忆够"把调度跑通"，但只实现了 AlphaGeometry2"共享事实"的最粗糙版本（逐字白板）。撑起完整长推导的四样东西——失败记忆、语义级事实复用、推一步记一步的交错、可信(已验证)事实层——基本都缺。

## 三、要补什么（按对"完整推导"的价值排序）

1. **失败记忆 + 跨轮/跨 session 持久化**（对应 Archon Review Agent / Rethlas key-failures）。把 reviewer 的具体 REJECT 理由沉淀成持久失败记忆，跨轮喂回 generator/writer。性价比最高，且直接接住已有的 reviewer（现在反对意见用完即丢）。
2. **Ledger 升级为"可信事实层"**：语义级匹配（不再逐字）+ 给每条引理标注"已被 `interval_region`/Lean 验过"。把共享事实从"粗糙且可能错"变"可靠"。
3. **复用现成零件**：`candidate_memory` 已有相似失败、启发式、推导树等表，评估接进 relaxed（需先解决审计 P5 的导入根分裂）。
4. （远期）S6 的进化构造库（FunSearch / PatternBoost）。

最契合"完整数学推导"、又能接住上次 reviewer 工作的，是 **#1**。即：reviewer 每次 REJECT 时，把"哪条 subgoal、什么数学反对、属于哪条路线"写进一个持久失败账本，下一轮 generator/writer 显式读到"这些方向已被否决、原因是……"。
