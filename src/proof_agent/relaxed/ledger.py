"""Lemma Ledger — append-only store of closed lemmas, fingerprint-deduped.

Cross-plan and cross-obligation cache. A lemma is admitted only if its
certificate has already passed the obligation gate (closed=True). Plan Set
Generator and Plan Developer both consult the ledger before doing fresh work.
"""
from __future__ import annotations

from pathlib import Path

from ..paths import ARTIFACTS_DIR
from .records import (
    LEMMA_LEDGER_JSONL,
    LemmaRecord,
    append_jsonl,
    fingerprint_statement,
    load_lemmas,
    normalize_statement,
)


class LemmaLedger:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else (ARTIFACTS_DIR / LEMMA_LEDGER_JSONL)
        self._records: dict[str, LemmaRecord] = {}
        self.reload()

    # --- I/O ---

    def reload(self) -> None:
        self._records = {rec.lemma_id: rec for rec in load_lemmas(self.path)}

    def append(self, record: LemmaRecord) -> bool:
        """Persist a new lemma. Returns True if newly added, False if dedupe hit.

        Caller is responsible for ensuring `record.closed is True` (i.e.
        the certificate already passed the obligation gate).
        """
        if not record.closed:
            raise ValueError("LemmaLedger only accepts closed lemmas (record.closed must be True)")
        existing = self._records.get(record.lemma_id)
        if existing is not None:
            return False
        self._records[record.lemma_id] = record
        append_jsonl(self.path, record)
        return True

    # --- Queries ---

    def __len__(self) -> int:
        return len(self._records)

    def all(self) -> list[LemmaRecord]:
        return list(self._records.values())

    def visible_to(self, oid: str) -> list[LemmaRecord]:
        """Return lemmas whose `reusable_scope` covers obligation `oid`."""
        key = str(oid or "").strip().upper()
        out = []
        for rec in self._records.values():
            scope = {str(s).strip().upper() for s in (rec.reusable_scope or [])}
            if "ALL" in scope or key in scope:
                out.append(rec)
        return out

    def find_equivalent(self, statement: str) -> LemmaRecord | None:
        """Return the cached lemma with the same fingerprint, if any."""
        fp = fingerprint_statement(statement)
        return self._records.get(fp)

    def render_for_prompt(self, oid: str, max_chars: int = 8000) -> str:
        """One-shot block of "already-proved lemmas" for prompt injection."""
        visible = self.visible_to(oid)
        if not visible:
            return "(none)"
        lines = []
        used = 0
        for rec in visible:
            entry = f"- [{rec.lemma_id}] (origin={rec.obligation_origin}) {rec.statement}"
            if used + len(entry) + 1 > max_chars:
                lines.append(f"...({len(visible) - len(lines)} more lemmas truncated)")
                break
            lines.append(entry)
            used += len(entry) + 1
        return "\n".join(lines)
