"""Failure Ledger — append-only store of subgoals that could not be closed.

The dual of the Lemma Ledger: where LemmaLedger remembers *successes* (closed
lemmas, success-only), this remembers *failures* — a subgoal the structural gate
or the semantic reviewer rejected, together with the objection. It is the
persistent failure memory the relaxed agent previously lacked: reviewer REJECT
reasons used to be consumed by a single intra-call revision and then thrown
away, so a later round or session would re-attempt the same sub-lemma blind.

Keyed by the subgoal fingerprint, latest objection wins. Consumers:
  - Proof Writer (developer.py): reads the prior reason for the exact sub-lemma
    it is about to draft, so it does not reproduce the rejected argument.
  - Plan Selector (selector.py): folds a dead plan's open-subgoal reasons into
    its obstruction so the next Plan Set Generator avoids the route.

Lives at the same default ARTIFACTS_DIR location every run, so the memory spans
both rounds and sessions automatically (like the Lemma Ledger).
"""
from __future__ import annotations

from pathlib import Path

from ..paths import ARTIFACTS_DIR
from .records import (
    FAILURES_JSONL,
    FailureRecord,
    append_jsonl,
    fingerprint_statement,
    load_failures,
)


class FailureLedger:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else (ARTIFACTS_DIR / FAILURES_JSONL)
        self._records: dict[str, FailureRecord] = {}
        self.reload()

    # --- I/O ---

    def reload(self) -> None:
        self._records = {rec.failure_id: rec for rec in load_failures(self.path)}

    def record(self, record: FailureRecord) -> None:
        """Persist a failure.

        Unlike LemmaLedger.append (which rejects a duplicate fingerprint), a
        repeat fingerprint here OVERWRITES the in-memory entry and is re-appended
        to disk: the most recent objection is the most useful feedback for the
        next attempt, and load_failures() collapses to latest-wins on reload.
        """
        self._records[record.failure_id] = record
        append_jsonl(self.path, record)

    # --- Queries ---

    def __len__(self) -> int:
        return len(self._records)

    def all(self) -> list[FailureRecord]:
        return list(self._records.values())

    def find_for(self, statement: str) -> FailureRecord | None:
        """Return the persisted failure for this exact sub-lemma, if any."""
        return self._records.get(fingerprint_statement(statement))
