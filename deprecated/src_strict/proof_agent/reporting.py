import hashlib
from datetime import datetime

from .agents import (
    correctness_checker,
    global_rev,
    logic_rev,
    orchestrator,
    potential_designer,
    proof_planner,
    proof_writer,
)
from .app_config import (
    MODEL_NAME,
    PDF_PARSE_BACKEND,
    PREFER_STREAMING,
    REQUEST_TIMEOUT_SECONDS,
    _fmt_temp,
    _normalize_whitespace,
    _sanitize_filename_component,
)
from .paths import REPORTS_DIR


def _truncate_filename_utf8(filename: str, max_bytes: int = 240) -> str:
    text = str(filename or "").strip() or "research_output.md"
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text

    if "." in text:
        stem, ext = text.rsplit(".", 1)
        suffix = f".{ext}"
    else:
        stem, suffix = text, ""

    suffix_bytes = suffix.encode("utf-8")
    budget = max(1, int(max_bytes) - len(suffix_bytes))
    trimmed = stem.encode("utf-8")[:budget]
    while True:
        try:
            safe_stem = trimmed.decode("utf-8").rstrip("._-")
            break
        except UnicodeDecodeError:
            trimmed = trimmed[:-1]
            if not trimmed:
                safe_stem = "research_output"
                break
    return f"{safe_stem or 'research_output'}{suffix}"


def _build_report_filename(goal_text):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    goal_norm = _normalize_whitespace(goal_text)
    goal_short = _sanitize_filename_component(goal_norm, max_len=56)
    goal_hash = hashlib.sha1(goal_norm.encode("utf-8")).hexdigest()[:8] if goal_norm else "00000000"

    model_short = _sanitize_filename_component(MODEL_NAME, max_len=36)
    backend_short = _sanitize_filename_component(PDF_PARSE_BACKEND, max_len=16)

    temps_token = (
        f"pot{_fmt_temp(potential_designer.temp)}_"
        f"pi{_fmt_temp(orchestrator.temp)}_"
        f"plan{_fmt_temp(proof_planner.temp)}_"
        f"write{_fmt_temp(proof_writer.temp)}_"
        f"rev{_fmt_temp(correctness_checker.temp)}-{_fmt_temp(logic_rev.temp)}-{_fmt_temp(global_rev.temp)}"
    )
    temps_short = _sanitize_filename_component(temps_token, max_len=52)
    stream_token = f"stream{1 if PREFER_STREAMING else 0}"
    timeout_token = f"to{REQUEST_TIMEOUT_SECONDS}"

    filename = (
        f"research_output_{ts}_{goal_short}_{goal_hash}_"
        f"{model_short}_{temps_short}_{stream_token}_{timeout_token}_{backend_short}.md"
    )
    return str(REPORTS_DIR / _truncate_filename_utf8(filename, max_bytes=240))
