import sqlite3
import tempfile
import unittest
from unittest import mock

from proof_agent.candidate_memory import CandidateRecord, MemoryManager
from proof_agent.research_system import AutonomousResearchSystem


class MemoryManagerRegressionTests(unittest.TestCase):
    def test_save_candidate_skips_identical_snapshots(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/memory.sqlite"
            manager = MemoryManager(db_path)
            candidate = CandidateRecord(candidate_id="phi_1", form="Phi = x")
            candidate.ensure_properties()

            first_snapshot = manager.save_candidate(candidate)
            second_snapshot = manager.save_candidate(CandidateRecord.from_dict(candidate.to_dict()))

            self.assertEqual(first_snapshot, second_snapshot)

            with sqlite3.connect(db_path) as conn:
                snapshot_count = int(conn.execute("SELECT COUNT(*) FROM candidate_snapshots").fetchone()[0] or 0)
            self.assertEqual(snapshot_count, 1)

    def test_make_unique_candidate_id_avoids_existing_collisions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/memory.sqlite"
            manager = MemoryManager(db_path)
            candidate = CandidateRecord(candidate_id="Phi_1", form="Phi = x")
            candidate.ensure_properties()
            manager.save_candidate(candidate)

            unique_id = manager.make_unique_candidate_id("Phi_1")

            self.assertEqual(unique_id, "Phi_1__v2")


class ResearchSystemRegressionTests(unittest.TestCase):
    def _make_system(self):
        system = AutonomousResearchSystem.__new__(AutonomousResearchSystem)
        system.literature_rag = None
        system.literature_rag_warning = ""
        return system

    def test_open_verification_needs_block_closure(self):
        system = self._make_system()
        open_draft = (
            "## Assumptions\nA\n"
            "## Claim\nB\n"
            "## Derivation\nC\n"
            "## Boundary Cases\nD\n"
            "## Verification Needs\n- still need a boundary check\n"
            "## Conclusion\nE\n"
        )
        closed_draft = open_draft.replace("- still need a boundary check", "None")

        self.assertIn(
            "still leaves open Verification Needs",
            system._proposition_closure_reason("N2", "n2_prop_1", open_draft),
        )
        self.assertEqual("", system._proposition_closure_reason("N2", "n2_prop_1", closed_draft))

    def test_q5_certificate_requires_all_required_reports_to_pass_and_include_numeric_mode(self):
        system = self._make_system()
        proposition = {
            "requires_tool": True,
            "tool_plan": {"must_certify": True},
        }

        mixed_reports = [
            {
                "request": {"request_id": "r1", "spec": {"mode": "numeric_1d"}},
                "report": {"status": "verified_pass", "mode": "numeric_1d"},
            },
            {
                "request": {"request_id": "r2", "spec": {"mode": "numeric_1d"}},
                "report": {"status": "tool_error", "mode": "numeric_1d"},
            },
        ]
        mixed_result = system._evaluate_q5_tool_certificate(proposition, mixed_reports)
        self.assertFalse(mixed_result["complete"])
        self.assertTrue(mixed_result["retryable"])

        symbolic_only_reports = [
            {
                "request": {"request_id": "r1", "spec": {"mode": "symbolic_multivar"}},
                "report": {"status": "verified_pass", "mode": "symbolic_multivar"},
            }
        ]
        symbolic_only_result = system._evaluate_q5_tool_certificate(proposition, symbolic_only_reports)
        self.assertFalse(symbolic_only_result["complete"])
        self.assertTrue(symbolic_only_result["retryable"])

        complete_reports = [
            {
                "request": {"request_id": "r1", "spec": {"mode": "numeric_1d"}},
                "report": {"status": "verified_pass", "mode": "numeric_1d"},
            },
            {
                "request": {"request_id": "r2", "spec": {"mode": "numeric_1d"}},
                "report": {"status": "verified_pass", "mode": "numeric_1d"},
            },
        ]
        complete_result = system._evaluate_q5_tool_certificate(proposition, complete_reports)
        self.assertTrue(complete_result["complete"])

    def test_initialize_literature_rag_degrades_gracefully(self):
        system = self._make_system()
        with mock.patch("proof_agent.research_system.LiteratureRAG", side_effect=RuntimeError("boom")):
            system._initialize_literature_rag("/tmp/paper.pdf", "# Paper\n\ntext")

        self.assertIsNone(system.literature_rag)
        self.assertIn("boom", system.literature_rag_warning)


if __name__ == "__main__":
    unittest.main()
