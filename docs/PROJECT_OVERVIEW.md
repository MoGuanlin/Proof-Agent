# Proof Agent · 全局总览

> 本文是项目当前**唯一的全局文档**，以**代码现状为准**汇总而成（覆盖了旧设计稿/计划/审查报告未反映的最新代码）。
> 历史文档（设计稿、phase1 计划/实施、两份审查报告）已归档到 [archive/](archive/)，仅供回溯。
> 修改计划（前瞻 roadmap）见 [plans/20260530/consolidated_roadmap.md](plans/20260530/consolidated_roadmap.md)。
> 最后更新：2026-05-30。

---

## 1. 项目目标与现状

- **大目标**：构建 agent 系统，寻找 Delaunay triangulation stretch factor 更低的**上界**。主论文 [Xia 2013](../data/papers/primary/1103.4361v2.pdf)，原始结果 ρ<1.998。
- **当前里程碑**：先证出 Xia 在论文 **Section 6 Conclusions** 中一句话提到的 **ρ ≤ 1.98** —— 把势函数换成 Φ_O(u,v) = η·L_n(u,v)（线段 uv 落在最末盘 O_n 内那段长度）。
- **路线转向**：项目已从 **strict**（N1–Q6 候选-命题循环，单方案串行）转向 **relaxed agent** 架构。strict 主流水线与单方案 `verify_section6_198.py` 已归档到 [../deprecated/](../deprecated/)。
- **当前阶段**：relaxed **Phase 1**，验证对象 = **S2**（六条 obligation S1–S6 中当前的 blocker）。
- **测试现状**：`PYTHONPATH=src python -m pytest tests/` → **52 passed, 8 skipped**（8 skipped 是 strict 主流水线归档后由 `@unittest.skipIf` 自动跳过的 `ResearchSystemRegressionTests`）。

---

## 2. 1.98 的数学背景（为什么"换成一个长度"反而更紧）

- 旧势函数 Φ 在极端链上"帮倒忙"；新 Φ = L_n(u,v)（落在末盘 O_n 内的段长）**自带正号**，把这个正贡献传到最终公式上，使常数链收紧。
- 关键常数自洽：λ = 643/250 = 2.572，B = 3/10 → **λ/(1+B) = 1.97846 < 1.98** ✓；基例需 λ > π/2 + η = 2.5708 ✓（slack 0.0012，紧但成立）。
- S1–S6 六条 obligation 的契约文案、当前 blocker、以及 S2/S3/S6 需重写之处，详见 [section6_contracts_review.md](section6_contracts_review.md)（仍是活动参考）。

---

## 3. relaxed agent 架构（按真实代码）

借鉴 Rethlas/Archon "A/B/C/D 多分解方案 + 跨会话记忆"、AlphaGeometry2 SKEST "并行搜索树共享已证事实"：**每条 obligation 同时跑 K 条结构不同方案，所有方案共享一个"已证子引理账本" + 一个"失败记忆账本"**。代码在 [../src/proof_agent/relaxed/](../src/proof_agent/relaxed/)。

### 3.1 模块职责

| 模块 | 角色 |
|---|---|
| [cli.py](../src/proof_agent/relaxed/cli.py) | 入口 `proof-agent-relaxed` + K 轮 obligation loop 编排；`--self-test` mock 自检 |
| [generator.py](../src/proof_agent/relaxed/generator.py) | Plan Set Generator：生成 K 条结构不同方案 + LLM-judge 两两去重（最多 3 次重生） |
| [developer.py](../src/proof_agent/relaxed/developer.py) | Plan Developer：对每个 open subgoal 证明（ledger 短路 → Writer 起草 → gate → **Reviewer 语义复核** → 入 ledger / 写 failure） |
| [selector.py](../src/proof_agent/relaxed/selector.py) | 状态推进（active/complete/dead）+ obligation 证书拼装 + 全死时 escalation |
| [ledger.py](../src/proof_agent/relaxed/ledger.py) | Lemma Ledger：跨方案/跨轮共享的"已证子引理白板"，scope 过滤 + prompt 渲染 |
| [failures.py](../src/proof_agent/relaxed/failures.py) | **Failure Ledger：失败记忆**（Ledger 的对偶）。reviewer/gate 的 REJECT 理由按 subgoal 指纹持久化，latest-wins，跨轮跨 session |
| [records.py](../src/proof_agent/relaxed/records.py) | `LemmaRecord` / `PlanRecord` / `FailureRecord` 数据类 + JSONL 读写 + 保守指纹（**不**做 alpha-rename，防误合并） |
| [obligations.py](../src/proof_agent/relaxed/obligations.py) | 零拷贝 gate verdict（六段式 manual_certificate + Verification Needs 收敛 None + 无 pending tool_requests）+ S2 契约 |
| [files_cache.py](../src/proof_agent/relaxed/files_cache.py) | Gemini Files API resumable 上传 + `.cache/llm_files_index.json` TTL 缓存（仅 google provider） |
| [literature.py](../src/proof_agent/relaxed/literature.py) | 非 google provider 的本地 Qdrant+Voyage RAG fallback，失败自动降级为空 |

### 3.2 一轮 obligation loop 的控制流（`cli.py::run_obligation_loop`）

```
启动：加载 Lemma Ledger + Failure Ledger + Plan Board（JSONL）+ 文献 packet
每轮 round:
  A. GENERATE  补足活跃方案到 K 条（Generator + judge 去重；用死路 obstruction 喂回，规避旧死路）
  B. DEVELOP   对每条活跃方案的每个 open subgoal:
                 查 Ledger 等价引理→命中即引用跳过（cross-pollination，轮内即时）
                 否则 Writer 起草（读 Failure Ledger 里这条 subgoal 的历史反对，最多 1 次 revision）
                 过 gate（结构）→ 过 Reviewer（语义）
                 闭合→入 Lemma Ledger；被拒→写 Failure Ledger
  C. SELECT    全 subgoal 闭合→拼 obligation 证书→过验收门→成功返回
                 否则更新状态；连续 N 轮无进展→dead，把 open-subgoal 失败原因折进 obstruction
  D. ESCALATE  所有方案 dead→Escalator 提"改契约/补外部引理"建议，返回 deadlock
```

### 3.3 复用的上层组件

- [agents.py](../src/proof_agent/agents.py)：`BaseAgent`（LLM 交互、流式、tag 抽取、`attached_file_uris` Files API 注入）+ `ReviewerAgent`（语义正确性 `check()`）。relaxed 的 Generator/Judge/Writer/Reviewer/Escalator 都是它们的薄封装（不同 role + 温度）。
- [literature_rag.py](../src/proof_agent/literature_rag.py)：只读 Qdrant+Voyage 检索（relaxed 不重建索引）。
- `app_config` / `paths` / `retry` / `logging_setup`：基础设施。
- **未接入**：`candidate_memory.py`（strict 时代 SQLite 大记忆，relaxed 暂未复用，见 roadmap）。

---

## 4. 三个设计成功信号（与"证出 1.98"解耦）

判定架构改动是否有效的信号（出自旧设计稿 §5）：

| 信号 | 含义 | 现状 |
|---|---|---|
| 1 | 至少一条 subgoal 入 Lemma Ledger 且过同款 gate | ✅ 真 |
| 2 | 第 2 轮 Generator 能引用第 1 轮新引理组合新方案（cross-pollination） | ✅ 真（共享内存 ledger 跨轮注入 prompt） |
| 3 | 死方案标记 + obstruction 写明 + 下一轮规避 | ✅ 已修复（早期审查 P1/P2 曾断，现已随 `failures.py` + selector 接线落地） |

> 若三信号在**真实 LLM** 下仍不出现，瓶颈在模型证明能力本身，应转向工具层/换更强模型，而非继续堆调度。

---

## 5. 文件布局

```text
.
├── src/proof_agent/        # 共享基建（LLM 适配 / 配置 / 路径 / 验证 / 记忆 / RAG / 日志）+ 工具 CLI
│   └── relaxed/            # relaxed agent：Generator/Developer/Selector/Ledger/FailureLedger/编排 CLI + gate
├── docs/                   # PROJECT_OVERVIEW.md(本文) + section6_contracts_review.md + Rethlas_Archon_final.pptx
│   ├── archive/           # 已归档的历史设计稿 / 计划 / 审查报告
│   └── plans/<时间戳>/     # 按时间戳归档的修改 plan（当前：20260530/consolidated_roadmap.md）
├── data/papers/            # 主论文 + 参考文献 PDF
├── tests/                  # test_regressions.py(共享基建) + test_relaxed.py(relaxed)
├── deprecated/             # strict 阶段归档：src_strict / scripts(含 verify_section6_198.py) / 旧文档 / 历史运行产物 + runtime/tmp/run.log 等散落残余
└── artifacts/              # relaxed 运行产物（section6_198_*.jsonl / 证书 / reports）
```

---

## 6. 如何运行 / 测试

```bash
pip install -e .                                    # 注册 CLI
proof-agent-relaxed --self-test                     # 无密钥跑调度链自检
proof-agent-relaxed --upload-files                  # 上传 Xia 2013 + 参考文献到 Gemini Files API
proof-agent-relaxed --do S2 --rounds 3 --k 3 --n 2  # 跑 relaxed 主循环（Phase 1：S2）
PYTHONPATH=src python -m pytest tests/              # 52 passed, 8 skipped（8 跳过=strict 归档测试）
```

产物落在 `artifacts/`：`section6_198_lemma_ledger.jsonl` / `section6_198_plans.jsonl` / `section6_198_S2_obligation_certificate.json` /（全死时）`section6_198_escalations.jsonl`，Files API 缓存在 `.cache/llm_files_index.json`（48h TTL）。

> 已知工程坑：存在三套导入根分裂（pip 装 `proof_agent` vs 测试 `PYTHONPATH=src`），详见 roadmap 的遗留项。

---

## 7. 参考架构

- **Rethlas/Archon**（PKU 证伪 Anderson 定理）：A/B/C/D 多分解方案 + 跨会话记忆/Review Agent + Matlas 语义检索。借鉴点：多方案并行、失败记忆。
- **AlphaGeometry2 (SKEST)**：多搜索树并行**共享已证事实**。借鉴点：Lemma Ledger 跨方案共享。
- **FunSearch / PatternBoost**：程序/构造数据库的进化搜索。借鉴点：S6 常数 B 进化搜索（Phase 3 远期）。

原件：[Rethlas_Archon_final.pptx](Rethlas_Archon_final.pptx)。
