# Relaxed v2 重写 · 取证式审查报告

> 日期：2026-05-29
> 范围：审查 2026-05-28 的 relaxed agent 重写（弃用 strict 主流水线 + `verify_section6_198.py`、按设计稿新建 `src/proof_agent/relaxed/`、改 `agents.py`/`pyproject.toml`、写两份 phase1 文档）。
> 动机：昨天的开发疑似部分使用了"降智 API"，需排查质量是否崩塌、并定位降质起点。
> 方法：mtime + git 重建时间线 → 逐条实跑核验 impl 文档的经验性主张 → 通读全部 9 个模块 + 测试 → fan out 20 个 agent 深审 + 对抗式复核 → 亲自复核关键结论（含逐行比对新 gate 与旧 `run_obligation`）。

---

## 0. 结论（要点）

- **没有发现"降智导致的质量崩塌"。** 昨天的工作整体质量偏高、文档诚实、代码可运行、测试是真的过。
- 设计稿 `agent_architecture_relaxed_v2.md` 与两份 phase1 文档**经得起核验**：对 4262 行旧脚本的行号引用**逐条准确**，文档里的测试断言**逐项属实**，数据结构与代码**逐字段一致**，连文档自曝的风险点都真实存在——这是**非降智**的特征。
- 真正的问题是**一小撮可定位、可修的工程缺陷**，集中在「最后写的编排代码（19:10–19:14）」与「昨天之前（05-23 重构）就埋下的导入混乱」，而非均匀散布的"到处是坑"。

## 1. 时间线 × 逐阶段质量

| 时间(05-28) | 产物 | 质量 | 备注 |
|---|---|---|---|
| 16:46–16:52 | deprecated/README、README、设计稿 v2(touch) | 高 | 弃用归档结构清晰、归因准确 |
| ~2h 空档 | — | — | 可能是换 session 的点，但下游无降质证据 |
| 19:00 | files_cache.py | 高 | Gemini resumable 协议拼得合理；返回 `(uri,mime)` 与 agents.py 解包契约对上 |
| 19:00 | agents.py 编辑 | 高 | diff 与文档完全一致，最小侵入 |
| 19:06–19:09 | obligations / ledger / records | 高 | gate 与 strict 语义等价；ledger/records 干净 |
| 19:10–19:14 | **generator / developer / selector / cli** | 中 | ← **所有真实缺陷集中在这里**（编排接线） |
| 19:14–19:16 | pyproject / test_relaxed | 高 | 15 个测试真过，CLI 注册正确 |
| 19:22–19:25 | phase1 plan / implementation | 高 | 主张全部可核验为真，含诚实的自我批评 |

> 注：旧脚本 / strict 模块 / 4 个工具 CLI / test_regressions 的 mtime 均为 **05-23 12:29**（rebase），属上一轮"Restructure project layout"，不是昨天产物。

## 2. 经验性核验：全部为真 ✅

- **测试**：`40 passed, 8 skipped`（需在仓库目录名为 `upper_bound_agent`、`PYTHONPATH=<父目录>` 的布局下跑——与 impl 文档一致）。
- **自测**：`[self-test] OK: K=3 generator + dedup + per-subgoal proof + assembly chain works`。
- **导入**：relaxed 包在 Py3.12 + `PYTHONPATH=src` 下干净导入，无 broken reference。
- **行号引用**：L909 / L2375 / L2865 / L3290 / L3510 / L3788 / L3959 **逐条命中**。
- **diff**：`agents.py` 与 `pyproject.toml` 改动与文档描述**逐字对上**。
- **gate 语义（自己逐行比对旧 `run_obligation`）**：S2 解析路径**等价**——两者都在 cert 缺失/过短/含占位符时拒绝、都要求 `## Verification Needs` 恰为 `None`、都把模型 status 升级为 closed（strict: `complete=True, status="verified"`；新 gate 同）。新 gate 对 `tool_requests` **更严格**（Phase 1 无 runner 一律拒），是保守而非放水。仅有的差异（占位符标记表略异、新 gate 也接受 `n/a`）**对 S2 无实质影响**。"正确性门不变"的硬约束成立。
- **常数自洽**：λ=643/250=2.572，B=3/10 → λ/(1+B)=**1.97846 < 1.98** ✓；基例需 λ>π/2+η=2.5708 ✓（slack 0.0012，紧但成立）。

## 3. 真实问题清单（按严重度 + 归属）

### 🔴 高 — 昨天晚段（19:10–19:14），最像"收尾接漏线"

**P1. stall / escalation 在默认 `--n 2` 下结构性失效**
位置：`cli.py:211-270` + `selector.py:36-60`。
每轮 `generate_plan_set` 都生成**全新** PlanRecord（`rounds_without_progress=0`、新 plan_id），单轮内最多 +1，而 dead 需 `>= 2`。所以**任何 plan 永不 dead → `if not active` 的 escalation 分支是死代码 → `dead_history` 恒空 → 第二轮 generator 拿不到任何"已死路线"反馈**。设计稿 §2.2[4] 要求"plan 跨 N 轮持续无进展才 dead"，但实现是"每轮重造 plan"，跨轮累积无法发生。**v2 §5 信号 3 名存实亡**。（`test_dead_status_after_n_stall_rounds` 是手动把计数器设成 2 测状态机；`run_obligation_loop` 本身**无任何测试**。）

**P2. `plan.obstruction` 永远是空串**
位置：`generator.py:246`。generator 的 prompt 向 LLM 要了 `known_obstruction`，但建 PlanRecord 时**写死 `obstruction=""`**、从不读取；selector 标 dead 时也不写。全仓库 `obstruction` **只被读、从不被写**。即使 P1 修好，反喂的也是空障碍——信号 3 的"写明障碍"半边同样失效。

### 🟡 中

**P3.** escalator prompt 让 LLM 读不存在/恒空的字段（`selector.py:128`）：要它读 `approach_summary`（PlanRecord 无此字段，生成后被丢）和 `obstruction`（恒空）。

**P4.** `generator.py` 的 `json.loads` 无 try/except（`generator.py:149,166`）：与**同一 19:11–19:12 窗口**的 `developer.py`、`selector.py` 不一致（那两个都包了 `try/except`）。畸形 LLM 输出会让整轮 crash。这处**包内不一致**是晚段最值得注意的"疑似降质"小信号。

**P5. 三套导入根分裂 —— 归属「预先存在（05-23），非昨天」**：`pip install` 装 `proof_agent`，测试 import `upper_bound_agent.src.proof_agent`，README 写 `PYTHONPATH=src`。后果：README "现可用 CLI" 宣称的 4 个命令（memory / memory-web / convert-pdf / api-status）在 `pip install -e .` 后**全部 `ModuleNotFoundError: upper_bound_agent`**；只有昨天新写、全用相对导入的 `proof-agent-relaxed` 能跑。这 4 个 CLI 与 test_regressions 的 mtime 都是 **05-23 12:29**，证明此混乱是 05-23 埋的，**昨天既没造成也没修**。

**P6. README 整体过时 —— 昨天（16:52，写在代码之前）**：没提 relaxed 子包、没提 `proof-agent-relaxed`、漏 test_relaxed.py、说 artifacts "当前为空"（实际有 13 个 git-tracked 文件待删）、文档的 pytest 命令在本检出会失败。属"写在代码之前的过时"，非降质。

### ⚪ 低 / nit

- `uses_lemmas` 被采集持久化却无人消费；cross-pollination 实际靠 LLM 读 ledger 块（设计 #10 本就如此，可接受）。
- `assemble_obligation_certificate` 拼出"六段套六段"的嵌套结构，靠 gate 宽松字符串匹配才过（`selector.py:63`）。
- `cli.py` 的 `_now_iso`、`generator.py` 的 `asdict`/`Any` 未使用。

## 4. 对"何时换成降智版"的判断

**找不到明确的质量悬崖。** 若硬要指认最弱的"疑似降智"信号，它们**全部落在 19:10–19:14 的编排层**（generator/selector/cli 的 stall 整合断裂 P1、obstruction 没接线 P2、generator 漏 json 守卫 P4）——但**同一时段也产出了高质量的 developer 和 gate**，早段模块（files_cache/obligations/ledger/records）与**全部文档**都干净。这更像"收尾阶段把编排的几根线接漏了 + 没测整合层"，**不像模型整体变笨**。预期的"弃用旧版/写新代码时一堆问题"**基本没有兑现**。

v2 §5 三个成功信号实测：**信号 1（子引理入账走同款 gate）= 真**；**信号 2（第二轮引用第一轮引理）= 真**（共享内存 ledger 跨轮注入 prompt）；**信号 3（死路标记 + 障碍 + 规避）= 断**（P1 + P2）。

## 5. 建议（未改任何代码）

修复工作量很小，优先级：

1. **P1 + P2（救回信号 3）**：同一处约 5–10 行接线——generator 保留 `known_obstruction`；让 plan 跨轮 carry（或 selector 标 dead 时写 obstruction）；把 stall 语义改成"跨轮累积"。这是恢复信号 3 的关键。
2. **P4（防整轮 crash）**：generator 的两处 `json.loads` 包 try/except，judge 解析失败默认 DIFFERENT。
3. **P6（README）**：补 relaxed 子包 / `proof-agent-relaxed` / test_relaxed.py，修正 pytest 命令。
4. **P5（导入根，需你拍板）**：定 canonical 根——要么全切 `proof_agent`（顺手修 4 个 CLI + 测试），要么保持 `upper_bound_agent` 并改 README 与 4 个 CLI。

> 本报告仅做排查与定位，未改动任何源码。

