# Relaxed Agent Phase 1 实施计划

> 状态：已实施完成（详见 [relaxed_agent_phase1_implementation.md](relaxed_agent_phase1_implementation.md)）
> 日期：2026-05-28
> 设计稿前身：[agent_architecture_relaxed_v2.md](agent_architecture_relaxed_v2.md)
> 契约 review：[section6_contracts_review.md](section6_contracts_review.md)

## Context

当前 relaxed 探索全部跑在已归档的 [deprecated/scripts/verify_section6_198.py](../deprecated/scripts/verify_section6_198.py)：单方案串行，跨多次运行 S2 始终 `blocked`（[deprecated/artifacts/section6_198/](../deprecated/artifacts/section6_198/) 一堆失败 packet）。问题不在 gate（gate 是对的），在调度——一条 obligation 只探一条路线，没有跨方案的事实共享。

借鉴 Rethlas/Archon "A/B/C/D 多分解方案 + 跨会话记忆"（PKU 证伪 Anderson）、AG2 SKEST "并行搜索树共享已证事实"（DeepMind 几何），把"单方案死磕"重写成"K 条结构不同方案串行 + 共享 Lemma Ledger"。

直接 milestone：证出 Xia Section 6 的 ρ ≤ 1.98（Φ = η·L_n 段长度势函数）。Phase 1 的具体验证对象：S2（当前 blocker）。

## 架构核心（每条 obligation 一轮 4 步）

```
1. Plan Set Generator   → K 条结构不同方案 {P_1..P_K}
                          每条 = (approach_name, ordered subgoals,
                                   uses_lemmas, known_obstruction)
                          交付后过 LLM-judge 两两去重，重复则要求重生

2. Plan Developer (×K)  → 串行: 对每条方案的每个 open subgoal:
                          先查 Ledger 文本指纹→有等价引理→直接引用
                          否则 call_llm_tagged 走 gate
                          证出→入 Ledger（后续方案立即可见，cross-pollination）

3. Plan Selector/Merger → 有方案全闭合→拼成 obligation 的 manual_certificate
                          否则把已证 subgoal 沉淀进 Ledger，下一轮重组

4. Stall Detector       → 单方案 N 轮无新增闭合→dead + obstruction text
                          反喂 Generator，下一轮规避同条死路
                          所有方案 dead→上报 escalation + Generator 提出契约修改
```

**硬约束（verdict 语义不动，但用全新代码实现）**：闭合判定与 strict 一致 —— manual_certificate 的 `## Verification Needs` 必须收敛为 `None`，pending tool_requests 不算证据。引理入 Ledger 走同款 gate。

## 全部 12 项设计决策（敲定过程见对话三轮 AskUserQuestion）

| # | 项 | 决定 | 理由 |
|---|---|---|---|
| 1 | 方案多样性 | LLM 自由生成 K 条 + 后处理去重 | 接受 LLM 可能找到我们没预设的攻击路线 |
| 2 | subgoal 粒度 | 细粒度（一两行可独立陈述） | 最大化 Ledger 复用率 |
| 3 | 存储载体 | 新 JSONL，独立于 SQLite candidate_memory | 与 strict candidate model 解耦，grep-friendly |
| 4 | 跨 obligation 共享 scope | `LemmaRecord.reusable_scope: list[str]` 默认 `["ALL"]`，可显式收窄 | 防误用 + 默认不限制复用 |
| 5 | Plan 去重 | LLM-judge 两两比较，重复要求 Generator 重生 | 跨领域可移植，捕获结构等价 |
| 6 | Lemma equivalence | 陈述文本指纹（normalize 后 hash），保守优先 | **漏合并的代价是重证一次；误合并损正确性，不可接受** |
| 7 | K, N | K=3, N=2（v2 默认） | 后期可调 |
| 8 | Phase 1 范围 | 仅 S2 端到端 | 验证 v2 §5 三个设计成功信号 |
| 9 | K 条方案执行 | 串行（一条证完再轮下一条） | 保证 cross-pollination 在轮内即时生效；deterministic 易调试 |
| 10 | 文献接入 | **不接 RAG**；改用远端大模型 file API 把参考文献整批上传 | LLM 自主决定何时引用 |
| 11 | 入口形态 | 注册为 CLI（`proof-agent-relaxed`） | 与现有 4 个工具 CLI 风格一致 |
| 12 | 死路信号 | 上报 escalation + Generator 输出契约修改提议 | 不重试烧 token，把球踢回人手 |

## 关键工程依赖（决策 #10 触发）

决策 #10 把"文献检索"从架构里删了，但带来一个新的 LLM 适配层需求：[src/proof_agent/agents.py](../src/proof_agent/agents.py) 的 `BaseAgent.call_llm` / `call_llm_tagged` 当前不支持文件附件。

实现方式：
- Phase 1 锁定到 Google Gemini provider（`LLM_PROVIDER=google` 在 [app_config.py](../src/proof_agent/app_config.py) 已就绪），在 `BaseAgent.__init__` 加一个 `attached_file_uris=None` 参数，仅在 google 分支真实使用，其他 provider 使用此参数会显式 raise NotImplementedError。
- 文件 ID 复用：第一次启动时把 `data/papers/primary/1103.4361v2.pdf` + `data/papers/references/*.pdf` 全部上传一次，把返回的 file URIs 缓存到 `.cache/llm_files_index.json`，后续每次 LLM call 直接附带（不重传，省额度、省时间）。Gemini Files API 的 file 有 48 小时 TTL，缓存里带 `expires_at` 字段，过期自动重传。
- Phase 2 再考虑泛化到其他 provider。

## 数据结构

```python
@dataclass
class LemmaRecord:
    lemma_id: str               # normalize(statement) 的 sha256 前 16 位
    obligation_origin: str      # 首次证出的 obligation: "S2".."S6"
    statement: str              # 引理陈述（原文）
    statement_normalized: str   # 去空白 + 数学符号标准化（去重用，**不**做 alpha-rename）
    certificate: str            # manual_certificate 全文（passes gate）
    closed: bool                # 同款 gate 判定结果（必须 True 才入库）
    reusable_scope: list[str]   # ["ALL"] 默认；可收窄如 ["S2"] / ["S2","S3"]
    tool_requests: list[dict]   # 若靠工具闭合，附可重放 spec
    plan_id: str                # 哪条 plan 证出来的（traceability）
    subgoal_index: int          # 该 plan 中的位置
    created_at: str             # ISO timestamp

@dataclass
class PlanRecord:
    plan_id: str                # 如 "S2_round01_P1"
    obligation_id: str          # "S2".."S6"
    round_idx: int              # 第几轮 Generator 输出的
    approach_name: str          # LLM 自定义，如"端点直接三项估计"
    subgoals: list[str]         # 有序局部引理陈述
    uses_lemmas: list[str]      # 引用的 LemmaRecord.lemma_id
    subgoal_status: dict[str, str]   # subgoal_text -> "open"/"closed"/"dead"
    status: str                 # "active" / "complete" / "dead"
    obstruction: str            # dead 时的解释（反喂 Generator）
    rounds_without_progress: int
    created_at: str             # ISO timestamp
    updated_at: str             # ISO timestamp
```

落地：
- `artifacts/section6_198_plans.jsonl` — 每行一个 PlanRecord 状态快照（追加式；最新状态由 plan_id 去重取最后一条）
- `artifacts/section6_198_lemma_ledger.jsonl` — 每行一个 LemmaRecord（追加式 + 文本指纹去重）
- `artifacts/section6_198_escalations.jsonl` — 每行一次"全方案 dead"事件 + Generator 提出的契约修改建议
- `.cache/llm_files_index.json` — Files API 的 ID 缓存（带 expires_at TTL）

## 模块布局

新建子包 [src/proof_agent/relaxed/](../src/proof_agent/relaxed/)：

```
src/proof_agent/relaxed/
  __init__.py
  records.py        # LemmaRecord / PlanRecord dataclass + JSONL load/append/dedup
  ledger.py         # LemmaLedger 类: 查询、入库、scope 过滤、normalize+fingerprint
  obligations.py    # gate verdict + S2 契约文本（零拷贝重写）
  generator.py      # Plan Set Generator (新 LLM 角色 + LLM-judge 去重)
  developer.py      # Plan Developer: per-subgoal call_llm_tagged + ledger lookup/append
  selector.py       # Plan Selector/Merger + Stall Detector + escalation
  files_cache.py    # Gemini Files API 上传 + .cache/llm_files_index.json TTL 管理
  cli.py            # proof-agent-relaxed 入口
```

`agents.py` 的 `BaseAgent.__init__` 加 `attached_file_uris=None` 参数（最小侵入式扩展），仅在 google provider 分支真实使用，其他分支显式 raise。

## CLI

```bash
proof-agent-relaxed --do S2 --rounds 3 --k 3 --n 2
proof-agent-relaxed --do S2 --resume       # 从最新 plans/ledger 续跑
proof-agent-relaxed --upload-files          # 仅触发文件上传 + 缓存（首次或过期后）
proof-agent-relaxed --self-test             # LLM-mock 流程验证调度链路（无需 API key）
```

注册到 [pyproject.toml](../pyproject.toml) `[project.scripts]`：`proof-agent-relaxed = "proof_agent.relaxed.cli:main"`。

## 端口与复用清单

**从 [src/proof_agent](../src/proof_agent) 直接复用：**

- `agents.BaseAgent(system_role=..., temperature=..., attached_file_uris=...)`：新 LLM 角色注册入口（Plan Set Generator / Plan Judge / Proof Writer / Contract Escalation Agent）
- `agents.BaseAgent.call_llm_tagged(prompt, tag_name, content_hint, ...)`：JSON/tagged 输出统一入口（含 stream / 重试 / partial recovery）
- `app_config` / `paths` / `retry` / `logging_setup`：基础设施

**端口策略 = 零拷贝：** 不从 [deprecated/scripts/verify_section6_198.py](../deprecated/scripts/verify_section6_198.py) 拷贝代码，**只保留 verdict 语义不变**（manual_certificate 必须六段式 + Verification Needs 收敛为 None + 无 pending tool_requests）。在 [relaxed/obligations.py](../src/proof_agent/relaxed/obligations.py) 完全重写 ~200 行实现这套 verdict。

**为什么不端口：** 实测端口路径会拖入 OpenRouter web-search、`Section6JsonAgent`、`_call_json_agent`、`section6_property_context` / `section6_continuation_context` 等 strict 时代 ~500 行不适用代码（决策 #10 已删除 web-search 路径），代价大于收益。

**Phase 1 不引入工具运行器**：S2 是 `ANALYTIC_CERTIFICATE_REQUIRED`（manual_certificate 必须自闭合，不允许 pending tool_requests）。`run_*_region_spec` 等区间证书引擎要等 Phase 2 引入 S3+ 时再重新实现。

## 实施步骤

1. **Files API 适配层**：扩展 `agents.BaseAgent` 加 `attached_file_uris` 参数；新建 `relaxed/files_cache.py` 负责 Gemini 上传 + TTL 缓存。先写 `--upload-files` CLI 跑通文件上传链路。
2. **零拷贝重写 obligation gate**：在 `relaxed/obligations.py` 实现 verdict 函数 `gate_obligation()` + S2 契约文本（claim、forbidden_shortcuts、context_notes）。
3. **records + ledger**：实现 `LemmaRecord` / `PlanRecord` 数据类、JSONL append/load、保守 normalize+fingerprint 去重。
4. **Plan Set Generator**：新 LLM 角色，prompt 强制结构差异；LLM-judge 两两去重 sub-step（最多 3 次重生）。
5. **Plan Developer**：per-subgoal 调 `call_llm_tagged`，前置 ledger 查询，闭合后写 ledger（最多 1 次 revision per subgoal）。
6. **Selector + Stall + Escalation**：拼装 / 沉淀 / dead 标记 / 契约修改建议输出。
7. **CLI 编排**：`proof-agent-relaxed --do S2 --rounds 3` 串起来；包含 `--self-test`。
8. **回归测试**：`tests/test_relaxed.py` 用 LLM mock 验证调度链路（不依赖真实 LLM）。

## 验证（设计成功信号，与"证出 1.98"解耦）

照 [agent_architecture_relaxed_v2.md](agent_architecture_relaxed_v2.md) §5：
1. 至少一条 subgoal 进入 Lemma Ledger 且通过同款 gate。
2. 第 2 轮 Generator 能引用第 1 轮新引理组合出新方案（cross-pollination）。
3. 死方案 obstruction 写明，下一轮 Generator 不再重复同一条死路。

三个信号都不出现 → 瓶颈在模型证明能力本身，转向工具层 / 换更强模型，而非继续堆调度。

端到端验证命令：

```bash
# 准备：把参考文献上传到 Gemini Files API
proof-agent-relaxed --upload-files

# 跑 3 轮 S2，K=3 N=2
proof-agent-relaxed --do S2 --rounds 3 --k 3 --n 2

# 检查产物
cat artifacts/section6_198_plans.jsonl | jq '.approach_name, .subgoal_status'
cat artifacts/section6_198_lemma_ledger.jsonl | jq '.lemma_id, .obligation_origin'
cat artifacts/section6_198_escalations.jsonl 2>/dev/null
```

## 分期

- **Phase 1（本期）**：上述 8 步，仅 S2 端到端。
- **Phase 2**：Rethlas-style cross-session Review Agent；扩到 S3（重新引入 `interval_region` / `numeric_region` tool runner）。
- **Phase 3**：S6 FunSearch 式常数 B 进化搜索。
- **独立 track**：file API 泛化到 OpenRouter / SiliconFlow（如这两 provider 提供等价能力）。



