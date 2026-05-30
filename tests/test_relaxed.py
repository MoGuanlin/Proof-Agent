"""Regression tests for the relaxed agent (proof_agent.relaxed.*)."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from proof_agent.agents import BaseAgent, ReviewerAgent
from proof_agent.relaxed import cli
from proof_agent.relaxed.cli import run_self_test
from proof_agent.relaxed.developer import _writer_prompt, prove_subgoal
from proof_agent.relaxed.failures import FailureLedger
from proof_agent.relaxed.generator import (
    _find_duplicate_pair,
    _judge_same,
    generate_plan_set,
    make_judge_agent,
)
from proof_agent.relaxed.ledger import LemmaLedger
from proof_agent.relaxed.obligations import (
    OBLIGATION_CONTRACTS,
    gate_obligation,
    get_contract,
    manual_certificate_issue,
    verification_needs_closed,
)
from proof_agent.relaxed.records import (
    FailureRecord,
    LemmaRecord,
    PlanRecord,
    fingerprint_statement,
    load_failures,
    normalize_statement,
)
from proof_agent.relaxed.selector import (
    assemble_obligation_certificate,
    update_plan_status,
)


CLOSED_CERTIFICATE = (
    "## Assumptions\nlambda=643/250, eta=1, n>=2.\n\n"
    "## Claim\nThe sub-lemma holds.\n\n"
    "## Derivation\n"
    "We expand the three terms |P|, lambda|D|, eta L_n at the relevant endpoint and combine. "
    "Each step uses the chord-vs-arc bound and the segment potential definition. "
    "We do not invoke any forbidden shortcut and do not appeal to v-independent monotonicity.\n\n"
    "## Boundary Cases\nThe n=2 base case is handled inline.\n\n"
    "## Verification Needs\nNone\n\n"
    "## Conclusion\nHence the sub-lemma is established.\n"
)


class NormalizeFingerprintTests(unittest.TestCase):
    def test_glyph_variants_collapse(self):
        a = normalize_statement("Upsilon_O(u, x) ≤ 0.")
        b = normalize_statement("upsilon_O(u, x) <= 0!")
        self.assertEqual(a, b)
        self.assertEqual(fingerprint_statement("Υ_O(u, x) <= 0"), fingerprint_statement("Upsilon_O(u, x) <= 0."))

    def test_distinct_variables_stay_distinct(self):
        # Conservative: NOT alpha-renamed.
        self.assertNotEqual(
            normalize_statement("Upsilon_O(u, x) < 0."),
            normalize_statement("Upsilon_O(u, y) < 0."),
        )


class GateTests(unittest.TestCase):
    def test_empty_draft_is_rejected(self):
        v = gate_obligation("S2", None)
        self.assertFalse(v.closed)
        self.assertIn("missing", v.reason)

    def test_open_verification_needs_blocks_analytic(self):
        cert = CLOSED_CERTIFICATE.replace("## Verification Needs\nNone", "## Verification Needs\npending interval check")
        v = gate_obligation("S2", {"status": "proved", "manual_certificate": cert})
        self.assertFalse(v.closed)
        self.assertIn("Verification Needs", v.reason)

    def test_pending_tool_requests_block_phase1(self):
        v = gate_obligation("S2", {
            "status": "proved",
            "manual_certificate": CLOSED_CERTIFICATE,
            "tool_requests": [{"request_id": "r1"}],
        })
        self.assertFalse(v.closed)
        self.assertIn("tool_requests", v.reason)

    def test_closed_certificate_passes(self):
        v = gate_obligation("S2", {
            "status": "proved", "manual_certificate": CLOSED_CERTIFICATE,
        })
        self.assertTrue(v.closed)
        self.assertTrue(v.needs_closed)

    def test_unknown_obligation_rejected(self):
        with self.assertRaises(KeyError):
            get_contract("S5")
        self.assertEqual(set(OBLIGATION_CONTRACTS), {"S2"})


class LedgerTests(unittest.TestCase):
    def _new_ledger(self) -> tuple[LemmaLedger, Path, tempfile.TemporaryDirectory]:
        td = tempfile.TemporaryDirectory()
        path = Path(td.name) / "lemmas.jsonl"
        return LemmaLedger(path=path), path, td

    def test_dedup_and_scope(self):
        led, _path, td = self._new_ledger()
        try:
            rec_all = LemmaRecord.from_statement(
                statement="|P_O(u, x)| <= (pi/2) * r_{n-1}.",
                certificate=CLOSED_CERTIFICATE, obligation_origin="S2",
            )
            self.assertTrue(led.append(rec_all))
            self.assertFalse(led.append(rec_all), "duplicate fingerprint must be rejected")
            rec_s2 = LemmaRecord.from_statement(
                statement="Endpoint identity used only by S2.",
                certificate=CLOSED_CERTIFICATE, obligation_origin="S2",
                reusable_scope=["S2"],
            )
            led.append(rec_s2)
            self.assertEqual(
                {r.lemma_id for r in led.visible_to("S2")},
                {rec_all.lemma_id, rec_s2.lemma_id},
            )
            self.assertEqual({r.lemma_id for r in led.visible_to("S3")}, {rec_all.lemma_id})
        finally:
            td.cleanup()

    def test_non_closed_rejected(self):
        led, _path, td = self._new_ledger()
        try:
            bad = LemmaRecord.from_statement(
                statement="Open lemma.", certificate="",
                obligation_origin="S2", closed=False,
            )
            with self.assertRaises(ValueError):
                led.append(bad)
        finally:
            td.cleanup()


class SchedulingChainSelfTest(unittest.TestCase):
    """End-to-end mock verifying the v2-design 3 success signals (cli.run_self_test)."""

    def test_self_test_returns_zero(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = run_self_test()
        self.assertEqual(rc, 0)
        self.assertIn("[self-test] OK", buf.getvalue())


class JudgeAndDevelopMocked(unittest.TestCase):
    def test_judge_same_parses_verdict(self):
        with mock.patch.object(BaseAgent, "call_llm_tagged",
                               lambda self, *a, **kw: json.dumps({"verdict": "SAME"})):
            self.assertTrue(_judge_same(make_judge_agent(), {"approach_name": "a"}, {"approach_name": "b"}))
        with mock.patch.object(BaseAgent, "call_llm_tagged",
                               lambda self, *a, **kw: json.dumps({"verdict": "DIFFERENT"})):
            self.assertFalse(_judge_same(make_judge_agent(), {"approach_name": "a"}, {"approach_name": "b"}))

    def test_prove_subgoal_ledger_short_circuit(self):
        td = tempfile.TemporaryDirectory()
        try:
            led = LemmaLedger(path=Path(td.name) / "lemmas.jsonl")
            sg = "Some sub-lemma statement."
            led.append(LemmaRecord.from_statement(
                statement=sg, certificate=CLOSED_CERTIFICATE,
                obligation_origin="S2", closed=True,
            ))
            plan = PlanRecord(
                plan_id="S2_round01_P1", obligation_id="S2", round_idx=1,
                approach_name="x", subgoals=[sg],
            )
            with mock.patch.object(BaseAgent, "call_llm_tagged",
                                   side_effect=AssertionError("ledger should short-circuit; LLM must not be called")):
                status, evidence = prove_subgoal(oid="S2", plan=plan, subgoal_index=0, ledger=led)
            self.assertEqual(status, "closed")
            self.assertTrue(evidence["ledger_hit"])
        finally:
            td.cleanup()

    def test_prove_subgoal_open_after_failed_attempts(self):
        bad_cert_payload = json.dumps({
            "id": "x", "title": "x", "claim": "x",
            "status": "proved", "summary": "x",
            "manual_certificate": "## Assumptions\n## Claim\n## Verification Needs\npending\n",
            "tool_requests": [],
        })
        td = tempfile.TemporaryDirectory()
        try:
            led = LemmaLedger(path=Path(td.name) / "lemmas.jsonl")
            plan = PlanRecord(
                plan_id="S2_round01_P1", obligation_id="S2", round_idx=1,
                approach_name="x", subgoals=["new statement that won't be in ledger"],
            )
            with mock.patch.object(BaseAgent, "call_llm_tagged",
                                   lambda self, *a, **kw: bad_cert_payload):
                status, evidence = prove_subgoal(oid="S2", plan=plan, subgoal_index=0, ledger=led)
            self.assertEqual(status, "open")
            self.assertGreaterEqual(evidence["attempts"], 2)
            self.assertEqual(len(led), 0)
        finally:
            td.cleanup()


class ReviewerGateTests(unittest.TestCase):
    """The semantic reviewer is a second gate AFTER the structural gate: a
    structurally-valid certificate must also pass ReviewerAgent.check before it
    enters the ledger (the math-correctness step gate_obligation cannot do)."""

    GOOD_PAYLOAD = json.dumps({
        "id": "x", "title": "x", "claim": "x",
        "status": "proved", "summary": "x",
        "manual_certificate": CLOSED_CERTIFICATE, "tool_requests": [],
    })

    def _plan_and_ledger(self, td):
        led = LemmaLedger(path=Path(td.name) / "lemmas.jsonl")
        plan = PlanRecord(
            plan_id="S2_round01_P1", obligation_id="S2", round_idx=1,
            approach_name="x", subgoals=["fresh subgoal not in ledger"],
        )
        return led, plan

    def test_reviewer_reject_then_pass_closes_on_second_attempt(self):
        # Structural gate passes both times; reviewer rejects attempt 1 (its
        # objection seeds the revision) then accepts attempt 2.
        td = tempfile.TemporaryDirectory()
        try:
            led, plan = self._plan_and_ledger(td)
            verdicts = iter([(False, "[REJECT] fatal: step 2 does not follow"),
                             (True, "[PASS] correct")])
            with mock.patch.object(BaseAgent, "call_llm_tagged",
                                   lambda self, *a, **kw: ReviewerGateTests.GOOD_PAYLOAD), \
                 mock.patch.object(ReviewerAgent, "check",
                                   lambda self, draft, context, review_directive="": next(verdicts)):
                status, evidence = prove_subgoal(
                    oid="S2", plan=plan, subgoal_index=0, ledger=led, review=True,
                )
            self.assertEqual(status, "closed")
            self.assertEqual(evidence["attempts"], 2)
            self.assertEqual(len(led), 1)
        finally:
            td.cleanup()

    def test_reviewer_persistent_reject_keeps_subgoal_open(self):
        # Gate passes but the reviewer always rejects: nothing enters the ledger.
        td = tempfile.TemporaryDirectory()
        try:
            led, plan = self._plan_and_ledger(td)
            with mock.patch.object(BaseAgent, "call_llm_tagged",
                                   lambda self, *a, **kw: ReviewerGateTests.GOOD_PAYLOAD), \
                 mock.patch.object(ReviewerAgent, "check",
                                   lambda self, *a, **kw: (False, "[REJECT] fatal: unsupported inequality")):
                status, evidence = prove_subgoal(
                    oid="S2", plan=plan, subgoal_index=0, ledger=led, review=True,
                )
            self.assertEqual(status, "open")
            self.assertGreaterEqual(evidence["attempts"], 2)
            self.assertEqual(len(led), 0)
            self.assertIn("reviewer", evidence["failure_reason"].lower())
        finally:
            td.cleanup()

    def test_review_disabled_closes_on_structural_gate_alone(self):
        # review=False must reproduce the old behavior: gate-only, no reviewer call.
        def _no_review(*a, **k):
            raise AssertionError("reviewer must not run when review=False")

        td = tempfile.TemporaryDirectory()
        try:
            led, plan = self._plan_and_ledger(td)
            with mock.patch.object(BaseAgent, "call_llm_tagged",
                                   lambda self, *a, **kw: ReviewerGateTests.GOOD_PAYLOAD), \
                 mock.patch.object(ReviewerAgent, "check", _no_review):
                status, evidence = prove_subgoal(
                    oid="S2", plan=plan, subgoal_index=0, ledger=led, review=False,
                )
            self.assertEqual(status, "closed")
            self.assertEqual(evidence["attempts"], 1)
            self.assertEqual(len(led), 1)
        finally:
            td.cleanup()


class FailureLedgerTests(unittest.TestCase):
    """The persistent failure store: dual of the Lemma Ledger (failure-only)."""

    def _new_ledger(self):
        td = tempfile.TemporaryDirectory()
        path = Path(td.name) / "failures.jsonl"
        return FailureLedger(path=path), path, td

    def test_record_find_and_latest_wins(self):
        led, path, td = self._new_ledger()
        try:
            stmt = "Upsilon_O(u, x) < 0 at endpoint a_{n-1}."
            self.assertIsNone(led.find_for(stmt))
            led.record(FailureRecord.from_statement(
                statement=stmt, reason="first objection", obligation_id="S2", kind="gate",
            ))
            self.assertEqual(led.find_for(stmt).reason, "first objection")
            # Same statement, newer objection overwrites (latest wins).
            led.record(FailureRecord.from_statement(
                statement=stmt, reason="sharper objection", obligation_id="S2", kind="reviewer",
            ))
            self.assertEqual(led.find_for(stmt).reason, "sharper objection")
            self.assertEqual(led.find_for(stmt).kind, "reviewer")
            self.assertEqual(len(led), 1)
            # Survives a reload from disk (cross-session persistence).
            reloaded = FailureLedger(path=path)
            self.assertEqual(reloaded.find_for(stmt).reason, "sharper objection")
            self.assertEqual(len(load_failures(path)), 1)
        finally:
            td.cleanup()

    def test_find_miss_returns_none(self):
        led, _path, td = self._new_ledger()
        try:
            self.assertIsNone(led.find_for("a statement never recorded"))
        finally:
            td.cleanup()


class FailureMemoryTests(unittest.TestCase):
    """prove_subgoal persists a failure when a subgoal stays open, the writer
    prompt surfaces it next time, and a dead plan folds it into its obstruction."""

    GOOD_PAYLOAD = json.dumps({
        "id": "x", "title": "x", "claim": "x",
        "status": "proved", "summary": "x",
        "manual_certificate": CLOSED_CERTIFICATE, "tool_requests": [],
    })
    BAD_GATE_PAYLOAD = json.dumps({
        "id": "x", "title": "x", "claim": "x",
        "status": "proved", "summary": "x",
        "manual_certificate": "## Assumptions\n## Claim\n## Verification Needs\npending\n",
        "tool_requests": [],
    })

    def _plan_and_stores(self, td):
        led = LemmaLedger(path=Path(td.name) / "lemmas.jsonl")
        failures = FailureLedger(path=Path(td.name) / "failures.jsonl")
        plan = PlanRecord(
            plan_id="S2_round01_P1", obligation_id="S2", round_idx=1,
            approach_name="endpoint-direct", subgoals=["fresh subgoal not in ledger"],
        )
        return led, failures, plan

    def test_persistent_reviewer_reject_records_reviewer_failure(self):
        td = tempfile.TemporaryDirectory()
        try:
            led, failures, plan = self._plan_and_stores(td)
            with mock.patch.object(BaseAgent, "call_llm_tagged",
                                   lambda self, *a, **kw: FailureMemoryTests.GOOD_PAYLOAD), \
                 mock.patch.object(ReviewerAgent, "check",
                                   lambda self, *a, **kw: (False, "[REJECT] chord-vs-arc applied in wrong direction")):
                status, _ev = prove_subgoal(
                    oid="S2", plan=plan, subgoal_index=0, ledger=led,
                    failures=failures, review=True,
                )
            self.assertEqual(status, "open")
            rec = failures.find_for(plan.subgoals[0])
            self.assertIsNotNone(rec)
            self.assertEqual(rec.kind, "reviewer")
            self.assertIn("chord-vs-arc", rec.reason)
            self.assertEqual(rec.plan_id, "S2_round01_P1")
            self.assertEqual(rec.round_idx, 1)
        finally:
            td.cleanup()

    def test_gate_failure_records_gate_failure(self):
        td = tempfile.TemporaryDirectory()
        try:
            led, failures, plan = self._plan_and_stores(td)
            with mock.patch.object(BaseAgent, "call_llm_tagged",
                                   lambda self, *a, **kw: FailureMemoryTests.BAD_GATE_PAYLOAD):
                status, _ev = prove_subgoal(
                    oid="S2", plan=plan, subgoal_index=0, ledger=led,
                    failures=failures, review=False,
                )
            self.assertEqual(status, "open")
            rec = failures.find_for(plan.subgoals[0])
            self.assertIsNotNone(rec)
            self.assertEqual(rec.kind, "gate")
        finally:
            td.cleanup()

    def test_no_failure_recorded_when_subgoal_closes(self):
        td = tempfile.TemporaryDirectory()
        try:
            led, failures, plan = self._plan_and_stores(td)
            verdicts = iter([(False, "[REJECT] fixable"), (True, "[PASS] correct")])
            with mock.patch.object(BaseAgent, "call_llm_tagged",
                                   lambda self, *a, **kw: FailureMemoryTests.GOOD_PAYLOAD), \
                 mock.patch.object(ReviewerAgent, "check",
                                   lambda self, draft, context, review_directive="": next(verdicts)):
                status, _ev = prove_subgoal(
                    oid="S2", plan=plan, subgoal_index=0, ledger=led,
                    failures=failures, review=True,
                )
            self.assertEqual(status, "closed")
            self.assertEqual(len(failures), 0)
            self.assertIsNone(failures.find_for(plan.subgoals[0]))
        finally:
            td.cleanup()

    def test_writer_prompt_surfaces_persisted_failure(self):
        plan = PlanRecord(
            plan_id="S2_round01_P1", obligation_id="S2", round_idx=1,
            approach_name="endpoint-direct", subgoals=["bound |P| at endpoint x"],
        )
        prompt = _writer_prompt(
            oid="S2", plan=plan, subgoal_index=0,
            subgoal_text=plan.subgoals[0], ledger_block="(none)",
            prior_persisted_failure="earlier proof mis-signed DeltaL",
        )
        self.assertIn("Persistent failure memory", prompt)
        self.assertIn("mis-signed DeltaL", prompt)
        # Absent when there is no persisted failure.
        clean = _writer_prompt(
            oid="S2", plan=plan, subgoal_index=0,
            subgoal_text=plan.subgoals[0], ledger_block="(none)",
        )
        self.assertNotIn("Persistent failure memory", clean)

    def test_dead_plan_obstruction_includes_persisted_objection(self):
        td = tempfile.TemporaryDirectory()
        try:
            led = LemmaLedger(path=Path(td.name) / "lemmas.jsonl")
            failures = FailureLedger(path=Path(td.name) / "failures.jsonl")
            sg = "never proved sub-lemma"
            failures.record(FailureRecord.from_statement(
                statement=sg, reason="unsupported inequality at step 3",
                obligation_id="S2", kind="reviewer",
            ))
            plan = PlanRecord(
                plan_id="P", obligation_id="S2", round_idx=1, approach_name="x",
                subgoals=[sg], rounds_without_progress=2,
            )
            update_plan_status(plan, led, stall_threshold=2, failures=failures)
            self.assertEqual(plan.status, "dead")
            self.assertIn("recorded objections", plan.obstruction)
            self.assertIn("unsupported inequality at step 3", plan.obstruction)
            self.assertIn("[reviewer]", plan.obstruction)
        finally:
            td.cleanup()


class SelectorAssemblyTests(unittest.TestCase):
    def test_assembly_requires_complete(self):
        td = tempfile.TemporaryDirectory()
        try:
            led = LemmaLedger(path=Path(td.name) / "lemmas.jsonl")
            sg1 = "first sub-lemma"
            sg2 = "second sub-lemma"
            led.append(LemmaRecord.from_statement(
                statement=sg1, certificate=CLOSED_CERTIFICATE,
                obligation_origin="S2", closed=True,
            ))
            led.append(LemmaRecord.from_statement(
                statement=sg2, certificate=CLOSED_CERTIFICATE,
                obligation_origin="S2", closed=True,
            ))
            plan = PlanRecord(
                plan_id="S2_round01_P1", obligation_id="S2", round_idx=1,
                approach_name="endpoint-direct", subgoals=[sg1, sg2],
            )
            update_plan_status(plan, led, stall_threshold=2)
            self.assertEqual(plan.status, "complete")
            draft = assemble_obligation_certificate(plan, led, "S2")
            self.assertIsNotNone(draft)
            self.assertEqual(draft["id"], "S2")
            verdict = gate_obligation("S2", draft)
            self.assertTrue(verdict.closed, verdict.reason)
        finally:
            td.cleanup()


    def test_dead_status_after_n_stall_rounds(self):
        td = tempfile.TemporaryDirectory()
        try:
            led = LemmaLedger(path=Path(td.name) / "lemmas.jsonl")
            plan = PlanRecord(
                plan_id="P", obligation_id="S2", round_idx=1, approach_name="x",
                subgoals=["never proved"],
                rounds_without_progress=2,
            )
            update_plan_status(plan, led, stall_threshold=2)
            self.assertEqual(plan.status, "dead")
        finally:
            td.cleanup()

    def test_obstruction_populated_on_dead(self):
        # P2: a plan that dies from stalling must carry a non-empty obstruction
        # so the next generator round can read it (design §5 signal 3).
        td = tempfile.TemporaryDirectory()
        try:
            led = LemmaLedger(path=Path(td.name) / "lemmas.jsonl")
            plan = PlanRecord(
                plan_id="P", obligation_id="S2", round_idx=1, approach_name="x",
                subgoals=["never proved"], rounds_without_progress=2,
            )
            update_plan_status(plan, led, stall_threshold=2)
            self.assertEqual(plan.status, "dead")
            self.assertTrue(plan.obstruction.strip(),
                            "dead plan must record a non-empty obstruction")
            self.assertIn("stalled", plan.obstruction)
        finally:
            td.cleanup()


class RunLoopPlanBoardTests(unittest.TestCase):
    """P1 regression: plans persist across rounds, stall to dead, then escalate
    (design §5 signal 3 — dead code before the Plan Board refactor)."""

    def test_run_loop_plan_dies_across_rounds(self):
        sg = "UNCLOSABLE-TEST-SUBGOAL: never enters the ledger."
        plan = PlanRecord(
            plan_id="S2_round01_P1", obligation_id="S2", round_idx=1,
            approach_name="stub-route", subgoals=[sg],
        )
        captured = {}

        def fake_generate(*, k, round_idx, **kw):
            return [plan] if round_idx == 1 else []

        def fake_develop(*, plan, **kw):
            return [(0, sg, "open", {"failure_reason": "stub"})]

        def fake_escalate(*, oid, dead_plans, **kw):
            captured["dead_plans"] = list(dead_plans)
            return {"proposal": {"next_step": "halt"}}

        with mock.patch.multiple(
            cli,
            _load_attached_uris=lambda: [],
            retrieve_literature_packet=lambda *a, **k: "",
            _persist_plan=lambda *a, **k: None,
            make_generator_agent=lambda **k: None,
            make_judge_agent=lambda **k: None,
            make_writer_agent=lambda **k: None,
            make_escalator_agent=lambda **k: None,
            generate_plan_set=fake_generate,
            develop_plan=fake_develop,
            write_escalation=fake_escalate,
        ):
            with redirect_stdout(io.StringIO()):
                rc = cli.run_obligation_loop(
                    oid="S2", rounds=3, k=1, n=2, stream=False, resume=False,
                )

        self.assertEqual(rc, 3, "all-dead board must escalate (exit code 3)")
        self.assertIn("dead_plans", captured, "write_escalation must run")
        dead = captured["dead_plans"]
        self.assertEqual(len(dead), 1)
        self.assertEqual(dead[0].status, "dead")
        self.assertGreaterEqual(dead[0].rounds_without_progress, 2)
        self.assertTrue(dead[0].obstruction.strip())


if __name__ == "__main__":
    unittest.main()
