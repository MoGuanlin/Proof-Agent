import json
import io
import sqlite3
import tempfile
import unittest
from unittest import mock

import proof_agent.agents as agent_mod
import proof_agent.app_config as app_cfg
import proof_agent.cli.test_api_status as probe_mod
from proof_agent.candidate_memory import CandidateRecord, MemoryManager
from proof_agent.cli.memory_admin import connect_db, delete_candidate, delete_snapshot, fetch_latest_candidates
from proof_agent.cli.memory_admin_web import namespace_stats, normalize_formula_tex, split_formula_text
from proof_agent.retry import IncompleteStreamError, _classify
from proof_agent.verification_tools import verify_numeric_1d

try:
    from proof_agent.research_system import AutonomousResearchSystem
except ModuleNotFoundError as exc:  # pragma: no cover - optional runtime dependency
    AutonomousResearchSystem = None
    _RESEARCH_SYSTEM_IMPORT_ERROR = exc
else:
    _RESEARCH_SYSTEM_IMPORT_ERROR = None


class MockHTTPResponse:
    def __init__(self, payload=None, lines=None, status_code=200):
        self._payload = payload or {}
        self._lines = list(lines or [])
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.text = json.dumps(self._payload) if self._payload else ""

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload

    def iter_lines(self, decode_unicode=False):
        del decode_unicode
        for line in self._lines:
            yield line


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

    def test_namespace_scopes_latest_candidates_and_candidate_ids(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/memory.sqlite"
            manager_a = MemoryManager(
                db_path,
                architecture_mode="candidate",
                prompt_snapshot_hash="hash_a",
            )
            manager_b = MemoryManager(
                db_path,
                architecture_mode="candidate",
                prompt_snapshot_hash="hash_b",
            )

            candidate_a = CandidateRecord(candidate_id="Phi_1", form="Phi = x")
            candidate_a.ensure_properties()
            manager_a.save_candidate(candidate_a)

            candidate_b = CandidateRecord(candidate_id="Phi_1", form="Phi = y")
            candidate_b.ensure_properties()
            manager_b.save_candidate(candidate_b)

            self.assertEqual(len(manager_a.load_latest_candidates()), 1)
            self.assertEqual(len(manager_b.load_latest_candidates()), 1)
            self.assertEqual(manager_a.load_latest_candidates()[0].form, "Phi = x")
            self.assertEqual(manager_b.load_latest_candidates()[0].form, "Phi = y")
            self.assertEqual(manager_a.make_unique_candidate_id("Phi_1"), "Phi_1__v2")
            self.assertEqual(manager_b.make_unique_candidate_id("Phi_1"), "Phi_1__v2")

    def test_search_memory_packet_is_concise_and_omits_proposition_snapshots(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/memory.sqlite"
            manager = MemoryManager(db_path)
            candidate = CandidateRecord(candidate_id="Phi_1", form="Phi = x + y")
            candidate.ensure_properties()
            candidate.mark_property("N2", "fail", note="monotonicity broke near terminal disk")
            candidate.mark_pruned("N2 failed")
            candidate.set_terminal_decision(action="continue_exploring", rationale="try another family")
            candidate.set_proposition_plan(
                "N2",
                [
                    {
                        "id": "n2_prop_1",
                        "title": "N2 core",
                        "claim": "Core monotonicity claim",
                        "verification_focus": "sign of the difference term",
                    }
                ],
            )
            candidate.mark_proposition("N2", "n2_prop_1", "fail", note="difference term changed sign")
            manager.save_candidate(candidate)

            summary = manager.search_memory_packet(max_candidates=4, max_chars=4000)

            self.assertIn("最近候选轨迹", summary)
            self.assertIn("monotonicity broke near terminal disk", summary)
            self.assertNotIn("propositions=", summary)
            self.assertNotIn("n2_prop_1=fail", summary)

    def test_property_learning_packet_keeps_history_but_omits_reuse_templates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/memory.sqlite"
            manager = MemoryManager(db_path)
            candidate = CandidateRecord(candidate_id="Phi_1", form="Phi = x + y")
            candidate.ensure_properties()
            candidate.mark_property("N2", "pass", note="local comparison closed")
            candidate.set_proposition_plan(
                "N2",
                [
                    {
                        "id": "n2_prop_1",
                        "title": "N2 core",
                        "claim": "Core monotonicity claim",
                        "verification_focus": "sign of the difference term",
                    }
                ],
            )
            candidate.mark_proposition(
                "N2",
                "n2_prop_1",
                "pass",
                note="difference term stayed nonnegative",
                artifact_key="property_N2_n2_prop_1",
                title="N2 core",
                claim="Core monotonicity claim",
                verification_focus="sign of the difference term",
            )
            candidate.artifacts["property_N2_n2_prop_1"] = (
                "## Assumptions\nA\n"
                "## Claim\nCore monotonicity claim\n"
                "## Derivation\nDifference term stays nonnegative.\n"
                "## Boundary Cases\nEndpoint handled.\n"
                "## Verification Needs\nNone\n"
                "## Conclusion\nN2 closes.\n"
            )
            manager.save_candidate(candidate)

            packet = manager.property_learning_packet("N2", form_text="Phi = x + y", max_items=4, max_chars=4000)

            self.assertIn("历史通过 proposition", packet)
            self.assertIn("difference term stayed nonnegative", packet)
            self.assertNotIn("可复用 proposition 模板", packet)
            self.assertNotIn("derivation_hint=", packet)

    def test_zero_limits_mean_unbounded_in_memory_views(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/memory.sqlite"
            manager = MemoryManager(db_path)

            first = CandidateRecord(candidate_id="Phi_1", form="Phi = x + y")
            first.ensure_properties()
            first.mark_property("N2", "fail", note="first long failure note")
            first.mark_pruned("first long failure note")
            manager.save_candidate(first)

            second = CandidateRecord(candidate_id="Phi_2", form="Phi = x + y + z")
            second.ensure_properties()
            second.mark_property("N2", "fail", note="second long failure note")
            second.mark_pruned("second long failure note")
            manager.save_candidate(second)

            similar = manager.find_similar_failures("Phi = x + y", limit=0)
            summary = manager.recent_candidate_summary(max_items=0, max_chars=0)

            self.assertEqual(2, len(similar))
            self.assertIn("Phi_1", summary)
            self.assertIn("Phi_2", summary)
            self.assertIn("first long failure note", summary)
            self.assertIn("second long failure note", summary)


class VerificationToolRegressionTests(unittest.TestCase):
    def test_numeric_1d_accepts_strategy_alias_and_symbolic_domain_constants(self):
        report = verify_numeric_1d(
            {
                "mode": "numeric_1d",
                "strategy": "interval_branch_and_bound_with_lipschitz",
                "variable": "alpha",
                "domain": ["pi/2", "pi"],
                "inequalities": [
                    {
                        "name": "always_negative",
                        "expression": "-1",
                        "relation": "<",
                        "threshold": 0,
                    }
                ],
                "grid_points": 201,
                "lipschitz": 1,
                "tolerance": 1e-10,
                "max_iterations": 1000,
                "min_width": 1e-12,
                "notes": [],
            }
        )

        self.assertEqual("verified_pass", report.status)
        self.assertEqual("branch_bound", report.details[0]["strategy"])
        self.assertAlmostEqual(3.141592653589793 / 2, report.details[0]["domain"][0])
        self.assertAlmostEqual(3.141592653589793, report.details[0]["domain"][1])

    def test_numeric_1d_supports_per_inequality_domains(self):
        report = verify_numeric_1d(
            {
                "mode": "numeric_1d",
                "strategy": "interval_branch_and_bound_with_lipschitz",
                "variable": "alpha",
                "domain": [
                    {"name": "g1", "interval": ["pi/2", "pi"]},
                    {"name": "g2", "interval": [0, "pi/2"]},
                ],
                "inequalities": [
                    {"name": "g1_neg", "expression": "-1", "relation": "<", "threshold": 0},
                    {"name": "g2_neg", "expression": "-2", "relation": "<", "threshold": 0},
                ],
                "grid_points": 201,
                "lipschitz": 1,
                "tolerance": 1e-10,
                "max_iterations": 1000,
                "min_width": 1e-12,
                "notes": [],
            }
        )

        self.assertEqual("verified_pass", report.status)
        self.assertAlmostEqual(3.141592653589793 / 2, report.details[0]["domain"][0])
        self.assertAlmostEqual(3.141592653589793, report.details[0]["domain"][1])
        self.assertAlmostEqual(0.0, report.details[1]["domain"][0])
        self.assertAlmostEqual(3.141592653589793 / 2, report.details[1]["domain"][1])

    def test_numeric_1d_treats_singular_samples_as_inconclusive_not_fail(self):
        report = verify_numeric_1d(
            {
                "mode": "numeric_1d",
                "strategy": "grid",
                "variable": "alpha",
                "domain": [0, 1],
                "inequalities": [
                    {
                        "name": "singular_but_negative_elsewhere",
                        "expression": "log(alpha) - 1",
                        "relation": "<",
                        "threshold": 0,
                    }
                ],
                "grid_points": 401,
                "lipschitz": 1,
                "tolerance": 1e-10,
                "max_iterations": 1000,
                "min_width": 1e-12,
                "notes": [],
            }
        )

        self.assertEqual("inconclusive", report.status)
        self.assertEqual("inconclusive", report.details[0]["status"])
        self.assertEqual(0, report.details[0]["sample_failures"])
        self.assertGreater(report.details[0]["invalid_samples"], 0)

    def test_namespace_stats_only_count_selected_namespace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/memory.sqlite"
            manager_a = MemoryManager(
                db_path,
                architecture_mode="candidate",
                prompt_snapshot_hash="hash_a",
            )
            manager_b = MemoryManager(
                db_path,
                architecture_mode="candidate",
                prompt_snapshot_hash="hash_b",
            )

            candidate_a = CandidateRecord(candidate_id="Phi_1", form="Phi = x")
            candidate_a.ensure_properties()
            candidate_a.set_proposition_plan(
                "N2",
                [
                    {
                        "id": "n2_prop_1",
                        "title": "Local check",
                        "claim": "x \\ge 0",
                        "verification_focus": "boundary",
                    }
                ],
            )
            candidate_a.mark_proposition(
                "N2",
                "n2_prop_1",
                "hypothesis",
                note="candidate A",
                artifact_key="property_N2_n2_prop_1",
                title="Local check",
                claim="x \\ge 0",
                verification_focus="boundary",
            )
            candidate_a.artifacts["property_N2_n2_prop_1"] = "Proof A"
            manager_a.save_candidate(candidate_a)

            candidate_b = CandidateRecord(candidate_id="Phi_1", form="Phi = y")
            candidate_b.ensure_properties()
            candidate_b.set_proposition_plan(
                "N2",
                [
                    {
                        "id": "n2_prop_1",
                        "title": "Local check",
                        "claim": "y \\ge 0",
                        "verification_focus": "boundary",
                    }
                ],
            )
            candidate_b.mark_proposition(
                "N2",
                "n2_prop_1",
                "hypothesis",
                note="candidate B",
                artifact_key="property_N2_n2_prop_1",
                title="Local check",
                claim="y \\ge 0",
                verification_focus="boundary",
            )
            candidate_b.artifacts["property_N2_n2_prop_1"] = "Proof B"
            manager_b.save_candidate(candidate_b)

            with connect_db(db_path) as conn:
                stats_a = namespace_stats(conn, manager_a.namespace_label())
                stats_all = namespace_stats(conn, None)

            self.assertEqual(1, stats_a["candidate_latest"])
            self.assertEqual(1, stats_a["candidate_snapshots"])
            self.assertEqual(len(candidate_a.property_status), stats_a["property_states"])
            self.assertEqual(1, stats_a["proposition_states"])
            self.assertEqual(1, stats_a["artifacts"])
            self.assertEqual(2, stats_all["candidate_latest"])
            self.assertEqual(2, stats_all["proposition_states"])
            self.assertEqual(2, stats_all["artifacts"])

    def test_delete_candidate_can_be_scoped_to_namespace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/memory.sqlite"
            manager_a = MemoryManager(
                db_path,
                architecture_mode="candidate",
                prompt_snapshot_hash="hash_a",
            )
            manager_b = MemoryManager(
                db_path,
                architecture_mode="candidate",
                prompt_snapshot_hash="hash_b",
            )

            candidate_a = CandidateRecord(candidate_id="Phi_1", form="Phi = x")
            candidate_a.ensure_properties()
            manager_a.save_candidate(candidate_a)

            candidate_b = CandidateRecord(candidate_id="Phi_1", form="Phi = y")
            candidate_b.ensure_properties()
            manager_b.save_candidate(candidate_b)

            with connect_db(db_path) as conn:
                delete_candidate(conn, "Phi_1", namespace=manager_a.namespace_label())
                remaining_a = fetch_latest_candidates(conn, namespace=manager_a.namespace_label())
                remaining_b = fetch_latest_candidates(conn, namespace=manager_b.namespace_label())

            self.assertEqual([], remaining_a)
            self.assertEqual(1, len(remaining_b))
            self.assertEqual("Phi = y", remaining_b[0]["form"])

    def test_delete_snapshot_keeps_other_namespace_latest_rows_intact(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/memory.sqlite"
            manager_a = MemoryManager(
                db_path,
                architecture_mode="candidate",
                prompt_snapshot_hash="hash_a",
            )
            manager_b = MemoryManager(
                db_path,
                architecture_mode="candidate",
                prompt_snapshot_hash="hash_b",
            )

            candidate_a = CandidateRecord(candidate_id="Phi_1", form="Phi = x")
            candidate_a.ensure_properties()
            first_snapshot = manager_a.save_candidate(candidate_a)

            candidate_a.form = "Phi = x + y"
            latest_snapshot = manager_a.save_candidate(candidate_a)

            candidate_b = CandidateRecord(candidate_id="Phi_1", form="Phi = z")
            candidate_b.ensure_properties()
            other_snapshot = manager_b.save_candidate(candidate_b)

            with connect_db(db_path) as conn:
                delete_snapshot(conn, latest_snapshot)
                remaining_a = fetch_latest_candidates(conn, namespace=manager_a.namespace_label())
                remaining_b = fetch_latest_candidates(conn, namespace=manager_b.namespace_label())

            self.assertEqual(1, len(remaining_a))
            self.assertEqual(first_snapshot, remaining_a[0]["snapshot_id"])
            self.assertEqual("Phi = x", remaining_a[0]["form"])
            self.assertEqual(1, len(remaining_b))
            self.assertEqual(other_snapshot, remaining_b[0]["snapshot_id"])
            self.assertEqual("Phi = z", remaining_b[0]["form"])

    def test_split_formula_text_separates_main_equation_from_explanation(self):
        formula, tail = split_formula_text(
            "Phi_O = phi * sum_{i=2}^{n} H_i, where phi = (3 / sqrt(5)) * (1 - lambda / rho)."
        )

        self.assertEqual("Phi_O = phi * sum_{i=2}^{n} H_i", formula)
        self.assertEqual("where phi = (3 / sqrt(5)) * (1 - lambda / rho).", tail)

    def test_normalize_formula_tex_converts_pseudo_tex_tokens(self):
        tex = normalize_formula_tex(
            "Phi_O = varphi * sum_{i=2}^{n} (||o_{i-1} o_i|| - sqrt(5)) + mu * cos(beta - gamma)"
        )

        self.assertIn(r"\Phi_O", tex)
        self.assertIn(r"\varphi", tex)
        self.assertIn(r"\sum_{i=2}^{n}", tex)
        self.assertIn(r"\lVert o_{i-1} o_i \rVert", tex)
        self.assertIn(r"\sqrt{5}", tex)
        self.assertIn(r"\mu \cdot \cos(\beta - \gamma)", tex)


class OpenAIProviderRegressionTests(unittest.TestCase):
    def test_app_config_openai_uses_openai_key_and_chat_completions_url(self):
        with mock.patch.object(app_cfg, "LLM_PROVIDER", "openai"), mock.patch.object(
            app_cfg,
            "OPENAI_API_KEY",
            "sk-test-openai",
        ), mock.patch.object(
            app_cfg,
            "OPENAI_BASE_URL",
            "https://www.luminai.cc/v1",
        ):
            self.assertEqual("sk-test-openai", app_cfg._active_api_key())
            self.assertEqual(
                "https://www.luminai.cc/v1/chat/completions",
                app_cfg._openai_compatible_api_url(),
            )

    def test_openai_headers_and_payload_include_reasoning_effort(self):
        agent = agent_mod.BaseAgent("Test Agent", system_role="system role", temperature=0.1)
        with mock.patch.object(agent_mod, "LLM_PROVIDER", "openai"), mock.patch.object(
            agent_mod,
            "MODEL_NAME",
            "gpt-5.4",
        ), mock.patch.object(
            agent_mod,
            "MODEL_REASONING_EFFORT",
            "xhigh",
        ), mock.patch.object(
            agent_mod,
            "_active_api_key",
            return_value="sk-openai",
        ), mock.patch.object(
            agent_mod,
            "_openai_compatible_extra_headers",
            return_value={},
        ):
            headers, payload = agent._headers_and_payload("Reply with pong")

        self.assertEqual("Bearer sk-openai", headers["Authorization"])
        self.assertEqual("gpt-5.4", payload["model"])
        self.assertEqual("xhigh", payload["reasoning_effort"])
        self.assertEqual("system role", payload["messages"][0]["content"])
        self.assertEqual("Reply with pong", payload["messages"][1]["content"])

    def test_openrouter_payload_does_not_include_reasoning_effort(self):
        agent = agent_mod.BaseAgent("Test Agent", system_role="system role", temperature=0.1)
        with mock.patch.object(agent_mod, "LLM_PROVIDER", "openrouter"), mock.patch.object(
            agent_mod,
            "MODEL_NAME",
            "openai/gpt-5.4",
        ), mock.patch.object(
            agent_mod,
            "_active_api_key",
            return_value="sk-openrouter",
        ), mock.patch.object(
            agent_mod,
            "_openai_compatible_extra_headers",
            return_value={},
        ):
            _, payload = agent._headers_and_payload("Reply with pong")

        self.assertNotIn("reasoning_effort", payload)

    def test_stream_openai_compatible_ignores_reasoning_when_hidden(self):
        agent = agent_mod.BaseAgent("Test Agent", system_role="system role")
        stream_lines = [
            b'data: {"choices":[{"delta":{"reasoning_content":"internal reasoning"}}]}',
            b'data: {"choices":[{"delta":{"content":"pong"}}]}',
            b"data: [DONE]",
        ]
        response = MockHTTPResponse(lines=stream_lines)

        with mock.patch.object(agent_mod, "MODEL_HIDE_REASONING_OUTPUT", True), mock.patch.object(
            agent_mod,
            "_openai_compatible_api_url",
            return_value="https://www.luminai.cc/v1/chat/completions",
        ), mock.patch.object(
            agent_mod,
            "_request_proxies",
            return_value=None,
        ), mock.patch.object(
            agent_mod.requests,
            "post",
            return_value=response,
        ):
            text = agent._stream_openai_compatible(
                headers={"Authorization": "Bearer sk-openai"},
                payload={"model": "gpt-5.4"},
                print_stream=False,
            )

        self.assertEqual("pong", text)

    def test_stream_openai_compatible_requires_done_marker(self):
        agent = agent_mod.BaseAgent("Test Agent", system_role="system role")
        response = MockHTTPResponse(lines=[b'data: {"choices":[{"delta":{"content":"partial proof"}}]}'])

        with mock.patch.object(agent_mod, "MODEL_HIDE_REASONING_OUTPUT", True), mock.patch.object(
            agent_mod,
            "_openai_compatible_api_url",
            return_value="https://www.luminai.cc/v1/chat/completions",
        ), mock.patch.object(
            agent_mod,
            "_request_proxies",
            return_value=None,
        ), mock.patch.object(
            agent_mod.requests,
            "post",
            return_value=response,
        ):
            with self.assertRaises(IncompleteStreamError) as ctx:
                agent._stream_openai_compatible(
                    headers={"Authorization": "Bearer sk-openai"},
                    payload={"model": "gpt-5.4"},
                    print_stream=False,
                )

        self.assertIn("without [DONE]", str(ctx.exception))
        self.assertEqual("partial proof", ctx.exception.partial_text)
        self.assertTrue(_classify(ctx.exception)[0])

    def test_non_stream_openai_keeps_existing_message_content_parsing(self):
        agent = agent_mod.BaseAgent("Test Agent", system_role="system role")
        response = MockHTTPResponse(payload={"choices": [{"message": {"content": "pong"}}]})

        with mock.patch.object(agent_mod, "LLM_PROVIDER", "openai"), mock.patch.object(
            agent_mod,
            "_openai_compatible_api_url",
            return_value="https://www.luminai.cc/v1/chat/completions",
        ), mock.patch.object(
            agent_mod,
            "_request_proxies",
            return_value=None,
        ), mock.patch.object(
            agent_mod.requests,
            "post",
            return_value=response,
        ):
            text = agent._call_non_stream(
                headers={"Authorization": "Bearer sk-openai"},
                payload={"model": "gpt-5.4"},
            )

        self.assertEqual("pong", text)

    def test_probe_openai_uses_chat_completions_and_reasoning_effort(self):
        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None, proxies=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            captured["timeout"] = timeout
            captured["proxies"] = proxies
            return MockHTTPResponse(payload={"choices": [{"message": {"content": "pong"}}]})

        with mock.patch.dict(
            "os.environ",
            {
                "OPENAI_BASE_URL": "https://www.luminai.cc/v1",
                "MODEL_REASONING_EFFORT": "xhigh",
            },
            clear=False,
        ), mock.patch.object(probe_mod.requests, "post", side_effect=fake_post):
            ok, detail = probe_mod.probe_openai(
                api_key="sk-openai",
                model="gpt-5.4",
                timeout_s=30,
                proxies=None,
            )

        self.assertTrue(ok)
        self.assertEqual("pong", detail["response_text"])
        self.assertEqual("https://www.luminai.cc/v1/chat/completions", captured["url"])
        self.assertEqual("xhigh", captured["json"]["reasoning_effort"])

    def test_test_api_status_reports_missing_openai_key(self):
        stdout = io.StringIO()
        with mock.patch.object(probe_mod, "load_dotenv_file", return_value=None), mock.patch.object(
            probe_mod.sys,
            "argv",
            ["test_api_status.py", "--provider", "openai", "--json"],
        ), mock.patch.dict(
            "os.environ",
            {
                "LLM_PROVIDER": "openai",
                "OPENAI_API_KEY": "",
            },
            clear=False,
        ), mock.patch(
            "sys.stdout",
            stdout,
        ):
            exit_code = probe_mod.main()

        self.assertEqual(2, exit_code)
        self.assertIn("missing api key for provider=openai", stdout.getvalue())

    def test_proposition_proof_repair_prompt_includes_partial_draft(self):
        agent = agent_mod.BaseAgent("Test Agent", system_role="system role")
        partial = (
            "<PROPOSITION_PROOF>\n"
            "## Assumptions\nA\n"
            "## Claim\nB\n"
            "## Derivation\nC\n"
        )
        repaired = (
            "<PROPOSITION_PROOF>\n"
            "## Assumptions\nA\n"
            "## Claim\nB\n"
            "## Derivation\nC\n"
            "## Boundary Cases\nD\n"
            "## Verification Needs\nNone\n"
            "## Conclusion\nE\n"
            "</PROPOSITION_PROOF>"
        )
        call_mock = mock.Mock(
            side_effect=[
                IncompleteStreamError("incomplete streaming response without [DONE]", partial_text=partial),
                repaired,
            ]
        )

        with mock.patch.object(agent, "call_llm", call_mock):
            result = agent.call_llm_tagged(
                "Original proposition task",
                tag_name="PROPOSITION_PROOF",
                content_hint="The output must contain the six sections Assumptions/Claim/Derivation/Boundary Cases/Verification Needs/Conclusion.",
                print_stream=False,
            )

        self.assertIn("## Conclusion\nE", result)
        repair_prompt = call_mock.call_args_list[1].args[0]
        self.assertIn("Original proposition task", repair_prompt)
        self.assertIn("## Assumptions\nA", repair_prompt)
        self.assertIn("Do not switch to reviewer mode", repair_prompt)

    def test_proposition_proof_repair_accepts_completed_six_section_draft(self):
        agent = agent_mod.BaseAgent("Test Agent", system_role="system role")
        raw = "<PROPOSITION_PROOF>\n## Assumptions\nA\n## Claim\nB\n## Derivation\nbroken"
        repaired = (
            "<PROPOSITION_PROOF>\n"
            "## Assumptions\nA\n"
            "## Claim\nB\n"
            "## Derivation\nC\n"
            "## Boundary Cases\nD\n"
            "## Verification Needs\nNone\n"
            "## Conclusion\nE\n"
            "</PROPOSITION_PROOF>"
        )
        call_mock = mock.Mock(side_effect=[raw, repaired])

        with mock.patch.object(agent, "call_llm", call_mock):
            result = agent.call_llm_tagged(
                "Original proposition task",
                tag_name="PROPOSITION_PROOF",
                content_hint="The output must contain the six sections Assumptions/Claim/Derivation/Boundary Cases/Verification Needs/Conclusion.",
                print_stream=False,
            )

        self.assertEqual(
            "## Assumptions\nA\n## Claim\nB\n## Derivation\nC\n## Boundary Cases\nD\n## Verification Needs\nNone\n## Conclusion\nE",
            result,
        )

    def test_review_result_full_text_fallback_still_works(self):
        agent = agent_mod.BaseAgent("Test Agent", system_role="system role")
        call_mock = mock.Mock(return_value="[PASS]\nLooks good.")

        with mock.patch.object(agent, "call_llm", call_mock):
            result = agent.call_llm_tagged(
                "Review this draft",
                tag_name="REVIEW_RESULT",
                content_hint="The first line must be exactly [PASS] or [REJECT].",
                print_stream=False,
            )

        self.assertEqual("[PASS]\nLooks good.", result)


@unittest.skipIf(
    AutonomousResearchSystem is None,
    f"research_system import unavailable in this environment: {_RESEARCH_SYSTEM_IMPORT_ERROR}",
)
class ResearchSystemRegressionTests(unittest.TestCase):
    def _make_system(self):
        system = AutonomousResearchSystem.__new__(AutonomousResearchSystem)
        system.literature_rag = None
        system.literature_rag_warning = ""
        return system

    @staticmethod
    def _make_q5_candidate():
        candidate = CandidateRecord(candidate_id="Q5_candidate", form="Phi = x")
        candidate.ensure_properties()
        return candidate

    def test_verification_needs_is_soft_for_non_q5_but_hard_for_q5(self):
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

        self.assertEqual("", system._proposition_closure_reason("N2", "n2_prop_1", open_draft))
        self.assertIn(
            "still leaves open Verification Needs",
            system._proposition_closure_reason("Q5", "q5_prop_1", open_draft),
        )
        self.assertEqual("", system._proposition_closure_reason("N2", "n2_prop_1", closed_draft))
        self.assertEqual("", system._proposition_closure_reason("Q5", "q5_prop_1", closed_draft))

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

    def test_non_q5_proposition_skips_tool_request_subflow(self):
        system = self._make_system()
        with mock.patch.object(
            system,
            "_request_proposition_tool_requests",
            side_effect=AssertionError("non-Q5 should not request tools"),
        ):
            draft, reports, certified = system._apply_proposition_tool_requests(
                candidate=object(),
                property_name="N2",
                proposition={"id": "n2_prop_1", "requires_tool": False},
                goal="goal",
                literature_context="",
                draft="## Verification Needs\n- reminder\n",
                property_context="context",
                property_guidance="guidance",
                proposition_meta={},
            )
        self.assertEqual("## Verification Needs\n- reminder\n", draft)
        self.assertEqual([], reports)
        self.assertFalse(certified)

    def test_q5_empty_tool_requests_trigger_forced_numeric_retry(self):
        system = self._make_system()
        candidate = self._make_q5_candidate()
        proposition = {
            "id": "Q5_numeric_certificate",
            "requires_tool": True,
            "tool_plan": {"must_certify": True},
        }
        draft = "## Verification Needs\n- need a numeric certificate\n"
        executable_request = {
            "request_id": "q5_numeric_retry",
            "tool_name": "verification",
            "justification": "forced numeric fallback",
            "spec": {
                "mode": "numeric_1d",
            },
        }
        report_payload = {
            "request": executable_request,
            "report": {"status": "verified_pass", "mode": "numeric_1d"},
        }

        with mock.patch.object(
            system,
            "_request_proposition_tool_requests",
            side_effect=[[], [executable_request]],
        ) as request_mock, mock.patch.object(
            system,
            "_execute_proposition_tool_requests",
            return_value=("patched draft", [report_payload], True),
        ):
            draft_out, reports, certified = system._apply_proposition_tool_requests(
                candidate=candidate,
                property_name="Q5",
                proposition=proposition,
                goal="goal",
                literature_context="",
                draft=draft,
                property_context="context",
                property_guidance="guidance",
                proposition_meta={},
            )

        self.assertEqual("patched draft", draft_out)
        self.assertEqual([report_payload], reports)
        self.assertTrue(certified)
        self.assertEqual(2, request_mock.call_count)
        self.assertNotIn("force_numeric_1d", request_mock.call_args_list[0].kwargs)
        self.assertTrue(request_mock.call_args_list[1].kwargs["force_numeric_1d"])
        self.assertTrue(
            any(entry.get("stage") == "tool_requests_force_retry" for entry in candidate.exploration_log)
        )

    def test_q5_empty_tool_requests_force_retry_can_still_fail_empty(self):
        system = self._make_system()
        candidate = self._make_q5_candidate()
        proposition = {
            "id": "Q5_numeric_certificate",
            "requires_tool": True,
            "tool_plan": {"must_certify": True},
        }

        with mock.patch.object(
            system,
            "_request_proposition_tool_requests",
            side_effect=[[], []],
        ) as request_mock:
            draft_out, reports, certified = system._apply_proposition_tool_requests(
                candidate=candidate,
                property_name="Q5",
                proposition=proposition,
                goal="goal",
                literature_context="",
                draft="## Verification Needs\n- still open\n",
                property_context="context",
                property_guidance="guidance",
                proposition_meta={},
            )

        self.assertEqual("## Verification Needs\n- still open\n", draft_out)
        self.assertEqual([], reports)
        self.assertFalse(certified)
        self.assertEqual(2, request_mock.call_count)
        self.assertTrue(request_mock.call_args_list[1].kwargs["force_numeric_1d"])
        self.assertTrue(
            any(entry.get("stage") == "tool_requests_force_failed" for entry in candidate.exploration_log)
        )

    def test_forced_numeric_request_generation_discards_non_executable_specs(self):
        system = self._make_system()
        candidate = self._make_q5_candidate()
        payload = json.dumps(
            [
                {
                    "request_id": "bad_symbolic",
                    "tool_name": "verification",
                    "justification": "wrong mode",
                    "spec": {
                        "status": "requested",
                        "mode": "symbolic_multivar",
                    },
                },
                {
                    "request_id": "missing_fields",
                    "tool_name": "verification",
                    "justification": "missing numeric fields",
                    "spec": {
                        "status": "requested",
                        "mode": "numeric_1d",
                        "variable": "alpha",
                    },
                },
            ]
        )

        with mock.patch.object(system, "_tool_request_reuse_context", return_value=""), mock.patch.object(
            system,
            "_compose_literature_packet",
            return_value="",
        ), mock.patch(
            "proof_agent.research_system.proof_writer.call_llm_tagged",
            return_value=payload,
        ):
            requests = system._request_proposition_tool_requests(
                candidate=candidate,
                property_name="Q5",
                proposition={"id": "Q5_numeric_certificate", "title": "Q5", "claim": "claim", "requires_tool": True},
                goal="goal",
                literature_context="",
                draft="## Verification Needs\n- need a numeric certificate\n",
                property_context="context",
                property_guidance="guidance",
                force_numeric_1d=True,
            )

        self.assertEqual([], requests)
        self.assertEqual([], candidate.tool_request_items("Q5", "Q5_numeric_certificate"))
        self.assertTrue(
            any(entry.get("stage") == "tool_requests_force_filtered" for entry in candidate.exploration_log)
        )
        self.assertTrue(
            any(entry.get("stage") == "tool_requests_force_invalid" for entry in candidate.exploration_log)
        )

    def test_q5_repair_empty_tool_requests_trigger_forced_numeric_retry(self):
        system = self._make_system()
        candidate = self._make_q5_candidate()
        proposition = {
            "id": "Q5_numeric_certificate",
            "requires_tool": True,
            "tool_plan": {"must_certify": True},
        }
        initial_request = {
            "request_id": "initial_numeric_request",
            "tool_name": "verification",
            "justification": "first attempt",
            "spec": {"mode": "numeric_1d"},
        }
        repaired_request = {
            "request_id": "repaired_numeric_request",
            "tool_name": "verification",
            "justification": "forced repair",
            "spec": {"mode": "numeric_1d"},
        }
        tool_error_payload = {
            "request": initial_request,
            "report": {"status": "tool_error", "mode": "numeric_1d"},
        }
        repaired_pass_payload = {
            "request": repaired_request,
            "report": {"status": "verified_pass", "mode": "numeric_1d"},
        }

        with mock.patch.object(
            system,
            "_request_proposition_tool_requests",
            side_effect=[[initial_request], [], [repaired_request]],
        ) as request_mock, mock.patch.object(
            system,
            "_execute_proposition_tool_requests",
            side_effect=[
                ("draft after first tool run", [tool_error_payload], False),
                ("draft after repaired tool run", [repaired_pass_payload], True),
            ],
        ):
            draft_out, reports, certified = system._apply_proposition_tool_requests(
                candidate=candidate,
                property_name="Q5",
                proposition=proposition,
                goal="goal",
                literature_context="",
                draft="## Verification Needs\n- still open\n",
                property_context="context",
                property_guidance="guidance",
                proposition_meta={},
            )

        self.assertEqual("draft after repaired tool run", draft_out)
        self.assertEqual([repaired_pass_payload], reports)
        self.assertTrue(certified)
        self.assertEqual(3, request_mock.call_count)
        self.assertTrue(request_mock.call_args_list[2].kwargs["force_numeric_1d"])
        self.assertTrue(
            any(entry.get("stage") == "tool_requests_force_retry" for entry in candidate.exploration_log)
        )

    def test_initialize_literature_rag_degrades_gracefully(self):
        system = self._make_system()
        with mock.patch("proof_agent.research_system.LiteratureRAG", side_effect=RuntimeError("boom")):
            system._initialize_literature_rag("/tmp/paper.pdf", "# Paper\n\ntext")

        self.assertIsNone(system.literature_rag)
        self.assertIn("boom", system.literature_rag_warning)


if __name__ == "__main__":
    unittest.main()
