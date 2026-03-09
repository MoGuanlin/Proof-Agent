import os

from app_config import (
    LITERATURE_MAX_CHARS,
    LLM_PROVIDER,
    MODEL_NAME,
    PREFER_STREAMING,
    REQUEST_TIMEOUT_SECONDS,
    _active_api_key,
)
from reporting import _build_report_filename
from research_system import AutonomousResearchSystem


PDF_FILE = "1103.4361v2.pdf"  # 替换为你的 PDF
TARGET_GOAL = """
    改进论文中的bound，达到更紧的结果。
    """


if __name__ == "__main__":
    print(
        f"🚀 启动配置: provider={LLM_PROVIDER}, model={MODEL_NAME}, "
        f"stream={1 if PREFER_STREAMING else 0}, timeout={REQUEST_TIMEOUT_SECONDS}s, "
        f"literature_max_chars={LITERATURE_MAX_CHARS}",
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
    elif not os.path.exists(PDF_FILE):
        print(f"请放置 {PDF_FILE} 后运行。")
    else:
        system = AutonomousResearchSystem()
        final_report = system.execute(PDF_FILE, TARGET_GOAL)

        if final_report is None:
            print("\n⚠️ 任务未完成，未生成报告文件。")
        else:
            output_path = _build_report_filename(TARGET_GOAL)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(final_report)
            print(f"\n✨ 报告已生成: {output_path}")
