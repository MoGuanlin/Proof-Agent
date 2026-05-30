# Relaxed Agent Phase 1 实施总结

> 状态：实施完成
> 日期：2026-05-28
> 对应计划：[relaxed_agent_phase1_plan.md](relaxed_agent_phase1_plan.md)

## 总览

按照 [relaxed_agent_phase1_plan.md](relaxed_agent_phase1_plan.md) 完整实施 8 步，新建包 [src/proof_agent/relaxed/](../src/proof_agent/relaxed/)，**1683 行**实现 + **254 行**测试，覆盖 v2 设计稿 §5 三个成功信号的全链路。

## 文件清单

| 文件 | 行数 | 角色 |
|---|---|---|
| [src/proof_agent/relaxed/__init__.py](../src/proof_agent/relaxed/__init__.py) | 4 | 包标识 |
| [src/proof_agent/relaxed/obligations.py](../src/proof_agent/relaxed/obligations.py) | 204 | 零拷贝 gate verdict 语义 + S2 契约 |
| [src/proof_agent/relaxed/records.py](../src/proof_agent/relaxed/records.py) | 161 | LemmaRecord / PlanRecord + 保守 normalize（**不**做 alpha-rename，防误合并） |
| [src/proof_agent/relaxed/ledger.py](../src/proof_agent/relaxed/ledger.py) | 85 | 跨方案共享、scope 过滤、prompt 渲染 |
| [src/proof_agent/relaxed/generator.py](../src/proof_agent/relaxed/generator.py) | 250 | Plan Set Generator + LLM-judge 两两去重，最多 3 次重生 |
| [src/proof_agent/relaxed/developer.py](../src/proof_agent/relaxed/developer.py) | 192 | `prove_subgoal()` — ledger 短路 → call_llm_tagged → gate → 写 ledger |
| [src/proof_agent/relaxed/selector.py](../src/proof_agent/relaxed/selector.py) | 200 | 状态推进、obligation 拼装、escalation 上报 |
| [src/proof_agent/relaxed/files_cache.py](../src/proof_agent/relaxed/files_cache.py) | 251 | Gemini Files API resumable 上传 + TTL 缓存 |
| [src/proof_agent/relaxed/cli.py](../src/proof_agent/relaxed/cli.py) | 336 | `proof-agent-relaxed` 入口 + mock 自测 |
| [tests/test_relaxed.py](../tests/test_relaxed.py) | 254 | 15 个回归测试 |

CLI 注册到 [pyproject.toml](../pyproject.toml) `[project.scripts]` 第一行：
```toml
proof-agent-relaxed = "proof_agent.relaxed.cli:main"
```

## 测试结果

```
$ PYTHONPATH=. python -m pytest upper_bound_agent/tests/ -q
40 passed, 8 skipped, 1 warning in 12.81s
```

- 25 共享基建回归测试（`tests/test_regressions.py`）
- 15 新 relaxed 测试（`tests/test_relaxed.py`）
- 8 skipped 是 `ResearchSystemRegressionTests` —— strict 主流水线归档后由 `@unittest.skipIf` 守卫自动跳过，符合预期

`--self-test` 也通过：

```
$ PYTHONPATH=. python -m proof_agent.relaxed.cli --self-test
[self-test] OK: K=3 generator + dedup + per-subgoal proof + assembly chain works
```

## 期间作出的 2 次设计修正

### 修正 1：normalize 简化（防误合并优先）

v2 设计稿建议 normalize 步骤里把变量名重命名到 canonical（v1, v2, ...），实测会引入误合并：

```
"For all x in O_n, |P_O(u, x)| <= ..."  vs  "For all y in O_n, |P_O(u, y)| <= ..."
```

alpha-rename 后两者会撞同一指纹，但实际 x 和 y 在不同 plan 里可能代指不同对象（端点 a 还是 b）。

按决策 #6 的标准——**漏合并 OK，误合并不可接受**——把 normalize 简化为：符号映射（Unicode 数学符号 → ASCII） + lowercase + 折叠空白 + 去尾标点。**不做 alpha-rename**。漏合并的代价只是重证一次，证书还在 Ledger 里互不冲突。

### 修正 2：从 deprecated 端口策略改为零拷贝重写

原计划"从 [deprecated/scripts/verify_section6_198.py](../deprecated/scripts/verify_section6_198.py) 端口 ~700 行 gate / context loaders"。读完 4400 行的脚本后发现，**只**端口 `write_obligation_with_review` 一个函数就需要拖入 ~15 个间接依赖（`Section6JsonAgent`、`_call_json_agent`、`_llm_json_agent`、`section6_property_context`、`section6_continuation_context`、`extract_relevant_context`、`SOURCE_AGENT_REGISTRY`、`SECTION6_WEB_SEARCH_*` 七个变量、…），合计 ~1500 行。

这些间接依赖里有大块**和新架构正交**的代码：
- `Section6JsonAgent._headers_and_payload` 里的 OpenRouter web-search 分支 —— 决策 #10 已明确不走 RAG/web-search，走 Files API。
- `section6_property_context` / `section6_continuation_context` 是 obligation 粒度的 prompt 脚手架；新架构的粒度是 subgoal，两层 prompt 形状不同。
- 大量基于 packet json + RAG 注入的上下文加载器，新架构这些上下文应该让 LLM 通过 attached file URI 自己看 PDF。

经用户确认改为**零拷贝完全重写**：[obligations.py](../src/proof_agent/relaxed/obligations.py) 200 行实现 verdict 语义（与 strict 一致：六段式 manual_certificate + Verification Needs 收敛 None + 无 pending tool_requests），其余完全原生写。Phase 1 不引入 tool runner（S2 是 ANALYTIC_CERTIFICATE_REQUIRED，本就不允许 pending tool_requests），`run_*_region_spec` 等区间证书引擎留到 Phase 2 再实现。

## 8 步实施实际过程

| 步骤 | 状态 | 关键文件 | 备注 |
|---|---|---|---|
| 1. Files API 适配 | ✓ | `agents.py` (扩展 2 处) + `files_cache.py` | 仅 google 分支真实生效；其他 provider 显式 raise |
| 2. 零拷贝重写 gate | ✓ | `obligations.py` | S2 唯一契约；7/7 smoke test 通过 |
| 3. records + ledger | ✓ | `records.py`, `ledger.py` | 修正 normalize 后 6/6 smoke test 通过 |
| 4. Plan Set Generator | ✓ | `generator.py` | LLM-judge 两两比较，最多 3 次重生 |
| 5. Plan Developer | ✓ | `developer.py` | ledger 短路 + 至多 1 次 revision |
| 6. Selector + Stall + Escalation | ✓ | `selector.py` | escalator 输出契约修改提议 |
| 7. CLI 编排 | ✓ | `cli.py` | `--self-test` 通过 mock 验证全链路 |
| 8. 回归测试 | ✓ | `tests/test_relaxed.py` | 15 测试全过；总测试 40 passed / 8 skipped |

## 真跑一次需要的步骤

```bash
# 1. 注册 CLI
pip install -e .

# 2. 准备 .env：LLM_PROVIDER=google + GEMINI_API_KEY

# 3. 把 Xia 2013 + 参考文献上传到 Gemini Files API
proof-agent-relaxed --upload-files

# 4. 跑 S2 端到端
proof-agent-relaxed --do S2 --rounds 3 --k 3 --n 2
```

成功输出会落到：
- `artifacts/section6_198_S2_obligation_certificate.json`（拼装好的 manual_certificate）
- `artifacts/section6_198_lemma_ledger.jsonl`（共享子引理）
- `artifacts/section6_198_plans.jsonl`（每轮 plan 状态快照）
- `artifacts/section6_198_escalations.jsonl`（如果全方案 dead）
- `.cache/llm_files_index.json`（Files API ID 缓存，48h TTL）

## 修改的现有文件清单

只动了 2 处既有文件，避免 churn：

1. [src/proof_agent/agents.py](../src/proof_agent/agents.py) — `BaseAgent.__init__` 加 `attached_file_uris=None` 参数；`_headers_and_payload` 的 google 分支注入 `fileData` parts，其他分支若 `attached_file_uris` 非空则 raise NotImplementedError。
2. [pyproject.toml](../pyproject.toml) — `[project.scripts]` 第一行加 `proof-agent-relaxed = "proof_agent.relaxed.cli:main"`。

未触碰：`research_system.py` / `reporting.py` / `cli/main.py` 全部已归档；其他 src/proof_agent 模块不变。

## 验证：v2 设计稿 §5 三个成功信号目前状态

由于本次实施未消耗 Gemini API key 跑真 LLM，三个信号是通过 `--self-test` 的 mock LLM **结构性**验证，不是真实证明能力的验证。

| 信号 | mock 验证 | 真跑验证 |
|---|---|---|
| 1. 至少一条 subgoal 进入 Lemma Ledger 且通过同款 gate | ✓ | 待真跑 |
| 2. 第 2 轮 Generator 能引用第 1 轮新引理组合出新方案（cross-pollination） | 部分（self-test 只跑 1 轮） | 待真跑 |
| 3. 死方案 obstruction 写明，下一轮 Generator 不再重复同一条死路 | ✓（dead 状态机已测 `test_dead_status_after_n_stall_rounds`） | 待真跑 |

## 未做事项（明确不做，避免范围蔓延）

按计划属于 Phase 2/3，本期一律不做：

- **跨 session Review Agent**（Rethlas 风格的 stall detector 跨会话版）—— 本期只在单 session 内用 `rounds_without_progress` 计数，跨 session 的 Review 留 Phase 2。
- **S6 FunSearch 风格常数 B 进化搜索** —— Phase 3。
- **`interval_region` / `numeric_region` tool runner** —— S2 是 ANALYTIC_CERTIFICATE_REQUIRED 用不上。S3+ 需要时 Phase 2 实现，参考 [deprecated/scripts/verify_section6_198.py:2375](../deprecated/scripts/verify_section6_198.py#L2375) `run_numeric_region_spec` 与 [:2865](../deprecated/scripts/verify_section6_198.py#L2865) `run_interval_region_spec`（区间分支限界 + Piyavskii 搜索）。
- **K 条方案并行**（线程池） —— 决策 #9 选了串行；并行化要等串行版稳定后。
- **Files API 泛化到非 google provider** —— 决策 #10 (a) 项明确 Phase 1 仅支持 google，其他 provider 调用 attached_file_uris 时显式 raise。
- **在 candidate_memory SQLite 里加 plans/lemmas 表** —— 决策 #3 选了独立 JSONL，不耦合到 strict candidate model。

## 风险与已知限制

1. **Files API 上传未端到端测过**：[files_cache.py](../src/proof_agent/relaxed/files_cache.py) 的 resumable upload 协议是按 Gemini 文档拼的，单元测试只覆盖 cache 读写逻辑（`_entry_is_fresh` 等），实际上传需要真 API key 才能验证。第一次跑 `--upload-files` 时若上传协议有偏差会暴露。

2. **`agents.py` 的 `__all__` 还存在悬挂引用 `REVIEWER_ROLE_OVERRIDES`**（strict 阶段遗留，定义在 [app_config.py:268](../src/proof_agent/app_config.py#L268)，但 `agents.py` 没 import）。这是 Phase 0 已经存在的状况，本次未修复（surgical changes 原则），但若有人写 `from proof_agent.agents import REVIEWER_ROLE_OVERRIDES` 会失败。relaxed/* 全部走 `BaseAgent` 直接导入，不踩这个坑。

3. **保守 normalize 的代价**：`"... in O_n ..."` 与 `"... \\in O_n ..."` 这种 LaTeX-vs-ASCII 差异如果不在 `_SYMBOL_MAP` 里就漏合并。当前覆盖 ≤ ≥ ≠ · × Φ Υ λ η π θ ρ α β γ δ ∂ ∈ ∀ ∃ 等常用 Unicode 数学符号；其他 LaTeX 序列（`\\in`, `\\leq` 等）不映射 —— 漏合并的代价只是重证一次，可以接受。后期如果观察到大量本应合并的引理被重证，按需扩 `_SYMBOL_MAP`。

4. **Generator 去重失败 fallback 较弱**：3 次重生仍有重复时，当前只是放行；未来可考虑接 Stall Detector 的 obstruction 反馈机制。

5. **Phase 1 没有 tool runner**：S2 范围内 OK，但若 LLM 在 manual_certificate 里写了 `tool_requests`，gate 会判 `not closed`。这是设计意图（避免 LLM 用工具糊弄分析性 obligation），但 prompt 里已明确告诉 LLM 不要写 tool_requests。

## 下一步建议

按用户决策，这部分不在本任务范围内，只做选项铺陈：

1. **Phase 1 真跑一次**：拿 Gemini API key 跑 `--upload-files` 然后 `--do S2 --rounds 3 --k 3 --n 2`，观察 v2 §5 三个信号是否在真实 LLM 下出现。
2. **如果 S2 在第一次真跑就 closed**：直接进入 Phase 2 —— 加 S3 契约 + 重新引入 `run_*_region_spec` tool runner。
3. **如果 S2 真跑仍 blocked**：诊断瓶颈：
   - `escalations.jsonl` 给的 `next_step` 是 `revise_contract` —— 契约还需要松绑（v2 设计稿 §1.4 指出 S2 现 claim 已经是 Υ-端点形式，但真实 LLM 可能还是觉得太抽象）。
   - 或 `supply_lemma` —— 缺关键外部引理，由人手工补 LemmaRecord 进 ledger。
   - 或 `switch_potential_function` —— Φ 设计层有问题，回到 v2 设计稿 §1.4。
4. **诊断模型证明能力**：若所有 escalation 路径都不通，把 `attached_file_uris` 拉满（确认 PDF 都被 LLM 看到了）+ 换更强模型（gemini-3.1-pro vs gemini-2.5-flash），如果还是不行 —— 瓶颈在模型，不在调度。


