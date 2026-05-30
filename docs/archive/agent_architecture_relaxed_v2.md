# Relaxed Agent v2 设计稿：多方案并行 + 共享子引理

> 状态：**设计稿，待 review，未动代码。**
> 目标读者：本项目维护者。
> 验证目标：在不改正确性门的前提下，让 agent 在 **S2**（当前 blocker）上从"单方案原地踏步"变成"多方案并行探索 + 复用已证子引理"，并把这套机制做成对 S3–S6 同样适用的通用骨架。
> 参考架构：北大 Rethlas/Archon（arXiv 2604.03789，A/B/C/D 多分解方案 + 跨会话记忆/Review）、DeepMind AlphaGeometry2（arXiv 2502.03544，SKEST 多搜索树共享已证事实）。
>
> **路径更新（2026-05-28）**：项目结构已重整。本文中对 `scripts/verify_section6_198.py` 的引用现指向 [../deprecated/scripts/verify_section6_198.py](../deprecated/scripts/verify_section6_198.py)；对 strict 主流水线模块（`research_system.py` / `reporting.py` / `cli/main.py`）的引用现指向 [../deprecated/src_strict/proof_agent/](../deprecated/src_strict/proof_agent/)。`src/proof_agent/` 现仅保留共享基建（`agents` / `app_config` / `paths` / `retry` / `verification_tools` / `candidate_memory` / `literature_rag` / `logging_setup` + 4 个工具 CLI）。

---

## 1. 为什么改这里

### 1.1 现状（单方案串行）

当前 relaxed 探索全部落在 [scripts/verify_section6_198.py](../scripts/verify_section6_198.py)：

- `Section6ProofAgent.develop_all_open` / `develop_obligation` 按 `REQUIRED_OBLIGATIONS` 顺序遍历。
- 对每条 obligation，`develop_single_obligation`（[L3788](../scripts/verify_section6_198.py#L3788)）**只生成一条证明路线**：一个 prompt → `write_obligation_with_review`（写稿 + correctness review 回路）→ `request_obligation_tool_requests`。
- 跨轮记忆是 `section6_198_exploration_summaries.jsonl`（`summarize_section6_exploration` 写、`load_section6_exploration_summary_context` 读回），本质是"把上一轮的 remaining_target / next_prompt_hint 喂回去让它接着写"。

**问题**：S2 已经跨多次运行（见 `artifacts/section6_198_s2_*` 一堆 packet/report）始终 `blocked`。单方案循环会反复在**同一条死路**上做表述微调，因为：

1. 一条 obligation 只探一种分解思路（例如 S2 一直在 `ΔP+ΔL ≤ 0` 的端点比较上打转）。
2. 没有"已证子引理"的概念——即使某轮证出了一个有用的局部不等式，下一轮也只是被 summary 转述，不能作为**已闭合的、可被其他方案直接引用**的资产。
3. 没有跨方案的事实共享，更没有"这条路线已死，换一条"的显式判断。

### 1.2 借来的两个机制

- **Rethlas 的 A/B/C/D**：对一个问题**同时**生成多个分解方案（Anderson 那条就是 A/B/C 失败、D 成功）。每个方案是一条独立的攻击路线，带各自的子目标和障碍。
- **AlphaGeometry2 的 SKEST**：多棵搜索树并行，**关键在于共享已证事实**——一棵树证出的引理，另一棵可以直接用。

把这两个合起来 = **每条 obligation 同时跑 K 条分解方案，所有方案共享一个"已证子引理账本"。**

---

## 2. 核心设计

### 2.1 三个新概念

| 概念 | 是什么 | 落到哪 |
|---|---|---|
| **Plan（分解方案）** | 对一条 obligation 的一条独立攻击路线：一组有序 subgoal + 障碍说明 + 用到哪些已证子引理 | 新数据结构 + `section6_198_plans.jsonl` |
| **Lemma Ledger（子引理账本）** | 跨方案、跨 obligation 共享的"已严格闭合的局部引理"池。每条引理带：陈述、证明证书（manual 或 tool）、闭合判定、可被谁引用 | 新数据结构 + `section6_198_lemma_ledger.jsonl` |
| **Plan Board（方案看板）** | 每条 obligation 当前活跃/失败/成功的方案集合，供调度和停滞检测 | 内存 + checkpoint |

### 2.2 改造后的 obligation 探索循环

```
对一条 obligation O（以 S2 为首个验证对象）:

  [1] Plan-Set Generator（新）
       输入: O 的契约 + Lemma Ledger 里 O 可用的已证引理 + 失败方案摘要
       输出: K 个分解方案 {P_1..P_K}，每个含
             - approach_name（如 "端点直接估计" / "前缀链归纳" / "ΔP 几何比较"）
             - subgoals: [g_1, g_2, ...]（有序局部引理）
             - uses_lemmas: [已账本引理 id]
             - known_obstruction: 这条路线已知的难点

  [2] Per-Plan Developer（复用现有 develop_single_obligation，加 plan 维度）
       对每个 P_i 并行:
         对 P_i 的每个 subgoal g_j:
           - 先查 Lemma Ledger: 若已有等价已证引理 → 直接引用，跳过
           - 否则 write_obligation_with_review 证 g_j（粒度 = 单个 subgoal，不是整条 O）
           - 若 g_j 闭合 → 写入 Lemma Ledger（其他方案立即可用）
         所有 subgoal 闭合 → P_i 标记 candidate_complete

  [3] Plan Selector / Merger（新）
       - 若某 P_i 的全部 subgoal 闭合，且拼起来满足 O 的 run_obligation 门 → O 闭合，选它做 manual_certificate
       - 若无方案整体闭合，但多个方案各证出了一部分 → 把已证 subgoal 全部沉淀进 Ledger，
         供下一轮 Plan-Set Generator 组合出新方案（cross-pollination）

  [4] Stall / Prune（新，轻量）
       - 一条方案的所有 subgoal 连续 N 轮无新增闭合 → 标记 dead，写明 obstruction
       - 一条 obligation 的所有活跃方案都 dead → 触发"换族"信号（回到契约层或 Φ 设计层）
```

**关键点**：第 [2] 步的 per-subgoal 证明**直接复用**现有 `write_obligation_with_review` + `request_obligation_tool_requests` + `run_obligation` 验证门——我们不动正确性判定，只是把"证明单位"从"整条 obligation"细化到"方案内的单个 subgoal"，并加一层方案调度和子引理共享。

### 2.3 Lemma Ledger 的闭合判定

子引理复用现有的门，不引入新的"可信度"概念：

- 一条 subgoal 引理被写入 Ledger 的条件 = 它自身通过 `run_obligation` 同款判定（manual_certificate 的 `## Verification Needs` 恰为 `None`，或带 passing 的 `interval_region`/`numeric_region` tool 证书）。
- 一条方案引用 Ledger 引理时，引理的陈述被注入 prompt 的 "可直接引用的已证引理" 块（类似现在的 `section6_dependency_summary`），但**附带它的证书指纹**，最终拼装 manual_certificate 时把被引用引理的证书一并带上，保证 `run_obligation` 仍能整体复核。

---

## 3. 数据结构（草案）

```python
@dataclass
class LemmaRecord:
    lemma_id: str            # 内容指纹 hash，去重用
    obligation_origin: str   # 首次在哪条 obligation 下被证出 (S2..S6)
    statement: str           # 引理陈述（归一化后）
    certificate: str         # manual_certificate 文本（Verification Needs=None）
    tool_requests: list      # 若靠工具闭合，附可重放 spec
    closed: bool             # run_obligation 同款门判定结果
    reusable_scope: list[str]  # 可被哪些 obligation 引用，默认全部

@dataclass
class PlanRecord:
    plan_id: str
    obligation_id: str       # S2..S6
    approach_name: str
    subgoals: list[str]      # 有序局部引理陈述
    uses_lemmas: list[str]   # 引用的 LemmaRecord.lemma_id
    subgoal_status: dict[str, str]  # subgoal -> closed/open/dead
    status: str              # active / complete / dead
    obstruction: str         # dead 时的原因
    rounds_without_progress: int
```

两个新 JSONL（与现有 `agent_memory.jsonl` / `exploration_summaries.jsonl` 并列，不动旧文件）：
- `artifacts/section6_198_lemma_ledger.jsonl`
- `artifacts/section6_198_plans.jsonl`

---

## 4. 函数级改造点（精确到现有符号）

**新增（不改签名的纯新增）**
- `generate_plan_set(packet, oid, ledger, dead_plans, *, k, ...)` → `list[PlanRecord]`。新 LLM 角色 `"Plan Set Generator"`（走 `_llm_json_agent`）。
- `develop_plan(packet, plan, ledger, *, context, ...)` → 更新后的 `PlanRecord` + 新增 `LemmaRecord` 列表。内部对每个 open subgoal 调用一个 `develop_single_obligation` 的 **subgoal 版**（见下）。
- `select_or_merge_plans(oid, plans, ledger) -> developed_obligation | None`：把 complete 方案拼成 obligation 的 manual_certificate；否则把已证 subgoal 沉淀进 ledger。
- `LemmaLedger` 读写工具（load/append/dedup-by-fingerprint/render-for-prompt）。

**改造（小幅）**
- `develop_single_obligation`（[L3788](../scripts/verify_section6_198.py#L3788)）抽出一个 `develop_proposition(packet, oid, target_statement, ledger, ...)`：把现在 prompt 里 "Develop obligation {oid}" 换成 "Prove this single subgoal: {target_statement}, you may cite these already-proved lemmas: {ledger snippet}"。整条 obligation 退化为"subgoal = 整条 claim"的特例，保证向后兼容。
- `Section6ProofAgent.develop_obligation`（[L3959](../scripts/verify_section6_198.py#L3959)）：在调用 `develop_single_obligation` 处，改为 `generate_plan_set → develop_plan(并行) → select_or_merge_plans`。
- `section6_dependency_summary`（[L909](../scripts/verify_section6_198.py#L909)）旁边加一个 `ledger_lemmas_for_prompt(oid, ledger)`，把可引用引理注入 prompt。

**完全不动**
- `run_obligation`（[L3290](../scripts/verify_section6_198.py#L3290)）、`constant_chain`（[L3510](../scripts/verify_section6_198.py#L3510)）、`build_report`、整个 `verify_inequality_interval` 区间证书引擎、`ANALYTIC_CERTIFICATE_REQUIRED` 等门集合。**正确性判定零改动**是本设计的硬约束。

**并行**：`develop_plan` 之间无数据依赖（共享 ledger 只读快照，写回在 join 后做去重合并），可用线程池或保守起见先串行跑、只保留"多方案 + 共享 ledger"的语义，确认有效后再并行化。

---

## 5. 用 S2 验证这套机制是否 work

S2 当前契约（[L263](../scripts/verify_section6_198.py#L263)）要求直接证端点 `Υ_O(u,x)<0`。预期 Plan-Set Generator 至少给出三条不同路线：

- **P1 端点直接三项估计**：把 `L_n(u,x)` 当正贡献，由 `|P_O(u,x)|−λ|D_O(u,x)|` 的 slack 吸收（契约本身建议的方向）。
- **P2 前缀链归纳**：用 `Υ_O(u,x)−Υ_{O'}(u,x)=ΔP+ΔL` 恒等式（`section6_s2_continuation_facts` 已备），把 strictness 落在前缀链归纳假设上。
- **P3 ΔP 几何比较**：分 `x=a_{n-1}` / `x=b_{n-1}` 两端点，各自几何比较 `P_O` 与 `P_{O'}`。

**判定本次重写成功的信号（与"证出 1.98"解耦）**：
1. 至少有一条 subgoal 进入 Lemma Ledger 且通过 `run_obligation` 同款门（证明"子引理闭合 + 复用"链路通了）。
2. 第 2 轮 Plan-Set Generator 能引用第 1 轮沉淀的引理组合出新方案（证明 cross-pollination 通了）。
3. 死方案被标记 + obstruction 写明，不再被重复尝试（证明停滞剪枝通了）。

即使 S2 最终仍没整体闭合，只要上面三个信号出现，就说明**架构改动有效**，再推 S3–S6 才有意义；若三个信号都不出现，说明瓶颈在模型证明能力本身，应转向 RAG/工具层或换更强模型，而不是继续堆调度。

---

## 6. 非目标 / 分期

- **本期不做**：跨会话 Review/停滞检测 agent（Rethlas 记忆系统那条，列为第二期）；S6 的 FunSearch 式常数 B 进化搜索（第三期）；Matlas 级语义检索升级（独立工作项）。
- **本期不碰**：`src/proof_agent/*` 主流水线和 strict N1–Q6 路径，改动全部隔离在 verify 脚本 + 新 JSONL。
- **本期不改**：任何正确性门、常数链、区间证书引擎。

---

## 7. 待确认问题

1. 并行度 K 取几（建议先 3）？并行执行先用线程池还是先串行跑通语义？
2. Lemma Ledger 的"引理等价/去重"——先用陈述文本指纹（保守、可能漏合并），还是上轻量语义匹配？
3. dead 判定的 N（连续无进展轮数）取几（建议 2）？
4. "所有方案 dead → 换族"信号，本期是只记录并停，还是直接回调到 Φ/契约设计层？
