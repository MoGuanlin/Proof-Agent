"""Plan / Lemma data records and append-only JSONL persistence."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

# JSONL files (one JSON object per line; latest record wins per primary key).
PLANS_JSONL = "section6_198_plans.jsonl"
LEMMA_LEDGER_JSONL = "section6_198_lemma_ledger.jsonl"
ESCALATIONS_JSONL = "section6_198_escalations.jsonl"
FAILURES_JSONL = "section6_198_failures.jsonl"


# --- Statement normalization (used to build lemma fingerprint) --------------
#
# Design: prefer false negatives (missed merges) over false positives (wrong
# merges). Wrongly identifying two distinct lemmas would corrupt downstream
# proofs; missing a merge only costs one re-proof. So we deliberately do NOT
# alpha-rename bound variables here — that would risk collapsing distinct
# statements that share variable letters by accident.

_WS_RE = re.compile(r"\s+")
# Greek/symbol → ASCII canonical form.
_SYMBOL_MAP = {
    "≤": "<=", "≥": ">=", "≠": "!=", "·": "*", "×": "*",
    "−": "-", "–": "-", "—": "-",
    "Φ": "Phi", "Υ": "Upsilon", "λ": "lambda", "η": "eta",
    "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta",
    "π": "pi", "θ": "theta", "ρ": "rho",
    "∂": "d", "∈": "in", "∀": "forall", "∃": "exists",
}
_TRAILING_PUNCT = ".,;:!?"


def normalize_statement(text: str) -> str:
    """Conservative normalization: maps unicode math glyphs to ASCII, collapses
    whitespace, lowercases, strips trailing punctuation. Does NOT alpha-rename
    variables — see module docstring for rationale.
    """
    s = str(text or "")
    for k, v in _SYMBOL_MAP.items():
        s = s.replace(k, v)
    s = _WS_RE.sub(" ", s).strip()
    s = s.lower()
    while s and s[-1] in _TRAILING_PUNCT:
        s = s[:-1].rstrip()
    return s


def fingerprint_statement(text: str) -> str:
    """Stable 16-char hex hash of normalize_statement(text)."""
    norm = normalize_statement(text)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


# --- Records ----------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class LemmaRecord:
    lemma_id: str               # fingerprint_statement(statement)
    obligation_origin: str      # "S2".."S6"
    statement: str              # original text
    statement_normalized: str   # normalize_statement(statement)
    certificate: str            # manual_certificate full text (must pass gate)
    closed: bool                # gate verdict
    reusable_scope: list[str] = field(default_factory=lambda: ["ALL"])
    tool_requests: list[dict] = field(default_factory=list)
    created_at: str = field(default_factory=_now_iso)
    plan_id: str = ""           # the plan that proved it (for traceability)
    subgoal_index: int = -1     # position within that plan's subgoals

    @classmethod
    def from_statement(
        cls, *, statement: str, certificate: str, obligation_origin: str,
        reusable_scope: list[str] | None = None, tool_requests: list[dict] | None = None,
        plan_id: str = "", subgoal_index: int = -1, closed: bool = True,
    ) -> "LemmaRecord":
        return cls(
            lemma_id=fingerprint_statement(statement),
            obligation_origin=obligation_origin,
            statement=statement,
            statement_normalized=normalize_statement(statement),
            certificate=certificate,
            closed=closed,
            reusable_scope=list(reusable_scope or ["ALL"]),
            tool_requests=list(tool_requests or []),
            plan_id=plan_id,
            subgoal_index=subgoal_index,
        )


@dataclass
class FailureRecord:
    """A subgoal that could not be closed — persistent failure memory.

    The dual of LemmaRecord (which is success-only): this captures *why* an
    attempt failed so a later round or session can avoid repeating it. Keyed by
    the subgoal fingerprint, latest objection wins. The Proof Writer reads the
    prior reason for the exact sub-lemma it is about to draft; a dead plan folds
    its open subgoals' reasons into its obstruction for the next Generator round.
    """
    failure_id: str             # fingerprint_statement(statement)
    obligation_id: str          # "S2".."S6"
    statement: str              # the subgoal text that failed
    statement_normalized: str   # normalize_statement(statement)
    reason: str                 # the gate / reviewer objection (last failure reason)
    kind: str                   # "reviewer" | "gate"
    approach_name: str = ""     # route the failing attempt belonged to
    plan_id: str = ""
    round_idx: int = -1
    attempts: int = 0           # draft attempts spent before giving up
    created_at: str = field(default_factory=_now_iso)

    @classmethod
    def from_statement(
        cls, *, statement: str, reason: str, obligation_id: str,
        kind: str = "gate", approach_name: str = "", plan_id: str = "",
        round_idx: int = -1, attempts: int = 0,
    ) -> "FailureRecord":
        return cls(
            failure_id=fingerprint_statement(statement),
            obligation_id=obligation_id,
            statement=statement,
            statement_normalized=normalize_statement(statement),
            reason=reason,
            kind=kind,
            approach_name=approach_name,
            plan_id=plan_id,
            round_idx=round_idx,
            attempts=attempts,
        )


@dataclass
class PlanRecord:
    plan_id: str                # e.g. "S2_round01_P1"
    obligation_id: str          # "S2".."S6"
    round_idx: int
    approach_name: str
    subgoals: list[str]
    uses_lemmas: list[str] = field(default_factory=list)
    subgoal_status: dict[str, str] = field(default_factory=dict)  # subgoal text -> "open"/"closed"/"dead"
    status: str = "active"      # "active" / "complete" / "dead"
    obstruction: str = ""
    rounds_without_progress: int = 0
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)


# --- JSONL I/O --------------------------------------------------------------

def append_jsonl(path: Path, record: LemmaRecord | PlanRecord | dict) -> None:
    """Atomic-ish append: open in 'a' mode (POSIX small writes are usually atomic)."""
    payload = asdict(record) if hasattr(record, "__dataclass_fields__") else dict(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> Iterable[dict]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def load_lemmas(path: Path) -> list[LemmaRecord]:
    """Load lemmas, deduped by lemma_id (latest wins)."""
    seen: dict[str, LemmaRecord] = {}
    for row in _read_jsonl(path):
        try:
            rec = LemmaRecord(**row)
        except TypeError:
            continue
        seen[rec.lemma_id] = rec
    return list(seen.values())


def load_plans(path: Path) -> list[PlanRecord]:
    """Load plans, deduped by plan_id (latest wins). Use this for resume."""
    seen: dict[str, PlanRecord] = {}
    for row in _read_jsonl(path):
        try:
            rec = PlanRecord(**row)
        except TypeError:
            continue
        seen[rec.plan_id] = rec
    return list(seen.values())


def load_failures(path: Path) -> list[FailureRecord]:
    """Load failures, deduped by failure_id (latest objection wins)."""
    seen: dict[str, FailureRecord] = {}
    for row in _read_jsonl(path):
        try:
            rec = FailureRecord(**row)
        except TypeError:
            continue
        seen[rec.failure_id] = rec
    return list(seen.values())
