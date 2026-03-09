import os
import re
import shlex
from urllib.parse import quote


def _load_dotenv_file(path=".env", override=True):
    """轻量读取 .env；默认覆盖同名环境变量，避免继承旧值。"""
    if not os.path.exists(path):
        return

    try:
        with open(path, "r", encoding="utf-8") as f:
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
                if key:
                    if override or key not in os.environ:
                        os.environ[key] = value
    except Exception:
        # .env 读取失败时保持静默，继续走系统环境变量
        pass


_dotenv_override = str(os.getenv("DOTENV_OVERRIDE", "1")).strip().lower() in {"1", "true", "yes", "on"}
_load_dotenv_file(".env", override=_dotenv_override)


def _env_flag(name, default=False):
    val = os.getenv(name)
    if val is None:
        return bool(default)
    return str(val).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name, default):
    val = os.getenv(name)
    if val is None:
        return int(default)
    try:
        return int(str(val).strip())
    except Exception:
        return int(default)


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


def _proxy_hint():
    return (
        os.getenv("HTTPS_PROXY")
        or os.getenv("https_proxy")
        or os.getenv("HTTP_PROXY")
        or os.getenv("http_proxy")
        or os.getenv("ALL_PROXY")
        or os.getenv("all_proxy")
        or "127.0.0.1:7890"
    )


def _normalize_provider(name):
    s = (name or "").strip().lower()
    alias = {
        "google": "google",
        "gemini": "google",
        "siliconflow": "siliconflow",
        "sf": "siliconflow",
        "硅基流动": "siliconflow",
        "轨迹流动": "siliconflow",
        "ai_hub_mixed": "ai_hub_mixed",
        "aihubmixed": "ai_hub_mixed",
        "aihub": "ai_hub_mixed",
        "mixed": "ai_hub_mixed",
    }
    return alias.get(s, "siliconflow")


LLM_PROVIDER = _normalize_provider(os.getenv("LLM_PROVIDER", "siliconflow"))
SILICONFLOW_API_KEY = os.getenv("SILICONFLOW_API_KEY", "").strip()
GOOGLE_API_KEY = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
AI_HUB_MIXED_API_KEY = os.getenv("AI_HUB_MIXED_MODEL_API_KEY", "").strip()
SILICONFLOW_API_URL = "https://api.siliconflow.cn/v1/chat/completions"
AI_HUB_MIXED_API_URL = os.getenv(
    "AI_HUB_MIXED_API_URL",
    "https://aihubmix.com/v1/chat/completions",
).strip()

MODEL_NAME = os.getenv(
    "MODEL_NAME",
    (
        "gemini-2.5-flash"
        if LLM_PROVIDER in {"google", "ai_hub_mixed"}
        else "Pro/deepseek-ai/DeepSeek-V3.2"
    ),
).strip()
REQUEST_TIMEOUT_SECONDS = _env_int("REQUEST_TIMEOUT_SECONDS", 3000)
LLM_MAX_ATTEMPTS = max(1, _env_int("LLM_MAX_ATTEMPTS", 3))
PREFER_STREAMING = _env_flag("PREFER_STREAMING", default=True)
REUSE_CUSTOM_PROMPTS = _env_flag("REUSE_CUSTOM_PROMPTS", default=True)
# <=0 表示不限制文献上下文长度
LITERATURE_MAX_CHARS = _env_int("LITERATURE_MAX_CHARS", 0)
# parser 固定为 marker-only
PDF_PARSE_BACKEND = "marker"
MARKER_FORCE_OCR = _env_flag("MARKER_FORCE_OCR", default=False)
MARKER_FORCE_CPU = _env_flag("MARKER_FORCE_CPU", default=False)
MARKER_EXTRA_ARGS = shlex.split(os.getenv("MARKER_EXTRA_ARGS", "").strip())
MARKER_TIMEOUT_SECONDS = _env_int("MARKER_TIMEOUT_SECONDS", 1800)
MARKER_DISABLE_MULTIPROCESSING = _env_flag("MARKER_DISABLE_MULTIPROCESSING", default=True)
MARKDOWN_CACHE_FILE = os.getenv("MARKDOWN_CACHE_FILE", "").strip()
LLM_LOG_PATH = os.getenv("LLM_LOG_PATH", "logs/llm_outputs.jsonl").strip()
LOG_LLM_IO = os.getenv("LOG_LLM_IO", "1").strip() != "0"
try:
    LLM_LOG_MAX_CHARS = int(os.getenv("LLM_LOG_MAX_CHARS", "400000"))
except Exception:
    LLM_LOG_MAX_CHARS = 400000


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


def _apply_reviewer_prompt_overrides(data):
    if not isinstance(data, dict):
        return data, False
    merged = dict(data)
    changed = False
    for name, prompt in REVIEWER_ROLE_OVERRIDES.items():
        if merged.get(name) != prompt:
            merged[name] = prompt
            changed = True
    return merged, changed


def _active_api_key():
    if LLM_PROVIDER == "google":
        return GOOGLE_API_KEY
    if LLM_PROVIDER == "ai_hub_mixed":
        return AI_HUB_MIXED_API_KEY
    return SILICONFLOW_API_KEY


def _google_api_url(stream=False):
    model = quote(MODEL_NAME, safe="")
    method = "streamGenerateContent?alt=sse" if stream else "generateContent"
    return f"https://generativelanguage.googleapis.com/v1beta/models/{model}:{method}?key={GOOGLE_API_KEY}"


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
    try:
        return f"{float(v):g}"
    except Exception:
        return "na"
