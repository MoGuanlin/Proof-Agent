"""Plan Selector + Stall Detector + Escalation.

After a development round finishes (Plan Developer has tried each open
subgoal of every active plan once), this module decides:

  - Which plans are now COMPLETE (all subgoals closed) — the first such
    plan becomes the obligation's manual_certificate.
  - Which plans are now DEAD (rounds_without_progress >= N) — their
    obstruction text is recorded for the next Plan Set Generator round.
  - When ALL plans for an obligation are dead — emit an Escalation event:
    a contract-modification proposal asking the user to revise the
    obligation contract or supply external lemmas.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..agents import BaseAgent
from ..paths import ARTIFACTS_DIR
from .failures import FailureLedger
from .ledger import LemmaLedger
from .obligations import gate_obligation, get_contract
from .records import (
    ESCALATIONS_JSONL,
    PlanRecord,
    _now_iso,
    append_jsonl,
)

DEFAULT_STALL_THRESHOLD = 2  # N: rounds without progress before marking dead


# --- Selector / Merger ------------------------------------------------------

def update_plan_status(
    plan: PlanRecord, ledger: LemmaLedger, *, stall_threshold: int,
    failures: FailureLedger | None = None,
) -> PlanRecord:
    """Refresh a plan's subgoal_status against the current ledger and recompute
    the plan's overall status (active/complete/dead).

    When `failures` is supplied, a plan that dies folds its open subgoals'
    persisted objections into its obstruction, so the next Plan Set Generator
    sees not just *that* the route stalled but the concrete math reasons.

    Mutates and returns the same PlanRecord for caller convenience.
    """
    closed_count = 0
    for sg in plan.subgoals:
        cached = ledger.find_equivalent(sg)
        if cached is not None and cached.closed:
            plan.subgoal_status[sg] = "closed"
            closed_count += 1
        else:
            plan.subgoal_status.setdefault(sg, "open")
            if plan.subgoal_status[sg] == "closed":
                # was previously marked closed but ledger doesn't show it:
                # treat as open (defensive).
                plan.subgoal_status[sg] = "open"

    if closed_count == len(plan.subgoals) and plan.subgoals:
        plan.status = "complete"
    elif plan.rounds_without_progress >= stall_threshold and plan.status != "complete":
        plan.status = "dead"
        _record_stall_obstruction(plan, failures)
    plan.updated_at = _now_iso()
    return plan


def _record_stall_obstruction(plan: PlanRecord, failures: FailureLedger | None = None) -> None:
    """Stamp a concrete obstruction onto a plan that just died from stalling.

    Preserves any obstruction the generator already anticipated (its
    `known_obstruction`) and appends what actually blocked the plan — the
    number of rounds without progress and which subgoals never closed. This is
    the text the next Plan Set Generator reads to avoid recreating the route
    (v2 design §5 signal 3), so it must be non-empty and specific.

    When `failures` is supplied, the concrete gate/reviewer objection persisted
    for each still-open subgoal is folded in too, upgrading the route feedback
    from "this stalled" to "this stalled because <math reason>".
    """
    open_subgoals = [sg for sg in plan.subgoals if plan.subgoal_status.get(sg) != "closed"]
    stall_note = (
        f"stalled after {plan.rounds_without_progress} round(s) without closing a new "
        f"subgoal; {len(open_subgoals)} of {len(plan.subgoals)} subgoal(s) still open: "
        f"{open_subgoals}"
    )
    if failures is not None:
        objections = []
        for sg in open_subgoals:
            rec = failures.find_for(sg)
            if rec is None:
                continue
            reason = rec.reason if len(rec.reason) <= 240 else rec.reason[:237] + "..."
            objections.append(f"[{rec.kind}] {reason}")
        if objections:
            stall_note += " | recorded objections: " + " ;; ".join(objections)
    prior = plan.obstruction.strip()
    plan.obstruction = f"{prior} | {stall_note}" if prior else stall_note


def assemble_obligation_certificate(plan: PlanRecord, ledger: LemmaLedger, oid: str) -> dict[str, Any] | None:
    """If `plan` is complete, stitch its subgoal certificates into one
    obligation-level manual_certificate and return the obligation draft dict.

    Returns None if the assembled certificate doesn't pass the gate
    (defensive: this should not happen if every subgoal was closed).
    """
    if plan.status != "complete":
        return None

    contract = get_contract(oid)
    parts: list[str] = []
    parts.append(f"## Assumptions")
    parts.append(
        f"Composed from {len(plan.subgoals)} sub-lemmas closed under approach "
        f"{plan.approach_name!r} (plan_id={plan.plan_id})."
    )
    parts.append("")
    parts.append("## Claim")
    parts.append(contract["claim"])
    parts.append("")
    parts.append("## Derivation")
    for idx, sg in enumerate(plan.subgoals, start=1):
        cached = ledger.find_equivalent(sg)
        if cached is None:
            return None
        parts.append(f"### Sub-lemma {idx} [{cached.lemma_id}] (origin={cached.obligation_origin})")
        parts.append(f"Statement: {cached.statement}")
        parts.append("")
        parts.append(cached.certificate.rstrip())
        parts.append("")
    parts.append("## Boundary Cases")
    parts.append("Boundary cases are handled within each sub-lemma's certificate.")
    parts.append("")
    parts.append("## Verification Needs")
    parts.append("None")
    parts.append("")
    parts.append("## Conclusion")
    parts.append(
        f"Combining the sub-lemmas above closes obligation {oid} via the "
        f"{plan.approach_name!r} route."
    )
    manual_certificate = "\n".join(parts)

    draft = {
        "id": oid,
        "title": contract["title"],
        "claim": contract["claim"],
        "status": "proved",
        "summary": f"Closed by plan {plan.plan_id} via {plan.approach_name!r}.",
        "manual_certificate": manual_certificate,
        "tool_requests": [],
    }
    verdict = gate_obligation(oid, draft)
    if not verdict.closed:
        # Defensive: report failure for diagnosis but do not silently accept.
        draft["_assembly_gate_failure"] = verdict.to_dict()
        return None
    return draft


# --- Escalation -------------------------------------------------------------

ESCALATOR_ROLE = (
    "You are the Contract Escalation Agent. ALL plans for one Section 6 obligation "
    "have been marked dead. Read the dead plans' approach_name and obstruction "
    "fields, the obligation contract, and the proved-lemma ledger snapshot. Then "
    "propose either (a) a concrete revision to the obligation contract that would "
    "make it provable in the current relaxed framework, or (b) a specific external "
    "lemma the human should supply. Be concrete; do NOT propose 'try harder'."
)


def make_escalator_agent(*, attached_file_uris=None, literature_packet=None) -> BaseAgent:
    return BaseAgent(
        "Contract Escalation Agent",
        ESCALATOR_ROLE,
        temperature=0.3,
        attached_file_uris=attached_file_uris,
        literature_packet=literature_packet,
    )


def write_escalation(
    *, oid: str, dead_plans: list[PlanRecord], ledger: LemmaLedger,
    escalator: BaseAgent | None = None,
    stream: bool | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    """All plans for `oid` have died. Ask the escalator LLM for a contract
    revision proposal, persist it to escalations.jsonl, return the record.
    """
    contract = get_contract(oid)
    dead_block = "\n".join(
        f"- plan_id={p.plan_id}; approach={p.approach_name!r}; obstruction: {p.obstruction or '(none recorded)'}"
        for p in dead_plans
    )
    prompt = f"""\
All {len(dead_plans)} plans for obligation {oid} are dead.

Obligation contract:
{json.dumps(contract, ensure_ascii=False, indent=2)}

Dead plans:
{dead_block}

Proved lemmas in ledger (visible to {oid}):
{ledger.render_for_prompt(oid)}

Output one JSON object with fields:
- "diagnosis": 2–3 sentences naming the deepest obstruction shared across the dead plans.
- "contract_revision_proposal": a concrete suggested change to the obligation
  contract (claim text, forbidden shortcuts, or context notes), or null if
  none applies.
- "external_lemma_request": either null, or an object with fields {{"statement": str, "rationale": str}}
  describing one lemma the human should supply.
- "next_step": "revise_contract" | "supply_lemma" | "switch_potential_function" | "halt".
"""
    esc = escalator or make_escalator_agent()
    raw = esc.call_llm_tagged(
        prompt,
        tag_name="ESCALATION",
        content_hint="object with diagnosis and next_step",
        stream=stream,
    )
    try:
        proposal = json.loads(raw)
    except json.JSONDecodeError:
        proposal = {"diagnosis": "(escalator output not valid JSON)", "raw": raw}

    record = {
        "obligation_id": oid,
        "created_at": _now_iso(),
        "dead_plan_ids": [p.plan_id for p in dead_plans],
        "proposal": proposal,
    }
    target = Path(path) if path else (ARTIFACTS_DIR / ESCALATIONS_JSONL)
    append_jsonl(target, record)
    return record
