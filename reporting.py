import hashlib
from datetime import datetime

from agents import architect, generator, global_rev, logic_rev, orchestrator, syntax_rev
from app_config import (
    MODEL_NAME,
    PDF_PARSE_BACKEND,
    PREFER_STREAMING,
    REQUEST_TIMEOUT_SECONDS,
    _fmt_temp,
    _normalize_whitespace,
    _sanitize_filename_component,
)


def _build_report_filename(goal_text):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    goal_norm = _normalize_whitespace(goal_text)
    goal_short = _sanitize_filename_component(goal_norm, max_len=56)
    goal_hash = hashlib.sha1(goal_norm.encode("utf-8")).hexdigest()[:8] if goal_norm else "00000000"

    model_short = _sanitize_filename_component(MODEL_NAME, max_len=36)
    backend_short = _sanitize_filename_component(PDF_PARSE_BACKEND, max_len=16)

    temps_token = (
        f"arch{_fmt_temp(architect.temp)}_"
        f"pi{_fmt_temp(orchestrator.temp)}_"
        f"gen{_fmt_temp(generator.temp)}_"
        f"rev{_fmt_temp(syntax_rev.temp)}-{_fmt_temp(logic_rev.temp)}-{_fmt_temp(global_rev.temp)}"
    )
    temps_short = _sanitize_filename_component(temps_token, max_len=52)
    stream_token = f"stream{1 if PREFER_STREAMING else 0}"
    timeout_token = f"to{REQUEST_TIMEOUT_SECONDS}"

    filename = (
        f"research_output_{ts}_{goal_short}_{goal_hash}_"
        f"{model_short}_{temps_short}_{stream_token}_{timeout_token}_{backend_short}.md"
    )
    return filename[:240]
