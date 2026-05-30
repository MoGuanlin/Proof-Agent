# Relaxed Agent · 修改计划汇总（前瞻 Roadmap）

> 日期：2026-05-30
> 本文汇总当前**仍有效**的修改计划，合并了历史 phase1 分期、记忆系统 review 的补强建议、以及取证审查的遗留项。
> 全局架构见 [../../PROJECT_OVERVIEW.md](../../PROJECT_OVERVIEW.md)；被取代的历史文档见 [../../archive/](../../archive/)。

---

## 一、已完成（截至 2026-05-30）

1. **Phase 1 S2 端到端调度链** —— Generator → Developer → Selector → 拼装/escalation 全链路，`--self-test` mock 通过，relaxed 27 测试绿。
2. **取证审查 P1/P2/P4 修复** —— 死方案永不 dead（stall 跨轮累积失效）、obstruction 恒空、generator `json.loads` 无守卫三处编排断线已修；信号 3 恢复。
3. **失败记忆（记忆 review 建议 #1）** —— 新增 [failures.py](../../../src/proof_agent/relaxed/failures.py) `FailureLedger`：reviewer/gate 的 REJECT 理由按 subgoal 指纹持久化、latest-wins、跨轮跨 session，Writer 起草前读、Selector 标 dead 时折进 obstruction。

---

## 二、下一步（按优先级）

### P0 · Phase 1 真跑 S2（最高优先，验证而非堆功能）

拿 Gemini API key 真跑，观察三个设计信号在**真实 LLM** 下是否出现：

```bash
proof-agent-relaxed --upload-files
proof-agent-relaxed --do S2 --rounds 3 --k 3 --n 2
```

- 若 S2 闭合 → 直接进 Phase 2。
- 若仍 blocked → 看 `escalations.jsonl` 的 `next_step`：`revise_contract`（契约太抽象）/ `supply_lemma`（缺外部引理，人工补 LemmaRecord）/ `switch_potential_function`（Φ 设计层问题）。
- 若三信号都不出现 → 瓶颈在模型能力，转工具层 / 换更强模型。

> 注意运行墙：见项目记忆里 Gemini proxy 30s idle wall、uuapi 传输墙（非流式 524、需 unset Clash proxy）等坑。

### P1 · Phase 2 —— 扩 S3 + 工具层 + 跨会话 Review

- 扩 **S3** 契约（[section6_contracts_review.md](../../section6_contracts_review.md) 标注"必须重写"）。
- 重新引入 `interval_region` / `numeric_region` **tool runner**（区间分支限界 + Piyavskii 搜索）：参考归档脚本 [deprecated/scripts/verify_section6_198.py](../../../deprecated/scripts/verify_section6_198.py) 的 `run_numeric_region_spec`(L2375) 与 `run_interval_region_spec`(L2865)。Phase 1 因 S2 是 ANALYTIC_CERTIFICATE_REQUIRED 未引入。
- **跨 session Review Agent**（Rethlas 风格停滞检测）：当前 stall 只在单 session 用 `rounds_without_progress` 计数。

### P2 · 记忆系统升级（记忆 review 建议 #2 / #3）

- **Ledger 升级为"可信事实层"**：从逐字指纹 → 语义级匹配（措辞一变就漏复用的问题），并给每条引理标注"已被 `interval_region`/Lean 验过"。
- **评估接入 `candidate_memory`**（71KB 代码、含相似失败/启发式/推导树表）：需**先解决下方 P5 导入根分裂**。

### P3 · 远期

- **S6 FunSearch 式常数 B 进化搜索**（PatternBoost 思路，构造极端链）。
- file API 泛化到非 google provider（OpenRouter / SiliconFlow，如其提供等价能力）。

---

## 三、已知遗留工程问题（待用户拍板）

**P5 · 三套导入根分裂**（取证审查指出，2026-05-23 重构埋下，非近期引入）：
- `pip install -e .` 装的是 `proof_agent`；
- `tests/` import `upper_bound_agent.src.proof_agent`；
- README 写 `PYTHONPATH=src`。

后果：4 个工具 CLI（memory / memory-web / convert-pdf / api-status）在 `pip install -e .` 后会 `ModuleNotFoundError: upper_bound_agent`；只有全用相对导入的 `proof-agent-relaxed` 能跑。

**需拍板 canonical 根**：要么全切 `proof_agent`（顺手修 4 个 CLI + 测试），要么保持 `upper_bound_agent` 并改 README 与 4 个 CLI。本任务仅记录，未修。
