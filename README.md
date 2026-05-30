# Proof Agent

寻找 Delaunay triangulation stretch factor 更低上界的 agent 系统。主论文 [Xia 2013](data/papers/primary/1103.4361v2.pdf)，原始 ρ<1.998；当前目标：证出 Xia 在 Section 6 中提到的 ρ≤1.98。

## 当前状态

项目从 strict（N1–Q6 候选-命题循环）转向 relaxed agent 架构重写，借鉴 Rethlas/Archon（PKU Anderson 证伪）、AlphaGeometry2 等近期 agent-for-math 工作。

- 全局总览（架构 / 数学背景 / 运行）：[docs/PROJECT_OVERVIEW.md](docs/PROJECT_OVERVIEW.md)
- 修改计划（前瞻 roadmap）：[docs/plans/20260530/consolidated_roadmap.md](docs/plans/20260530/consolidated_roadmap.md)
- S1–S6 契约 review：[docs/section6_contracts_review.md](docs/section6_contracts_review.md)
- 参考架构：[docs/Rethlas_Archon_final.pptx](docs/Rethlas_Archon_final.pptx)
- 历史设计/计划/审查文档归档：[docs/archive/](docs/archive/)
- 历史代码归档（strict 主流水线 / verify 脚本 / 旧文档 / 历史运行产物）：[deprecated/](deprecated/)（见 [deprecated/README.md](deprecated/README.md)）

## Directory Layout

```text
.
├── src/proof_agent/    # 共享基建（LLM 适配 / 配置 / 路径 / 验证 / 记忆 / RAG / 日志）+ 工具类 CLI
│   └── relaxed/        # relaxed agent：Generator / Developer / Selector / 编排 CLI + 正确性门
├── docs/               # PROJECT_OVERVIEW.md(全局) + section6_contracts_review.md + archive/ + plans/<时间戳>/
├── data/papers/        # 主论文 + 参考文献 PDF
├── tests/              # test_regressions.py（共享基建）+ test_relaxed.py（relaxed agent）
├── deprecated/         # 归档目录（strict 阶段产物 + 散落历史残余 runtime/tmp/run.log）
└── artifacts/          # relaxed 运行产物（section6_198_*.jsonl / 证书 / reports）
```

## Install / Env

```bash
pip install -e .              # 基础
pip install -e '.[analysis]'  # numpy / sympy
pip install -e '.[rag]'       # Qdrant + Voyage
```

Python ≥ 3.11。`.env` 放仓库根；关键项：`LLM_PROVIDER`（`siliconflow` / `google` / `ai_hub_mixed` / `openrouter`）、`MODEL_NAME`、对应 provider 密钥、`REQUEST_TIMEOUT_SECONDS`、`PREFER_STREAMING`、`LITERATURE_RAG_*`，详见 [app_config.py](src/proof_agent/app_config.py)。

## 现可用 CLI

```bash
proof-agent-relaxed --self-test                    # 无密钥跑调度链自检（Generator→Developer→Selector→assembly）
proof-agent-relaxed --upload-files                 # 上传 Xia 2013 + 参考文献到 Gemini Files API
proof-agent-relaxed --do S2 --rounds 3 --k 3 --n 2 # 跑 relaxed 主循环（Phase 1：S2）
proof-agent-memory list --status active            # 候选列表（.cache/candidate_memory.sqlite）
proof-agent-memory-web --host 0.0.0.0 --port 8765  # 只读 Web 看板
proof-agent-convert-pdf --help                     # Marker PDF → Markdown
proof-agent-api-status --help                      # provider 可达性探测
```

strict 阶段的 `proof-agent-run` / `scripts/start_main.sh` / `scripts/export_candidates.py` / `scripts/verify_section6_198.py` 已全部归档到 [deprecated/](deprecated/)；`.cache/candidate_memory.sqlite` 中已有的候选可继续用上述 CLI 查看作历史参考。

## Test

```bash
PYTHONPATH=src python -m pytest tests/
```

## Notes

- 所有相对路径按仓库根解析（见 [paths.py](src/proof_agent/paths.py)）。
- `LiteratureRAG` 在依赖 / 密钥缺失时自动降级。
