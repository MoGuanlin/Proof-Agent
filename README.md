# Proof Agent

面向几何证明的自主研究代理：围绕一篇主论文维护一组候选潜在函数 / 改进路线，循环地"提出 → 规划 → 证明 → 复核 → 修剪"，把每一步的尝试、工具调用、复核意见、最终判定全部存入 SQLite，可随时续跑、复盘与可视化。

## Directory Layout

```text
.
├── src/proof_agent/
│   ├── cli/
│   │   ├── main.py                 # proof-agent-run 入口
│   │   ├── memory_admin.py         # CLI 版记忆库检视 / 删除
│   │   ├── memory_admin_web.py     # Web UI（只读看板）
│   │   ├── convert_pdf_to_markdown.py
│   │   └── test_api_status.py
│   ├── research_system.py          # 主循环：候选探索、续跑
│   ├── agents.py                   # 多 provider LLM 适配（Gemini / OpenAI-兼容）
│   ├── candidate_memory.py         # SQLite 快照 + 命名空间
│   ├── literature_rag.py           # 文献 RAG（Qdrant + Voyage 可选）
│   ├── verification_tools.py       # 数值 / 符号验证
│   ├── reporting.py                # 研究报告命名与落盘
│   ├── app_config.py               # 环境变量集中读取
│   └── paths.py                    # 项目路径常量
├── scripts/
│   ├── start_main.sh               # 后台启动 + 自动写日志
│   └── export_candidates.py        # 把 SQLite 导出成 Markdown 报告
├── data/papers/
│   ├── primary/                    # 主论文 PDF（默认 1103.4361v2.pdf）
│   └── references/                 # 参考文献 PDF
├── docs/                           # 架构与说明文档
├── artifacts/
│   ├── reports/                    # 历史研究输出（含 candidates/ 导出）
│   └── prompt_snapshots/           # 运行时导出的角色配置快照
├── runtime/
│   ├── logs/                       # start_main.sh 写入的日志
│   ├── nohup/                      # 旧 nohup 输出
│   └── pids/                       # main.pid
└── .cache/                         # Markdown / RAG / candidate_memory.sqlite
```

## Install

```bash
pip install -e .                           # 基础依赖
pip install -e '.[analysis]'               # 启用 numpy / sympy 数值验证
pip install -e '.[rag]'                    # 启用 Qdrant + Voyage 文献 RAG
```

Python ≥ 3.11。`.env` 放在项目根目录，`DOTENV_OVERRIDE=1` 时会覆盖 shell 环境变量。

## Environment (.env 关键项)

| 变量 | 作用 | 默认 |
|---|---|---|
| `LLM_PROVIDER` | `siliconflow` / `google` / `ai_hub_mixed` / `openrouter` | `siliconflow` |
| `MODEL_NAME` | 具体模型 ID，例如 `gemini-3.1-pro-preview` | — |
| `GEMINI_API_KEY` / `SILICONFLOW_API_KEY` / `OPENROUTER_API_KEY` / `AI_HUB_MIXED_MODEL_API_KEY` | 各 provider 密钥 | — |
| `REQUEST_TIMEOUT_SECONDS` | 单次 LLM 请求超时 | `3000` |
| `PREFER_STREAMING` | 是否优先走 SSE 流式 | `true` |
| `HTTP_PROXY` / `HTTPS_PROXY` | 代理 | — |
| `LITERATURE_RAG_*` | 文献切块、检索、embedding 模型等 | 见 [app_config.py](src/proof_agent/app_config.py) |
| `VOYAGE_API_RETRY_COUNT` / `VOYAGE_API_RETRY_BACKOFF_SECONDS` | Voyage embedding 重试 | `2` / `3s` |

## Run

**前台（终端直接看滚屏）：**

```bash
proof-agent-run
# 或
PYTHONPATH=src python -m proof_agent.cli.main
```

**后台 + 自动落盘日志（推荐）：**

```bash
bash scripts/start_main.sh
# 日志：runtime/logs/<MODEL>_<TIMESTAMP>.log
# PID ：runtime/pids/main.pid
# 追踪：tail -F runtime/logs/*.log
```

`start_main.sh` 会检查 PID 文件避免重复启动；要停止直接 `kill $(cat runtime/pids/main.pid)`。

## 断点续跑

启动时会读 `.cache/candidate_memory.sqlite`，若有 `status='active'` 的候选则从它的下一条未完成 property 继续（[research_system.py:100-168](src/proof_agent/research_system.py#L100-L168)），无需手动指定。**续跑锚点是 `memory_namespace = candidate::<prompt_snapshot_hash>`**——改了 prompt 快照会切到新命名空间，旧 active 候选不会被自动接上。

已知限制：
- snapshot 不是一次原子写完，进程在两次 commit 之间挂掉时续跑可能看到半状态；命题一旦标 `pass/fail` 不会回滚。
- **LLM 调用没有网络重试**（[agents.py](src/proof_agent/agents.py)）。只有两处应用层 retry：标签解析失败时 `max_format_attempts=3` 重新叫 LLM、Voyage embedding 2 次线性退避。`requests.ConnectionError / ReadTimeout / 429 / 5xx` 等异常会直接抛出到外层 `except`，当次探索被跳过但**不会重发请求**。网络不稳时建议用 `scripts/start_main.sh` 后台跑，异常后用同一命令续跑。

## 观察运行中的 agent

**1. Web 看板**（只读、轮询刷新）

```bash
proof-agent-memory-web --host 0.0.0.0 --port 8765
# 浏览器打开 http://127.0.0.1:8765
```

页面：
- `/`：候选列表、状态徽章、最新 snapshot 元信息
- `/search`：按派生关系展开候选树
- `/candidate?cid=…`：候选详情、`exploration_log` 时间线、每个 property 的 pass/fail/hypothesis、artifact 明细
- `/snapshot?sid=…`：snapshot 原始 JSON
- `/run?log=<file>`：从 `runtime/logs/` 里抽取 agent 事件行，3 秒整页 reload

看板是从 SQLite 读取，**只能看到已落盘的 snapshot**，正在写的 property / 正在跑的工具调用要等下一次 snapshot 才看得到。没有 SSE/WebSocket 推送。

**2. CLI 检视**

```bash
proof-agent-memory list --status active
proof-agent-memory show --candidate-id C6_distance_gap_correction
proof-agent-memory history --candidate-id C6_distance_gap_correction
proof-agent-memory delete-candidate --candidate-id C1_symmetric_coefficients --yes
```

**3. 导出 Markdown 报告**

把当前命名空间下所有候选导出成人类可读文件（每个候选一份）：

```bash
python scripts/export_candidates.py
# 输出：artifacts/reports/candidates/<namespace>__<candidate>.md + index.md

# 常用选项
python scripts/export_candidates.py --candidate C6_distance_gap_correction
python scripts/export_candidates.py --include-history        # 附每次 snapshot 的表
python scripts/export_candidates.py --all-namespaces
python scripts/export_candidates.py --out /tmp/dump
```

每份 Markdown 包含：Form / Intuition / Risk notes / Estimated C / Proof plan / 每个 property 的 proposition plan + 证明草稿 + 工具请求 / Pruned reason / Terminal report / Post-terminal decision。

## 其他 CLI

```bash
proof-agent-api-status --help        # 探测 provider 可达性
proof-agent-convert-pdf --help       # Marker PDF → Markdown（主论文转写）
```

## Notes

- 默认主论文：`data/papers/primary/1103.4361v2.pdf`；最终研究报告写到 `artifacts/reports/`。
- 运行时角色快照主文件是 `artifacts/prompt_snapshots/customized_prompts.snapshot.json`；`customized_prompts.json` 只是兼容性提示文件，不是可编辑配置源。改动 snapshot 会产生新的 `prompt_snapshot_hash`，进而切换 `memory_namespace`。
- 所有相对路径按仓库根解析，不依赖当前 shell 的 `cwd`（见 [paths.py](src/proof_agent/paths.py)）。
- `LiteratureRAG` 在依赖 / 密钥缺失时自动降级，不会阻塞主流程启动。
- 当前全局 `print()` 直出，尚未接入 `logging` 模块；如需结构化日志，最快的方式是在 [cli/main.py](src/proof_agent/cli/main.py) 入口加 `logging.basicConfig(handlers=[FileHandler(...), StreamHandler()])`。
