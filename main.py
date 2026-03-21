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
目标不是复现或停留在论文与 Section 6 中提到的 ρ <= 1.98。
1.98 只能视为文献中给出的一个已知中间改进方向或里程碑，不是本次研究的最终目标。

你的真实任务是：
在严格遵守原论文几何框架、符号系统与证明要求的前提下，尽可能把 Delaunay triangulation 的 stretch factor 上界进一步压低到小于 1.98 的方向推进，并且只输出有数学支撑的结论。

强约束如下：
1. 不得把“达到 1.98”当作任务完成。
2. 若能提出比 1.98 更小的候选上界，必须给出该候选上界所依赖的关键引理、势函数修改、参数条件与证明缺口。
3. 若暂时无法严格证明小于 1.98 的新上界，不得假装已经改进成功；应明确说明当前卡住的最关键瓶颈、失败原因、哪个局部不等式阻止继续下降，以及下一步最值得推进的方向。
4. 1.98 可以作为 baseline、对照目标或第一阶段检查点，但后续任务规划必须继续尝试向更小上界推进，而不是自动收缩回“证明 1.98”。
5. 可以重写原有任务分解；优先寻找能真正推动上界低于 1.98 的新势函数、新参数配置、新局部极值分析或新的数值验证方案。
6. 所有局部结论都必须标注其支持范围，禁止把局部修正直接夸大成“已完成最终全局证明”。

最终希望得到的不是“围绕 1.98 重复展开”，而是：
- 给出一个严格支持的新更小上界；

"""



if __name__ == "__main__":
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
    elif not os.path.exists(PDF_FILE):
        print(f"请放置 {PDF_FILE} 后运行。")
    else:
        system = AutonomousResearchSystem()
        print(
            f"🗄️ 记忆库: {system.memory.store_path}",
            flush=True,
        )
        final_report = system.execute(PDF_FILE, TARGET_GOAL)

        if final_report is None:
            print("\n⚠️ 任务未完成，未生成报告文件。")
        else:
            output_path = _build_report_filename(TARGET_GOAL)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(final_report)
            print(f"\n✨ 报告已生成: {output_path}")
