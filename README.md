# Proof Agent

用于几何证明探索的研究代理项目。现在的仓库结构按职责拆分为源码、数据、文档、研究产物和运行时文件，避免所有内容继续堆在根目录。

## Directory Layout

```text
.
├── src/proof_agent/            # 核心包源码
│   ├── cli/                    # CLI 入口
│   ├── paths.py                # 项目路径常量
│   ├── research_system.py      # 主研究流程
│   ├── agents.py               # LLM 适配层
│   ├── candidate_memory.py     # SQLite 记忆库
│   ├── literature_rag.py       # 文献检索 / RAG
│   ├── verification_tools.py   # 数值 / 符号验证工具
│   └── reporting.py            # 报告命名与落盘
├── scripts/
│   └── start_main.sh           # 后台启动主流程
├── data/papers/
│   ├── primary/                # 主论文 PDF
│   └── references/             # 参考文献 PDF
├── docs/                       # 架构与说明文档
├── artifacts/
│   ├── reports/                # 历史研究输出
│   └── prompt_snapshots/       # 运行时导出的角色配置
├── runtime/
│   ├── logs/                   # 运行日志
│   ├── nohup/                  # 旧 nohup 输出
│   └── pids/                   # 进程 PID 文件
└── .cache/                     # Markdown / RAG 缓存
```

## Run

推荐先安装为可编辑包：

```bash
pip install -e .
```

常用命令：

```bash
proof-agent-run
proof-agent-memory --help
proof-agent-memory-web --help
proof-agent-convert-pdf --help
proof-agent-api-status --help
```

如果不安装包，也可以直接：

```bash
PYTHONPATH=src python -m proof_agent.cli.main
```

后台运行：

```bash
bash scripts/start_main.sh
```

## Notes

- 默认主论文位置是 `data/papers/primary/1103.4361v2.pdf`。
- 最终研究报告会写到 `artifacts/reports/`。
- 运行时角色快照主文件是 `artifacts/prompt_snapshots/customized_prompts.snapshot.json`；`customized_prompts.json` 只是兼容性提示文件，不是可编辑配置源。
- 大部分相对路径现在都按仓库根目录解析，不再依赖当前 shell 的工作目录。
- `LiteratureRAG` 现在会在依赖或密钥缺失时自动降级，不会阻塞主流程启动。
