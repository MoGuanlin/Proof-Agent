"""proof-agent-relaxed CLI orchestrator.

Usage:
  proof-agent-relaxed --do S2 --rounds 3 --k 3 --n 2
  proof-agent-relaxed --do S2 --resume
  proof-agent-relaxed --upload-files
  proof-agent-relaxed --self-test
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..app_config import LLM_PROVIDER, _active_api_key
from ..logging_setup import configure_logging
from ..paths import (
    ARTIFACTS_DIR,
    PRIMARY_PAPER_PATH,
    REFERENCE_PAPERS_DIR,
    ensure_directory,
)
from .developer import develop_plan, make_reviewer_agent, make_writer_agent, prove_subgoal
from .failures import FailureLedger
from .files_cache import get_active_file_uris, upload_files
from .generator import generate_plan_set, make_generator_agent, make_judge_agent
from .ledger import LemmaLedger
from .literature import retrieve_literature_packet
from .obligations import OBLIGATION_CONTRACTS
from .records import (
    PLANS_JSONL,
    PlanRecord,
    append_jsonl,
    load_plans,
)
from .selector import (
    DEFAULT_STALL_THRESHOLD,
    assemble_obligation_certificate,
    make_escalator_agent,
    update_plan_status,
    write_escalation,
)


# --- Helpers ---------------------------------------------------------------

def _collect_reference_pdfs() -> list[Path]:
    paths = [PRIMARY_PAPER_PATH] if PRIMARY_PAPER_PATH.exists() else []
    if REFERENCE_PAPERS_DIR.exists():
        paths.extend(sorted(REFERENCE_PAPERS_DIR.glob("*.pdf")))
    return paths


def _load_attached_uris() -> list[tuple[str, str]]:
    """Read .cache/llm_files_index.json for active file URIs."""
    return get_active_file_uris()


def _summarize_dead_plans(plans: list[PlanRecord]) -> list[dict]:
    return [
        {"approach_name": p.approach_name, "obstruction": p.obstruction}
        for p in plans
        if p.status == "dead"
    ]


def _persist_plan(path: Path, plan: PlanRecord) -> None:
    append_jsonl(path, plan)


# --- Self-test (no network) ------------------------------------------------

def run_self_test() -> int:
    """Verify the scheduling chain end-to-end with a mocked LLM.

    Mocks BaseAgent.call_llm_tagged to return canned subgoal proofs that pass
    the gate, so the full pipeline (Generator -> Developer -> Selector ->
    assembly) can be exercised without API access.
    """
    from unittest import mock

    from ..agents import BaseAgent

    canned_plan_set = json.dumps({
        "plans": [
            {
                "approach_name": "endpoint-direct",
                "approach_summary": "Bound the three terms |P|, lambda|D|, eta L_n at the two endpoints separately.",
                "subgoals": ["sg-A: bound |P| at endpoint x.", "sg-B: combine to get Upsilon < 0."],
                "uses_lemmas": [],
                "known_obstruction": "tightness of |P| upper bound",
            },
            {
                "approach_name": "prefix-induction",
                "approach_summary": "Reduce to the n-1 case via the prefix chain identity.",
                "subgoals": ["sg-C: state the chain identity.", "sg-D: apply induction hypothesis."],
                "uses_lemmas": [],
                "known_obstruction": "induction base for n=2",
            },
            {
                "approach_name": "deltaP-comparison",
                "approach_summary": "Compare DeltaP geometrically across the two endpoint candidates.",
                "subgoals": ["sg-E: compute DeltaP.", "sg-F: sign-control via DeltaL."],
                "uses_lemmas": [],
                "known_obstruction": "case split on chord direction",
            },
        ]
    })

    canned_judge_diff = json.dumps({"verdict": "DIFFERENT", "reason": "different proof strategies"})

    canned_subgoal = json.dumps({
        "id": "stub", "title": "stub", "claim": "stub",
        "status": "proved", "summary": "stub",
        "manual_certificate": (
            "## Assumptions\nlambda=643/250, eta=1, n>=2.\n\n"
            "## Claim\nThe stated subgoal holds.\n\n"
            "## Derivation\n"
            "We expand the three terms |P|, lambda|D|, eta L_n at the relevant endpoint and combine. "
            "Each step uses the chord-vs-arc bound and the segment potential definition. "
            "We do not invoke any forbidden shortcut and do not appeal to v-independent monotonicity.\n\n"
            "## Boundary Cases\nThe n=2 base case is handled inline.\n\n"
            "## Verification Needs\nNone\n\n"
            "## Conclusion\nHence the subgoal is established.\n"
        ),
        "tool_requests": [],
    })

    sequence = iter([
        canned_plan_set,                                            # generator
        canned_judge_diff, canned_judge_diff, canned_judge_diff,    # 3 pairwise judge calls
        canned_subgoal, canned_subgoal,                             # plan 1: 2 subgoals
        canned_subgoal, canned_subgoal,                             # plan 2: 2 subgoals
        canned_subgoal, canned_subgoal,                             # plan 3: 2 subgoals
    ])

    def mock_call(self, prompt, tag_name="FINAL_OUTPUT", content_hint="",
                  stream=None, print_stream=True, max_format_attempts=3):
        return next(sequence)

    with mock.patch.object(BaseAgent, "call_llm_tagged", mock_call):
        ledger_path = ARTIFACTS_DIR / "section6_198_lemma_ledger.selftest.jsonl"
        plans_path = ARTIFACTS_DIR / "section6_198_plans.selftest.jsonl"
        for p in (ledger_path, plans_path):
            if p.exists():
                p.unlink()
        ledger = LemmaLedger(path=ledger_path)
        plans = generate_plan_set(oid="S2", k=3, round_idx=1, ledger=ledger)
        assert len(plans) == 3, f"expected 3 plans, got {len(plans)}"
        for plan in plans:
            for idx in range(len(plan.subgoals)):
                status, _evidence = prove_subgoal(
                    oid="S2", plan=plan, subgoal_index=idx, ledger=ledger,
                    review=False,  # self-test exercises the scheduling chain, not the reviewer
                )
                assert status == "closed", f"subgoal {idx} of {plan.plan_id} did not close"
            update_plan_status(plan, ledger, stall_threshold=2)
            _persist_plan(plans_path, plan)
        complete = [p for p in plans if p.status == "complete"]
        assert complete, "no plan reached complete status"
        certificate = assemble_obligation_certificate(complete[0], ledger, "S2")
        assert certificate is not None, "obligation assembly failed"
        for path in (ledger_path, plans_path):
            if path.exists():
                path.unlink()
    print("[self-test] OK: K=3 generator + dedup + per-subgoal proof + assembly chain works")
    return 0


# --- Run loop --------------------------------------------------------------

def run_obligation_loop(
    *, oid: str, rounds: int, k: int, n: int, stream: bool | None,
    resume: bool, review: bool = True,
) -> int:
    if oid not in OBLIGATION_CONTRACTS:
        print(f"error: obligation {oid!r} not in scope (Phase 1 supports: "
              f"{sorted(OBLIGATION_CONTRACTS)}).", file=sys.stderr)
        return 2

    ensure_directory(ARTIFACTS_DIR)
    plans_path = ARTIFACTS_DIR / PLANS_JSONL
    ledger = LemmaLedger()  # default path artifacts/section6_198_lemma_ledger.jsonl
    failures = FailureLedger()  # persistent failure memory (cross-round + cross-session)

    # Literature source depends on provider: google uses the Gemini Files API
    # (attached_file_uris); other providers (e.g. uuapi -> gpt5.5) inject the
    # local Qdrant+Voyage RAG packet as a prompt prefix. Gemini URIs are gated
    # on provider so a populated llm_files_index.json never reaches a non-google
    # call (which would raise NotImplementedError in BaseAgent).
    literature_packet = None
    if LLM_PROVIDER == "google":
        attached = _load_attached_uris()
        if attached:
            print(f"[info] attached {len(attached)} file(s) to Gemini context")
        else:
            print("[warn] no Gemini files attached; LLM will only see prompt context. "
                  "Run `proof-agent-relaxed --upload-files` first to attach Xia 2013 + references.")
    else:
        attached = []
        literature_packet = retrieve_literature_packet(oid)
        if literature_packet:
            print(f"[info] local RAG literature packet: {len(literature_packet)} chars "
                  f"injected into {LLM_PROVIDER} prompts")
        else:
            print("[warn] local RAG packet empty; LLM will only see prompt context "
                  "(contract + ledger).")
    print(f"[info] ledger: {len(ledger)} lemmas already in {ledger.path}")
    print(f"[info] failure memory: {len(failures)} record(s) in {failures.path}")

    file_uris = attached
    generator = make_generator_agent(attached_file_uris=file_uris, literature_packet=literature_packet)
    judge = make_judge_agent(attached_file_uris=file_uris, literature_packet=literature_packet)
    writer = make_writer_agent(attached_file_uris=file_uris, literature_packet=literature_packet)
    reviewer = (make_reviewer_agent(attached_file_uris=file_uris, literature_packet=literature_packet)
                if review else None)
    escalator = make_escalator_agent(attached_file_uris=file_uris, literature_packet=literature_packet)
    print(f"[info] semantic reviewer: {'ON (gate + reviewer must both pass)' if review else 'OFF (structural gate only)'}")

    # --- Plan Board: plans persist ACROSS rounds (design §2.1 "Plan Board"). ---
    # Active plans carry rounds_without_progress forward, so the Stall Detector
    # can actually trip after N stagnant rounds (design §2.2[4] / §5 signal 3).
    # Resume rebuilds the board from persisted state — load_plans already keeps
    # the latest record per plan_id.
    board: list[PlanRecord] = []
    start_round = 1
    if resume:
        board = [p for p in load_plans(plans_path) if p.obligation_id == oid]
        if board:
            start_round = max(p.round_idx for p in board) + 1
            print(f"[resume] loaded {len(board)} plan(s); continuing from round {start_round}")

    for round_idx in range(start_round, start_round + rounds):
        print(f"\n=== Round {round_idx} for obligation {oid} ===")

        # Refill vacated slots so the board holds up to k ACTIVE plans. Round 1
        # generates all k; later rounds top up only the slots freed by dead /
        # complete plans, seeding the generator with dead obstructions so it
        # avoids recreating routes that already failed (design §5 signal 3).
        active = [p for p in board if p.status == "active"]
        need = k - len(active)
        if need > 0:
            new_plans = generate_plan_set(
                oid=oid, k=need, round_idx=round_idx, ledger=ledger,
                dead_summaries=_summarize_dead_plans(board) or None,
                generator=generator, judge=judge, stream=stream,
            )
            for plan in new_plans:
                _persist_plan(plans_path, plan)
            board.extend(new_plans)
            active = [p for p in board if p.status == "active"]

        # Develop each active plan once this round. A shared ledger means a
        # subgoal closed by an earlier plan is immediately citable by later
        # plans (intra-round cross-pollination, design decision #9).
        for plan in active:
            print(f"[plan] {plan.plan_id}: {plan.approach_name} "
                  f"({len(plan.subgoals)} subgoals)")
            outcomes = develop_plan(
                oid=oid, plan=plan, ledger=ledger, failures=failures, writer=writer,
                reviewer=reviewer, review=review, stream=stream,
            )
            closed_this_round = 0
            for idx, sg, status, evidence in outcomes:
                if status == "closed":
                    closed_this_round += 1
                    print(f"  [closed] subgoal {idx+1}: {sg!r} -> "
                          f"{evidence.get('lemma_id')}")
                else:
                    print(f"  [open]   subgoal {idx+1}: {sg!r} -> "
                          f"{evidence.get('failure_reason', '?')}")
            if closed_this_round == 0:
                plan.rounds_without_progress += 1
            else:
                plan.rounds_without_progress = 0
            update_plan_status(plan, ledger, stall_threshold=n, failures=failures)
            _persist_plan(plans_path, plan)

            if plan.status == "complete":
                draft = assemble_obligation_certificate(plan, ledger, oid)
                if draft is not None:
                    out_path = ARTIFACTS_DIR / f"section6_198_{oid}_obligation_certificate.json"
                    out_path.write_text(json.dumps(draft, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
                    print(f"\n[SUCCESS] obligation {oid} closed by plan {plan.plan_id}; "
                          f"certificate written to {out_path}")
                    return 0

        # End-of-round bookkeeping over the WHOLE board (not just this round's
        # plans): a plan that died two rounds ago still counts as dead here.
        active = [p for p in board if p.status == "active"]
        dead = [p for p in board if p.status == "dead"]
        complete = [p for p in board if p.status == "complete"]
        print(f"[round {round_idx}] board: active={len(active)} dead={len(dead)} "
              f"complete={len(complete)}; ledger now has {len(ledger)} lemmas")

        if not active:
            print(f"[escalation] all plans for {oid} are dead; emitting contract revision proposal")
            record = write_escalation(oid=oid, dead_plans=dead, ledger=ledger,
                                      escalator=escalator, stream=stream)
            print(json.dumps(record["proposal"], ensure_ascii=False, indent=2))
            return 3

    print(f"\n[exhausted] {rounds} rounds elapsed without closing {oid}")
    return 4


# --- Argparse driver -------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="proof-agent-relaxed",
                                     description="Relaxed agent for Section 6 ρ ≤ 1.98 (Phase 1: S2).")
    parser.add_argument("--do", dest="oid", default="S2",
                        help="obligation id to attempt (default: S2; Phase 1 supports S2 only)")
    parser.add_argument("--rounds", type=int, default=3, help="max generator rounds (default: 3)")
    parser.add_argument("--k", type=int, default=3, help="plans per round (default: 3)")
    parser.add_argument("--n", type=int, default=DEFAULT_STALL_THRESHOLD,
                        help=f"rounds without progress before plan dies (default: {DEFAULT_STALL_THRESHOLD})")
    parser.add_argument("--resume", action="store_true",
                        help="continue from latest persisted state in artifacts/")
    parser.add_argument("--no-stream", action="store_true",
                        help="disable LLM streaming (default: use config PREFER_STREAMING)")
    parser.add_argument("--no-review", action="store_true",
                        help="skip the semantic reviewer; close sub-lemmas on the structural gate alone "
                             "(faster/cheaper, but no math correctness check)")
    parser.add_argument("--upload-files", action="store_true",
                        help="upload Xia 2013 + references to Gemini Files API and exit")
    parser.add_argument("--force-reupload", action="store_true",
                        help="with --upload-files, reupload even if cached entries are fresh")
    parser.add_argument("--self-test", action="store_true",
                        help="run scheduling chain with mocked LLM (no API key needed)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.self_test:
        return run_self_test()

    if args.upload_files:
        if LLM_PROVIDER != "google":
            print(f"error: --upload-files requires LLM_PROVIDER=google "
                  f"(current: {LLM_PROVIDER!r})", file=sys.stderr)
            return 2
        if not _active_api_key():
            print("error: GEMINI_API_KEY/GOOGLE_API_KEY not set", file=sys.stderr)
            return 2
        pdfs = _collect_reference_pdfs()
        if not pdfs:
            print(f"error: no PDFs found under "
                  f"{PRIMARY_PAPER_PATH.parent} or {REFERENCE_PAPERS_DIR}", file=sys.stderr)
            return 2
        upload_files(pdfs, force=args.force_reupload, verbose=True)
        return 0

    if not _active_api_key():
        print(f"error: no API key for LLM_PROVIDER={LLM_PROVIDER!r}", file=sys.stderr)
        return 2

    log_path = configure_logging(tag=f"relaxed_{args.oid}")
    print(f"[log] {log_path}")
    stream = None if not args.no_stream else False
    return run_obligation_loop(
        oid=args.oid.strip().upper(), rounds=args.rounds, k=args.k, n=args.n,
        stream=stream, resume=args.resume, review=not args.no_review,
    )


if __name__ == "__main__":
    raise SystemExit(main())
