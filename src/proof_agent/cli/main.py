import os

from proof_agent.app_config import (
    LITERATURE_MAX_CHARS,
    LLM_PROVIDER,
    MODEL_NAME,
    PREFER_STREAMING,
    REQUEST_TIMEOUT_SECONDS,
    _active_api_key,
)
from proof_agent.paths import PRIMARY_PAPER_PATH
from proof_agent.reporting import _build_report_filename
from proof_agent.research_system import AutonomousResearchSystem


PDF_FILE = str(PRIMARY_PAPER_PATH)
TARGET_GOAL = """
The goal is not to reproduce or stop at the paper's Section 6 milestone of rho <= 1.98.
Treat 1.98 only as a known intermediate direction or milestone from the literature, not as the final target of this run.

Your real task is:
Under the original paper's geometric framework, notation system, and proof requirements, push the Delaunay triangulation stretch-factor upper bound as far below 1.98 as possible, and output only mathematically supported conclusions.

Hard constraints:
1. Do not treat "reaching 1.98" as task completion.
2. If you propose a candidate upper bound smaller than 1.98, you must state the key lemmas, potential-function modifications, parameter conditions, and remaining proof gaps on which it depends.
3. If you cannot yet prove a new upper bound strictly below 1.98, do not pretend the improvement has already succeeded. Instead, state the main bottleneck, the failure reason, the local inequality blocking further descent, and the most worthwhile next direction.
4. 1.98 may be used as a baseline, comparison target, or phase-one checkpoint, but subsequent planning must keep pushing toward smaller upper bounds rather than collapsing back to "prove 1.98".
5. You may rewrite the existing task decomposition. Prioritize genuinely useful new potential functions, parameter settings, local extremal analyses, or numeric-verification schemes that could move the upper bound below 1.98.
6. Every local conclusion must state its scope of support. Do not inflate a local repair into "the final global proof is complete".

The desired outcome is not repeated discussion around 1.98, but instead:
- a rigorously supported new smaller upper bound;

"""


def main():
    print(
        f"🚀 启动配置: provider={LLM_PROVIDER}, model={MODEL_NAME}, "
        f"stream={1 if PREFER_STREAMING else 0}, timeout={REQUEST_TIMEOUT_SECONDS}s, "
        f"literature_max_chars={LITERATURE_MAX_CHARS}, "
        "architecture_mode=candidate",
        flush=True,
    )
    api_key = _active_api_key()
    if not api_key:
        if LLM_PROVIDER == "google":
            print("请先设置 GEMINI_API_KEY（或 GOOGLE_API_KEY，环境变量或 .env 文件）。")
        elif LLM_PROVIDER == "ai_hub_mixed":
            print("请先设置 AI_HUB_MIXED_MODEL_API_KEY（环境变量或 .env 文件）。")
        else:
            print("请先设置 SILICONFLOW_API_KEY（环境变量或 .env 文件）。")
        return 1
    elif not os.path.exists(PDF_FILE):
        print(f"请放置 {PDF_FILE} 后运行。")
        return 1
    else:
        system = AutonomousResearchSystem()
        print(
            f"🗄️ 记忆库: {system.memory.store_path}",
            flush=True,
        )
        final_report = system.execute(PDF_FILE, TARGET_GOAL)

        if final_report is None:
            print("\n⚠️ 任务未完成，未生成报告文件。")
            return 1
        else:
            output_path = _build_report_filename(TARGET_GOAL)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(final_report)
            print(f"\n✨ 报告已生成: {output_path}")
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
