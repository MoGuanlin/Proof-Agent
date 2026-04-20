import os
import re
import shlex
from urllib.parse import quote

from .paths import CACHE_DIR, ENV_FILE, resolve_from_project


def _load_dotenv_file(path=ENV_FILE, override=True):
    dotenv_path = resolve_from_project(path)
    if not os.path.exists(dotenv_path):
        return

    with open(dotenv_path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'").strip('"')
            if key and (override or key not in os.environ):
                os.environ[key] = value


_dotenv_override = str(os.getenv("DOTENV_OVERRIDE", "1")).strip().lower() in {"1", "true", "yes", "on"}
_load_dotenv_file(ENV_FILE, override=_dotenv_override)


def _env_flag(name, default=False):
    val = os.getenv(name)
    if val is None:
        return bool(default)
    return str(val).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name, default):
    val = os.getenv(name)
    if val is None:
        return int(default)
    return int(str(val).strip())


def _env_non_negative_int(name, default):
    return max(0, _env_int(name, default))


def _env_csv(name, default=""):
    raw = os.getenv(name, default)
    return [item.strip() for item in str(raw or "").split(",") if item.strip()]


def _env_path(name, default="", allow_empty=False):
    raw = os.getenv(name)
    if raw is None:
        raw = default
    return resolve_from_project(raw, empty_ok=allow_empty)


def _request_proxies():
    """
    统一构造 requests 代理配置：
    - 仅使用 HTTP_PROXY / HTTPS_PROXY，避免 ALL_PROXY(socks5) 干扰 HTTPS API。
    - 若仅设置了其一，则复用到另一个协议，减少环境差异导致的不稳定。
    """
    http_proxy = (os.getenv("HTTP_PROXY") or os.getenv("http_proxy") or "").strip()
    https_proxy = (os.getenv("HTTPS_PROXY") or os.getenv("https_proxy") or "").strip()

    if not http_proxy and https_proxy:
        http_proxy = https_proxy
    if not https_proxy and http_proxy:
        https_proxy = http_proxy
    if not http_proxy and not https_proxy:
        return None
    return {"http": http_proxy, "https": https_proxy}


def _normalize_provider(name):
    s = (name or "").strip().lower()
    alias = {
        "google": "google",
        "gemini": "google",
        "openai": "openai",
        "oai": "openai",
        "siliconflow": "siliconflow",
        "sf": "siliconflow",
        "硅基流动": "siliconflow",
        "轨迹流动": "siliconflow",
        "ai_hub_mixed": "ai_hub_mixed",
        "aihubmixed": "ai_hub_mixed",
        "aihub": "ai_hub_mixed",
        "mixed": "ai_hub_mixed",
        "openrouter": "openrouter",
        "or": "openrouter",
    }
    return alias.get(s, "siliconflow")


LLM_PROVIDER = _normalize_provider(os.getenv("LLM_PROVIDER", "siliconflow"))
SILICONFLOW_API_KEY = os.getenv("SILICONFLOW_API_KEY", "").strip()
GOOGLE_API_KEY = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
AI_HUB_MIXED_API_KEY = os.getenv("AI_HUB_MIXED_MODEL_API_KEY", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
SILICONFLOW_API_URL = "https://api.siliconflow.cn/v1/chat/completions"
AI_HUB_MIXED_API_URL = os.getenv(
    "AI_HUB_MIXED_API_URL",
    "https://aihubmix.com/v1/chat/completions",
).strip()
OPENAI_BASE_URL = os.getenv(
    "OPENAI_BASE_URL",
    "https://api.openai.com/v1",
).strip().rstrip("/")
OPENROUTER_API_URL = os.getenv(
    "OPENROUTER_API_URL",
    "https://openrouter.ai/api/v1/chat/completions",
).strip()
OPENROUTER_HTTP_REFERER = os.getenv("OPENROUTER_HTTP_REFERER", "").strip()
OPENROUTER_TITLE = os.getenv("OPENROUTER_TITLE", "proof_agent").strip()

MODEL_NAME = os.getenv(
    "MODEL_NAME",
    (
        "gemini-2.5-flash"
        if LLM_PROVIDER in {"google", "ai_hub_mixed"}
        else (
            "anthropic/claude-opus-4.6"
            if LLM_PROVIDER == "openrouter"
            else ("gpt-5.4" if LLM_PROVIDER == "openai" else "Pro/deepseek-ai/DeepSeek-V3.2")
        )
    ),
).strip()
MODEL_REASONING_EFFORT = os.getenv("MODEL_REASONING_EFFORT", "xhigh").strip()
MODEL_HIDE_REASONING_OUTPUT = _env_flag("MODEL_HIDE_REASONING_OUTPUT", default=True)
REQUEST_TIMEOUT_SECONDS = _env_int("REQUEST_TIMEOUT_SECONDS", 3000)
PREFER_STREAMING = _env_flag("PREFER_STREAMING", default=True)
DISABLE_TEXT_TRUNCATION = _env_flag("DISABLE_TEXT_TRUNCATION", default=False)
# <=0 表示不限制文献上下文长度
LITERATURE_MAX_CHARS = _env_non_negative_int("LITERATURE_MAX_CHARS", 0)
LITERATURE_RAG_CHUNK_CHARS = _env_non_negative_int("LITERATURE_RAG_CHUNK_CHARS", 1800)
LITERATURE_RAG_OVERLAP_CHARS = _env_non_negative_int("LITERATURE_RAG_OVERLAP_CHARS", 240)
LITERATURE_RAG_TOP_K = _env_non_negative_int("LITERATURE_RAG_TOP_K", 10)
LITERATURE_RAG_PER_DOCUMENT_LIMIT = _env_non_negative_int("LITERATURE_RAG_PER_DOCUMENT_LIMIT", 0)
LITERATURE_RAG_MAX_CHARS = _env_non_negative_int("LITERATURE_RAG_MAX_CHARS", 5000)
LITERATURE_RAG_SUMMARY_CHARS = _env_non_negative_int("LITERATURE_RAG_SUMMARY_CHARS", 2500)
LITERATURE_RAG_DB_FILE = _env_path("LITERATURE_RAG_DB_FILE", CACHE_DIR / "literature_rag.sqlite")
LITERATURE_RAG_VECTOR_DIM = _env_int("LITERATURE_RAG_VECTOR_DIM", 1024)
LITERATURE_RAG_EMBEDDING_MODEL = os.getenv(
    "LITERATURE_RAG_EMBEDDING_MODEL",
    "voyage-context-3",
).strip()
LITERATURE_RAG_RERANK_MODEL = os.getenv(
    "LITERATURE_RAG_RERANK_MODEL",
    "rerank-2.5",
).strip()
LITERATURE_RAG_RETRIEVAL_CANDIDATES = _env_non_negative_int("LITERATURE_RAG_RETRIEVAL_CANDIDATES", 40)
LITERATURE_RAG_QDRANT_PATH = _env_path("LITERATURE_RAG_QDRANT_PATH", "", allow_empty=True)
LITERATURE_RAG_QDRANT_URL = os.getenv("LITERATURE_RAG_QDRANT_URL", "").strip()
LITERATURE_RAG_QDRANT_API_KEY = os.getenv("LITERATURE_RAG_QDRANT_API_KEY", "").strip()
LITERATURE_RAG_QDRANT_COLLECTION = os.getenv("LITERATURE_RAG_QDRANT_COLLECTION", "literature_rag").strip()
LITERATURE_RAG_QDRANT_TIMEOUT = _env_non_negative_int("LITERATURE_RAG_QDRANT_TIMEOUT", 30)
VOYAGE_API_KEY = os.getenv("VOYAGE_API_KEY", "").strip()
VOYAGE_API_TIMEOUT = _env_non_negative_int("VOYAGE_API_TIMEOUT", 60)
VOYAGE_API_RETRY_COUNT = _env_non_negative_int("VOYAGE_API_RETRY_COUNT", 2)
VOYAGE_API_RETRY_BACKOFF_SECONDS = _env_non_negative_int("VOYAGE_API_RETRY_BACKOFF_SECONDS", 3)
LITERATURE_EXTRA_MARKDOWN_GLOBS = _env_csv("LITERATURE_EXTRA_MARKDOWN_GLOBS", "")
# parser 固定为 marker-only
PDF_PARSE_BACKEND = "marker"
MARKER_FORCE_OCR = _env_flag("MARKER_FORCE_OCR", default=False)
MARKER_FORCE_CPU = _env_flag("MARKER_FORCE_CPU", default=False)
MARKER_EXTRA_ARGS = shlex.split(os.getenv("MARKER_EXTRA_ARGS", "").strip())
MARKER_TIMEOUT_SECONDS = _env_non_negative_int("MARKER_TIMEOUT_SECONDS", 1800)
MARKER_DISABLE_MULTIPROCESSING = _env_flag("MARKER_DISABLE_MULTIPROCESSING", default=True)
MARKDOWN_CACHE_FILE = _env_path("MARKDOWN_CACHE_FILE", "", allow_empty=True)
CANDIDATE_MEMORY_FILE = _env_path("CANDIDATE_MEMORY_FILE", CACHE_DIR / "candidate_memory.sqlite")
CANDIDATE_MAX_COUNT = _env_non_negative_int("CANDIDATE_MAX_COUNT", 16)
PROOF_REFINEMENT_MAX_ROUNDS = _env_non_negative_int("PROOF_REFINEMENT_MAX_ROUNDS", 5)
PROPOSITION_REVIEW_MAX_ROUNDS = _env_non_negative_int("PROPOSITION_REVIEW_MAX_ROUNDS", 5)
LOCAL_PROPERTY_DECISION_MAX_PROPERTIES = _env_non_negative_int("LOCAL_PROPERTY_DECISION_MAX_PROPERTIES", 0)
MEMORY_SUMMARIZE_MAX_CANDIDATES = _env_non_negative_int("MEMORY_SUMMARIZE_MAX_CANDIDATES", 15)
MEMORY_TERMINAL_REPORT_MAX_ITEMS = _env_non_negative_int("MEMORY_TERMINAL_REPORT_MAX_ITEMS", 10)
MEMORY_PROPERTY_PACKET_MAX_ITEMS = _env_non_negative_int("MEMORY_PROPERTY_PACKET_MAX_ITEMS", 6)
MEMORY_REUSE_MAX_ITEMS = _env_non_negative_int("MEMORY_REUSE_MAX_ITEMS", 6)
MEMORY_SIMILAR_FAILURE_LIMIT = _env_non_negative_int("MEMORY_SIMILAR_FAILURE_LIMIT", 5)

# 文本截断预算：0 表示不截断；DISABLE_TEXT_TRUNCATION=1 时统一失效
PROMPT_DEFAULT_MAX_CHARS = _env_non_negative_int("PROMPT_DEFAULT_MAX_CHARS", 24000)
PROMPT_LITERATURE_PACKET_MAX_CHARS = _env_non_negative_int("PROMPT_LITERATURE_PACKET_MAX_CHARS", 120000)
REVIEWER_ASSUMPTIONS_MAX_CHARS = _env_non_negative_int("REVIEWER_ASSUMPTIONS_MAX_CHARS", 3200)
REVIEWER_CLAIM_MAX_CHARS = _env_non_negative_int("REVIEWER_CLAIM_MAX_CHARS", 2400)
REVIEWER_DERIVATION_MAX_CHARS = _env_non_negative_int("REVIEWER_DERIVATION_MAX_CHARS", 5200)
REVIEWER_VERIFICATION_NEEDS_MAX_CHARS = _env_non_negative_int("REVIEWER_VERIFICATION_NEEDS_MAX_CHARS", 2400)
REVIEWER_CONCLUSION_MAX_CHARS = _env_non_negative_int("REVIEWER_CONCLUSION_MAX_CHARS", 2400)
REVIEWER_CONTEXT_MAX_CHARS = _env_non_negative_int("REVIEWER_CONTEXT_MAX_CHARS", 2400)

MEMORY_RECENT_CANDIDATE_SUMMARY_MAX_CHARS = _env_non_negative_int("MEMORY_RECENT_CANDIDATE_SUMMARY_MAX_CHARS", 6000)
MEMORY_SIMILAR_FAILURE_SUMMARY_MAX_CHARS = _env_non_negative_int("MEMORY_SIMILAR_FAILURE_SUMMARY_MAX_CHARS", 2400)
MEMORY_PROPOSITION_REUSE_PACKET_MAX_CHARS = _env_non_negative_int("MEMORY_PROPOSITION_REUSE_PACKET_MAX_CHARS", 5000)
MEMORY_TOOL_REQUEST_REUSE_PACKET_MAX_CHARS = _env_non_negative_int("MEMORY_TOOL_REQUEST_REUSE_PACKET_MAX_CHARS", 4500)
MEMORY_PROPERTY_LEARNING_PACKET_MAX_CHARS = _env_non_negative_int("MEMORY_PROPERTY_LEARNING_PACKET_MAX_CHARS", 3200)
MEMORY_TERMINAL_REPORT_SUMMARY_MAX_CHARS = _env_non_negative_int("MEMORY_TERMINAL_REPORT_SUMMARY_MAX_CHARS", 8000)
MEMORY_DERIVED_TREE_SUMMARY_MAX_CHARS = _env_non_negative_int("MEMORY_DERIVED_TREE_SUMMARY_MAX_CHARS", 6000)
MEMORY_SEARCH_PACKET_MAX_CHARS = _env_non_negative_int("MEMORY_SEARCH_PACKET_MAX_CHARS", 8000)
MEMORY_SEARCH_PACKET_TERMINAL_SECTION_MAX_CHARS = _env_non_negative_int("MEMORY_SEARCH_PACKET_TERMINAL_SECTION_MAX_CHARS", 2600)
MEMORY_SEARCH_PACKET_RECENT_CANDIDATES_SECTION_MAX_CHARS = _env_non_negative_int("MEMORY_SEARCH_PACKET_RECENT_CANDIDATES_SECTION_MAX_CHARS", 5000)

POST_TERMINAL_LITERATURE_SNIPPET_MAX_CHARS = _env_non_negative_int("POST_TERMINAL_LITERATURE_SNIPPET_MAX_CHARS", 9000)
POST_TERMINAL_LITERATURE_SUMMARY_MAX_CHARS = _env_non_negative_int("POST_TERMINAL_LITERATURE_SUMMARY_MAX_CHARS", 4000)
POST_TERMINAL_TERMINAL_SUMMARY_MAX_CHARS = _env_non_negative_int("POST_TERMINAL_TERMINAL_SUMMARY_MAX_CHARS", 7000)
DIRECTION_MEMORY_MAX_CHARS = _env_non_negative_int("DIRECTION_MEMORY_MAX_CHARS", 8000)
DIRECTION_TERMINAL_SUMMARY_MAX_CHARS = _env_non_negative_int("DIRECTION_TERMINAL_SUMMARY_MAX_CHARS", 7000)
DIRECTION_LITERATURE_SNIPPET_MAX_CHARS = _env_non_negative_int("DIRECTION_LITERATURE_SNIPPET_MAX_CHARS", 12000)
DIRECTION_LITERATURE_SUMMARY_MAX_CHARS = _env_non_negative_int("DIRECTION_LITERATURE_SUMMARY_MAX_CHARS", 5000)
DESIGN_MEMORY_MAX_CHARS = _env_non_negative_int("DESIGN_MEMORY_MAX_CHARS", 8000)
DESIGN_LITERATURE_SNIPPET_MAX_CHARS = _env_non_negative_int("DESIGN_LITERATURE_SNIPPET_MAX_CHARS", 12000)
DESIGN_LITERATURE_SUMMARY_MAX_CHARS = _env_non_negative_int("DESIGN_LITERATURE_SUMMARY_MAX_CHARS", 5000)
CANDIDATE_PLAN_LITERATURE_SNIPPET_MAX_CHARS = _env_non_negative_int("CANDIDATE_PLAN_LITERATURE_SNIPPET_MAX_CHARS", 5000)
CANDIDATE_PLAN_LITERATURE_SUMMARY_MAX_CHARS = _env_non_negative_int("CANDIDATE_PLAN_LITERATURE_SUMMARY_MAX_CHARS", 2200)
CANDIDATE_FAILURE_CONTEXT_MAX_CHARS = _env_non_negative_int("CANDIDATE_FAILURE_CONTEXT_MAX_CHARS", 1800)
CANDIDATE_PLANNING_SEARCH_MEMORY_MAX_CHARS = _env_non_negative_int("CANDIDATE_PLANNING_SEARCH_MEMORY_MAX_CHARS", 2200)
CANDIDATE_PLANNING_PROPERTY_MAX_CHARS = _env_non_negative_int("CANDIDATE_PLANNING_PROPERTY_MAX_CHARS", 750)
CANDIDATE_PLANNING_TOTAL_MAX_CHARS = _env_non_negative_int("CANDIDATE_PLANNING_TOTAL_MAX_CHARS", 5600)
PROPERTY_FAILURE_CONTEXT_MAX_CHARS = _env_non_negative_int("PROPERTY_FAILURE_CONTEXT_MAX_CHARS", 1600)
PROPERTY_LEARNING_CONTEXT_MAX_CHARS = _env_non_negative_int("PROPERTY_LEARNING_CONTEXT_MAX_CHARS", 2200)
LOCAL_PROPERTY_PACKET_MAX_CHARS = _env_non_negative_int("LOCAL_PROPERTY_PACKET_MAX_CHARS", 1500)
LOCAL_PROPERTY_MEMORY_TOTAL_MAX_CHARS = _env_non_negative_int("LOCAL_PROPERTY_MEMORY_TOTAL_MAX_CHARS", 9000)
LOCAL_PLANNER_LITERATURE_SNIPPET_MAX_CHARS = _env_non_negative_int("LOCAL_PLANNER_LITERATURE_SNIPPET_MAX_CHARS", 8000)
LOCAL_PLANNER_LITERATURE_SUMMARY_MAX_CHARS = _env_non_negative_int("LOCAL_PLANNER_LITERATURE_SUMMARY_MAX_CHARS", 3500)
PROPOSITION_PLANNER_REUSE_MAX_CHARS = _env_non_negative_int("PROPOSITION_PLANNER_REUSE_MAX_CHARS", 4200)
PROPOSITION_PLANNER_LITERATURE_SNIPPET_MAX_CHARS = _env_non_negative_int("PROPOSITION_PLANNER_LITERATURE_SNIPPET_MAX_CHARS", 12000)
PROPOSITION_PLANNER_LITERATURE_SUMMARY_MAX_CHARS = _env_non_negative_int("PROPOSITION_PLANNER_LITERATURE_SUMMARY_MAX_CHARS", 5000)
TOOL_REQUEST_VERIFICATION_NEEDS_MAX_CHARS = _env_non_negative_int("TOOL_REQUEST_VERIFICATION_NEEDS_MAX_CHARS", 2400)
TOOL_REQUEST_REUSE_MAX_CHARS = _env_non_negative_int("TOOL_REQUEST_REUSE_MAX_CHARS", 4200)
TOOL_REQUEST_LITERATURE_SNIPPET_MAX_CHARS = _env_non_negative_int("TOOL_REQUEST_LITERATURE_SNIPPET_MAX_CHARS", 10000)
TOOL_REQUEST_LITERATURE_SUMMARY_MAX_CHARS = _env_non_negative_int("TOOL_REQUEST_LITERATURE_SUMMARY_MAX_CHARS", 4000)
TOOL_REQUEST_DRAFT_MAX_CHARS = _env_non_negative_int("TOOL_REQUEST_DRAFT_MAX_CHARS", 24000)
PROPOSITION_WRITER_REUSE_MAX_CHARS = _env_non_negative_int("PROPOSITION_WRITER_REUSE_MAX_CHARS", 4200)
PROPOSITION_WRITER_LITERATURE_SNIPPET_MAX_CHARS = _env_non_negative_int("PROPOSITION_WRITER_LITERATURE_SNIPPET_MAX_CHARS", 12000)
PROPOSITION_WRITER_LITERATURE_SUMMARY_MAX_CHARS = _env_non_negative_int("PROPOSITION_WRITER_LITERATURE_SUMMARY_MAX_CHARS", 5000)
PROPOSITION_FAIL_LOG_VERIFICATION_NEEDS_MAX_CHARS = _env_non_negative_int("PROPOSITION_FAIL_LOG_VERIFICATION_NEEDS_MAX_CHARS", 800)
PROOF_REFINEMENT_LITERATURE_SNIPPET_MAX_CHARS = _env_non_negative_int("PROOF_REFINEMENT_LITERATURE_SNIPPET_MAX_CHARS", 12000)
PROOF_REFINEMENT_LITERATURE_SUMMARY_MAX_CHARS = _env_non_negative_int("PROOF_REFINEMENT_LITERATURE_SUMMARY_MAX_CHARS", 5000)
PROOF_REFINEMENT_PROPERTY_BUNDLE_MAX_CHARS = _env_non_negative_int("PROOF_REFINEMENT_PROPERTY_BUNDLE_MAX_CHARS", 80000)

# 非 DISABLE_TEXT_TRUNCATION 管辖的采样/预览限制：0 表示不限制
# 这里保留“会明显影响探索行为或 prompt 可见内容”的限制。
# 内部去重/签名归一化/关键句摘录等实现细节继续保留硬编码，不暴露到 .env。
MEMORY_PROPOSITION_SNAPSHOT_MAX_ITEMS = _env_non_negative_int("MEMORY_PROPOSITION_SNAPSHOT_MAX_ITEMS", 6)
TERMINAL_REPORT_PROPOSITION_SNAPSHOT_MAX_ITEMS = _env_non_negative_int("TERMINAL_REPORT_PROPOSITION_SNAPSHOT_MAX_ITEMS", 8)
MEMORY_DEPENDENCY_KNOWLEDGE_MAX_ITEMS = _env_non_negative_int("MEMORY_DEPENDENCY_KNOWLEDGE_MAX_ITEMS", 6)
MEMORY_HEURISTIC_MAX_ITEMS = _env_non_negative_int("MEMORY_HEURISTIC_MAX_ITEMS", 6)
MEMORY_PROPOSITION_HISTORY_MAX_ITEMS = _env_non_negative_int("MEMORY_PROPOSITION_HISTORY_MAX_ITEMS", 6)
MEMORY_TOOL_HISTORY_MAX_ITEMS = _env_non_negative_int("MEMORY_TOOL_HISTORY_MAX_ITEMS", 6)
MEMORY_RECENT_CANDIDATE_FORM_PREVIEW_MAX_CHARS = _env_non_negative_int("MEMORY_RECENT_CANDIDATE_FORM_PREVIEW_MAX_CHARS", 220)
MEMORY_RECENT_CANDIDATE_REASON_PREVIEW_MAX_CHARS = _env_non_negative_int("MEMORY_RECENT_CANDIDATE_REASON_PREVIEW_MAX_CHARS", 180)
MEMORY_RECENT_CANDIDATE_RISK_PREVIEW_MAX_CHARS = _env_non_negative_int("MEMORY_RECENT_CANDIDATE_RISK_PREVIEW_MAX_CHARS", 180)
MEMORY_SIMILAR_FAILURE_FORM_PREVIEW_MAX_CHARS = _env_non_negative_int("MEMORY_SIMILAR_FAILURE_FORM_PREVIEW_MAX_CHARS", 180)
MEMORY_SIMILAR_FAILURE_REASON_PREVIEW_MAX_CHARS = _env_non_negative_int("MEMORY_SIMILAR_FAILURE_REASON_PREVIEW_MAX_CHARS", 180)
MEMORY_SIMILAR_FAILURE_PROP_NOTE_PREVIEW_MAX_CHARS = _env_non_negative_int("MEMORY_SIMILAR_FAILURE_PROP_NOTE_PREVIEW_MAX_CHARS", 120)
MEMORY_DERIVED_TREE_CHILDREN_PREVIEW_MAX_ITEMS = _env_non_negative_int("MEMORY_DERIVED_TREE_CHILDREN_PREVIEW_MAX_ITEMS", 6)
PROPERTY_PASS_NOTE_MAX_ITEMS = _env_non_negative_int("PROPERTY_PASS_NOTE_MAX_ITEMS", 3)


REVIEWER_ROLE_OVERRIDES = {
    "Syntax Inspector": """
You are the Syntax Inspector, a rigorous but neutral reviewer for mathematical drafts in computational geometry.

Scope:
- Judge only the current task context and the current draft.
- Do not import objections from earlier rounds unless the same issue is still present in the current text.
- If the draft explicitly declares a replacement definition, coordinate system, or reparameterization and then uses it consistently, do not reject merely because it differs from Xia's original notation.

Decision policy:
- Distinguish fatal issues from fixable issues.
- Reject only for concrete symbolic or type errors, self-contradictory variable dependencies, undeclared changes of meaning that break a derivation, or claims that materially exceed what the derivation establishes.
- Treat notation drift, missing reminders, and local omissions as fixable unless they invalidate a proof step.
- For subtasks, evaluate only the local claim required by the current subtask, not the final global theorem.

Checks:
- Object/property distinction such as O_i versus o_i.
- Scalar, norm, path-length, and derivative type consistency.
- Index ranges and declared variable dependencies.
- Coordinate conventions and domain restrictions for alpha, beta, gamma, and X_{o_n}.
- Consistent use of any new potential function or alternative parameterization.

Output only:
<REVIEW_RESULT>
[PASS] or [REJECT]
...
</REVIEW_RESULT>

If PASS, list any non-fatal fixes still recommended.
If REJECT, list only the fatal issues and concrete repair steps.
Do not output anything outside the tag.
""".strip(),
    "Logic Auditor": """
You are the Logic Auditor, a rigorous but neutral mathematical reviewer for derivations in computational geometry.

Scope:
- Judge only the current task context and the current draft.
- Audit whether the stated assumptions, parameter dependencies, inequalities, and conclusions are sufficient for the current subtask.
- Do not import objections from prior rounds unless they remain literally true in the current draft.

Decision policy:
- Distinguish fatal logical flaws from fixable presentation issues.
- Reject only for concrete algebraic mistakes, broken chain-rule or dependency reasoning, contradictions between assumptions and derivation, missing steps that invalidate the claimed conclusion, or claims materially stronger than the draft supports.
- Missing exposition, notation cleanup, and routine boundary-case reminders are fixable unless they change the truth of the derivation.
- If the draft proves only a local lemma, parameterization, or derivative identity, evaluate only that local result and do not require it to establish the final rho bound by itself.

Checks:
- Whether assumptions and independent variables are explicitly declared and then used consistently.
- Whether each inequality or derivative step follows from prior statements.
- Whether local conclusions stay within the support of the derivation.
- Whether a stronger global theorem is being asserted without sufficient support.

Output only:
<REVIEW_RESULT>
[PASS] or [REJECT]
...
</REVIEW_RESULT>

If PASS, include any non-fatal fixes that should still be made.
If REJECT, include only the fatal reasons and concrete repair steps.
Do not output anything outside the tag.
""".strip(),
    "Global Adversary": """
You are the Global Adversary, an independent reviewer for whole-proof consistency and conclusion control.

Scope:
- Review the current draft neutrally for material inconsistencies, unsupported global leaps, and contradictions across sections.
- Respect any explicitly stated replacement definitions or task-local contracts if they are used consistently.
- Do not reject merely because the draft is incomplete at the global-theorem level when the current task is only a local subtask.

Decision policy:
- Reject only for material contradictions, impossible geometric assumptions, essential proof gaps that block the stated conclusion, or claims that materially exceed the demonstrated support.
- Treat stylistic issues, missing exposition, and local cleanup as fixable unless they alter the mathematical validity.

Checks:
- Global consistency of assumptions, notation, and coordinate choices.
- Whether the conclusion matches the proved scope.
- Whether any subtask draft is overclaiming a final theorem or rho improvement.

Output only:
<REVIEW_RESULT>
[PASS] or [REJECT]
...
</REVIEW_RESULT>

If PASS, list any non-fatal fixes still recommended.
If REJECT, list only the fatal issues and concrete repair steps.
Do not output anything outside the tag.
""".strip(),
}


AUXILIARY_ROLE_OVERRIDES = {
    "Domain Surveyor": """
You are the Domain Surveyor for mathematical research planning in computational geometry.

Scope:
- You are not a reviewer and you do not issue verdicts.
- Diagnose bottlenecks, dependency risks, hidden assumptions, and open questions for the current task only.
- Use the paper's notation faithfully, but focus on where the current task is likely to fail or need extra support.

Output policy:
- Do not output [PASS], [REJECT], [ACCEPT], [REVISE], Final Decision, Conclusion Label, or Reviewer Verification.
- Do not act like a judge; act like a technical scout.
- Prefer concise, structured notes that help the next agent decide what to prove or repair.
""".strip(),
    "Cross-Domain Grafter": """
You are the Cross-Domain Grafter for mathematical research planning in computational geometry.

Scope:
- You are not a reviewer and you do not issue verdicts.
- Propose usable tools, analogies, or proof templates that can be mapped onto the current task.
- Be explicit about preconditions, failure modes, and what must already be true for the proposed method to work.

Output policy:
- Do not output [PASS], [REJECT], [ACCEPT], [REVISE], Final Decision, Conclusion Label, or Reviewer Verification.
- Do not grade the draft; provide candidate methods and the tradeoffs of using them.
- Keep the advice actionable and task-specific.
""".strip(),
}
def _active_api_key():
    if LLM_PROVIDER == "google":
        return GOOGLE_API_KEY
    if LLM_PROVIDER == "ai_hub_mixed":
        return AI_HUB_MIXED_API_KEY
    if LLM_PROVIDER == "openai":
        return OPENAI_API_KEY
    if LLM_PROVIDER == "openrouter":
        return OPENROUTER_API_KEY
    return SILICONFLOW_API_KEY


def _openai_compatible_api_url():
    if LLM_PROVIDER == "openai":
        return f"{OPENAI_BASE_URL}/chat/completions"
    if LLM_PROVIDER == "ai_hub_mixed":
        return AI_HUB_MIXED_API_URL
    if LLM_PROVIDER == "openrouter":
        return OPENROUTER_API_URL
    return SILICONFLOW_API_URL


def _openai_compatible_extra_headers():
    headers = {}
    if LLM_PROVIDER == "openrouter":
        if OPENROUTER_HTTP_REFERER:
            headers["HTTP-Referer"] = OPENROUTER_HTTP_REFERER
        if OPENROUTER_TITLE:
            headers["X-OpenRouter-Title"] = OPENROUTER_TITLE
    return headers


def _google_api_url(stream=False):
    model = quote(MODEL_NAME, safe="")
    method = "streamGenerateContent" if stream else "generateContent"
    if stream:
        query = f"alt=sse&key={GOOGLE_API_KEY}"
    else:
        query = f"key={GOOGLE_API_KEY}"
    return f"https://generativelanguage.googleapis.com/v1beta/models/{model}:{method}?{query}"


def _extract_google_text(data):
    if not isinstance(data, dict):
        return ""
    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return ""
    content = candidates[0].get("content") if isinstance(candidates[0], dict) else {}
    parts = content.get("parts") if isinstance(content, dict) else []
    if not isinstance(parts, list):
        return ""
    chunks = []
    for part in parts:
        if isinstance(part, dict):
            text = part.get("text")
            if text:
                chunks.append(str(text))
    return "".join(chunks).strip()


def _normalize_whitespace(text):
    if text is None:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


def _sanitize_filename_component(text, max_len=48):
    """
    清洗文件名片段：保留中英文、数字、下划线、短横线、点号，其他字符统一为下划线。
    """
    s = _normalize_whitespace(text)
    if not s:
        return "na"
    s = re.sub(r"[\\/:*?\"<>|]+", "_", s)
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("._-")
    if not s:
        return "na"
    return s[:max_len].rstrip("._-") or "na"


def _fmt_temp(v):
    return f"{float(v):g}"
