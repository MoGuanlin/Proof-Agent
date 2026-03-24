import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from glob import glob

from agents import (
    correctness_checker,
    global_rev,
    logic_rev,
    orchestrator,
    potential_designer,
    proof_planner,
    proof_writer,
)
from app_config import (
    CANDIDATE_MAX_COUNT,
    CANDIDATE_MEMORY_FILE,
    DISABLE_TEXT_TRUNCATION,
    LOCAL_PROPERTY_DECISION_MAX_PROPERTIES,
    LITERATURE_MAX_CHARS,
    LITERATURE_RAG_CHUNK_CHARS,
    LITERATURE_RAG_DB_FILE,
    LITERATURE_RAG_EMBEDDING_MODEL,
    LITERATURE_RAG_MAX_CHARS,
    LITERATURE_RAG_OVERLAP_CHARS,
    LITERATURE_RAG_PER_DOCUMENT_LIMIT,
    LITERATURE_RAG_SUMMARY_CHARS,
    LITERATURE_RAG_TOP_K,
    LITERATURE_RAG_VECTOR_DIM,
    LITERATURE_EXTRA_MARKDOWN_GLOBS,
    MARKDOWN_CACHE_FILE,
    MARKER_DISABLE_MULTIPROCESSING,
    MARKER_EXTRA_ARGS,
    MARKER_FORCE_CPU,
    MARKER_FORCE_OCR,
    MARKER_TIMEOUT_SECONDS,
    MEMORY_PROPERTY_PACKET_MAX_ITEMS,
    MEMORY_REUSE_MAX_ITEMS,
    MEMORY_SIMILAR_FAILURE_LIMIT,
    MEMORY_SUMMARIZE_MAX_CANDIDATES,
    MEMORY_TERMINAL_REPORT_MAX_ITEMS,
    PDF_PARSE_BACKEND,
    PROOF_REFINEMENT_MAX_ROUNDS,
    PROPOSITION_REVIEW_MAX_ROUNDS,
    REVIEWER_ROLE_OVERRIDES,
)
from candidate_memory import CandidateRecord, MemoryManager
from literature_rag import LiteratureRAG
from verification_tools import render_verification_report, run_verification_spec


class CandidateExplorationPipeline:
    """Encapsulate the candidate exploration loop as a self-contained pipeline."""

    def __init__(self, system, goal, literature_context, max_candidate_count):
        self.system = system
        self.goal = goal
        self.literature_context = literature_context
        self.max_candidate_count = max(1, int(max_candidate_count or 1))
        self.direction = ""
        self.search_stage = ""
        self.passed_candidates = []
        self.pruned_candidates = []
        self.refinement_target = None
        self.refinement_report = ""

    def _select_best_passed_candidate(self, passed_candidates):
        items = list(passed_candidates or [])
        if not items:
            return None
        if len(items) == 1:
            return items[0]

        ranking_payload = []
        for index, candidate in enumerate(items, start=1):
            ranking_payload.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "estimated_c": candidate.estimated_c or "[unknown]",
                    "risk_notes": candidate.risk_notes or "[none]",
                    "terminal_decision": dict(candidate.terminal_decision or {}),
                    "terminal_report": (candidate.artifacts or {}).get("terminal_report", "") or "[missing]",
                    "order_index": index,
                }
            )

        prompt = (
            f"总目标: {self.goal}\n"
            "下面是所有已通过候选的终态信息。请只从这些候选中选出当前最适合进入 proof refinement 的一个。\n\n"
            f"{json.dumps(ranking_payload, ensure_ascii=False, indent=2)}\n\n"
            "请输出 JSON 对象，字段必须包括：best_candidate_id, rationale, ranking。\n"
            "其中 ranking 必须是数组；每个元素必须包含 candidate_id, rank, rationale。\n"
            "排序时请综合 terminal_report、estimated_c、risk_notes，以及候选是否更成熟、更适合进入证明完善阶段。\n"
            "不要提出新的候选，不要输出列表外的 candidate_id。\n"
            "<PASSED_CANDIDATE_RANKING> 标签内只能放 JSON 对象。"
        )
        try:
            payload = self.system._parse_json_object(
                orchestrator.call_llm_tagged(
                    prompt,
                    tag_name="PASSED_CANDIDATE_RANKING",
                    content_hint="标签内必须是合法 JSON 对象。",
                    print_stream=True,
                )
            )
            candidate_id = str(payload.get("best_candidate_id", "")).strip()
            for candidate in items:
                if candidate.candidate_id == candidate_id:
                    return candidate
        except Exception:
            pass
        return items[-1]

    def run(self):
        self.direction = self.system._build_candidate_direction(
            self.goal,
            self.literature_context,
            is_initial=True,
        )
        for candidate_index in range(1, self.max_candidate_count + 1):
            if self.search_stage in {"proof_refinement", "stop"}:
                break
            self._run_candidate_round(candidate_index)

        if not self.search_stage:
            if self.passed_candidates:
                self.search_stage = "proof_refinement_budget_exhausted"
                self.refinement_target = self.refinement_target or self._select_best_passed_candidate(self.passed_candidates)
            else:
                self.search_stage = "budget_exhausted"

        if self.refinement_target is not None:
            self.refinement_report = self.system._run_proof_refinement(
                self.goal,
                self.literature_context,
                self.refinement_target,
            )

        return {
            "passed_candidates": self.passed_candidates,
            "pruned_candidates": self.pruned_candidates,
            "search_stage": self.search_stage,
            "refinement_target": self.refinement_target,
            "refinement_report": self.refinement_report,
        }

    def _run_candidate_round(self, candidate_index):
        candidate = self.system._design_candidate(
            self.goal,
            self.literature_context,
            self.direction,
            candidate_index,
        )
        print(f"\n▶️ 候选 {candidate_index}: {candidate.candidate_id}")
        print(f"   form={candidate.form}")
        self.system.memory.save_candidate(candidate)
        self._explore_candidate(candidate, candidate_index)

    def _explore_candidate(self, candidate, candidate_index):
        self.system._plan_candidate_proof(candidate, self.goal, self.literature_context)
        self.system.memory.save_candidate(candidate)
        if candidate.status == "pruned":
            self._finalize_terminal_candidate(candidate, candidate_index, self.pruned_candidates)
            return

        pending_properties = [
            prop
            for prop in (candidate.priority or self.system.property_order)
            if prop in self.system.property_order
        ]
        seen_properties = set()

        while pending_properties:
            property_name = pending_properties.pop(0)
            if property_name in seen_properties:
                continue
            seen_properties.add(property_name)
            print(f"  🔬 检查性质 {property_name}...")
            ok = self.system._run_candidate_property(
                candidate,
                property_name,
                self.goal,
                self.literature_context,
            )
            self.system.memory.save_candidate(candidate)
            if not ok:
                self._finalize_terminal_candidate(candidate, candidate_index, self.pruned_candidates)
                return

            decision = self.system._decide_candidate_next_property(
                candidate,
                self.goal,
                self.literature_context,
                last_property=property_name,
            )
            if decision["action"] == "prune_candidate":
                reason = decision.get("rationale", "").strip() or f"local planner pruned after {property_name}"
                candidate.mark_pruned(reason)
                candidate.append_log(
                    "prune",
                    "local planner pruned candidate after property pass",
                    property=property_name,
                    reason=reason,
                )
                self.system.memory.save_candidate(candidate)
                self._finalize_terminal_candidate(candidate, candidate_index, self.pruned_candidates)
                return
            if decision["action"] == "complete_candidate":
                break

            ordered_remaining = [
                prop
                for prop in decision.get("updated_priority") or []
                if prop not in seen_properties
            ]
            if not ordered_remaining:
                ordered_remaining = [
                    prop
                    for prop in self.system.property_order
                    if prop not in seen_properties
                ]
            pending_properties = ordered_remaining

        if candidate.status != "pruned":
            candidate.mark_passed()
            candidate.append_log("candidate_pass", "candidate passed all scheduled properties")
            self._finalize_terminal_candidate(candidate, candidate_index, self.passed_candidates)

    def _finalize_terminal_candidate(self, candidate, candidate_index, bucket):
        terminal_report = self.system._record_terminal_candidate(candidate, bucket)
        decision = self.system._decide_after_terminal_candidate(
            self.goal,
            self.literature_context,
            candidate,
            self.passed_candidates,
            self.pruned_candidates,
            remaining_budget=self.max_candidate_count - candidate_index,
        )
        if decision["action"] == "continue_exploring" and candidate_index < self.max_candidate_count:
            self.direction = decision["next_direction"] or self.system._build_candidate_direction(
                self.goal,
                self.literature_context,
                terminal_report=candidate.artifacts.get("terminal_report", terminal_report),
                is_initial=False,
            )
            return

        self.search_stage = decision["action"]
        if decision["action"] == "proof_refinement" and candidate.status == "passed":
            self.refinement_target = candidate
            return

        remaining_budget = self.max_candidate_count - candidate_index
        if (
            int(remaining_budget) <= 0
            and candidate.status != "passed"
            and self.refinement_target is None
        ):
            best_passed = self._select_best_passed_candidate(self.passed_candidates)
            if best_passed is not None:
                self.search_stage = "proof_refinement_budget_exhausted_after_failures"
                self.refinement_target = best_passed


class AutonomousResearchSystem:
    def __init__(self):
        self.candidate_reviewers = [correctness_checker, logic_rev, global_rev]
        self.architecture_mode = "candidate"
        self.memory = MemoryManager(CANDIDATE_MEMORY_FILE)
        self.property_order = ["N1", "N2", "N3", "D4", "Q5", "Q6"]
        self.literature_rag = None

    @staticmethod
    def _sanitize_reviewer_directive(text):
        cleaned = str(text or "").strip()
        cleaned = re.sub(
            r"\[(ACCEPT WITH REVISIONS|ACCEPT|REVISE|COMMENT)\]",
            "[PASS]",
            cleaned,
            flags=re.IGNORECASE,
        )
        hard_constraint = (
            "### Output Format Hard Constraint\n"
            "- 在 <REVIEW_RESULT> 标签内，第一行必须且只能是 [PASS] 或 [REJECT]。\n"
            "- 不得使用 [ACCEPT]、[REVISE]、[COMMENT] 或其它替代结论标签。\n"
            "- 若仅发现可修补问题，仍输出 [PASS]，并在后续行列出修补建议。"
        )
        return f"{cleaned}\n\n{hard_constraint}".strip()

    @staticmethod
    def _cache_path_for_pdf(pdf_path):
        cache_dir = os.path.join(".cache", "markitdown")
        os.makedirs(cache_dir, exist_ok=True)
        abs_pdf_path = os.path.abspath(pdf_path)
        path_hash = hashlib.sha1(abs_pdf_path.encode("utf-8")).hexdigest()[:12]
        stem = os.path.splitext(os.path.basename(pdf_path))[0]
        marker_cfg = (
            f"fo{1 if MARKER_FORCE_OCR else 0}_"
            f"fc{1 if MARKER_FORCE_CPU else 0}_"
            f"dmp{1 if MARKER_DISABLE_MULTIPROCESSING else 0}_"
            f"{' '.join(MARKER_EXTRA_ARGS)}"
        )
        marker_tag = hashlib.sha1(marker_cfg.encode("utf-8")).hexdigest()[:10]
        return os.path.join(cache_dir, f"{stem}.{path_hash}.marker.{marker_tag}.md")

    @staticmethod
    def _marker_env():
        env = os.environ.copy()
        if MARKER_FORCE_CPU:
            env["TORCH_DEVICE"] = "cpu"
            env["CUDA_VISIBLE_DEVICES"] = ""
        return env

    @staticmethod
    def _parse_with_marker(pdf_path):
        marker_single = shutil.which("marker_single")
        marker_cli = shutil.which("marker")
        if not marker_single and not marker_cli:
            raise RuntimeError("marker CLI not found")

        with tempfile.TemporaryDirectory(prefix="marker_out_") as out_dir, tempfile.TemporaryDirectory(prefix="marker_in_") as in_dir:
            if marker_single:
                cmd = [marker_single, pdf_path, "--output_dir", out_dir, "--output_format", "markdown"]
            else:
                staged_pdf = os.path.join(in_dir, os.path.basename(pdf_path))
                shutil.copy2(pdf_path, staged_pdf)
                cmd = [marker_cli, in_dir, "--output_dir", out_dir, "--output_format", "markdown"]

            if MARKER_FORCE_OCR:
                cmd.append("--PdfProvider_force_ocr")
            if MARKER_DISABLE_MULTIPROCESSING:
                cmd.append("--disable_multiprocessing")
            cmd.extend(MARKER_EXTRA_ARGS)

            print(f"⏳ marker running (timeout={MARKER_TIMEOUT_SECONDS}s): {' '.join(cmd)}")
            subprocess.run(
                cmd,
                check=True,
                env=AutonomousResearchSystem._marker_env(),
                timeout=MARKER_TIMEOUT_SECONDS,
            )

            md_candidates = []
            for root, _, files in os.walk(out_dir):
                for name in files:
                    if name.lower().endswith(".md"):
                        md_candidates.append(os.path.join(root, name))
            md_candidates.sort()
            if not md_candidates:
                raise RuntimeError("marker produced no markdown")
            with open(md_candidates[0], "r", encoding="utf-8") as handle:
                return handle.read()

    def _load_or_parse_pdf_markdown(self, pdf_path, force_reparse=False):
        if MARKDOWN_CACHE_FILE and not force_reparse:
            cache_path = os.path.abspath(os.path.expanduser(MARKDOWN_CACHE_FILE))
            print(f"📦 [Step 2/5] 使用指定 Markdown 缓存: {cache_path}")
            with open(cache_path, "r", encoding="utf-8") as handle:
                return handle.read()

        cache_path = self._cache_path_for_pdf(pdf_path)
        pdf_mtime = os.path.getmtime(pdf_path)
        if not force_reparse and os.path.exists(cache_path) and os.path.getmtime(cache_path) >= pdf_mtime:
            print(f"📦 [Step 2/5] 命中解析缓存: {cache_path}")
            with open(cache_path, "r", encoding="utf-8") as handle:
                return handle.read()

        print(
            "📄 [Step 2/5] 解析文献"
            f"（backend={PDF_PARSE_BACKEND}, marker_force_ocr={MARKER_FORCE_OCR}, "
            f"marker_force_cpu={MARKER_FORCE_CPU}, marker_extra_args={MARKER_EXTRA_ARGS}）..."
        )
        raw_md = self._parse_with_marker(pdf_path)
        with open(cache_path, "w", encoding="utf-8") as handle:
            handle.write(raw_md)
        print(f"💾 解析结果已缓存: {cache_path}")
        return raw_md

    @staticmethod
    def _extract_json_array(raw_text):
        text = str(raw_text or "").strip()
        start = text.find("[")
        if start < 0:
            return text

        depth = 0
        in_string = False
        escape = False
        for idx in range(start, len(text)):
            ch = text[idx]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue

            if ch == '"':
                in_string = True
            elif ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    return text[start : idx + 1]
        return text[start:]

    @staticmethod
    def _extract_json_object(raw_text):
        text = str(raw_text or "").strip()
        start = text.find("{")
        if start < 0:
            return text

        depth = 0
        in_string = False
        escape = False
        for idx in range(start, len(text)):
            ch = text[idx]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue

            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : idx + 1]
        return text[start:]

    @staticmethod
    def _repair_json_string_escapes(raw_text):
        text = str(raw_text or "")
        repaired = []
        in_string = False
        idx = 0

        while idx < len(text):
            ch = text[idx]
            if not in_string:
                repaired.append(ch)
                if ch == '"':
                    in_string = True
                idx += 1
                continue

            if ch == '"':
                repaired.append(ch)
                in_string = False
                idx += 1
                continue

            if ch == "\\":
                if idx + 1 >= len(text):
                    repaired.append("\\\\")
                    idx += 1
                    continue

                nxt = text[idx + 1]
                if nxt in '"\\/bfnrt':
                    repaired.append("\\")
                    repaired.append(nxt)
                    idx += 2
                    continue

                if idx + 5 < len(text) and nxt == "u":
                    hex_digits = text[idx + 2 : idx + 6]
                    if all(c in "0123456789abcdefABCDEF" for c in hex_digits):
                        repaired.append("\\")
                        repaired.append("u")
                        repaired.append(hex_digits)
                        idx += 6
                        continue

                repaired.append("\\\\")
                idx += 1
                continue

            if ch == "\n":
                repaired.append("\\n")
                idx += 1
                continue

            if ch == "\r":
                repaired.append("\\r")
                idx += 1
                continue

            if ch == "\t":
                repaired.append("\\t")
                idx += 1
                continue

            repaired.append(ch)
            idx += 1

        return "".join(repaired)

    @classmethod
    def _parse_json_object(cls, raw_text):
        raw = str(raw_text or "").strip()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            candidate = cls._extract_json_object(raw)
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                parsed = json.loads(cls._repair_json_string_escapes(candidate))
        if not isinstance(parsed, dict):
            raise ValueError("expected JSON object")
        return parsed

    @classmethod
    def _parse_json_array(cls, raw_text):
        raw = str(raw_text or "").strip()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            candidate = cls._extract_json_array(raw)
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                parsed = json.loads(cls._repair_json_string_escapes(candidate))
        if not isinstance(parsed, list):
            raise ValueError("expected JSON array")
        return parsed

    @staticmethod
    def _truncate_for_prompt(text, max_chars=24000):
        text = str(text or "").strip()
        if DISABLE_TEXT_TRUNCATION:
            return text
        if max_chars <= 0 or len(text) <= max_chars:
            return text
        return text[:max_chars]

    @staticmethod
    def _extract_markdown_section(md_text, section_title):
        match = re.search(
            rf"(?ims)^##\s*{re.escape(section_title)}\s*$([\s\S]*?)(?=^##\s+|\Z)",
            str(md_text or ""),
        )
        return match.group(1).strip() if match else ""

    @staticmethod
    def _replace_markdown_section(md_text, section_title, new_body):
        text = str(md_text or "")
        replacement = f"## {section_title}\n{str(new_body or '').strip()}\n"
        pattern = rf"(?ims)^##\s*{re.escape(section_title)}\s*$([\s\S]*?)(?=^##\s+|\Z)"
        if re.search(pattern, text):
            return re.sub(pattern, lambda _match: replacement, text, count=1)
        suffix = "\n\n" if text.strip() else ""
        return f"{text.rstrip()}{suffix}{replacement}".strip()

    @staticmethod
    def _strip_tool_reports(md_text):
        text = str(md_text or "").strip()
        if not text:
            return ""
        stripped = re.sub(
            r"(?ims)\n*##\s*Tool Request\s*\n[\s\S]*?(?=\n##\s+|\Z)",
            "",
            text,
        )
        return re.sub(r"\n{3,}", "\n\n", stripped).strip()

    @staticmethod
    def _build_literature_context(raw_md, max_chars=None):
        if max_chars is None:
            max_chars = LITERATURE_MAX_CHARS
        normalized = re.sub(r"\n{3,}", "\n\n", str(raw_md or "")).strip()
        if DISABLE_TEXT_TRUNCATION:
            return normalized
        max_chars = int(max_chars)
        if max_chars <= 0 or len(normalized) <= max_chars:
            return normalized

        head_budget = int(max_chars * 0.7)
        head = normalized[:head_budget]
        keywords = ("theorem", "lemma", "proposition", "bound", "proof", "convergence", "assumption")
        picked = []
        seen = set()
        for line in normalized.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            lowered = stripped.lower()
            if any(keyword in lowered for keyword in keywords):
                key = stripped[:180]
                if key in seen:
                    continue
                seen.add(key)
                picked.append(stripped)
            if sum(len(item) for item in picked) >= max_chars - head_budget:
                break

        if not picked:
            return head
        return f"{head}\n\n[Key Excerpts]\n{'\n'.join(picked)}"[:max_chars]

    @staticmethod
    def _read_markdown_file(path):
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()

    def _build_literature_documents(self, pdf_path, raw_md):
        documents = []
        normalized = str(raw_md or "").strip()
        if normalized:
            documents.append(
                {
                    "source_path": os.path.abspath(pdf_path),
                    "title": os.path.splitext(os.path.basename(pdf_path))[0] or "Primary Paper",
                    "text": normalized,
                    "metadata": {"kind": "primary_pdf_markdown"},
                }
            )

        extra_patterns = [os.path.join(".cache", "markitdown", "*.md"), *LITERATURE_EXTRA_MARKDOWN_GLOBS]
        seen_paths = set()
        for pattern in extra_patterns:
            for match in sorted(glob(pattern)):
                abs_path = os.path.abspath(match)
                if abs_path in seen_paths or not os.path.isfile(abs_path):
                    continue
                seen_paths.add(abs_path)
                try:
                    text = self._read_markdown_file(abs_path)
                except Exception:
                    continue
                if not str(text or "").strip():
                    continue
                documents.append(
                    {
                        "source_path": abs_path,
                        "title": os.path.splitext(os.path.basename(abs_path))[0] or "Literature Note",
                        "text": text,
                        "metadata": {"kind": "markdown_corpus"},
                    }
                )
        return documents

    def _initialize_literature_rag(self, pdf_path, raw_md):
        documents = self._build_literature_documents(pdf_path, raw_md)
        if not documents:
            self.literature_rag = None
            return
        self.literature_rag = LiteratureRAG(
            documents,
            db_path=LITERATURE_RAG_DB_FILE,
            chunk_chars=LITERATURE_RAG_CHUNK_CHARS,
            overlap_chars=LITERATURE_RAG_OVERLAP_CHARS,
            vector_dim=LITERATURE_RAG_VECTOR_DIM,
            embedding_model=LITERATURE_RAG_EMBEDDING_MODEL,
        )

    def _compose_literature_packet(
        self,
        base_context,
        query,
        top_k=None,
        snippet_max_chars=None,
        summary_max_chars=None,
    ):
        summary_budget = LITERATURE_RAG_SUMMARY_CHARS if summary_max_chars is None else int(summary_max_chars)
        snippet_budget = LITERATURE_RAG_MAX_CHARS if snippet_max_chars is None else int(snippet_max_chars)
        top_k = LITERATURE_RAG_TOP_K if top_k is None else int(top_k)

        summary = self._truncate_for_prompt(base_context, max_chars=summary_budget)
        snippets = ""
        if self.literature_rag is not None and str(query or "").strip():
            snippets = self.literature_rag.render(
                query,
                top_k=top_k,
                max_chars=snippet_budget,
                per_document_limit=LITERATURE_RAG_PER_DOCUMENT_LIMIT,
            )

        if summary and snippets:
            return f"文献摘要:\n{summary}\n\n相关文献片段:\n{snippets}"
        return snippets or summary or ""

    def _customize_team(self, macro_goal, literature_context):
        del macro_goal, literature_context
        print("🎨 [Step 1/5] 加载静态专家指令集...")
        team = [
            orchestrator,
            potential_designer,
            proof_planner,
            proof_writer,
            correctness_checker,
            logic_rev,
            global_rev,
        ]

        configured_prompts = {}
        for agent in team:
            override_prompt = REVIEWER_ROLE_OVERRIDES.get(agent.name)
            if override_prompt:
                agent.role = override_prompt
            elif not str(agent.role or "").strip():
                agent.role = f"你是 {agent.name}，请围绕当前数学任务给出严谨、可执行的输出。"
            configured_prompts[agent.name] = agent.role

        with open("customized_prompts.json", "w", encoding="utf-8") as handle:
            json.dump(configured_prompts, handle, ensure_ascii=False, indent=2)
        print("📝 已保存静态角色配置: customized_prompts.json")
        print("✅ 专家团队角色加载完成。")

    def _generate_reviewer_directive(self, reviewer, context, draft, stage_label="子任务审查"):
        reviewer_name = reviewer.name if hasattr(reviewer, "name") else "Reviewer"
        assumptions = self._truncate_for_prompt(self._extract_markdown_section(draft, "Assumptions"), max_chars=3200) or "[缺失]"
        claim = self._truncate_for_prompt(self._extract_markdown_section(draft, "Claim"), max_chars=2400) or "[缺失]"
        derivation = self._truncate_for_prompt(self._extract_markdown_section(draft, "Derivation"), max_chars=5200) or "[缺失]"
        conclusion = self._truncate_for_prompt(self._extract_markdown_section(draft, "Conclusion"), max_chars=2400) or "[缺失]"
        q6_contract = ""
        if "q6" in str(context or "").lower():
            q6_contract = (
                "8) 若 Q6 草稿声称“可压到 1.98”“支持 rho < 1.98”或其他全局上界改进，"
                "必须检查 Derivation 是否真的写出了明确常数链条；若没有，视为致命 overclaim 并判 [REJECT]。\n"
            )
        directive = (
            f"阶段: {stage_label}\n"
            f"审稿人: {reviewer_name}\n"
            f"Current Task Contract:\n"
            f"- 任务上下文: {self._truncate_for_prompt(context, max_chars=2400)}\n"
            f"- 当前假设: {assumptions}\n"
            f"- 当前主张: {claim}\n"
            f"- 当前结论: {conclusion}\n"
            "高优先级检查项:\n"
            "1) 仅审查当前草稿，不继承过往轮次已修复的问题。\n"
            "2) 检查草稿中的关键符号、变量依赖、坐标设定是否前后一致。\n"
            "3) 检查 Derivation 中每个关键推导是否真的支持当前 Claim，而不是只给直觉说明。\n"
            "4) 区分致命问题与可修补问题；缺少提示语、记号轻微漂移、边界提醒不足默认是可修补问题。\n"
            "5) 若当前任务只是局部性质、参数化、偏导或中间不等式，不得要求其独立完成最终全局定理。\n"
            "6) 若草稿主动宣称最终全局定理、最终上界或完整证明闭环已经完成，而上下文并未要求该强度，视为致命 overclaim。\n"
            "7) 若 Derivation 已明确声明替代定义或重参数化，只要后续自洽，不得仅因不同于原文习惯而驳回。\n"
            f"{q6_contract}"
            f"Derivation 摘录:\n{derivation}\n"
        )
        return self._sanitize_reviewer_directive(directive)

    def _candidate_property_snapshot(self, candidate):
        bits = []
        for prop in self.property_order:
            detail = candidate.property_status.get(prop) or {}
            status = str(detail.get("status", "")).strip() or "untested"
            bits.append(f"{prop}={status}")
        return ", ".join(bits)

    def _build_terminal_candidate_report(self, candidate):
        property_lines = []
        for prop in self.property_order:
            detail = candidate.property_status.get(prop) or {}
            status = str(detail.get("status", "")).strip() or "untested"
            note = str(detail.get("note", "")).strip()
            line = f"- {prop}: {status}"
            if note:
                line += f" | {note}"
            property_lines.append(line)
        proposition_lines = []
        for prop in self.property_order:
            snapshot = candidate.proposition_snapshot(prop, max_items=8)
            if snapshot:
                proposition_lines.append(f"- {prop}: {snapshot}")
        decision = candidate.terminal_decision or {}
        return (
            f"Candidate ID: {candidate.candidate_id}\n"
            f"Status: {candidate.status}\n"
            f"Form: {candidate.form}\n"
            f"Derived From: {candidate.derived_from or '[none]'}\n"
            f"Intuition: {candidate.intuition or '[missing]'}\n"
            f"Estimated C: {candidate.estimated_c or '[unknown]'}\n"
            f"Risk Notes: {candidate.risk_notes or '[none]'}\n"
            f"Pruned Reason: {candidate.pruned_reason or '[none]'}\n"
            "Property Status:\n"
            f"{chr(10).join(property_lines)}\n"
            "Proposition Status:\n"
            f"{chr(10).join(proposition_lines) if proposition_lines else '[none]'}\n"
            "Post-Terminal Decision:\n"
            f"- action: {decision.get('action', '[none]') or '[none]'}\n"
            f"- rationale: {decision.get('rationale', '[none]') or '[none]'}\n"
            f"- next_direction: {decision.get('next_direction', '[none]') or '[none]'}"
        )

    @staticmethod
    def _property_guidance(property_name):
        guidance = {
            "N1": (
                "N1 合同: 必须明确写出基础情形/规范化情形，并说明候选势函数在该情形下如何精确满足要求。"
                "禁止只给口头直觉；若仍需额外验证，必须在 Verification Needs 中明确写出。"
            ),
            "N2": (
                "N2 合同: 必须展示去掉末端盘后的单调性比较式，说明两边对象、参数和差值来源。"
            ),
            "N3": (
                "N3 合同: 必须明确内部分裂后的次可加性或对应不等式，并交代分裂点两侧项如何重组。"
            ),
            "D4": (
                "D4 合同: 必须说明关于终点 v 的依赖如何被控制，尤其是凸性、消去或无关性论证不能跳步。"
            ),
            "Q5": (
                "Q5 合同: 必须把可解析部分与需要数值证书的部分分开写；只要数值证书未验证通过，就不能视为该性质完成。"
            ),
            "Q6": (
                "Q6 合同: 必须围绕极端链/极端配置给出下界分析，不要把一般情形直觉误当成极端链结论。"
                "应直接给出极端链下界的数学论证，并明确最危险的边界配置。"
                "若没有写出明确常数链条（新常数如何进入下界比例、Lemma 3 型估计、以及与 rho/lambda 的关系），"
                "不得声称“可压到 1.98”“支持 rho < 1.98”或“允许更小全局上界”；此时 Conclusion 只能写候选方向或局部约束。"
                "Q6 默认由大模型 reviewer 做逻辑审查，不要求生成工具验证协议。"
            ),
        }
        return guidance.get(property_name, "")

    @staticmethod
    def _verification_needs_closed(section_text):
        normalized = re.sub(r"\s+", "", str(section_text or "")).lower()
        if not normalized:
            return True
        closed_markers = (
            "none",
            "[none]",
            "n/a",
            "na",
            "无需验证",
            "无需额外验证",
            "不需要额外验证",
            "无需额外的数值或符号验证",
            "无需额外数值或符号验证",
            "无需数值或符号验证",
            "无需进一步数值或符号验证",
            "无额外验证",
            "无",
            "无。",
            "无进一步验证",
            "已闭合",
            "已验证完毕",
            "nofurtherverification",
            "noadditionalverification",
            "noadditionalnumericalorsymbolicverification",
            "closed",
        )
        return any(marker in normalized for marker in closed_markers)

    def _normalize_verification_needs_section(self, draft):
        text = str(draft or "").strip()
        if not text:
            return ""
        section = self._extract_markdown_section(text, "Verification Needs")
        if self._verification_needs_closed(section):
            return self._replace_markdown_section(text, "Verification Needs", "None")

        cleaned_lines = []
        for raw_line in str(section or "").splitlines():
            line = str(raw_line).strip()
            if not line:
                continue
            line = re.sub(r"^(?:[-*•]+|\d+[.)])\s*", "", line).strip()
            if not line:
                continue
            cleaned_lines.append(f"- {line}")
        normalized_body = "\n".join(cleaned_lines) if cleaned_lines else "None"
        return self._replace_markdown_section(text, "Verification Needs", normalized_body)

    @staticmethod
    def _verification_needs_is_none(section_text):
        return str(section_text or "").strip().lower() == "none"

    def _q6_constant_chain_feedback(self, draft):
        claim = self._extract_markdown_section(draft, "Claim")
        conclusion = self._extract_markdown_section(draft, "Conclusion")
        claim_zone = f"{claim}\n{conclusion}".strip()
        normalized_claim = re.sub(r"\s+", " ", claim_zone.lower())
        overclaim_markers = (
            "1.98",
            "rho < 1.98",
            "rho<1.98",
            "压到 1.98",
            "压到1.98",
            "可压到 1.98",
            "可压到1.98",
            "支持 rho < 1.98",
            "支持rho<1.98",
            "更小全局上界",
            "global upper bound",
            "smaller upper bound",
        )
        if not any(marker in normalized_claim for marker in overclaim_markers):
            return ""

        derivation = self._extract_markdown_section(draft, "Derivation")
        boundary = self._extract_markdown_section(draft, "Boundary Cases")
        support_zone = re.sub(r"\s+", " ", f"{derivation}\n{boundary}".lower())
        support_markers = (
            "lemma 3",
            "lemma3",
            "引理 3",
            "引理3",
            "rho",
            "lambda",
            "常数链",
            "下界常数",
            "lower bound constant",
            "lower-bound constant",
            "extreme chain",
            "极端链",
        )
        support_hits = sum(1 for marker in support_markers if marker in support_zone)
        if support_hits >= 4:
            return ""
        return (
            "[REJECT]\n"
            "致命问题: 当前 Q6 草稿在 Claim/Conclusion 中声称“可压到 1.98”或等价的全局上界改进，"
            "但 Derivation/Boundary Cases 没有写出足够明确的常数链条，无法说明新常数如何进入极端链下界、"
            "Lemma 3 型估计以及 rho/lambda 关系。\n"
            "修复要求: 删除该全局改进结论，或补出完整常数链条后再声称 1.98 / rho < 1.98。"
        )

    def _record_terminal_candidate(self, candidate, bucket):
        terminal_report = self._build_terminal_candidate_report(candidate)
        candidate.artifacts["terminal_report"] = terminal_report
        candidate.append_log(
            "terminal",
            "candidate reached terminal state",
            terminal_status=candidate.status,
            property_snapshot=self._candidate_property_snapshot(candidate),
        )
        self.memory.save_candidate(candidate)
        bucket.append(candidate)
        return terminal_report

    def _default_post_terminal_decision(self, candidate, remaining_budget):
        if int(remaining_budget) <= 0:
            action = "stop"
        elif candidate.status == "passed":
            action = "proof_refinement"
        else:
            action = "continue_exploring"
        return {
            "action": action,
            "rationale": "default fallback decision",
            "next_direction": candidate.source_direction if action == "continue_exploring" else "",
            "stage": "post_terminal",
        }

    def _normalize_post_terminal_decision(self, candidate, decision, remaining_budget):
        normalized = dict(decision or {})
        action = str(normalized.get("action", "")).strip().lower()
        if action not in {"continue_exploring", "proof_refinement", "stop"}:
            action = self._default_post_terminal_decision(candidate, remaining_budget)["action"]
        if int(remaining_budget) <= 0 and action == "continue_exploring":
            action = "stop"
        if candidate.status != "passed" and action == "proof_refinement":
            action = "continue_exploring" if int(remaining_budget) > 0 else "stop"
        next_direction = str(normalized.get("next_direction", "")).strip()
        if action != "continue_exploring":
            next_direction = ""
        rationale = str(normalized.get("rationale", "")).strip() or "no rationale provided"
        return {
            "action": action,
            "rationale": rationale,
            "next_direction": next_direction,
            "stage": str(normalized.get("stage", "")).strip() or "post_terminal",
        }

    def _decide_after_terminal_candidate(
        self,
        goal,
        literature_context,
        candidate,
        passed_candidates,
        pruned_candidates,
        remaining_budget,
    ):
        fallback = self._default_post_terminal_decision(candidate, remaining_budget)
        if int(remaining_budget) <= 0:
            candidate.set_terminal_decision(**fallback)
            candidate.artifacts["post_terminal_decision"] = json.dumps(fallback, ensure_ascii=False, indent=2)
            candidate.append_log("post_terminal_decision", "no remaining budget; stop", decision=fallback)
            self.memory.save_candidate(candidate)
            return fallback

        literature_packet = self._compose_literature_packet(
            literature_context,
            query=(
                f"{goal}\n{candidate.form}\n{candidate.status}\n"
                f"{candidate.pruned_reason}\npost terminal decision next direction"
            ),
            top_k=LITERATURE_RAG_TOP_K,
            snippet_max_chars=9000,
            summary_max_chars=4000,
        )
        terminal_report = candidate.artifacts.get("terminal_report") or self._build_terminal_candidate_report(candidate)
        recent_summary = self.memory.terminal_report_summary(
            max_items=MEMORY_TERMINAL_REPORT_MAX_ITEMS,
            max_chars=7000,
        )
        prompt = (
            f"总目标: {goal}\n"
            f"文献关键上下文:\n{self._truncate_for_prompt(literature_packet, max_chars=18000)}\n\n"
            f"最新终态候选报告:\n{terminal_report}\n\n"
            f"近期终态候选摘要:\n{recent_summary or '[暂无]'}\n\n"
            f"当前统计: passed={len(passed_candidates)}, pruned={len(pruned_candidates)}, remaining_budget={remaining_budget}\n\n"
            "请输出 JSON 对象，字段必须包括：action, rationale, next_direction, stage。\n"
            "其中 action 只能取：continue_exploring, proof_refinement, stop。\n"
            "规则：\n"
            "1) 若最新候选已通过全部性质，但仍有明显优化空间，可选 continue_exploring；\n"
            "2) 若最新候选已通过且更适合转入证明完善阶段，可选 proof_refinement；\n"
            "3) 若当前搜索应停止，可选 stop；\n"
            "4) 只有 action=continue_exploring 时，next_direction 才应非空，并且必须具体；\n"
            "5) <POST_TERMINAL_DECISION> 标签内只能放 JSON 对象。"
        )
        try:
            raw_decision = self._parse_json_object(
                orchestrator.call_llm_tagged(
                    prompt,
                    tag_name="POST_TERMINAL_DECISION",
                    content_hint="标签内必须是合法 JSON 对象。",
                    print_stream=True,
                )
            )
        except Exception as exc:
            raw_decision = dict(fallback)
            raw_decision["rationale"] = f"defaulted because decision parsing failed: {exc}"
        decision = self._normalize_post_terminal_decision(candidate, raw_decision, remaining_budget)
        candidate.set_terminal_decision(**decision)
        candidate.artifacts["post_terminal_decision"] = json.dumps(decision, ensure_ascii=False, indent=2)
        candidate.append_log("post_terminal_decision", "post-terminal decision recorded", decision=decision)
        candidate.artifacts["terminal_report"] = self._build_terminal_candidate_report(candidate)
        self.memory.save_candidate(candidate)
        return decision

    def _build_candidate_direction(self, goal, literature_context, terminal_report="", is_initial=False):
        memory_summary = self.memory.summarize_for_prompt(
            max_candidates=MEMORY_SUMMARIZE_MAX_CANDIDATES,
            max_chars=12000,
        )
        terminal_reports = self.memory.terminal_report_summary(
            max_items=MEMORY_TERMINAL_REPORT_MAX_ITEMS,
            max_chars=7000,
        )
        literature_packet = self._compose_literature_packet(
            literature_context,
            query=f"{goal}\n{terminal_report}\n候选设计方向\nN1 N2 N3 D4 Q5 Q6",
            top_k=LITERATURE_RAG_TOP_K,
            snippet_max_chars=12000,
            summary_max_chars=5000,
        )
        prompt_header = (
            "请给出首轮探索方向。"
            if is_initial
            else "请基于最新终态报告给出下一轮探索方向。"
        )
        terminal_block = (
            f"最新终态报告:\n{terminal_report}\n\n"
            if str(terminal_report or "").strip()
            else ""
        )
        prompt = (
            f"总目标: {goal}\n"
            f"文献关键上下文:\n{self._truncate_for_prompt(literature_packet, max_chars=24000)}\n\n"
            f"{terminal_block}"
            f"近期终态候选摘要:\n{terminal_reports or '[暂无终态候选]'}\n\n"
            f"候选历史摘要:\n{memory_summary or '[暂无历史候选]'}\n\n"
            f"{prompt_header}\n"
            "请给出下一条最值得探索的势函数设计方向，重点回答：\n"
            "1) 下一轮应该优先尝试什么势函数族或参数调整；\n"
            "2) 为什么它比历史失败路径更有希望；\n"
            "3) 下一轮最先检查哪个性质，以及为什么。\n"
            "请在 <DIRECTION> 标签内输出 3-8 行的具体方向说明。"
        )
        return orchestrator.call_llm_tagged(
            prompt,
            tag_name="DIRECTION",
            content_hint="输出简洁、具体的下一轮设计方向。",
            print_stream=True,
        ).strip()

    def _design_candidate(self, goal, literature_context, direction, candidate_index):
        memory_summary = self.memory.summarize_for_prompt(
            max_candidates=MEMORY_SUMMARIZE_MAX_CANDIDATES,
            max_chars=12000,
        )
        literature_packet = self._compose_literature_packet(
            literature_context,
            query=f"{goal}\n{direction}\n新势函数候选\n{' '.join(self.property_order)}",
            top_k=LITERATURE_RAG_TOP_K,
            snippet_max_chars=12000,
            summary_max_chars=5000,
        )
        prompt = (
            f"研究目标: {goal}\n"
            f"文献关键上下文:\n{self._truncate_for_prompt(literature_packet, max_chars=24000)}\n\n"
            f"当前方向:\n{direction}\n\n"
            f"历史候选摘要:\n{memory_summary or '[暂无历史候选]'}\n\n"
            "请提出一个新的势函数候选，并输出 JSON 对象，字段必须包括：\n"
            "candidate_id, form, derived_from, intuition, predicted_properties。\n"
            "其中 predicted_properties 必须是对象，键仅可使用 N1,N2,N3,D4,Q5,Q6；每个值为一段简短判断，说明该性质大概率通过/困难点。\n"
            "要求：\n"
            "1) form 必须给出明确的数学形式，而不是泛泛建议；\n"
            "2) intuition 必须说明与已有候选相比，为什么值得试；\n"
            "3) 若只是微调历史候选，要在 derived_from 中写明来源；\n"
            "4) <CANDIDATE_JSON> 标签内只能放 JSON 对象，不得输出额外解释。"
        )
        payload = self._parse_json_object(
            potential_designer.call_llm_tagged(
                prompt,
                tag_name="CANDIDATE_JSON",
                content_hint="标签内必须是合法 JSON 对象。",
            )
        )
        candidate_id = str(payload.get("candidate_id", "")).strip() or f"candidate_{candidate_index:03d}"
        candidate = CandidateRecord(
            candidate_id=candidate_id,
            form=str(payload.get("form", "")).strip(),
            derived_from=str(payload.get("derived_from", "")).strip() or None,
            intuition=str(payload.get("intuition", "")).strip(),
            source_direction=direction,
        )
        candidate.ensure_properties()
        predicted = payload.get("predicted_properties") or {}
        if isinstance(predicted, dict):
            for prop, note in predicted.items():
                if prop in candidate.property_status:
                    candidate.mark_property(prop, "hypothesis", note=str(note).strip())
        candidate.append_log("design", "candidate proposed", predicted_properties=predicted)
        return candidate

    def _plan_candidate_proof(self, candidate, goal, literature_context):
        planning_memory = self._candidate_planning_memory(candidate)
        literature_packet = self._compose_literature_packet(
            literature_context,
            query=(
                f"{goal}\n{candidate.form}\n{candidate.intuition}\n{candidate.source_direction}\n"
                "proof plan reusable props needs redo priority N2 N3 D4 Q5 Q6"
            ),
            top_k=LITERATURE_RAG_TOP_K,
            snippet_max_chars=5000,
            summary_max_chars=2200,
        )
        prompt = (
            f"研究目标: {goal}\n"
            f"文献关键上下文:\n{self._truncate_for_prompt(literature_packet, max_chars=9000)}\n\n"
            f"候选 ID: {candidate.candidate_id}\n"
            f"候选形式: {candidate.form}\n"
            f"设计动机: {candidate.intuition}\n"
            f"来源方向: {candidate.source_direction}\n\n"
            f"候选/性质记忆:\n{planning_memory or '[暂无]'}\n\n"
            "请输出 JSON 对象，字段必须包括：\n"
            "reusable_props, needs_redo, priority, risk_notes, estimated_c, obvious_failure。\n"
            "其中 obvious_failure 必须是对象，包含 status, property, reason 三个字段；若无显然失败则 status=false。\n"
            "要求：\n"
            "1) priority 必须按最合适的验证顺序排列，优先必要条件；\n"
            "2) 若某性质在规划阶段即可判定明显失败，直接在 obvious_failure 中给出；\n"
            "3) <PROOF_PLAN_JSON> 标签内只能放 JSON 对象。"
        )
        payload = self._parse_json_object(
            proof_planner.call_llm_tagged(
                prompt,
                tag_name="PROOF_PLAN_JSON",
                content_hint="标签内必须是合法 JSON 对象。",
            )
        )
        candidate.reusable_props = [
            str(item).strip() for item in payload.get("reusable_props") or [] if str(item).strip()
        ]
        candidate.needs_redo = [
            str(item).strip() for item in payload.get("needs_redo") or [] if str(item).strip()
        ]
        priority = [str(item).strip() for item in payload.get("priority") or [] if str(item).strip()]
        normalized_priority = []
        for item in priority + list(self.property_order):
            if item in candidate.property_status and item not in normalized_priority:
                normalized_priority.append(item)
        candidate.priority = normalized_priority or list(self.property_order)
        candidate.risk_notes = str(payload.get("risk_notes", "")).strip()
        candidate.estimated_c = str(payload.get("estimated_c", "")).strip()
        candidate.artifacts["proof_plan"] = json.dumps(payload, ensure_ascii=False, indent=2)
        candidate.append_log("plan", "proof plan created", payload=payload)

        obvious_failure = payload.get("obvious_failure") or {}
        if str(obvious_failure.get("status", "")).strip().lower() in {"true", "1", "yes"} or obvious_failure.get("status") is True:
            fail_property = str(obvious_failure.get("property", "")).strip() or "unknown"
            reason = str(obvious_failure.get("reason", "")).strip() or "planner identified an obvious failure"
            if fail_property in candidate.property_status:
                candidate.mark_property(fail_property, "fail", note=reason)
            candidate.mark_pruned(f"{fail_property} 规划阶段失败: {reason}")
            candidate.append_log("prune", "planner pruned candidate", property=fail_property, reason=reason)
        return payload

    def _candidate_memory_context(self, candidate):
        similar = self.memory.find_similar_failures(
            candidate.form,
            limit=MEMORY_SIMILAR_FAILURE_LIMIT,
        )
        if not similar:
            return self.memory.summarize_for_prompt(
                max_candidates=MEMORY_SUMMARIZE_MAX_CANDIDATES,
                max_chars=5000,
            )
        lines = [
            self.memory.summarize_for_prompt(
                max_candidates=MEMORY_SUMMARIZE_MAX_CANDIDATES,
                max_chars=3000,
            )
        ]
        for item in similar:
            lines.append(
                f"- 相似失败 {item.candidate_id}: form={item.form}; pruned_reason={item.pruned_reason or '[缺失]'}"
            )
        return "\n".join(line for line in lines if line).strip()

    @staticmethod
    def _remaining_candidate_properties(candidate, property_order):
        remaining = []
        for prop in property_order:
            detail = candidate.property_status.get(prop) or {}
            status = str(detail.get("status", "")).strip().lower()
            if status != "pass":
                remaining.append(prop)
        return remaining

    def _candidate_planning_memory(self, candidate):
        parts = [
            self.memory.summarize_for_prompt(
                max_candidates=MEMORY_SUMMARIZE_MAX_CANDIDATES,
                max_chars=2500,
            )
        ]
        for prop in self.property_order:
            packet = self.memory.property_memory_packet(
                prop,
                form_text=candidate.form,
                max_items=MEMORY_PROPERTY_PACKET_MAX_ITEMS,
                max_chars=900,
            )
            if packet:
                parts.append(f"[{prop} proposition 记忆]\n{packet}")
        return self._truncate_for_prompt("\n\n".join(part for part in parts if part), max_chars=6500)

    def _property_memory_context(self, candidate, property_name):
        base_context = self._candidate_memory_context(candidate)
        property_packet = self.memory.property_memory_packet(
            property_name,
            form_text=candidate.form,
            max_items=MEMORY_PROPERTY_PACKET_MAX_ITEMS,
            max_chars=2500,
        )
        if not property_packet:
            return base_context
        return "\n\n".join(
            part
            for part in [base_context, f"[{property_name} proposition 级历史知识]\n{property_packet}"]
            if part
        ).strip()

    def _property_reuse_context(
        self,
        candidate,
        property_name,
        max_items=MEMORY_REUSE_MAX_ITEMS,
        max_chars=2200,
    ):
        return self.memory.proposition_reuse_packet(
            property_name,
            form_text=candidate.form,
            max_items=max_items,
            max_chars=max_chars,
        )

    def _tool_request_reuse_context(
        self,
        candidate,
        property_name,
        max_items=MEMORY_REUSE_MAX_ITEMS,
        max_chars=2200,
    ):
        return self.memory.tool_request_reuse_packet(
            property_name,
            form_text=candidate.form,
            max_items=max_items,
            max_chars=max_chars,
        )

    def _proposition_dependency_context(self, candidate, property_name, proposition):
        dep_ids = [str(dep).strip() for dep in proposition.get("dependencies") or [] if str(dep).strip()]
        if not dep_ids:
            return "[无]"
        items = candidate.proposition_items(property_name)
        lines = []
        for dep_id in dep_ids:
            entry = items.get(dep_id) or {}
            artifact = candidate.artifacts.get(str(entry.get("artifact_key", "")).strip(), "")
            conclusion = self._extract_markdown_section(artifact, "Conclusion") if artifact else ""
            note = conclusion or str(entry.get("note", "")).strip() or "[无结论摘要]"
            lines.append(
                f"- {dep_id}: status={entry.get('status', 'unknown')}; "
                f"title={entry.get('title', dep_id) or dep_id}; note={note}"
            )
        return "\n".join(lines).strip() or "[无]"

    def _default_candidate_progress_decision(self, candidate):
        remaining = self._remaining_candidate_properties(candidate, self.property_order)
        if not remaining:
            action = "complete_candidate"
            next_property = ""
        else:
            action = "continue_candidate"
            next_property = remaining[0]
        return {
            "action": action,
            "next_property": next_property,
            "updated_priority": remaining,
            "rationale": "default in-candidate planner decision",
            "risk_update": candidate.risk_notes or "",
        }

    def _normalize_candidate_progress_decision(self, candidate, decision):
        fallback = self._default_candidate_progress_decision(candidate)
        normalized = dict(decision or {})
        remaining = self._remaining_candidate_properties(candidate, self.property_order)
        allowed = {"continue_candidate", "prune_candidate", "complete_candidate"}
        action = str(normalized.get("action", "")).strip().lower()
        if action not in allowed:
            action = fallback["action"]

        updated_priority = []
        raw_priority = normalized.get("updated_priority") or []
        for item in list(raw_priority) + remaining:
            prop = str(item).strip()
            if prop in remaining and prop not in updated_priority:
                updated_priority.append(prop)
        if not updated_priority and remaining:
            updated_priority = list(remaining)

        next_property = str(normalized.get("next_property", "")).strip()
        if next_property not in updated_priority:
            next_property = updated_priority[0] if updated_priority else ""

        if action == "complete_candidate" and remaining:
            action = "continue_candidate"
        if action == "continue_candidate" and not updated_priority:
            action = "complete_candidate"
            next_property = ""
        if action == "prune_candidate":
            next_property = ""
            updated_priority = []

        rationale = str(normalized.get("rationale", "")).strip() or fallback["rationale"]
        risk_update = str(normalized.get("risk_update", "")).strip()
        return {
            "action": action,
            "next_property": next_property,
            "updated_priority": updated_priority,
            "rationale": rationale,
            "risk_update": risk_update,
        }

    def _decide_candidate_next_property(self, candidate, goal, literature_context, last_property):
        fallback = self._default_candidate_progress_decision(candidate)
        if fallback["action"] == "complete_candidate":
            return fallback

        remaining = self._remaining_candidate_properties(candidate, self.property_order)
        if LOCAL_PROPERTY_DECISION_MAX_PROPERTIES > 0:
            property_scope = remaining[: int(LOCAL_PROPERTY_DECISION_MAX_PROPERTIES)]
        else:
            property_scope = remaining
        property_packets = []
        for prop in property_scope:
            packet = self.memory.property_memory_packet(
                prop,
                form_text=candidate.form,
                max_items=MEMORY_PROPERTY_PACKET_MAX_ITEMS,
                max_chars=1800,
            )
            if packet:
                property_packets.append(f"[{prop}]\n{packet}")
        literature_packet = self._compose_literature_packet(
            literature_context,
            query=(
                f"{goal}\n{candidate.form}\n{candidate.intuition}\n"
                f"{self._candidate_property_snapshot(candidate)}\n"
                f"last_property={last_property}\nnext local proof step"
            ),
            top_k=LITERATURE_RAG_TOP_K,
            snippet_max_chars=8000,
            summary_max_chars=3500,
        )
        prompt = (
            f"总目标: {goal}\n"
            f"文献关键上下文:\n{self._truncate_for_prompt(literature_packet, max_chars=18000)}\n\n"
            f"候选 ID: {candidate.candidate_id}\n"
            f"候选形式: {candidate.form}\n"
            f"设计动机: {candidate.intuition or '[缺失]'}\n"
            f"主要风险: {candidate.risk_notes or '[暂无]'}\n"
            f"当前性质快照: {self._candidate_property_snapshot(candidate)}\n"
            f"刚刚完成的性质: {last_property}\n"
            f"剩余未通过性质: {', '.join(remaining) or '[无]'}\n\n"
            f"相关性质记忆:\n{self._truncate_for_prompt(chr(10).join(property_packets), max_chars=12000) or '[暂无]'}\n\n"
            "你现在是探索层内部的本地 proof planner。"
            "请基于当前候选的局部进展，决定下一步在该候选内部该做什么。"
            "输出 JSON 对象，字段必须包括：action, next_property, updated_priority, rationale, risk_update。\n"
            "规则：\n"
            "1) action 只能取 continue_candidate, prune_candidate, complete_candidate；\n"
            "2) 只有当所有六个性质都已通过时，才能输出 complete_candidate；\n"
            "3) 若存在明显跨性质张力，说明当前候选应在本地提前放弃，可输出 prune_candidate；\n"
            "4) next_property 和 updated_priority 只能使用剩余未通过的性质名；\n"
            "5) updated_priority 必须是字符串数组，表示本候选内部后续验证顺序；\n"
            "6) risk_update 应简短更新当前候选最值得警惕的风险；\n"
            "7) <LOCAL_PLANNER_DECISION> 标签内只能放 JSON 对象。"
        )
        try:
            raw = self._parse_json_object(
                proof_planner.call_llm_tagged(
                    prompt,
                    tag_name="LOCAL_PLANNER_DECISION",
                    content_hint="标签内必须是合法 JSON 对象。",
                    print_stream=True,
                )
            )
        except Exception as exc:
            raw = dict(fallback)
            raw["rationale"] = f"defaulted because local planner decision parsing failed: {exc}"

        decision = self._normalize_candidate_progress_decision(candidate, raw)
        candidate.artifacts[f"local_planner_decision_after_{last_property}"] = json.dumps(
            decision,
            ensure_ascii=False,
            indent=2,
        )
        candidate.append_log(
            "local_planner_decision",
            f"local planner decided next step after {last_property}",
            decision=decision,
        )
        if decision.get("updated_priority"):
            candidate.priority = list(decision["updated_priority"])
        if decision.get("risk_update"):
            candidate.risk_notes = decision["risk_update"]
        self.memory.save_candidate(candidate)
        return decision

    @staticmethod
    def _normalize_tool_request_id(value, fallback):
        cleaned = re.sub(r"[^A-Za-z0-9_:-]+", "_", str(value or "").strip())
        return cleaned or fallback

    def _default_property_propositions(self, property_name):
        return [
            {
                "id": f"{property_name.lower()}_prop_1",
                "title": f"{property_name} core proposition",
                "claim": f"Establish the core local claim needed for {property_name}.",
                "dependencies": [],
                "verification_focus": f"Check the main mathematical step for {property_name}.",
                "requires_tool": property_name == "Q5",
                "tool_plan": (
                    {
                        "should_request": True,
                        "preferred_mode": "numeric_1d",
                        "goal": "Provide the proposition-level numeric certificate required by Q5.",
                        "must_certify": True,
                    }
                    if property_name == "Q5"
                    else {}
                ),
            }
        ]

    @staticmethod
    def _parse_bool_flag(value):
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

    def _normalize_tool_plan(self, property_name, requires_tool, raw_tool_plan):
        if not isinstance(raw_tool_plan, dict):
            raw_tool_plan = {}
        if not requires_tool and property_name != "Q5":
            return {}
        preferred_mode = str(raw_tool_plan.get("preferred_mode", "")).strip().lower()
        if preferred_mode not in {"numeric_1d", "symbolic_multivar"}:
            preferred_mode = "numeric_1d" if property_name == "Q5" and requires_tool else ""
        goal = str(raw_tool_plan.get("goal", "")).strip()
        should_request = self._parse_bool_flag(raw_tool_plan.get("should_request")) if raw_tool_plan else bool(requires_tool)
        must_certify = self._parse_bool_flag(raw_tool_plan.get("must_certify")) if raw_tool_plan else bool(property_name == "Q5" and requires_tool)
        if not requires_tool and not should_request and not goal and not preferred_mode and not must_certify:
            return {}
        return {
            "should_request": bool(should_request),
            "preferred_mode": preferred_mode,
            "goal": goal,
            "must_certify": bool(must_certify),
        }

    @staticmethod
    def _render_tool_plan(tool_plan):
        if not isinstance(tool_plan, dict) or not tool_plan:
            return "[none]"
        return json.dumps(tool_plan, ensure_ascii=False, indent=2)

    def _plan_property_propositions(self, candidate, property_name, goal, literature_context):
        property_guidance = self._property_guidance(property_name)
        memory_context = self._property_memory_context(candidate, property_name)
        reuse_context = self._property_reuse_context(
            candidate,
            property_name,
            max_items=MEMORY_REUSE_MAX_ITEMS,
            max_chars=4200,
        )
        literature_packet = self._compose_literature_packet(
            literature_context,
            query=(
                f"{goal}\n{candidate.form}\n{property_name}\n{candidate.intuition}\n"
                f"{candidate.risk_notes}\nproposition decomposition"
            ),
            top_k=LITERATURE_RAG_TOP_K,
            snippet_max_chars=12000,
            summary_max_chars=5000,
        )
        prompt = (
            f"研究目标: {goal}\n"
            f"文献关键上下文:\n{self._truncate_for_prompt(literature_packet, max_chars=24000)}\n\n"
            f"候选 ID: {candidate.candidate_id}\n"
            f"候选形式: {candidate.form}\n"
            f"当前性质: {property_name}\n"
            f"设计动机: {candidate.intuition}\n"
            f"当前性质合同:\n{property_guidance or '[无]'}\n\n"
            f"候选历史/性质记忆:\n{memory_context or '[暂无]'}\n\n"
            f"历史可复用 proposition 模板:\n{reuse_context or '[暂无]'}\n\n"
            "请把当前性质拆成 1-4 个可单独审查的 proposition，并输出 JSON 数组。\n"
            "每个元素必须包含字段：id, title, claim, dependencies, verification_focus, requires_tool, tool_plan。\n"
            "规则：\n"
            "1) proposition 必须足够局部，能被 reviewer 单独判定通过/失败；\n"
            "2) dependencies 必须是字符串数组，引用当前性质内部更早的 proposition id；\n"
            "3) verification_focus 必须明确 reviewer 最该检查的数学脆弱点；\n"
            "4) 只有 Q5 中真正承载一维数值证书的 proposition 才能 requires_tool=true；其它性质必须 false；\n"
            "5) tool_plan 必须是对象；若 requires_tool=true，则 tool_plan 至少写出 should_request, preferred_mode, goal, must_certify；\n"
            "6) 若 requires_tool=false，则 tool_plan 应为空对象，或明确 should_request=false；\n"
            "7) 不得把多个性质混在同一个 proposition 里；\n"
            "8) <PROPERTY_PROPOSITIONS> 标签内只能放 JSON 数组。"
        )
        try:
            payload = self._parse_json_array(
                proof_planner.call_llm_tagged(
                    prompt,
                    tag_name="PROPERTY_PROPOSITIONS",
                    content_hint="标签内必须是合法 JSON 数组。",
                )
            )
        except Exception as exc:
            fallback = self._default_property_propositions(property_name)
            candidate.set_proposition_plan(property_name, fallback)
            candidate.append_log(
                "proposition_plan_fallback",
                f"{property_name} proposition plan fell back to default",
                reason=str(exc),
            )
            candidate.artifacts[f"proposition_plan_{property_name}"] = json.dumps(
                fallback,
                ensure_ascii=False,
                indent=2,
            )
            return fallback

        propositions = []
        for index, item in enumerate(payload, start=1):
            if not isinstance(item, dict):
                continue
            prop_id = str(item.get("id", "")).strip() or f"{property_name.lower()}_prop_{index}"
            requires_tool = self._parse_bool_flag(item.get("requires_tool")) if property_name == "Q5" else False
            proposition = {
                "id": prop_id,
                "title": str(item.get("title", "")).strip() or f"{property_name} proposition {index}",
                "claim": str(item.get("claim", "")).strip() or f"Establish proposition {index} for {property_name}.",
                "dependencies": [
                    str(dep).strip()
                    for dep in item.get("dependencies") or []
                    if str(dep).strip()
                ],
                "verification_focus": str(item.get("verification_focus", "")).strip()
                or f"Check the key step for proposition {index}.",
                "requires_tool": requires_tool,
                "tool_plan": self._normalize_tool_plan(property_name, requires_tool, item.get("tool_plan")),
            }
            propositions.append(proposition)
        if not propositions:
            propositions = self._default_property_propositions(property_name)

        candidate.set_proposition_plan(property_name, propositions)
        candidate.artifacts[f"proposition_plan_{property_name}"] = json.dumps(
            propositions,
            ensure_ascii=False,
            indent=2,
        )
        candidate.append_log(
            "proposition_plan",
            f"{property_name} proposition plan created",
            propositions=propositions,
        )
        return propositions

    def _request_proposition_tool_requests(
        self,
        candidate,
        property_name,
        proposition,
        goal,
        literature_context,
        draft,
        property_context,
        property_guidance,
    ):
        proposition_id = str(proposition.get("id", "")).strip() or f"{property_name.lower()}_prop"
        proposition_title = str(proposition.get("title", "")).strip() or proposition_id
        proposition_claim = str(proposition.get("claim", "")).strip() or "[缺失]"
        tool_plan = proposition.get("tool_plan") or {}
        verification_needs = self._truncate_for_prompt(
            self._extract_markdown_section(draft, "Verification Needs"),
            max_chars=2400,
        ) or "[未显式给出]"
        verification_closed = self._verification_needs_is_none(verification_needs)
        tool_reuse_context = self._tool_request_reuse_context(
            candidate,
            property_name,
            max_items=MEMORY_REUSE_MAX_ITEMS,
            max_chars=4200,
        )
        literature_packet = self._compose_literature_packet(
            literature_context,
            query=(
                f"{goal}\n{candidate.form}\n{property_name}\n{proposition_title}\n"
                f"{proposition_claim}\n{verification_needs}\n"
                "tool request verification numeric symbolic certificate"
            ),
            top_k=LITERATURE_RAG_TOP_K,
            snippet_max_chars=10000,
            summary_max_chars=4000,
        )
        clean_draft = self._strip_tool_reports(draft)
        prompt = (
            f"总目标: {goal}\n"
            f"文献关键上下文:\n{self._truncate_for_prompt(literature_packet, max_chars=18000)}\n\n"
            f"{property_context}\n"
            f"候选 ID: {candidate.candidate_id}\n"
            f"候选形式: {candidate.form}\n"
            f"当前性质: {property_name}\n"
            f"当前性质合同:\n{property_guidance or '[无]'}\n\n"
            "当前 proposition:\n"
            f"- ID: {proposition_id}\n"
            f"- Title: {proposition_title}\n"
            f"- Claim: {proposition_claim}\n"
            f"- Verification Focus: {str(proposition.get('verification_focus', '')).strip() or '[缺失]'}\n"
            f"- Planner Says Requires Tool: {'yes' if self._parse_bool_flag(proposition.get('requires_tool')) else 'no'}\n\n"
            f"Planner Tool Plan:\n{self._render_tool_plan(tool_plan)}\n\n"
            f"当前 proposition 草稿:\n{self._truncate_for_prompt(clean_draft, max_chars=24000)}\n\n"
            f"当前草稿中的 Verification Needs:\n{verification_needs}\n\n"
            f"历史可复用 tool request 模板:\n{tool_reuse_context or '[暂无]'}\n\n"
            "请判断当前 proposition 是否需要调用外部工具，并输出 JSON 数组。若不需要任何工具，输出 []。\n"
            "每个元素必须包含字段：request_id, tool_name, justification, spec。\n"
            "规则：\n"
            "1) 只有当工具能直接关闭当前 proposition 中尚未闭合的 Verification Needs 时，才可以请求工具；\n"
            "1.1) 只有当 `Verification Needs` 严格等于 `None` 时，才允许输出 []；\n"
            "1.2) 若 `Verification Needs` 不是 `None`，则禁止输出 []，必须给出至少一个可执行的 tool request；\n"
            "2) 当前系统支持的 tool_name 只有 verification；\n"
            "3) spec 必须是 verification_tools 可执行的 JSON 对象；支持 mode=numeric_1d 或 mode=symbolic_multivar；\n"
            "4) 若 mode=numeric_1d，则 spec 必须包含 status, mode, strategy, variable, domain, inequalities, grid_points, lipschitz, tolerance, max_iterations, min_width, notes；\n"
            "5) 若 mode=symbolic_multivar，则 spec 必须包含 status, mode, variables, assumptions, simplifications, partial_derivatives, inequality_checks, substitutions, notes；\n"
            "5.1) partial_derivatives 中每个元素必须包含 expression 和 wrt 两个字段；字段名必须严格写成 wrt，不得写 with_respect_to 或其他别名；\n"
            "5.2) inequality_checks 中每个元素必须把比较拆成 expression, relation, threshold 三个字段；例如要验证 f(x) > 0，应写成 {\"expression\": \"f(x)\", \"relation\": \">\", \"threshold\": 0}；不得把 >、<、>=、<=、== 直接写进 expression；\n"
            "5.3) symbolic_multivar 的合法最小示例：partial_derivatives=[{\"expression\":\"cos(theta)/(cos(alpha)-cos(gamma))\",\"wrt\":\"alpha\"}]，inequality_checks=[{\"expression\":\"cos(theta)*sin(alpha)/(cos(alpha)-cos(gamma))**2\",\"relation\":\">\",\"threshold\":0}]；\n"
            "6) expression 必须是 Python 数学表达式，只能使用变量名和 sin/cos/tan/asin/acos/atan/sqrt/log/exp/abs/pi/e，不得使用 LaTeX，也不得在 expression 中写比较符号；\n"
            "7) 不得虚构文献里不存在的公式；如果只是候选表达式或近似重写，必须在 justification 或 notes 中明确承认；\n"
            "8) 对 Q5，如果 proposition 的关键缺口是局部一维数值证书，应优先请求 numeric_1d；只有在确实需要符号偏导/符号不等式时才用 symbolic_multivar；\n"
            "9) 若 `Verification Needs` 不是 `None`，你不能因为无法给出 spec 就输出 []；此时必须尽力给出最可执行的 spec；\n"
            "10) 输出前请自检：partial_derivatives 是否使用 wrt；inequality_checks 是否使用 expression/relation/threshold；\n"
            "11) <TOOL_REQUESTS> 标签内只能放 JSON 数组。"
        )
        try:
            payload = self._parse_json_array(
                proof_writer.call_llm_tagged(
                    prompt,
                    tag_name="TOOL_REQUESTS",
                    content_hint="标签内必须是合法 JSON 数组。",
                    print_stream=True,
                )
            )
        except Exception as exc:
            candidate.append_log(
                "tool_request_fallback",
                f"{property_name}:{proposition_id} tool request generation failed",
                reason=str(exc),
            )
            payload = []

        requests = []
        for index, item in enumerate(payload, start=1):
            if not isinstance(item, dict):
                continue
            request_id = self._normalize_tool_request_id(
                item.get("request_id", ""),
                fallback=f"{proposition_id}_tool_{index}",
            )
            tool_name = str(item.get("tool_name", "")).strip().lower() or "verification"
            justification = str(item.get("justification", "")).strip()
            spec = item.get("spec") or {}
            if not isinstance(spec, dict):
                spec = {}
            requests.append(
                {
                    "request_id": request_id,
                    "tool_name": tool_name,
                    "justification": justification,
                    "spec": spec,
                }
            )

        if not requests and not verification_closed:
            candidate.append_log(
                "tool_requests_invalid",
                f"{property_name}:{proposition_id} returned empty tool requests despite open Verification Needs",
                verification_needs=verification_needs,
            )

        artifact_key = f"tool_requests_{property_name}_{proposition_id}"
        candidate.artifacts[artifact_key] = json.dumps(requests, ensure_ascii=False, indent=2)
        candidate.set_tool_requests(property_name, proposition_id, requests)
        candidate.append_log(
            "tool_requests",
            f"{property_name}:{proposition_id} requested {len(requests)} tools",
            requests=requests,
        )
        return requests

    def _run_property_verification(self, candidate, property_name, proposition, tool_request):
        request_id = self._normalize_tool_request_id(
            (tool_request or {}).get("request_id", ""),
            fallback=f"{str(proposition.get('id', '')).strip() or property_name.lower()}_tool",
        )
        tool_name = str((tool_request or {}).get("tool_name", "")).strip().lower() or "verification"
        justification = str((tool_request or {}).get("justification", "")).strip()
        spec = dict((tool_request or {}).get("spec") or {})

        if tool_name != "verification":
            report = {
                "status": "unavailable",
                "mode": "none",
                "summary": f"unsupported tool request: {tool_name}",
                "details": [],
                "notes": justification,
            }
        else:
            try:
                report = run_verification_spec(spec)
            except Exception as exc:
                report = {
                    "status": "tool_error",
                    "mode": str(spec.get("mode", "unknown")).strip() or "unknown",
                    "summary": f"verification tool error: {exc}",
                    "details": [],
                    "notes": str(spec.get("notes", "")).strip(),
                }

        rendered_report = render_verification_report(spec, report)
        rendered = "\n".join(
            [
                "## Tool Request",
                f"- Request ID: {request_id}",
                f"- Tool: {tool_name}",
                f"- Justification: {justification or '[none]'}",
                "",
                rendered_report,
            ]
        ).strip()
        artifact_key = f"tool_{property_name}_{str(proposition.get('id', 'prop')).strip() or 'prop'}_{request_id}"
        candidate.artifacts[artifact_key] = rendered
        report_payload = report.to_dict() if hasattr(report, "to_dict") else dict(report or {})
        candidate.record_tool_result(
            property_name,
            proposition.get("id", ""),
            request_id=request_id,
            tool_name=tool_name,
            justification=justification,
            spec=spec,
            report=report_payload,
            artifact_key=artifact_key,
        )
        candidate.append_log(
            "tool_execution",
            f"{property_name}:{proposition.get('id', 'prop')} executed tool request {request_id}",
            tool_name=tool_name,
            report_status=report_payload.get("status", "unknown"),
            spec=spec,
        )
        print(
            f"    🛠️ 工具执行完成 {property_name}:{proposition.get('id', 'prop')} "
            f"request={request_id} status={report_payload.get('status', 'unknown')} "
            f"summary={str(report_payload.get('summary', '') or '[none]').strip()}",
            flush=True,
        )
        return rendered, report_payload, artifact_key

    def _apply_proposition_tool_requests(
        self,
        candidate,
        property_name,
        proposition,
        goal,
        literature_context,
        draft,
        property_context,
        property_guidance,
        proposition_meta,
    ):
        requests = self._request_proposition_tool_requests(
            candidate,
            property_name,
            proposition,
            goal,
            literature_context,
            draft,
            property_context,
            property_guidance,
        )
        if not requests:
            return draft, [], False

        rendered_chunks = []
        report_payloads = []
        certified = False
        proposition_id = proposition.get("id", "") or "prop"
        for item in requests:
            rendered, report_payload, artifact_key = self._run_property_verification(
                candidate,
                property_name,
                proposition,
                item,
            )
            rendered_chunks.append(rendered)
            report_payloads.append(
                {
                    "request": item,
                    "report": report_payload,
                    "artifact_key": artifact_key,
                }
            )
            status = str(report_payload.get("status", "")).strip().lower()
            if status == "verified_pass":
                certified = True
            if status == "verified_fail":
                reason = report_payload.get("summary", "tool request reported a counterexample")
                candidate.mark_property(property_name, "fail", note=str(reason), artifact_key=artifact_key)
                candidate.mark_pruned(f"{property_name} proposition {proposition_id} 工具验证失败: {reason}")
                candidate.mark_proposition(
                    property_name,
                    proposition_id,
                    "fail",
                    note=str(reason),
                    artifact_key=artifact_key,
                    **proposition_meta,
                )
                candidate.append_log(
                    "prune",
                    "tool request pruned candidate",
                    property=property_name,
                    proposition_id=proposition_id,
                    request_id=item.get("request_id", ""),
                    reason=reason,
                )
                merged = f"{draft.strip()}\n\n" + "\n\n".join(chunk.strip() for chunk in rendered_chunks if chunk.strip())
                return merged.strip(), report_payloads, certified

        clean_draft = self._strip_tool_reports(draft)
        if rendered_chunks:
            draft = f"{clean_draft}\n\n" + "\n\n".join(chunk.strip() for chunk in rendered_chunks if chunk.strip())
        else:
            draft = clean_draft
        return draft.strip(), report_payloads, certified

    def _run_property_proposition(
        self,
        candidate,
        property_name,
        proposition,
        goal,
        literature_context,
        property_context,
        memory_context,
        property_guidance,
    ):
        proposition_title = str(proposition.get("title", "")).strip() or proposition.get("id", "proposition")
        proposition_claim = str(proposition.get("claim", "")).strip() or "[缺失]"
        verification_focus = str(proposition.get("verification_focus", "")).strip() or "[缺失]"
        dependencies = ", ".join(proposition.get("dependencies") or []) or "[无]"
        requires_tool = self._parse_bool_flag(proposition.get("requires_tool")) if property_name == "Q5" else False
        tool_plan = proposition.get("tool_plan") or {}
        proposition_meta = {
            "title": proposition_title,
            "claim": proposition_claim,
            "dependencies": proposition.get("dependencies") or [],
            "verification_focus": verification_focus,
            "requires_tool": requires_tool,
            "tool_plan": tool_plan,
        }
        candidate.mark_proposition(
            property_name,
            proposition.get("id", ""),
            "in_progress",
            note="proposition draft started",
            **proposition_meta,
        )
        literature_packet = self._compose_literature_packet(
            literature_context,
            query=(
                f"{goal}\n{candidate.form}\n{property_name}\n{proposition_title}\n"
                f"{proposition_claim}\n{verification_focus}\n{dependencies}"
            ),
            top_k=LITERATURE_RAG_TOP_K,
            snippet_max_chars=12000,
            summary_max_chars=5000,
        )
        dependency_context = self._proposition_dependency_context(candidate, property_name, proposition)
        reuse_context = self._property_reuse_context(
            candidate,
            property_name,
            max_items=MEMORY_REUSE_MAX_ITEMS,
            max_chars=4200,
        )
        prompt_base = (
            f"总目标: {goal}\n"
            f"文献关键上下文:\n{self._truncate_for_prompt(literature_packet, max_chars=24000)}\n\n"
            f"{property_context}\n"
            f"候选历史/性质记忆:\n{memory_context or '[暂无]'}\n\n"
            f"当前性质合同:\n{property_guidance or '[无额外合同]'}\n\n"
            f"历史可复用 proposition 模板:\n{reuse_context or '[暂无]'}\n\n"
            "当前 proposition:\n"
            f"- ID: {proposition.get('id', '[missing]')}\n"
            f"- Title: {proposition_title}\n"
            f"- Claim: {proposition_claim}\n"
            f"- Dependencies: {dependencies}\n"
            f"- Verification Focus: {verification_focus}\n"
            f"- Requires Tool: {'yes' if requires_tool else 'no'}\n\n"
            f"- Planned Tool Plan: {self._render_tool_plan(tool_plan)}\n\n"
            f"当前候选内部依赖 proposition 摘要:\n{dependency_context}\n\n"
            "请只针对当前 proposition 输出证明草稿，严格使用以下 Markdown 骨架：\n"
            "## Assumptions\n"
            "## Claim\n"
            "## Derivation\n"
            "## Boundary Cases\n"
            "## Verification Needs\n"
            "## Conclusion\n"
            "要求：\n"
            "1) 只能证明当前 proposition，不得一次性声称整个性质或全局上界已经完成；\n"
            "2) 若 proposition 依赖更早的 proposition，只能把这些依赖当作已通过的局部前提，不得跳过新的关键步骤；\n"
            "3) 若 Requires Tool=yes，必须明确区分已证明部分与仍需数值验证的部分。\n"
            "4) `## Verification Needs` 必须使用严格标准格式：若当前 proposition 已闭合，唯一允许内容是 `None`；"
            "若仍有未闭合验证项，只能逐行输出 `- ...` 列表，不得写自然语言段落，不得写“无需额外的数值或符号验证”这类句子。\n"
            f"{'5) Q6 额外硬约束: 若没有写出明确常数链条（新常数如何进入极端链下界、Lemma 3 型估计以及 rho/lambda 关系），不得声称“可压到 1.98”“支持 rho < 1.98”或“允许更小全局上界”；未闭合时 Conclusion 只能写候选方向或局部约束。' if property_name == 'Q6' else ''}"
        )
        draft = proof_writer.call_llm_tagged(
            prompt_base,
            tag_name="PROPOSITION_PROOF",
            content_hint="必须包含 Assumptions/Claim/Derivation/Boundary Cases/Verification Needs/Conclusion 六节。",
        )
        draft = self._normalize_verification_needs_section(draft)

        draft, tool_reports, tool_certified = self._apply_proposition_tool_requests(
            candidate,
            property_name,
            proposition,
            goal,
            literature_context,
            draft,
            property_context,
            property_guidance,
            proposition_meta,
        )
        draft = self._normalize_verification_needs_section(draft)
        if candidate.status == "pruned":
            return False, "", False
        if property_name == "Q5" and requires_tool and not tool_certified:
            statuses = [
                str((item.get("report") or {}).get("status", "")).strip().lower() or "missing"
                for item in tool_reports
            ]
            reason = (
                f"Q5 proposition {proposition.get('id', '[missing]')} requires a verified numeric certificate, "
                f"but explicit tool requests produced statuses {statuses or ['missing']}"
            )
            candidate.mark_property(property_name, "fail", note=reason)
            candidate.mark_pruned(reason)
            candidate.mark_proposition(
                property_name,
                proposition.get("id", ""),
                "fail",
                note=reason,
                **proposition_meta,
            )
            candidate.append_log(
                "prune",
                "Q5 proposition blocked by missing/insufficient explicit tool certificate",
                proposition_id=proposition.get("id", ""),
                statuses=statuses or ["missing"],
            )
            return False, "", False

        final_feedback = ""
        for attempt in range(max(1, int(PROPOSITION_REVIEW_MAX_ROUNDS or 1))):
            current_round_pass = True
            if property_name == "Q6":
                q6_feedback = self._q6_constant_chain_feedback(draft)
                if q6_feedback:
                    final_feedback = q6_feedback
                    current_round_pass = False
                    draft = proof_writer.call_llm_tagged(
                        f"{prompt_base}\n\n当前草稿:\n{draft}\n\n修正反馈:\n{q6_feedback}\n\n"
                        "请仅修复被指出的致命问题，保持六节骨架不变；若当前 proposition 结论过强，缩小到真正成立的范围。",
                        tag_name="PROPOSITION_PROOF",
                        content_hint="输出修订后的完整 proposition 草稿，并保留六节固定结构。",
                    )
                    draft = self._normalize_verification_needs_section(draft)
                    draft, tool_reports, tool_certified = self._apply_proposition_tool_requests(
                        candidate,
                        property_name,
                        proposition,
                        goal,
                        literature_context,
                        draft,
                        property_context,
                        property_guidance,
                        proposition_meta,
                    )
                    draft = self._normalize_verification_needs_section(draft)
                    if candidate.status == "pruned":
                        return False, "", False
                    if property_name == "Q5" and requires_tool and not tool_certified:
                        statuses = [
                            str((item.get("report") or {}).get("status", "")).strip().lower() or "missing"
                            for item in tool_reports
                        ]
                        reason = (
                            f"Q5 proposition {proposition.get('id', '[missing]')} requires a verified numeric certificate, "
                            f"but explicit tool requests produced statuses {statuses or ['missing']}"
                        )
                        candidate.mark_property(property_name, "fail", note=reason)
                        candidate.mark_pruned(reason)
                        candidate.mark_proposition(
                            property_name,
                            proposition.get("id", ""),
                            "fail",
                            note=reason,
                            **proposition_meta,
                        )
                        candidate.append_log(
                            "prune",
                            "Q5 proposition blocked by missing/insufficient explicit tool certificate after Q6 revision",
                            proposition_id=proposition.get("id", ""),
                            statuses=statuses or ["missing"],
                        )
                        return False, "", False
                    continue
            for reviewer in self.candidate_reviewers:
                review_context = (
                    f"{property_context}\n"
                    f"当前 proposition: {proposition_title}\n"
                    f"Claim: {proposition_claim}\n"
                    f"Dependencies: {dependencies}\n"
                    f"研究目标: {goal}"
                )
                review_directive = self._generate_reviewer_directive(
                    reviewer,
                    review_context,
                    draft,
                    stage_label=f"{candidate.candidate_id}:{property_name}:{proposition.get('id', 'prop')}:评审第{attempt + 1}轮",
                )
                verdict, feedback = reviewer.check(draft, review_context, review_directive=review_directive)
                if verdict:
                    continue
                final_feedback = feedback
                current_round_pass = False
                draft = proof_writer.call_llm_tagged(
                    f"{prompt_base}\n\n当前草稿:\n{draft}\n\n修正反馈:\n{feedback}\n\n"
                    "请仅修复被指出的致命问题，保持六节骨架不变；若当前 proposition 结论过强，缩小到真正成立的范围。",
                    tag_name="PROPOSITION_PROOF",
                    content_hint="输出修订后的完整 proposition 草稿，并保留六节固定结构。",
                )
                draft = self._normalize_verification_needs_section(draft)
                draft, tool_reports, tool_certified = self._apply_proposition_tool_requests(
                    candidate,
                    property_name,
                    proposition,
                    goal,
                    literature_context,
                    draft,
                    property_context,
                    property_guidance,
                    proposition_meta,
                )
                draft = self._normalize_verification_needs_section(draft)
                if candidate.status == "pruned":
                    return False, "", False
                if property_name == "Q5" and requires_tool and not tool_certified:
                    statuses = [
                        str((item.get("report") or {}).get("status", "")).strip().lower() or "missing"
                        for item in tool_reports
                    ]
                    reason = (
                        f"Q5 proposition {proposition.get('id', '[missing]')} requires a verified numeric certificate, "
                        f"but explicit tool requests produced statuses {statuses or ['missing']}"
                    )
                    candidate.mark_property(property_name, "fail", note=reason)
                    candidate.mark_pruned(reason)
                    candidate.mark_proposition(
                        property_name,
                        proposition.get("id", ""),
                        "fail",
                        note=reason,
                        **proposition_meta,
                    )
                    candidate.append_log(
                        "prune",
                        "Q5 proposition blocked by missing/insufficient explicit tool certificate after revision",
                        proposition_id=proposition.get("id", ""),
                        statuses=statuses or ["missing"],
                    )
                    return False, "", False
                break
            if current_round_pass:
                artifact_key = f"property_{property_name}_{proposition.get('id', 'prop')}"
                candidate.artifacts[artifact_key] = draft
                verification_needs = self._extract_markdown_section(draft, "Verification Needs")
                if property_name == "N1" and not self._verification_needs_closed(verification_needs):
                    reason = f"N1 proposition {proposition.get('id', '[missing]')} still leaves open Verification Needs"
                    candidate.mark_property(property_name, "fail", note=reason)
                    candidate.mark_pruned(reason)
                    candidate.mark_proposition(
                        property_name,
                        proposition.get("id", ""),
                        "fail",
                        note=reason,
                        artifact_key=artifact_key,
                        **proposition_meta,
                    )
                    candidate.append_log(
                        "prune",
                        "N1 proposition blocked by unresolved verification needs",
                        proposition_id=proposition.get("id", ""),
                        verification_needs=self._truncate_for_prompt(verification_needs, max_chars=800),
                    )
                    return False, "", False
                candidate.mark_proposition(
                    property_name,
                    proposition.get("id", ""),
                    "pass",
                    note=self._extract_markdown_section(draft, "Conclusion"),
                    artifact_key=artifact_key,
                    **proposition_meta,
                )
                candidate.append_log(
                    "proposition_pass",
                    f"{property_name}:{proposition.get('id', 'prop')} passed review",
                )
                return True, draft, tool_certified

        reason = final_feedback or "proposition review failed"
        candidate.mark_property(property_name, "fail", note=reason)
        candidate.mark_pruned(f"{property_name} proposition {proposition.get('id', '[missing]')} 失败: {reason}")
        candidate.mark_proposition(
            property_name,
            proposition.get("id", ""),
            "fail",
            note=reason,
            **proposition_meta,
        )
        candidate.append_log(
            "proposition_fail",
            f"{property_name}:{proposition.get('id', 'prop')} failed review",
            feedback=final_feedback,
        )
        return False, "", False

    def _run_candidate_property(self, candidate, property_name, goal, literature_context):
        property_context = (
            f"候选 ID: {candidate.candidate_id}\n"
            f"候选形式: {candidate.form}\n"
            f"当前性质: {property_name}\n"
            f"设计动机: {candidate.intuition}\n"
            f"可复用部分: {', '.join(candidate.reusable_props) or '[暂无]'}\n"
            f"需重写部分: {', '.join(candidate.needs_redo) or '[暂无]'}\n"
            f"主要风险: {candidate.risk_notes or '[暂无]'}\n"
        )
        memory_context = self._property_memory_context(candidate, property_name)
        property_guidance = self._property_guidance(property_name)
        propositions = self._plan_property_propositions(
            candidate,
            property_name,
            goal,
            literature_context,
        )
        proposition_outputs = []
        q5_tool_certified = False

        for proposition in propositions:
            ok, proposition_draft, used_tool = self._run_property_proposition(
                candidate,
                property_name,
                proposition,
                goal,
                literature_context,
                property_context,
                memory_context,
                property_guidance,
            )
            if not ok:
                return False
            proposition_outputs.append((proposition, proposition_draft))
            if used_tool:
                q5_tool_certified = True

        if property_name == "Q5" and not q5_tool_certified:
            reason = "Q5 proposition plan completed without any proposition obtaining a verified numeric certificate"
            candidate.mark_property(property_name, "fail", note=reason)
            candidate.mark_pruned(reason)
            candidate.append_log("prune", "Q5 missing proposition-level numeric certificate")
            return False

        assembled_parts = []
        final_notes = []
        for index, (proposition, proposition_draft) in enumerate(proposition_outputs, start=1):
            assembled_parts.extend(
                [
                    f"### Proposition {index}: {proposition.get('title', proposition.get('id', 'prop'))}",
                    "",
                    proposition_draft.strip(),
                    "",
                ]
            )
            conclusion = self._extract_markdown_section(proposition_draft, "Conclusion")
            if conclusion:
                final_notes.append(conclusion)

        artifact_key = f"property_{property_name}"
        candidate.artifacts[artifact_key] = "\n".join(assembled_parts).strip()
        candidate.mark_property(
            property_name,
            "pass",
            note=" | ".join(final_notes[:3]) if final_notes else f"{len(proposition_outputs)} propositions passed",
            artifact_key=artifact_key,
        )
        candidate.append_log(
            "property_pass",
            f"{property_name} passed review at proposition granularity",
            proposition_count=len(proposition_outputs),
        )
        return True

    def _run_proof_refinement(self, goal, literature_context, candidate):
        property_sections = []
        for prop in candidate.priority or self.property_order:
            artifact_key = (candidate.property_status.get(prop) or {}).get("artifact_key", "")
            artifact = candidate.artifacts.get(artifact_key, "")
            if artifact:
                property_sections.extend([f"## Property {prop}", "", artifact.strip(), ""])
        property_bundle = "\n".join(property_sections).strip() or "[暂无性质证明草稿]"
        literature_packet = self._compose_literature_packet(
            literature_context,
            query=(
                f"{goal}\n{candidate.form}\nproof refinement\n"
                f"{candidate.estimated_c}\n{candidate.risk_notes}\n"
                f"{self._candidate_property_snapshot(candidate)}"
            ),
            top_k=LITERATURE_RAG_TOP_K,
            snippet_max_chars=12000,
            summary_max_chars=5000,
        )
        prompt_base = (
            f"总目标: {goal}\n"
            f"文献关键上下文:\n{self._truncate_for_prompt(literature_packet, max_chars=24000)}\n\n"
            f"候选 ID: {candidate.candidate_id}\n"
            f"候选形式: {candidate.form}\n"
            f"设计动机: {candidate.intuition or '[缺失]'}\n"
            f"Estimated C: {candidate.estimated_c or '[unknown]'}\n"
            f"Risk Notes: {candidate.risk_notes or '[none]'}\n"
            f"Property Snapshot: {self._candidate_property_snapshot(candidate)}\n"
            f"Terminal Report:\n{candidate.artifacts.get('terminal_report', '[missing]')}\n\n"
            "已通过的性质证明材料:\n"
            f"{self._truncate_for_prompt(property_bundle, max_chars=80000)}\n\n"
            "你现在处于 proof refinement 阶段。请把已经通过的局部性质整理成一份更连贯的候选证明包，"
            "用于判断这个候选是否值得进入后续人工深挖。"
            "严格使用以下 Markdown 骨架：\n"
            "## Candidate Statement\n"
            "## Property Map\n"
            "## Reusable Components\n"
            "## Refined Proof Outline\n"
            "## Tight Spots\n"
            "## Next Actions\n"
            "要求：\n"
            "1) 只能整理当前候选已经通过的性质，不得虚构未证明的 Proposition；\n"
            "2) 必须明确最脆弱的两个环节，尤其是 Q5 数值证书和 Q6 极端链下界之间的张力；\n"
            "3) 若某步仍需人工/额外验证，必须写进 Tight Spots 或 Next Actions；\n"
            "4) 不得声称最终全局上界已经完全确立，除非材料中已经明确支持；\n"
            "5) 若没有明确写出来自已通过 Q6 材料的常数链条，不得声称“可压到 1.98”“支持 rho < 1.98”或等价的全局上界改进；\n"
            "6) <PROOF_REFINEMENT> 标签内只能放完整 Markdown 正文。"
        )
        draft = proof_writer.call_llm_tagged(
            prompt_base,
            tag_name="PROOF_REFINEMENT",
            content_hint=(
                "必须包含 Candidate Statement/Property Map/Reusable Components/"
                "Refined Proof Outline/Tight Spots/Next Actions 六节。"
            ),
            print_stream=True,
        )

        final_feedback = ""
        max_rounds = max(1, int(PROOF_REFINEMENT_MAX_ROUNDS or 1))
        passed = False
        for attempt in range(max_rounds):
            current_round_pass = True
            for reviewer in self.candidate_reviewers:
                review_context = (
                    f"proof refinement for candidate {candidate.candidate_id}\n"
                    f"goal={goal}\nproperty_snapshot={self._candidate_property_snapshot(candidate)}"
                )
                review_directive = self._generate_reviewer_directive(
                    reviewer,
                    review_context,
                    draft,
                    stage_label=f"{candidate.candidate_id}:proof_refinement:评审第{attempt + 1}轮",
                )
                verdict, feedback = reviewer.check(draft, review_context, review_directive=review_directive)
                if verdict:
                    continue
                current_round_pass = False
                final_feedback = feedback
                draft = proof_writer.call_llm_tagged(
                    f"{prompt_base}\n\n当前 refinement 草稿:\n{draft}\n\n修正反馈:\n{feedback}\n\n"
                    "请仅修复 reviewer 指出的致命问题，保持六节骨架不变，并明确哪些地方仍然只是候选证明包而非最终定理证明。",
                    tag_name="PROOF_REFINEMENT",
                    content_hint="输出修订后的完整 refinement 草稿，并保留六节固定结构。",
                    print_stream=True,
                )
                break
            if current_round_pass:
                passed = True
                break

        artifact_key = "proof_refinement"
        candidate.artifacts[artifact_key] = draft
        candidate.artifacts["proof_refinement_meta"] = json.dumps(
            {
                "status": "pass" if passed else "needs_followup",
                "feedback": final_feedback,
            },
            ensure_ascii=False,
            indent=2,
        )
        candidate.append_log(
            "proof_refinement",
            "proof refinement completed" if passed else "proof refinement needs follow-up",
            status="pass" if passed else "needs_followup",
            feedback=final_feedback,
            artifact_key=artifact_key,
        )
        self.memory.save_candidate(candidate)
        return draft

    def _synthesize_candidate_report(self, goal, passed_candidates, pruned_candidates):
        parts = [
            f"# {str(goal or '').strip() or '研究报告'}",
            "",
            "## Candidate Search Summary",
            f"- Architecture Mode: {self.architecture_mode}",
            f"- Passed Candidates: {len(passed_candidates)}",
            f"- Pruned Candidates: {len(pruned_candidates)}",
        ]

        if passed_candidates:
            parts.extend(["", "## Passed Candidates"])
            for candidate in passed_candidates:
                parts.extend(
                    [
                        "",
                        f"### {candidate.candidate_id}",
                        f"- Form: {candidate.form}",
                        f"- Derived From: {candidate.derived_from or '[none]'}",
                        f"- Intuition: {candidate.intuition or '[missing]'}",
                        f"- Estimated C: {candidate.estimated_c or '[unknown]'}",
                        f"- Risk Notes: {candidate.risk_notes or '[none]'}",
                        f"- Property Snapshot: {self._candidate_property_snapshot(candidate)}",
                        f"- Terminal Decision: {(candidate.terminal_decision or {}).get('action', '[none]') or '[none]'}",
                    ]
                )
                for prop in candidate.priority or self.property_order:
                    artifact_key = (candidate.property_status.get(prop) or {}).get("artifact_key", "")
                    artifact = candidate.artifacts.get(artifact_key, "")
                    if artifact:
                        parts.extend(["", f"#### Property {prop}", "", artifact])
                refinement_artifact = candidate.artifacts.get("proof_refinement", "")
                if refinement_artifact:
                    refinement_meta = self._parse_json_object(
                        candidate.artifacts.get("proof_refinement_meta", "{}")
                    )
                    parts.extend(
                        [
                            "",
                            "#### Proof Refinement",
                            "",
                            f"- Status: {refinement_meta.get('status', '[unknown]') or '[unknown]'}",
                        ]
                    )
                    feedback = str(refinement_meta.get("feedback", "")).strip()
                    if feedback:
                        parts.extend(["- Reviewer Follow-up:", "", "```text", feedback, "```"])
                    parts.extend(["", refinement_artifact])
        else:
            parts.extend(["", "## Passed Candidates", "", "暂无通过全部性质审查的候选。"])

        parts.extend(["", "## Search Trajectory"])
        terminal_candidates = list(passed_candidates) + list(pruned_candidates)
        if not terminal_candidates:
            parts.extend(["", "暂无终态候选轨迹。"])
        else:
            for candidate in terminal_candidates:
                terminal_report = candidate.artifacts.get("terminal_report", "")
                parts.extend(
                    [
                        "",
                        f"### {candidate.candidate_id}",
                        f"- Status: {candidate.status}",
                        f"- Source Direction: {candidate.source_direction or '[missing]'}",
                        f"- Derived From: {candidate.derived_from or '[none]'}",
                        f"- Property Snapshot: {self._candidate_property_snapshot(candidate)}",
                        f"- Terminal Decision: {(candidate.terminal_decision or {}).get('action', '[none]') or '[none]'}",
                        f"- Decision Rationale: {(candidate.terminal_decision or {}).get('rationale', '[none]') or '[none]'}",
                    ]
                )
                if terminal_report:
                    parts.extend(["", "```text", terminal_report, "```"])

        parts.extend(["", "## Pruned Candidates"])
        if not pruned_candidates:
            parts.extend(["", "暂无被剪枝的候选。"])
        else:
            for candidate in pruned_candidates:
                props = []
                for prop, detail in candidate.property_status.items():
                    status = str((detail or {}).get("status", "")).strip()
                    if status and status != "untested":
                        props.append(f"{prop}={status}")
                parts.extend(
                    [
                        "",
                        f"### {candidate.candidate_id}",
                        f"- Form: {candidate.form}",
                        f"- Derived From: {candidate.derived_from or '[none]'}",
                        f"- Status: {candidate.status}",
                        f"- Property Snapshot: {', '.join(props) or '[none]'}",
                        f"- Pruned Reason: {candidate.pruned_reason or '[missing]'}",
                        f"- Terminal Decision: {(candidate.terminal_decision or {}).get('action', '[none]') or '[none]'}",
                    ]
                )

        return "\n".join(parts).strip()

    def _execute_candidate(self, pdf_path, goal, force_reparse=False):
        raw_md = self._load_or_parse_pdf_markdown(pdf_path, force_reparse=force_reparse)
        literature_context = self._build_literature_context(raw_md)
        self._initialize_literature_rag(pdf_path, raw_md)
        self._customize_team(goal, literature_context)

        print("🧠 [Step 3/6] 进入 candidate-centric 探索模式...")
        pipeline = CandidateExplorationPipeline(
            self,
            goal,
            literature_context,
            max_candidate_count=CANDIDATE_MAX_COUNT,
        )
        pipeline_result = pipeline.run()
        passed_candidates = pipeline_result["passed_candidates"]
        pruned_candidates = pipeline_result["pruned_candidates"]
        search_stage = pipeline_result["search_stage"]
        refinement_target = pipeline_result["refinement_target"]

        print("\n🧩 [Step 5/6] 汇总候选搜索结果...")
        final_report = self._synthesize_candidate_report(goal, passed_candidates, pruned_candidates)
        if search_stage:
            final_report = f"{final_report}\n\n## Search Exit Decision\n- Final Stage: {search_stage}".strip()
        if refinement_target is not None:
            final_report = (
                f"{final_report}\n"
                f"- Refinement Target: {refinement_target.candidate_id}"
            ).strip()
        print("\n🏆 [Step 6/6] 候选搜索流程结束。")
        return final_report

    def execute(self, pdf_path, goal, force_reparse=False):
        return self._execute_candidate(pdf_path, goal, force_reparse=force_reparse)
