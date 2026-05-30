# Deprecated Archive

本目录归档项目 strict 阶段的代码、脚本、文档与运行产物，**仅供参考与回溯**，不在主流程中使用。

- **归档时间**：2026-05-28
- **归档原因**：项目转向 relaxed agent 架构重写（设计稿见 [../docs/agent_architecture_relaxed_v2.md](../docs/agent_architecture_relaxed_v2.md)），借鉴 Rethlas/Archon、AlphaGeometry2 等近期 agent-for-math 工作的多方案并行 + 共享子引理思路，目标至少证出 Xia 在 Section 6 中提及的 1.98 上界。旧的 strict 主流水线（N1–Q6 候选-命题循环）与单方案 `verify_section6_198.py` 不再适合作为继续推进的载体。

## 目录结构

- `src_strict/proof_agent/` — strict 主流水线模块
  - `research_system.py`：N1–Q6 候选探索主循环
  - `reporting.py`：strict run 研究报告命名
  - `cli/main.py`：旧 `proof-agent-run` 入口（已从 `pyproject.toml` 移除）
- `scripts/` — 弃用脚本
  - `verify_section6_198.py`：单方案 S1–S6 验证器（S1 闭合，S2 一直 blocked，是本次重写的直接动机）
  - `export_candidates.py`：strict candidate 模型的 SQLite → Markdown 导出
  - `start_main.sh`：后台启动 `proof-agent-run` 的脚本
- `docs/` — 已被取代的设计 / 状态文档
  - `agent_architecture.md`：strict 架构原始稿（N1–Q6）
  - `agent_architecture_relaxed.md`：relaxed v1，已被 v2 取代
  - `current_project_architecture_summary.md`、`potential_function_requirements.md`：strict 阶段笔记
  - `proof_agent_status.pptx`：strict 阶段汇报
  - `1103.4361v2.pdf`：Xia 论文副本（权威拷贝在 [../data/papers/primary/1103.4361v2.pdf](../data/papers/primary/1103.4361v2.pdf)）
- `artifacts/` — strict 与 verify 运行产物
  - `section6_198/`：`verify_section6_198.py` 多次运行的 packet/report/log/agent_memory/exploration_summaries
  - `reports/`：strict 主流水线输出的 `research_output_*.md`（含 `candidates/` 导出）
  - `prompt_snapshots/`：strict 阶段角色 prompt 快照
- `tmp/make_status_pptx.py`：生成已弃用 `proof_agent_status.pptx` 的脚本

## 仍在主流程中保留的内容（不在本目录）

- `src/proof_agent/` 中除上面 3 个 strict-only 模块外的全部：`agents.py`、`app_config.py`、`paths.py`、`retry.py`、`verification_tools.py`、`candidate_memory.py`、`literature_rag.py`、`logging_setup.py`，以及 4 个工具类 CLI（`memory_admin`、`memory_admin_web`、`convert_pdf_to_markdown`、`test_api_status`）—— relaxed 重写要直接复用的 LLM 适配器 / 配置 / 路径 / 验证引擎 / 候选记忆 / 文献 RAG / 日志 / 周边工具。
- `tests/test_regressions.py`：只覆盖保留的共享基建，未涉及 strict 主流水线。
- `docs/agent_architecture_relaxed_v2.md`、`docs/section6_contracts_review.md`、`docs/Rethlas_Archon_final.pptx`：relaxed 重写的活动设计与参考资料。
- `data/papers/`：源论文（主论文 + 参考文献）。

## 关于运行归档代码

本目录代码原 import 路径形如 `from upper_bound_agent.src.proof_agent.X import …`。归档后这些路径未做修改，**直接执行会失败**——它们假定运行目录在仓库父目录、`PYTHONPATH` 指向 `src/`，且某些被引用的 strict 文件已被本目录副本取代。复现历史结果时，先把 `src_strict/proof_agent/` 中需要的模块拷回 `src/proof_agent/`，或调整 `PYTHONPATH` 让两套并存。
