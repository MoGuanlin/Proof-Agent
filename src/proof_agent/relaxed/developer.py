"""Plan Developer: prove individual subgoals, share results via Lemma Ledger.

For each open subgoal in a plan:
  1. Look up the Ledger; if an equivalent lemma is already proved, cite it.
  2. Otherwise, call the Proof Writer LLM (BaseAgent) to draft a 6-section
     manual_certificate for THIS subgoal.
  3. Run the obligation gate against the draft (verdict semantics: subgoal
     treated as an analytic obligation in the same family as its parent oid).
  4. If the gate passes AND a semantic reviewer accepts the math, persist into
     the Ledger so other plans see it next iteration. If either the gate or the
     reviewer rejects, emit a revision round seeded with the failure reason.

This is the zero-copy rewrite of the strict-era write_obligation_with_review
loop. Granularity is subgoal (not whole obligation). gate_obligation() is a
cheap, stable STRUCTURAL pre-filter (6 sections, Verification Needs == None);
the optional ReviewerAgent then checks the MATH before a sub-lemma is admitted,
restoring the LLM-as-correctness-checker the gate alone cannot provide.
"""
from __future__ import annotations

import json
from typing import Any

from ..agents import BaseAgent, ReviewerAgent
from .failures import FailureLedger
from .ledger import LemmaLedger
from .obligations import (
    SKELETON_HEADINGS,
    gate_obligation,
    get_contract,
)
from .records import FailureRecord, LemmaRecord, PlanRecord

MAX_DRAFT_ATTEMPTS = 2  # initial draft + at most one revision per subgoal-round


# --- LLM role ---------------------------------------------------------------

PROOF_WRITER_ROLE = (
    "You are the Proof Writer for the Section 6 ρ ≤ 1.98 effort. You write rigorous "
    "but minimal proofs, one sub-lemma at a time. Stay strictly within the stated "
    "subgoal — do NOT prove the parent obligation, do NOT reach for the global "
    "theorem, do NOT introduce new constants. Your output must be a complete "
    "six-section manual_certificate; if any verification step is genuinely "
    "deferred, name it explicitly in `## Verification Needs`. If no deferral "
    "remains, write `None` there exactly."
)


def make_writer_agent(*, attached_file_uris=None, literature_packet=None) -> BaseAgent:
    return BaseAgent(
        "Proof Writer",
        PROOF_WRITER_ROLE,
        temperature=0.4,
        attached_file_uris=attached_file_uris,
        literature_packet=literature_packet,
    )


SUBGOAL_REVIEWER_ROLE = (
    "You are the Subgoal Reviewer for the Section 6 ρ ≤ 1.98 effort. You check a "
    "single sub-lemma's proof for mathematical correctness: algebraic/symbolic "
    "validity, whether the derivation actually establishes the stated sub-lemma, "
    "and whether any forbidden shortcut was used. You judge ONLY this local "
    "sub-lemma — never demand that it prove the parent obligation or the global "
    "1.98 bound by itself."
)


def make_reviewer_agent(*, attached_file_uris=None, literature_packet=None) -> ReviewerAgent:
    return ReviewerAgent(
        "Subgoal Reviewer",
        SUBGOAL_REVIEWER_ROLE,
        temperature=0.0,
        attached_file_uris=attached_file_uris,
        literature_packet=literature_packet,
    )


# --- Prompt -----------------------------------------------------------------

def _writer_prompt(
    *, oid: str, plan: PlanRecord, subgoal_index: int, subgoal_text: str,
    ledger_block: str, prior_failure: str = "", prior_persisted_failure: str = "",
) -> str:
    contract = get_contract(oid)
    forbidden = "\n".join(f"- {item}" for item in contract.get("forbidden_shortcuts", []))
    notes = "\n".join(f"- {item}" for item in contract.get("context_notes", []))
    skeleton = "\n".join(SKELETON_HEADINGS)
    failure_block = (
        f"\n\nPrior attempt was rejected by the gate with this reason:\n{prior_failure}\n"
        "Repair only what the gate flagged; do not re-architect the proof."
    ) if prior_failure.strip() else ""
    # Persistent failure memory: an earlier round/session already tried THIS
    # exact sub-lemma and it was rejected. Unlike `prior_failure` (same call's
    # last gate verdict), this objection survived across rounds, so a genuinely
    # different argument is needed — not a local repair.
    persisted_block = (
        f"\n\nPersistent failure memory — an earlier attempt at THIS EXACT "
        f"sub-lemma (possibly in a prior round or session) was rejected:\n"
        f"{prior_persisted_failure}\n"
        "Do not reproduce that argument; take a materially different approach to "
        "this sub-lemma."
    ) if prior_persisted_failure.strip() else ""
    return f"""\
Prove this single sub-lemma. The parent obligation is {oid}, but you are
NOT proving the obligation — only this sub-lemma.

Plan context (for orientation only; you are responsible for THIS subgoal alone):
- approach_name: {plan.approach_name}
- subgoal index: {subgoal_index + 1} of {len(plan.subgoals)}
- full subgoal list: {json.dumps(plan.subgoals, ensure_ascii=False)}

Subgoal to prove:
> {subgoal_text}

Already-proved lemmas you may cite directly (cite by [lemma_id]):
{ledger_block}

Forbidden shortcuts (parent obligation contract):
{forbidden}

Context notes:
{notes}{failure_block}{persisted_block}

Output requirements:
- Return one JSON object with these fields:
  - "id": "{plan.plan_id}_sg{subgoal_index + 1}"
  - "title": short label
  - "claim": exact restatement of the subgoal above
  - "status": "proved" if your derivation closes the subgoal, else "blocked"
  - "summary": 1–2 sentences
  - "manual_certificate": a six-section markdown block with these exact headings,
    in this exact order, each non-empty:
{skeleton}
  - "tool_requests": [] (Phase 1 has no tool runner; output only fully analytic
    proofs).
- The `## Verification Needs` section must contain only the literal word `None`
  (case-insensitive) if the proof closes; otherwise list the exact remaining
  items (in which case `status` must NOT be "proved").
"""


def _review_context(
    *, oid: str, plan: PlanRecord, subgoal_index: int, subgoal_text: str,
    ledger_block: str,
) -> str:
    """Context block handed to ReviewerAgent.check so it scopes its review to
    exactly this local sub-lemma (not the parent obligation / global theorem)."""
    contract = get_contract(oid)
    forbidden = "\n".join(f"- {item}" for item in contract.get("forbidden_shortcuts", []))
    return f"""\
This is a single LOCAL sub-lemma of obligation {oid} ({contract['title']}), NOT the
whole obligation and NOT the global ρ ≤ 1.98 theorem. Judge only whether the proof
below correctly establishes exactly this sub-lemma.

Sub-lemma under review (subgoal {subgoal_index + 1} of {len(plan.subgoals)}, approach {plan.approach_name!r}):
> {subgoal_text}

Forbidden shortcuts — using any of these is a fatal issue:
{forbidden}

Already-proved lemmas the proof MAY cite as given (do not demand their proofs):
{ledger_block}
"""


# --- Per-subgoal driver -----------------------------------------------------

def prove_subgoal(
    *, oid: str, plan: PlanRecord, subgoal_index: int,
    ledger: LemmaLedger,
    failures: FailureLedger | None = None,
    writer: BaseAgent | None = None,
    reviewer: ReviewerAgent | None = None,
    review: bool = True,
    stream: bool | None = None,
) -> tuple[str, dict[str, Any]]:
    """Try to close one subgoal of `plan`.

    Returns (status, evidence) where:
      - status in {"closed", "open"}; "closed" means a LemmaRecord was added
        to the ledger (or already present), "open" means the gate refused.
      - evidence is a dict with diagnostic info: {draft, gate_verdict,
        attempts, ledger_hit, lemma_id}.
    """
    if subgoal_index < 0 or subgoal_index >= len(plan.subgoals):
        raise IndexError(f"subgoal_index {subgoal_index} out of range for plan {plan.plan_id}")
    subgoal_text = plan.subgoals[subgoal_index]

    # Step 1: Ledger short-circuit.
    cached = ledger.find_equivalent(subgoal_text)
    if cached is not None and cached.closed:
        return "closed", {
            "ledger_hit": True,
            "lemma_id": cached.lemma_id,
            "attempts": 0,
            "draft": None,
            "gate_verdict": None,
        }

    # Step 2: Draft (with at most one revision round).
    wr = writer or make_writer_agent()
    rev = reviewer or (make_reviewer_agent() if review else None)
    ledger_block = ledger.render_for_prompt(oid)
    # Persistent failure memory for THIS exact sub-lemma (from a prior round /
    # session). Injected into every draft attempt so the writer avoids the
    # argument that was already rejected before this call began.
    persisted = failures.find_for(subgoal_text) if failures is not None else None
    persisted_reason = persisted.reason if persisted is not None else ""
    last_draft: dict[str, Any] | None = None
    last_verdict = None
    last_failure_reason = ""
    last_review = ""

    for attempt in range(1, MAX_DRAFT_ATTEMPTS + 1):
        raw = wr.call_llm_tagged(
            _writer_prompt(
                oid=oid, plan=plan, subgoal_index=subgoal_index,
                subgoal_text=subgoal_text, ledger_block=ledger_block,
                prior_failure=last_failure_reason,
                prior_persisted_failure=persisted_reason,
            ),
            tag_name="SUBGOAL_PROOF",
            content_hint="object with manual_certificate inside",
            stream=stream,
        )
        try:
            draft = json.loads(raw)
        except json.JSONDecodeError as exc:
            last_failure_reason = f"writer output is not valid JSON: {exc}"
            continue
        last_draft = draft
        verdict = gate_obligation(oid, draft)
        last_verdict = verdict
        if not verdict.closed:
            last_failure_reason = verdict.reason
            continue

        # Structural gate passed (certificate SHAPE is valid). Now the semantic
        # reviewer checks the MATH before the sub-lemma enters the ledger. A
        # REJECT becomes the next attempt's failure reason so the writer repairs
        # the exact flaw the reviewer named (subgoal-level failure absorption).
        if rev is not None:
            try:
                passed, review_text = rev.check(
                    str(draft.get("manual_certificate", "") or ""),
                    _review_context(
                        oid=oid, plan=plan, subgoal_index=subgoal_index,
                        subgoal_text=subgoal_text, ledger_block=ledger_block,
                    ),
                )
            except RuntimeError as exc:
                # Unparseable verdict: fail safe to NOT-verified. A correctness
                # gate must never admit a proof on an inconclusive review.
                passed, review_text = False, f"reviewer verdict unparseable: {exc}"
            last_review = review_text
            if not passed:
                last_failure_reason = f"semantic reviewer rejected: {review_text}"
                continue

        lemma = LemmaRecord.from_statement(
            statement=subgoal_text,
            certificate=str(draft.get("manual_certificate", "") or ""),
            obligation_origin=oid,
            tool_requests=list(draft.get("tool_requests") or []),
            plan_id=plan.plan_id,
            subgoal_index=subgoal_index,
            closed=True,
        )
        ledger.append(lemma)
        return "closed", {
            "ledger_hit": False,
            "lemma_id": lemma.lemma_id,
            "attempts": attempt,
            "draft": draft,
            "gate_verdict": verdict.to_dict(),
            "review": last_review,
        }

    # The subgoal could not be closed after every attempt. Persist the final
    # objection as failure memory so the next round/session does not re-attempt
    # this exact sub-lemma blind (latest objection overwrites any earlier one).
    if failures is not None:
        kind = "reviewer" if last_failure_reason.startswith("semantic reviewer rejected") else "gate"
        failures.record(FailureRecord.from_statement(
            statement=subgoal_text,
            reason=last_failure_reason,
            obligation_id=oid,
            kind=kind,
            approach_name=plan.approach_name,
            plan_id=plan.plan_id,
            round_idx=plan.round_idx,
            attempts=MAX_DRAFT_ATTEMPTS,
        ))

    return "open", {
        "ledger_hit": False,
        "lemma_id": None,
        "attempts": MAX_DRAFT_ATTEMPTS,
        "draft": last_draft,
        "gate_verdict": last_verdict.to_dict() if last_verdict else None,
        "failure_reason": last_failure_reason,
        "review": last_review,
    }


# --- Per-plan driver --------------------------------------------------------

def develop_plan(
    *, oid: str, plan: PlanRecord, ledger: LemmaLedger,
    failures: FailureLedger | None = None,
    writer: BaseAgent | None = None,
    reviewer: ReviewerAgent | None = None,
    review: bool = True,
    stream: bool | None = None,
) -> list[tuple[int, str, str, dict[str, Any]]]:
    """Try to advance every still-open subgoal of `plan` once, in order.

    Subgoals already marked ``"closed"`` in ``plan.subgoal_status`` are skipped
    (their proof is cached in the Ledger). Remaining subgoals go through
    :func:`prove_subgoal` — which itself short-circuits on a Ledger hit — and
    the resulting status is written back into ``plan.subgoal_status``.

    Running plans serially with a shared `ledger` means a subgoal closed by an
    earlier plan this round is immediately citable by later plans
    (intra-round cross-pollination, design decision #9). A single `writer`
    agent is reused across subgoals so its attached files load once.

    Returns ``(subgoal_index, subgoal_text, status, evidence)`` for each subgoal
    *attempted* this call (skipped ones are omitted); the caller uses it to
    measure progress and log. It does NOT mutate ``plan.status`` or
    ``rounds_without_progress`` — that is the Selector's job
    (:func:`selector.update_plan_status`).
    """
    wr = writer or make_writer_agent()
    rev = reviewer or (make_reviewer_agent() if review else None)
    outcomes: list[tuple[int, str, str, dict[str, Any]]] = []
    for idx, subgoal in enumerate(plan.subgoals):
        if plan.subgoal_status.get(subgoal) == "closed":
            continue
        status, evidence = prove_subgoal(
            oid=oid, plan=plan, subgoal_index=idx, ledger=ledger,
            failures=failures, writer=wr, reviewer=rev, review=review, stream=stream,
        )
        plan.subgoal_status[subgoal] = status
        outcomes.append((idx, subgoal, status, evidence))
    return outcomes
