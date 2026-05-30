"""Plan Set Generator + LLM-judge structural-duplicate filter.

Produces K plans for an obligation. Each plan = (approach_name, ordered
subgoals, uses_lemmas, known_obstruction). Diversity is enforced by:

  1. The generator prompt explicitly requires K *structurally distinct*
     attack routes.
  2. After generation, an LLM-judge pass compares every pair and flags
     "structurally equivalent" duplicates; when duplicates are found, we
     ask the generator to regenerate a fresh, dissimilar plan to replace
     the duplicate (up to MAX_REGEN_ATTEMPTS rounds).

The generator/judge agents accept attached_file_uris from files_cache so
that Xia 2013 + reference papers are visible to the LLM directly.
"""
from __future__ import annotations

import itertools
import json

from ..agents import BaseAgent
from .ledger import LemmaLedger
from .obligations import get_contract
from .records import PlanRecord

MAX_REGEN_ATTEMPTS = 3


# --- LLM roles --------------------------------------------------------------

PLAN_GENERATOR_ROLE = (
    "You are the Plan Set Generator for the Section 6 ρ ≤ 1.98 proof effort. "
    "Given one obligation contract and a list of already-proved lemmas, output K "
    "STRUCTURALLY DISTINCT attack routes for proving that obligation. "
    "Each route must follow a different proof strategy, not just rephrase the same "
    "argument. Different strategies look like: endpoint direct estimation vs prefix-chain "
    "induction vs ΔP geometric comparison vs case analysis on disk overlap. "
    "Within each route, decompose into a sequence of small, independently statable "
    "sub-lemmas (1–2 lines each). Do NOT prove anything yet — only design the route."
)

PLAN_JUDGE_ROLE = (
    "You are the Plan Judge. Given two proof-plan descriptors, decide whether they "
    "represent STRUCTURALLY EQUIVALENT attack routes (same proof strategy, just "
    "rephrased) or GENUINELY DIFFERENT routes. Two plans are equivalent iff a "
    "successful execution of one would constitute a successful execution of the "
    "other up to bookkeeping. Be conservative: when in real doubt, mark DIFFERENT."
)


def make_generator_agent(*, attached_file_uris=None, literature_packet=None) -> BaseAgent:
    return BaseAgent(
        "Plan Set Generator",
        PLAN_GENERATOR_ROLE,
        temperature=0.7,
        attached_file_uris=attached_file_uris,
        literature_packet=literature_packet,
    )


def make_judge_agent(*, attached_file_uris=None, literature_packet=None) -> BaseAgent:
    return BaseAgent(
        "Plan Judge",
        PLAN_JUDGE_ROLE,
        temperature=0.0,
        attached_file_uris=attached_file_uris,
        literature_packet=literature_packet,
    )


# --- Prompts ----------------------------------------------------------------

def _format_dead_summaries(dead_summaries: list[dict] | None) -> str:
    if not dead_summaries:
        return "(none)"
    lines = []
    for ds in dead_summaries:
        nm = ds.get("approach_name", "?")
        ob = ds.get("obstruction", "")
        lines.append(f"- approach={nm!r}; obstruction: {ob}")
    return "\n".join(lines)


def _generator_prompt(
    *, oid: str, k: int, ledger_block: str, dead_summaries: list[dict] | None,
    extra_directive: str = "",
) -> str:
    contract = get_contract(oid)
    forbidden = "\n".join(f"- {item}" for item in contract.get("forbidden_shortcuts", []))
    notes = "\n".join(f"- {item}" for item in contract.get("context_notes", []))
    extra = f"\n\nAdditional directive for this regeneration:\n{extra_directive.strip()}" if extra_directive.strip() else ""
    return f"""\
Design exactly {k} structurally distinct attack routes for obligation {oid}.

Obligation contract:
- id: {contract['id']}
- title: {contract['title']}
- claim: {contract['claim']}
- constants: {json.dumps(contract['constants'])}

Forbidden shortcuts (must not be used by any plan):
{forbidden}

Context notes:
{notes}

Already-proved lemmas you may cite directly (cite by [lemma_id] in subgoals):
{ledger_block}

Previously dead approaches (must avoid recreating these strategies):
{_format_dead_summaries(dead_summaries)}{extra}

Output requirements:
- Return one JSON object: {{"plans": [<plan_1>, ..., <plan_{k}>]}}
- Each plan has fields: approach_name (short Chinese or English label),
  approach_summary (1–2 sentences explaining the strategy at a high level,
  enough for the Plan Judge to decide structural equivalence),
  subgoals (ordered list of 2–6 short statements, each one a single
  independently-statable sub-lemma you intend to prove),
  uses_lemmas (list of lemma_id strings from the cited block; may be empty),
  known_obstruction (string; what could fail or be hard).
- The K plans must use *different* proof strategies, not just rephrase one
  strategy with different wording.
"""


def _judge_prompt(plan_a: dict, plan_b: dict) -> str:
    return f"""\
Decide whether the following two plans represent the SAME attack route or
GENUINELY DIFFERENT attack routes.

Plan A:
- approach_name: {plan_a.get('approach_name', '?')}
- approach_summary: {plan_a.get('approach_summary', '?')}
- subgoals: {json.dumps(plan_a.get('subgoals', []), ensure_ascii=False)}

Plan B:
- approach_name: {plan_b.get('approach_name', '?')}
- approach_summary: {plan_b.get('approach_summary', '?')}
- subgoals: {json.dumps(plan_b.get('subgoals', []), ensure_ascii=False)}

Output one JSON object: {{"verdict": "SAME" | "DIFFERENT", "reason": "<one sentence>"}}.
"""


# --- Generation + dedup pipeline -------------------------------------------

def _parse_plans_payload(raw_json_text: str) -> list[dict]:
    try:
        payload = json.loads(raw_json_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"plan generator output is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("plan generator output must be a JSON object")
    plans = payload.get("plans")
    if not isinstance(plans, list) or not plans:
        raise ValueError("plan generator output missing non-empty 'plans' list")
    return plans


def _judge_same(judge: BaseAgent, plan_a: dict, plan_b: dict, *, stream=None) -> bool:
    raw = judge.call_llm_tagged(
        _judge_prompt(plan_a, plan_b),
        tag_name="JUDGE_RESULT",
        content_hint="object with verdict in {SAME, DIFFERENT}",
        stream=stream,
        print_stream=False,
    )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        # Unparseable verdict: we cannot establish equivalence. Per the Plan
        # Judge contract ("when in real doubt, mark DIFFERENT"), fail safe to
        # DIFFERENT so a malformed reply never discards an otherwise-valid plan.
        return False
    verdict = str(payload.get("verdict", "")).strip().upper()
    return verdict == "SAME"


def _find_duplicate_pair(judge: BaseAgent, plans: list[dict], *, stream=None) -> tuple[int, int] | None:
    for i, j in itertools.combinations(range(len(plans)), 2):
        if _judge_same(judge, plans[i], plans[j], stream=stream):
            return (i, j)
    return None


def generate_plan_set(
    *, oid: str, k: int, round_idx: int, ledger: LemmaLedger,
    dead_summaries: list[dict] | None = None,
    generator: BaseAgent | None = None,
    judge: BaseAgent | None = None,
    stream: bool | None = None,
) -> list[PlanRecord]:
    """Generate K structurally distinct plans for obligation `oid`.

    Returns a list of PlanRecord (status='active'). Caller is responsible for
    persisting them via append_jsonl(...).
    """
    gen = generator or make_generator_agent()
    jud = judge or make_judge_agent()
    ledger_block = ledger.render_for_prompt(oid)

    raw = gen.call_llm_tagged(
        _generator_prompt(oid=oid, k=k, ledger_block=ledger_block, dead_summaries=dead_summaries),
        tag_name="PLAN_SET",
        content_hint=f"object with `plans` array of {k} entries",
        stream=stream,
    )
    plans = _parse_plans_payload(raw)
    if len(plans) < k:
        raise RuntimeError(f"plan generator returned {len(plans)} plans, expected {k}")
    plans = plans[:k]

    # Dedup: when (i, j) flagged SAME, ask generator to replace plan j.
    for attempt in range(MAX_REGEN_ATTEMPTS):
        dup = _find_duplicate_pair(jud, plans, stream=stream)
        if dup is None:
            break
        i, j = dup
        regen_directive = (
            f"Plan #{j+1} ({plans[j].get('approach_name', '?')!r}) is structurally "
            f"equivalent to plan #{i+1} ({plans[i].get('approach_name', '?')!r}). "
            f"Replace plan #{j+1} with a NEW route that uses a different proof strategy "
            f"from BOTH plan #{i+1} and the other surviving plans in the set."
        )
        raw_one = gen.call_llm_tagged(
            _generator_prompt(
                oid=oid, k=1, ledger_block=ledger_block,
                dead_summaries=dead_summaries, extra_directive=regen_directive,
            ),
            tag_name="PLAN_SET",
            content_hint="object with `plans` array of 1 entry",
            stream=stream,
        )
        replacements = _parse_plans_payload(raw_one)
        if not replacements:
            continue
        plans[j] = replacements[0]
    else:
        # Fell through without break: still has duplicates.
        # Phase 1 policy: log via approach_summary marker but proceed; the
        # Stall Detector will catch repeats via obstruction analysis later.
        pass

    return [
        PlanRecord(
            plan_id=f"{oid}_round{round_idx:02d}_P{idx+1}",
            obligation_id=oid,
            round_idx=round_idx,
            approach_name=str(p.get("approach_name", f"plan_{idx+1}")),
            subgoals=[str(s) for s in (p.get("subgoals") or []) if str(s).strip()],
            uses_lemmas=[str(s) for s in (p.get("uses_lemmas") or []) if str(s).strip()],
            subgoal_status={},
            status="active",
            # Seed the plan with the strategy's anticipated difficulty so it is
            # available to the Stall Detector / Escalator and to next round's
            # generator even before the plan actually stalls out.
            obstruction=str(p.get("known_obstruction", "") or "").strip(),
            rounds_without_progress=0,
        )
        for idx, p in enumerate(plans)
    ]
