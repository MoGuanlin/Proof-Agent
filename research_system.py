import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from agents import (
    BaseAgent,
    architect,
    generator,
    global_rev,
    grafter,
    logic_rev,
    orchestrator,
    surveyor,
    syntax_rev,
)
from app_config import (
    LITERATURE_MAX_CHARS,
    MARKDOWN_CACHE_FILE,
    MARKER_DISABLE_MULTIPROCESSING,
    MARKER_EXTRA_ARGS,
    MARKER_FORCE_CPU,
    MARKER_FORCE_OCR,
    MARKER_TIMEOUT_SECONDS,
    PDF_PARSE_BACKEND,
    REUSE_CUSTOM_PROMPTS,
    REVIEWER_ROLE_OVERRIDES,
    _apply_reviewer_prompt_overrides,
)


class AutonomousResearchSystem:
    def __init__(self):
        self.reviewers = [syntax_rev, logic_rev, global_rev]

    @staticmethod
    def _cache_path_for_pdf(pdf_path):
        """为每个 PDF 生成稳定的缓存文件路径。"""
        cache_dir = os.path.join(".cache", "markitdown")
        os.makedirs(cache_dir, exist_ok=True)
        abs_pdf_path = os.path.abspath(pdf_path)
        path_hash = hashlib.sha1(abs_pdf_path.encode("utf-8")).hexdigest()[:12]
        stem = os.path.splitext(os.path.basename(pdf_path))[0]
        backend_tag = "marker"
        marker_cfg = (
            f"fo{1 if MARKER_FORCE_OCR else 0}_"
            f"fc{1 if MARKER_FORCE_CPU else 0}_"
            f"dmp{1 if MARKER_DISABLE_MULTIPROCESSING else 0}_"
            f"{' '.join(MARKER_EXTRA_ARGS)}"
        )
        marker_tag = hashlib.sha1(marker_cfg.encode("utf-8")).hexdigest()[:10]
        return os.path.join(cache_dir, f"{stem}.{path_hash}.{backend_tag}.{marker_tag}.md")

    @staticmethod
    def _parse_with_marker(pdf_path):
        """
        使用 marker-pdf 将 PDF 转 Markdown。
        兼容不同 CLI 形态：marker_single 或 marker。
        """

        def _cmd_text(cmd):
            return " ".join(cmd)

        def _called_process_err(cmd, e):
            stderr = (getattr(e, "stderr", "") or "")[:4000]
            stdout = (getattr(e, "stdout", "") or "")[:1200]
            return RuntimeError(
                f"cmd={_cmd_text(cmd)}; exit={e.returncode}; stderr={stderr}; stdout={stdout}"
            )

        def _supports_legacy_output_dir_fallback(stderr_text):
            s = (stderr_text or "").lower()
            return "--output_dir" in s and (
                "no such option" in s
                or "unrecognized arguments" in s
                or "unexpected option" in s
            )

        def _is_cuda_arch_incompatible(stderr_text):
            s = (stderr_text or "").lower()
            return (
                "is not compatible with the current pytorch installation" in s
                or "minimum and maximum cuda capability supported by this version of pytorch" in s
            )

        def _marker_cpu_env():
            env = os.environ.copy()
            env["TORCH_DEVICE"] = "cpu"
            # 强制让 torch.cuda.is_available() 为 False，避免 marker/surya 继续选到 cuda。
            env["CUDA_VISIBLE_DEVICES"] = ""
            return env

        def _run_cmd_and_read_markdown(cmd, out_dir, run_env=None, run_note=""):
            def _append_capped(prev, chunk, cap):
                merged = prev + chunk
                if len(merged) <= cap:
                    return merged
                return merged[-cap:]

            def _run_realtime(cmd_args, env_vars):
                proc = subprocess.Popen(
                    cmd_args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env_vars,
                )
                out_tail = ""
                err_tail = ""

                def _to_text(buf):
                    if not buf:
                        return ""
                    if isinstance(buf, bytes):
                        return buf.decode("utf-8", errors="replace")
                    return str(buf)

                try:
                    stdout_data, stderr_data = proc.communicate(timeout=MARKER_TIMEOUT_SECONDS)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    stdout_data, stderr_data = proc.communicate()
                    out_text = _to_text(stdout_data)
                    err_text = _to_text(stderr_data)
                    out_tail = _append_capped(out_tail, out_text, 1200)
                    err_tail = _append_capped(err_tail, err_text, 4000)
                    raise subprocess.TimeoutExpired(
                        cmd=cmd_args,
                        timeout=MARKER_TIMEOUT_SECONDS,
                        output=out_tail,
                        stderr=err_tail,
                    )

                out_text = _to_text(stdout_data)
                err_text = _to_text(stderr_data)
                if out_text:
                    sys.stdout.write(out_text)
                    sys.stdout.flush()
                if err_text:
                    sys.stderr.write(err_text)
                    sys.stderr.flush()
                out_tail = _append_capped(out_tail, out_text, 1200)
                err_tail = _append_capped(err_tail, err_text, 4000)

                rc = proc.returncode
                if rc != 0:
                    raise subprocess.CalledProcessError(
                        returncode=rc,
                        cmd=cmd_args,
                        output=out_tail,
                        stderr=err_tail,
                    )
                return out_tail, err_tail

            note = f" [{run_note}]" if run_note else ""
            print(
                f"⏳ marker running{note} (timeout={MARKER_TIMEOUT_SECONDS}s): "
                + _cmd_text(cmd)
            )
            proc_stdout, proc_stderr = _run_realtime(cmd, run_env)
            md_candidates = []
            for root, _, files in os.walk(out_dir):
                for name in files:
                    if name.lower().endswith(".md"):
                        md_candidates.append(os.path.join(root, name))
            md_candidates.sort()
            if md_candidates:
                with open(md_candidates[0], "r", encoding="utf-8") as f:
                    return f.read()
            raise RuntimeError(
                "marker ran but no markdown output found. "
                f"stdout={proc_stdout[:1200]} stderr={proc_stderr[:4000]}"
            )

        def _run_with_cpu_fallback(cmd, out_dir):
            if MARKER_FORCE_CPU:
                return _run_cmd_and_read_markdown(
                    cmd,
                    out_dir,
                    run_env=_marker_cpu_env(),
                    run_note="force cpu (env)",
                )
            try:
                return _run_cmd_and_read_markdown(cmd, out_dir)
            except subprocess.CalledProcessError as e:
                if not _is_cuda_arch_incompatible(e.stderr):
                    raise
                return _run_cmd_and_read_markdown(
                    cmd,
                    out_dir,
                    run_env=_marker_cpu_env(),
                    run_note="force cpu",
                )

        marker_single = shutil.which("marker_single")
        marker_cli = shutil.which("marker")
        if not marker_single and not marker_cli:
            raise RuntimeError(
                "marker CLI not found. Install marker-pdf first, then ensure `marker_single` is in PATH."
            )

        with tempfile.TemporaryDirectory(prefix="marker_out_") as out_dir, tempfile.TemporaryDirectory(prefix="marker_in_") as in_dir:
            commands = []

            if marker_single:
                single_tail = ["--output_format", "markdown"]
                if MARKER_FORCE_OCR:
                    # 新版 marker/marker_single 推荐使用 PdfProvider_force_ocr
                    single_tail.append("--PdfProvider_force_ocr")
                if MARKER_DISABLE_MULTIPROCESSING:
                    single_tail.append("--disable_multiprocessing")
                if MARKER_EXTRA_ARGS:
                    single_tail.extend(MARKER_EXTRA_ARGS)
                # 新版 marker_single 形态：marker_single [OPTIONS] FPATH
                commands.append(
                    {
                        "kind": "marker_single",
                        "cmd": [marker_single, pdf_path, "--output_dir", out_dir] + single_tail,
                        # 兼容部分旧版（位置参数 output_dir）；仅在 --output_dir 参数不兼容时才回退。
                        "legacy_cmd": [marker_single, pdf_path, out_dir] + single_tail,
                    }
                )

            # 仅当 marker_single 不可用时，才回退 marker 批处理入口。
            if marker_cli and not marker_single:
                # marker 新版 CLI 形态：marker [OPTIONS] IN_FOLDER
                pdf_name = os.path.basename(pdf_path)
                staged_pdf = os.path.join(in_dir, pdf_name)
                shutil.copy2(pdf_path, staged_pdf)

                cli_tail = ["--output_dir", out_dir, "--output_format", "markdown"]
                if MARKER_FORCE_OCR:
                    # 新版 marker 的强制 OCR 选项
                    cli_tail.append("--PdfProvider_force_ocr")
                if MARKER_DISABLE_MULTIPROCESSING:
                    cli_tail.append("--disable_multiprocessing")
                if MARKER_EXTRA_ARGS:
                    cli_tail.extend(MARKER_EXTRA_ARGS)
                commands.append({"kind": "marker_cli", "cmd": [marker_cli, in_dir] + cli_tail})

            last_err = None
            for entry in commands:
                cmd = entry["cmd"]
                try:
                    return _run_with_cpu_fallback(cmd, out_dir)
                except Exception as e:
                    if isinstance(e, subprocess.TimeoutExpired):
                        last_err = RuntimeError(
                            f"cmd={_cmd_text(cmd)}; timeout after {MARKER_TIMEOUT_SECONDS}s"
                        )
                        break
                    if not isinstance(e, subprocess.CalledProcessError):
                        last_err = e
                        break

                    # 仅在检测到 --output_dir 不兼容时，才尝试 marker_single 旧版位置参数兼容路径。
                    legacy_cmd = entry.get("legacy_cmd")
                    if legacy_cmd and _supports_legacy_output_dir_fallback(e.stderr):
                        try:
                            return _run_with_cpu_fallback(legacy_cmd, out_dir)
                        except Exception as legacy_e:
                            if isinstance(legacy_e, subprocess.TimeoutExpired):
                                last_err = RuntimeError(
                                    f"cmd={_cmd_text(legacy_cmd)}; timeout after {MARKER_TIMEOUT_SECONDS}s"
                                )
                            elif isinstance(legacy_e, subprocess.CalledProcessError):
                                last_err = _called_process_err(legacy_cmd, legacy_e)
                            else:
                                last_err = legacy_e
                        break

                    last_err = _called_process_err(cmd, e)
                    break

            raise RuntimeError(f"marker parse failed: {last_err}")

    def _parse_pdf_with_backend(self, pdf_path):
        text = self._parse_with_marker(pdf_path)
        print("✅ parser selected: marker")
        return text

    def _load_or_parse_pdf_markdown(self, pdf_path, force_reparse=False):
        """
        优先读取缓存；缓存不存在或 PDF 更新时重新解析并回写缓存。
        force_reparse=True 时强制重解析。
        """
        if MARKDOWN_CACHE_FILE and not force_reparse:
            manual_cache_path = os.path.abspath(os.path.expanduser(MARKDOWN_CACHE_FILE))
            if os.path.exists(manual_cache_path):
                print(f"📦 [Step 2/5] 使用指定 Markdown 缓存: {manual_cache_path}")
                with open(manual_cache_path, "r", encoding="utf-8") as f:
                    return f.read()
            print(f"⚠️ 指定 Markdown 缓存不存在，回退到常规解析: {manual_cache_path}")

        cache_path = self._cache_path_for_pdf(pdf_path)
        pdf_mtime = os.path.getmtime(pdf_path)
        cache_exists = os.path.exists(cache_path)
        cache_fresh = cache_exists and os.path.getmtime(cache_path) >= pdf_mtime

        if cache_fresh and not force_reparse:
            print(f"📦 [Step 2/5] 命中解析缓存: {cache_path}")
            with open(cache_path, "r", encoding="utf-8") as f:
                return f.read()

        print(
            "📄 [Step 2/5] 解析文献"
            f"（backend={PDF_PARSE_BACKEND}, marker_force_ocr={MARKER_FORCE_OCR}, "
            f"marker_force_cpu={MARKER_FORCE_CPU}, marker_extra_args={MARKER_EXTRA_ARGS}）..."
        )
        try:
            raw_md = self._parse_pdf_with_backend(pdf_path)
            with open(cache_path, "w", encoding="utf-8") as f:
                f.write(raw_md)
            print(f"💾 解析结果已缓存: {cache_path}")
            return raw_md
        except Exception:
            if cache_exists:
                print(f"⚠️ 解析失败，回退到旧缓存: {cache_path}")
                with open(cache_path, "r", encoding="utf-8") as f:
                    return f.read()
            raise

    @staticmethod
    def _parse_tasks(raw_text, fallback_goal):
        """尽量从模型输出中提取 JSON 数组子任务。"""
        try:
            parsed = json.loads(raw_text)
            if isinstance(parsed, list) and parsed:
                return [str(x) for x in parsed]
        except Exception:
            pass

        match = re.search(r"\[[\s\S]*?\]", raw_text)
        if match:
            try:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, list) and parsed:
                    return [str(x) for x in parsed]
            except Exception:
                pass

        return [fallback_goal]

    @staticmethod
    def _build_literature_context(raw_md, max_chars=None):
        """提取用于定制专家提示词的文献关键信息。"""
        if not raw_md:
            return ""
        if max_chars is None:
            max_chars = LITERATURE_MAX_CHARS
        try:
            max_chars = int(max_chars)
        except Exception:
            max_chars = 0

        normalized = re.sub(r"\n{3,}", "\n\n", raw_md).strip()
        if max_chars <= 0:
            return normalized
        if len(normalized) <= max_chars:
            return normalized

        head_budget = int(max_chars * 0.7)
        head = normalized[:head_budget]

        # 额外抓取含关键词的行，避免只看到前言而缺少关键结论/定理信息。
        keywords = ("theorem", "lemma", "proposition", "bound", "proof", "convergence", "assumption")
        picked = []
        seen = set()
        for line in normalized.splitlines():
            s = line.strip()
            if not s:
                continue
            low = s.lower()
            if any(k in low for k in keywords):
                key = s[:180]
                if key in seen:
                    continue
                seen.add(key)
                picked.append(s)
            if sum(len(x) for x in picked) >= max_chars - head_budget:
                break

        if picked:
            extra = "\n".join(picked)
            return f"{head}\n\n[Key Excerpts]\n{extra}"[:max_chars]
        return head

    @staticmethod
    def _extract_tagged_prompt(text, tag_name="SYSTEM_PROMPT"):
        """提取 <SYSTEM_PROMPT>...</SYSTEM_PROMPT> 标签内容。"""
        return BaseAgent._extract_tagged_content(text, tag_name)

    def _customize_team(self, macro_goal, literature_context):
        """核心：根据任务和文献上下文动态生成全员提示词"""
        print("🎨 [Step 1/5] Prompt Architect 正在为该课题定制专家指令集...")

        targets = {
            orchestrator: "作为 PI，你需要将此任务拆解为多个逻辑严密的子任务，并按优先级排序，确保每一步都有数学支撑。",
            surveyor: "作为领域专家，请聚焦于该任务涉及的 SOTA 瓶颈和数值计算挑战。",
            grafter: "作为跨学科专家，请为该任务寻找同构的物理或数学工具。",
            generator: "作为生成专家，利用逻辑自修复能力处理 Markdown 乱码，输出完美的 LaTeX 推导。",
            syntax_rev: "以中性验证方式检查符号一致性、矩阵维度、变量定义与坐标设定，区分致命错误与可修补问题。",
            logic_rev: "以中性验证方式审计数学推导的严密性、变量依赖、链式法则和结论强度，只有致命错误才驳回。",
            global_rev: "做全局一致性审查，识别实质性漏洞和结论越界，避免对抗式人格化措辞。",
        }

        generated_prompts = {}
        for agent, mission in targets.items():
            override_prompt = REVIEWER_ROLE_OVERRIDES.get(agent.name)
            if override_prompt:
                agent.role = override_prompt
                generated_prompts[agent.name] = override_prompt
                continue
            prompt = (
                f"任务目标: {macro_goal}\n"
                f"文献关键信息:\n{literature_context}\n\n"
                f"请为 {agent.name} 撰写一个极其专业的 System Prompt。要求：{mission}\n"
                "请确保 prompt 明确引用文献中的核心对象、假设和符号体系。\n"
                "若该角色属于 reviewer，必须采用中性验证语气，不得使用 hostile/adversarial/brutal/merciless 等人格设定；"
                "必须区分致命问题与可修补问题；只能使用 [PASS]/[REJECT] 作为结论标签；"
                "并且只评估当前子任务可支持的局部结论，不要求局部草稿单独证明最终全局定理。\n"
                "你必须且只允许按如下格式输出，不要任何额外说明：\n"
                "<SYSTEM_PROMPT>\n"
                "...这里是完整可用的 system prompt...\n"
                "</SYSTEM_PROMPT>"
            )
            final_prompt = architect.call_llm_tagged(
                prompt,
                tag_name="SYSTEM_PROMPT",
                content_hint="标签内只放 system prompt 正文。",
                print_stream=True,
            ).strip()
            agent.role = final_prompt
            generated_prompts[agent.name] = final_prompt

        with open("customized_prompts.json", "w", encoding="utf-8") as f:
            json.dump(generated_prompts, f, ensure_ascii=False, indent=2)
        print("📝 已保存定制后的 System Prompts: customized_prompts.json")
        print("✅ 专家团队全员已完成针对性强化。")

    @staticmethod
    def _load_customized_prompts(path="customized_prompts.json"):
        if not REUSE_CUSTOM_PROMPTS:
            return False
        if not os.path.exists(path):
            return False

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"⚠️ 读取定制提示词缓存失败，回退在线定制: {e}")
            return False

        if not isinstance(data, dict):
            print("⚠️ 定制提示词缓存格式错误，回退在线定制。")
            return False

        data, patched = _apply_reviewer_prompt_overrides(data)
        if patched:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                print(f"🔧 已更新 reviewer 提示词缓存: {path}")
            except Exception as e:
                print(f"⚠️ 写回 reviewer 提示词缓存失败，将继续使用内存中的修正版: {e}")

        team = [orchestrator, surveyor, grafter, generator, syntax_rev, logic_rev, global_rev]
        for agent in team:
            role = data.get(agent.name)
            if not isinstance(role, str) or not role.strip():
                print(f"⚠️ 定制提示词缓存缺少 {agent.name}，回退在线定制。")
                return False

        for agent in team:
            agent.role = str(data[agent.name]).strip()

        print(f"📦 [Step 1/5] 命中定制提示词缓存: {path}")
        print("✅ 专家团队角色已从缓存加载。")
        return True

    @staticmethod
    def _truncate_for_prompt(text, max_chars=24000):
        s = str(text or "").strip()
        if max_chars <= 0:
            return s
        if len(s) <= max_chars:
            return s
        return s[:max_chars]

    @staticmethod
    def _extract_markdown_section(md_text, section_title):
        if not md_text:
            return ""
        pattern = rf"(?ims)^##\s*{re.escape(section_title)}\s*$([\s\S]*?)(?=^##\s+|\Z)"
        match = re.search(pattern, str(md_text))
        if not match:
            return ""
        return match.group(1).strip()

    @staticmethod
    def _tokenize_for_overlap(text):
        s = str(text or "").lower()
        tokens = set(re.findall(r"[a-z][a-z0-9_+-]{1,}", s))
        for block in re.findall(r"[\u4e00-\u9fff]{2,}", s):
            for i in range(len(block) - 1):
                tokens.add(block[i : i + 2])
        return tokens

    def _build_task_summary(self, task_idx, task_text, draft, max_chars=0):
        task = self._truncate_for_prompt(task_text, max_chars=0)
        assumptions = self._truncate_for_prompt(
            self._extract_markdown_section(draft, "Assumptions"), max_chars=0
        )
        claim = self._truncate_for_prompt(
            self._extract_markdown_section(draft, "Claim"), max_chars=0
        )
        conclusion = self._truncate_for_prompt(
            self._extract_markdown_section(draft, "Conclusion"), max_chars=0
        )
        symbol_section = self._extract_markdown_section(draft, "Symbol Table")
        symbol_lines = [ln.strip() for ln in symbol_section.splitlines() if ln.strip()][:8]
        symbols = self._truncate_for_prompt(" | ".join(symbol_lines), max_chars=0)
        summary = (
            f"### 子任务摘要 {task_idx}\n"
            f"任务: {task}\n"
            f"关键假设: {assumptions or '[缺失]'}\n"
            f"关键符号: {symbols or '[缺失]'}\n"
            f"核心结论: {claim or '[缺失]'}\n"
            f"最终结论: {conclusion or '[缺失]'}"
        )
        return self._truncate_for_prompt(summary, max_chars=max_chars)

    def _retrieve_relevant_full_context(self, task_text, draft_records, max_items=2, max_chars=8000):
        if not draft_records:
            return ""

        query_tokens = self._tokenize_for_overlap(task_text)
        if not query_tokens:
            return ""

        scored = []
        total = len(draft_records)
        for idx, rec in enumerate(draft_records):
            rec_tokens = rec.get("tokens") or set()
            overlap = len(query_tokens & rec_tokens)
            if overlap <= 0:
                continue
            recency_boost = (idx + 1) / max(total, 1)
            scored.append((overlap + 0.25 * recency_boost, idx, rec))

        if not scored:
            return ""

        scored.sort(key=lambda x: x[0], reverse=True)
        parts = []
        used_chars = 0
        per_item_budget = max(1200, max_chars // max(1, max_items))
        for _, idx, rec in scored[:max_items]:
            body = self._truncate_for_prompt(rec.get("draft", ""), max_chars=per_item_budget)
            chunk = f"### 相关历史定稿 {idx+1}\n{body}".strip()
            if not chunk:
                continue
            if used_chars + len(chunk) > max_chars:
                remain = max_chars - used_chars
                if remain <= 200:
                    break
                chunk = self._truncate_for_prompt(chunk, max_chars=remain)
            parts.append(chunk)
            used_chars += len(chunk) + 2

        return "\n\n".join(parts).strip()

    def _generate_reviewer_directive(self, reviewer, context, draft, stage_label="子任务审查"):
        """
        根据当前证明草稿显式生成 reviewer 的定制审查指令，避免静态审查模板漏检。
        """
        reviewer_name = reviewer.name if hasattr(reviewer, "name") else "Reviewer"
        draft_snippet = self._truncate_for_prompt(draft, max_chars=0)
        directive_prompt = (
            f"阶段: {stage_label}\n"
            f"审稿人: {reviewer_name}\n"
            f"任务上下文: {context}\n\n"
            "请基于下述“当前证明草稿”生成一份审查指令（给该审稿人使用）。\n"
            "要求：\n"
            "1) 指令必须显式引用当前草稿中的关键对象/符号/主张；\n"
            "2) 先给出一个简短的 Current Task Contract，总结本轮显式采用的定义、坐标系、独立变量和目标结论；\n"
            "3) 给出 5-8 条高优先级检查项，每条都必须可执行、可判定；\n"
            "4) 明确区分“致命问题（才可判 [REJECT]）”与“可修补问题（应给出修补建议但不自动 [REJECT]）”；\n"
            "5) 若当前子任务只是局部参数化、偏导、引理或中间不等式，只评估该局部目标，不要求其独立证明最终全局定理或最终 rho 改进；\n"
            "6) 不要复述整篇草稿，不要给出与当前草稿无关的泛化建议，不要使用 hostile/adversarial/brutal/merciless 等人格化措辞。\n\n"
            f"当前证明草稿:\n{draft_snippet}"
        )
        directive = architect.call_llm_tagged(
            directive_prompt,
            tag_name="REVIEW_DIRECTIVE",
            content_hint="输出审查指令正文，不要外层解释。",
            print_stream=False,
        )
        return directive.strip()

    def _synthesize_full_report(self, goal, global_context):
        """将各子任务定稿整合为一份连贯全文，并做一次全文一致性复核。"""
        merged = "\n\n".join(global_context).strip()
        if not merged:
            return ""

        synth_prompt = (
            f"总任务目标: {goal}\n\n"
            "下面是按子任务产出的定稿，请整合为一份完整、连贯、可直接阅读的最终证明文档。\n"
            "要求：\n"
            "1) 统一符号和记号，避免同一对象多种命名；\n"
            "2) 去除重复段落，修复前后引用断裂；\n"
            "3) 明确主结论、关键引理、证明步骤与结论边界；\n"
            "4) 严禁引入任何新的假设、定义或结论；只能重排、润色、合并已有内容；\n"
            "5) 若子任务之间存在冲突，优先保留明确声明且推导自洽的版本，并在文中显式说明该选择；\n"
            "6) 必须保留每个子任务的推导细节（包括关键假设、符号定义、核心不等式与边界条件），不得仅保留摘要结论；\n"
            "7) 输出 Markdown 正文，不要解释你如何整合。\n\n"
            f"子任务定稿集合:\n{merged}"
        )
        full_draft = generator.call_llm_tagged(
            synth_prompt,
            tag_name="FULL_REPORT_DRAFT",
            content_hint="输出完整 Markdown 正文。",
        )

        for attempt in range(10):
            print(f"  🧩 全文复核轮次 {attempt+1}...")
            all_pass = True
            for rev in self.reviewers:
                review_directive = self._generate_reviewer_directive(
                    rev,
                    "全文整合与一致性审查",
                    full_draft,
                    stage_label=f"全文复核第{attempt+1}轮",
                )
                ok, feedback = rev.check(
                    full_draft,
                    "全文整合与一致性审查",
                    review_directive=review_directive,
                )
                if not ok:
                    print(f"  ❌ 全文被 {rev.name} 驳回！修订中...")
                    full_draft = generator.call_llm_tagged(
                        f"请根据以下审稿意见修订全文：\n{feedback}\n\n当前全文:\n{full_draft}\n\n"
                        "若审稿意见指出主结论过强、证据不足或存在冲突，允许你降级主结论、缩小适用范围；必要时明确撤回原主张。",
                        tag_name="FULL_REPORT_DRAFT",
                        content_hint="输出修订后的完整 Markdown 正文；若原主结论过强，允许降级或撤回。",
                    )
                    all_pass = False
                    break
                else:
                    print(f"  ✅ 全文 {rev.name} Pass.")
            if all_pass:
                break

        return full_draft.strip()

    def execute(self, pdf_path, goal, force_reparse=False):
        try:
            raw_md = self._load_or_parse_pdf_markdown(pdf_path, force_reparse=force_reparse)
        except Exception as e:
            print(f"❌ 解析失败: {e}")
            return None

        literature_context = self._build_literature_context(raw_md)
        if not self._load_customized_prompts():
            self._customize_team(goal, literature_context)
        working_paper_context = self._truncate_for_prompt(raw_md, max_chars=0)

        print("🧠 [Step 3/6] PI 正在制定研究路径...")
        decomp_res = orchestrator.call_llm_tagged(
            f"背景文献: {working_paper_context}\n"
            f"任务目标: {goal}\n"
            "请按优先级给出子任务列表，数量上限 8 个（可少不可多）。"
            "每个子任务必须足够具体且可验证（应能被独立判定完成/未完成）。"
            "请输出 JSON 数组，每个元素必须包含："
            "id,title,description,priority,expected_impact,verification。",
            tag_name="TASKS_JSON",
            content_hint="标签内必须是合法 JSON 数组字符串，不要代码块；数组长度<=8；每项含 verification。",
        )
        tasks = self._parse_tasks(decomp_res, goal)

        print("🛠️ [Step 4/6] 启动迭代推导链...")
        global_context = []
        summary_context = []
        draft_records = []
        for i, t in enumerate(tasks):
            print(f"\n▶️ 攻坚任务 {i+1}: {t[:]}...")
            ctx = self._truncate_for_prompt("\n\n".join(summary_context), max_chars=0)
            retrieved_full_context = self._retrieve_relevant_full_context(
                t,
                draft_records,
                max_items=2,
                max_chars=0,
            )

            s_adv = surveyor.call_llm_tagged(
                f"分析瓶颈 (背景:{ctx}): {t}",
                tag_name="SURVEY_NOTES",
                content_hint="给出结构化瓶颈分析与可执行建议。",
            )
            g_adv = grafter.call_llm_tagged(
                f"提供数学武器 (基于:{s_adv}): {t}",
                tag_name="GRAFT_PLAN",
                content_hint="给出可落地的方法嫁接方案。",
            )

            retrieval_block = (
                f"\n相关历史定稿全文片段(按相关性检索):\n{retrieved_full_context}\n"
                if retrieved_full_context
                else "\n"
            )
            draft = generator.call_llm_tagged(
                f"原始文献摘录: {working_paper_context}\n背景摘要: {ctx}{retrieval_block}任务: {t}\n建议: {g_adv}\n"
                "请完成严谨推导，并严格按以下 Markdown 骨架输出：\n"
                "## Assumptions\n"
                "## Symbol Table\n"
                "## Claim\n"
                "## Derivation\n"
                "## Boundary Cases\n"
                "## Conclusion\n"
                "若任务涉及重定义，请在 Assumptions 中明确写出“替代定义/新定义”。\n"
                "Claim 和 Conclusion 只能陈述当前子任务直接建立的局部结果；若本轮只是参数化、偏导公式或中间引理，不得宣称已经证明最终全局 bound 或 rho 改进。\n"
                "如需改动坐标系、独立变量或函数依赖，必须在 Assumptions 中显式声明，并在 Derivation 中保持一致。",
                tag_name="DERIVATION_DRAFT",
                content_hint="必须包含 Assumptions/Symbol Table/Claim/Derivation/Boundary Cases/Conclusion 六节。",
            )

            for attempt in range(10):
                print(f"  🔍 评审轮次 {attempt+1}...")
                all_pass = True
                for rev in self.reviewers:
                    review_directive = self._generate_reviewer_directive(
                        rev,
                        t,
                        draft,
                        stage_label=f"子任务{i+1}评审第{attempt+1}轮",
                    )
                    ok, feedback = rev.check(draft, t, review_directive=review_directive)
                    if not ok:
                        print(f"  ❌ {rev.name} 驳回！修正中...")
                        draft = generator.call_llm_tagged(
                            f"修正反馈: {feedback}\n当前草稿: {draft}\n"
                            "请优先修复被指出的问题，并保持固定骨架。\n"
                            "若审稿意见表明当前结论过强，优先把 Claim 和 Conclusion 缩小到当前材料真正支持的局部结果，而不是强行保留原结论。\n"
                            "除非本轮确实完成了全局证明，否则不要宣称最终 rho 改进或最终定理已经成立。\n"
                            "如需改动定义、坐标系或变量依赖，必须在 Assumptions 中显式声明，并同步修正后续推导。\n"
                            "禁止为了保住原结论而回避审稿意见。\n"
                            "保持以下固定骨架：\n"
                            "## Assumptions\n"
                            "## Symbol Table\n"
                            "## Claim\n"
                            "## Derivation\n"
                            "## Boundary Cases\n"
                            "## Conclusion",
                            tag_name="DERIVATION_DRAFT",
                            content_hint="输出修订后的完整推导，且保留六节固定结构。",
                        )
                        all_pass = False
                        break
                    else:
                        print(f"  ✅ {rev.name} Pass.")
                if all_pass:
                    break

            global_context.append(f"### 定稿 {i+1}\n{draft}")
            summary = self._build_task_summary(i + 1, str(t), draft)
            summary_context.append(summary)
            draft_records.append(
                {
                    "task": str(t),
                    "summary": summary,
                    "draft": draft,
                    "tokens": self._tokenize_for_overlap(f"{t}\n{summary}"),
                }
            )

        print("\n🧩 [Step 5/6] 合成子任务定稿为最终全文...")
        final_report = self._synthesize_full_report(goal, global_context)
        if not final_report:
            final_report = "\n\n".join(global_context)

        print("\n🏆 [Step 6/6] 科研任务圆满完成！")
        return final_report
