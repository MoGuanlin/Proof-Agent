from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
PACKAGE_DIR = SRC_DIR / "proof_agent"

DOCS_DIR = PROJECT_ROOT / "docs"
DATA_DIR = PROJECT_ROOT / "data"
PAPERS_DIR = DATA_DIR / "papers"
PRIMARY_PAPERS_DIR = PAPERS_DIR / "primary"
REFERENCE_PAPERS_DIR = PAPERS_DIR / "references"

ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"
REPORTS_DIR = ARTIFACTS_DIR / "reports"
PROMPT_SNAPSHOTS_DIR = ARTIFACTS_DIR / "prompt_snapshots"

RUNTIME_DIR = PROJECT_ROOT / "runtime"
LOGS_DIR = RUNTIME_DIR / "logs"
NOHUP_DIR = RUNTIME_DIR / "nohup"
PIDS_DIR = RUNTIME_DIR / "pids"

CACHE_DIR = PROJECT_ROOT / ".cache"
MARKDOWN_CACHE_DIR = CACHE_DIR / "markitdown"
TMP_DIR = PROJECT_ROOT / "tmp"

ENV_FILE = PROJECT_ROOT / ".env"
START_SCRIPT_FILE = PROJECT_ROOT / "scripts" / "start_main.sh"
MAIN_PID_FILE = PIDS_DIR / "main.pid"
PROMPT_SNAPSHOT_FILE = PROMPT_SNAPSHOTS_DIR / "customized_prompts.snapshot.json"
LEGACY_PROMPT_SNAPSHOT_FILE = PROMPT_SNAPSHOTS_DIR / "customized_prompts.json"
PRIMARY_PAPER_PATH = PRIMARY_PAPERS_DIR / "1103.4361v2.pdf"


def ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_project_dirs() -> None:
    for path in (
        DOCS_DIR,
        PRIMARY_PAPERS_DIR,
        REFERENCE_PAPERS_DIR,
        REPORTS_DIR,
        PROMPT_SNAPSHOTS_DIR,
        LOGS_DIR,
        NOHUP_DIR,
        PIDS_DIR,
        MARKDOWN_CACHE_DIR,
        TMP_DIR,
    ):
        ensure_directory(path)


def resolve_from_project(raw_path: str | Path | None, *, empty_ok: bool = False) -> str:
    if raw_path is None:
        return "" if empty_ok else str(PROJECT_ROOT)
    text = str(raw_path).strip()
    if not text:
        return "" if empty_ok else str(PROJECT_ROOT)
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return str(path.resolve())


def resolve_glob_from_project(pattern: str | Path | None) -> str:
    return resolve_from_project(pattern, empty_ok=True)


ensure_project_dirs()
