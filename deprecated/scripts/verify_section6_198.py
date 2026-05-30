#!/usr/bin/env python3
"""External verifier scaffold for Xia Section 6's rho <= 1.98 claim.

This file intentionally does not modify the proof_agent source framework. It is
an outside proof-ledger runner that can import the existing verification tools,
execute any numeric/symbolic tool specs supplied in a JSON proof packet, and
machine-check the final constant chain.

The script does not prove the Section 6 claim by itself. A successful run
requires a proof packet that supplies closed S1-S6 obligations and executable
certificates where needed.

Examples:
    python scripts/verify_section6_198.py
    python scripts/verify_section6_198.py --full
    python scripts/verify_section6_198.py --dev
    python scripts/verify_section6_198.py --do S6
    python scripts/verify_section6_198.py --template
    python scripts/verify_section6_198.py --spec artifacts/section6_198_packet.json
"""
from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import itertools
import json
import logging
import math
import os
import random
import re
import requests
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, getcontext
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PROJECT_ROOT.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# Section 6 runs are long and often go through unstable OpenAI-compatible
# gateways. Keep these as script defaults; .env or CLI flags can override them.
os.environ.setdefault("LLM_RETRY_MAX_ATTEMPTS", "12")
os.environ.setdefault("LLM_RETRY_MAX_SECONDS", "120")

from upper_bound_agent.src.proof_agent.verification_tools import compile_expression, render_verification_report, run_verification_spec
from upper_bound_agent.src.proof_agent.candidate_memory import MemoryManager
from upper_bound_agent.src.proof_agent.agents import (
    BaseAgent,
    correctness_checker as source_correctness_checker,
    potential_designer as source_potential_designer,
    proof_planner as source_proof_planner,
    proof_writer as source_proof_writer,
)
from upper_bound_agent.src.proof_agent.app_config import (
    CANDIDATE_MEMORY_FILE,
    DISABLE_TEXT_TRUNCATION,
    LLM_PROVIDER,
    MODEL_NAME,
    PROMPT_DEFAULT_MAX_CHARS,
    PROMPT_LITERATURE_PACKET_MAX_CHARS,
    REQUEST_TIMEOUT_SECONDS,
    _active_api_key,
    _openai_compatible_api_url,
    _openai_compatible_extra_headers,
    _request_proxies,
)
from upper_bound_agent.src.proof_agent.paths import ARTIFACTS_DIR, DOCS_DIR, PRIMARY_PAPER_PATH
from upper_bound_agent.src.proof_agent.retry import IncompleteStreamError, with_http_retry


getcontext().prec = 60


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return bool(default)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return int(default)
    return int(str(value).strip())


def env_optional_int(name: str) -> int | None:
    value = os.getenv(name)
    if value is None or str(value).strip() == "":
        return None
    return int(str(value).strip())


def env_csv(name: str, default: str = "") -> tuple[str, ...]:
    raw = os.getenv(name, default)
    return tuple(item.strip() for item in str(raw or "").split(",") if item.strip())


REQUIRED_OBLIGATIONS = ("S1", "S2", "S3", "S4", "S5", "S6")
# S1 and S2 must be closed analytically. S3 and S6 still need manual
# proof context, but relaxed Section 6 may close their remaining explicit
# inequalities with replayable tool certificates.
ANALYTIC_CERTIFICATE_REQUIRED = {"S1", "S2"}
MANUAL_CERTIFICATE_REQUIRED = {"S1", "S2", "S3", "S6"}
TOOL_AUGMENTED_CERTIFICATE_ALLOWED = {"S3", "S4", "S5", "S6"}
PROVED_STATUSES = {"proved", "verified", "pass", "closed"}
PASSING_TOOL_STATUSES = {"verified_pass"}
REGION_MODES = {"numeric_region", "interval_region"}
MIN_MANUAL_CERTIFICATE_CHARS = 120
PLACEHOLDER_MARKERS = (
    "placeholder",
    "replace with",
    "fill in",
    "todo",
    "tbd",
    "[missing]",
    "待填",
    "占位",
)
SAFE_REGION_FUNCTIONS = {
    "abs",
    "acos",
    "asin",
    "atan",
    "cos",
    "exp",
    "log",
    "sin",
    "sqrt",
    "tan",
}
SAFE_REGION_CONSTANTS = {"pi": math.pi, "e": math.e}
DEFAULT_AUTO_PACKET = ARTIFACTS_DIR / "section6_198_auto_packet.json"
DEFAULT_AUTO_REPORT = ARTIFACTS_DIR / "section6_198_auto_report.md"
DEFAULT_AUTO_LOG = ARTIFACTS_DIR / "section6_198_run.log"
DEFAULT_SECTION6_MEMORY = ARTIFACTS_DIR / "section6_198_agent_memory.jsonl"
DEFAULT_SECTION6_EXPLORATION_SUMMARY = ARTIFACTS_DIR / "section6_198_exploration_summaries.jsonl"
DEFAULT_CONTEXT_CHARS = 300000
DEFAULT_PAPER_CONTEXT_CHARS = 60000
SECTION6_STAGE_CONTEXT_CHARS = (
    min(PROMPT_LITERATURE_PACKET_MAX_CHARS, max(PROMPT_DEFAULT_MAX_CHARS, PROMPT_DEFAULT_MAX_CHARS * 4))
    if DISABLE_TEXT_TRUNCATION
    else PROMPT_DEFAULT_MAX_CHARS
)
DEFAULT_HEADER_CONTEXT_CHARS = min(120000, PROMPT_LITERATURE_PACKET_MAX_CHARS)
DEFAULT_PLANNER_CONTEXT_CHARS = SECTION6_STAGE_CONTEXT_CHARS
DEFAULT_OBLIGATION_CONTEXT_CHARS = SECTION6_STAGE_CONTEXT_CHARS
DEFAULT_REVIEW_CONTEXT_CHARS = (
    min(PROMPT_LITERATURE_PACKET_MAX_CHARS, max(24000, PROMPT_DEFAULT_MAX_CHARS * 2))
    if DISABLE_TEXT_TRUNCATION
    else max(12000, PROMPT_DEFAULT_MAX_CHARS // 2)
)
DEFAULT_TOOL_CONTEXT_CHARS = SECTION6_STAGE_CONTEXT_CHARS
DEFAULT_REPAIR_CONTEXT_CHARS = DEFAULT_REVIEW_CONTEXT_CHARS
DEFAULT_MEMORY_CONTEXT_CHARS = env_int(
    "SECTION6_MEMORY_CONTEXT_CHARS",
    12000 if DISABLE_TEXT_TRUNCATION else 8000,
)
DEFAULT_EXPLORATION_SUMMARY_CONTEXT_CHARS = env_int("SECTION6_EXPLORATION_SUMMARY_CONTEXT_CHARS", 5000)
DEFAULT_CORRECTNESS_REVIEW_ROUNDS = 2
SECTION6_WEB_SEARCH = env_flag("SECTION6_WEB_SEARCH", default=False)
SECTION6_WEB_SEARCH_AGENTS = env_csv("SECTION6_WEB_SEARCH_AGENTS", "Proof Writer")
SECTION6_WEB_SEARCH_ENGINE = os.getenv("SECTION6_WEB_SEARCH_ENGINE", "").strip()
SECTION6_WEB_SEARCH_CONTEXT_SIZE = os.getenv("SECTION6_WEB_SEARCH_CONTEXT_SIZE", "").strip()
SECTION6_WEB_SEARCH_MAX_RESULTS = env_optional_int("SECTION6_WEB_SEARCH_MAX_RESULTS")
SECTION6_WEB_SEARCH_MAX_TOTAL_RESULTS = env_optional_int("SECTION6_WEB_SEARCH_MAX_TOTAL_RESULTS")
SECTION6_WEB_SEARCH_ALLOWED_DOMAINS = env_csv("SECTION6_WEB_SEARCH_ALLOWED_DOMAINS", "")
# Keep prompt context canonical: proof obligations come from SECTION6_TEMPLATE
# and stage-specific prompts; external Markdown notes are human design material.
CONTEXT_FILES: tuple[Path, ...] = ()
PAPER_PDF_CANDIDATES = (
    DOCS_DIR / "1103.4361v2.pdf",
    PRIMARY_PAPER_PATH,
)
PAPER_CONTEXT_PATTERNS = (
    "Conclusions",
    "6\n\nConclusions",
    "6 Conclusions",
    "length of the segment of uv inside On",
    "length of the segment of uv inside O",
    "length of the segment of uv inside",
    "improve the upper bound to 1.98",
    "upper bound to 1.98",
    "potential function",
    "target function",
    "Lemma 2",
    "Lemma 3",
    "Proposition 2",
    "Proposition 3",
    "Proposition 4",
    "Proposition 5",
    "Proposition 6",
    "Proposition 7",
    "Proposition 8",
    "Proposition 9",
    "Proposition 10",
    "Appendix",
)

SECTION6_SOURCE_PROPERTY_ANALOGUES = {
    "S1": "N1/base case",
    "S2": "N2 analogue/direct terminal endpoint Upsilon estimate",
    "S3": "N3 analogue/compensated obstructed split at Upsilon level",
    "S4": "D4/terminal localization with v-dependent potential",
    "S5": "Q5/multivariable regional descent certificate",
    "S6": "Q6/positive extremal-chain terminal-penetration lower bound",
}


SECTION6_TEMPLATE: dict[str, Any] = {
    "claim_id": "xia_section6_rho_1_98",
    "paper_claim": "Section 6 claims that using the length of the segment of uv inside O_n as the potential can improve the bound to 1.98.",
    "potential": {
        "form": "Phi_eta(O,u,v) = eta * L_n(u,v)",
        "definition": "L_n(u,v) is the length of the segment of uv inside the terminal disk O_n.",
        "normalization_notes": "Fill in the exact sign and coefficient eta used by the proof attempt.",
        "eta": "",
    },
    "accepted_tool_modes": [
        "numeric_1d",
        "symbolic_multivar",
        "numeric_region",
        "interval_region",
    ],
    "constant_chain": {
        "rho_target": "1.98",
        "lambda": "",
        "phi_lower_bound_B": "",
        "notes": "Use Phi >= B*|P|. The theorem checker verifies lambda/(1+B) <= rho_target. Equivalently, phi_lower_bound_C may be supplied for Phi >= -C*|P|.",
    },
    "proof_plan": {
        "proof_order": ["S1", "S2", "S3", "S4", "S5", "S6"],
        "obligations": {},
        "risk_notes": "",
        "early_failure_checks": [],
    },
    "obligations": [
        {
            "id": "S1",
            "title": "Base case",
            "claim": "For n=1, the segment u-v lies inside O_1, so L_1(u,v)=|D_O(u,v)|. Prove |P_O(u,v)|-(lambda-eta)|D_O(u,v)|<0 for every nondegenerate u!=v using |P_O|<=(pi/2)|D_O|.",
            "status": "missing",
            "summary": "Close the one-disk induction base. With eta=1 and lambda=643/250 this reduces to lambda-eta>pi/2.",
            "manual_certificate": "",
            "tool_requests": [],
        },
        {
            "id": "S2",
            "title": "Terminal endpoint Upsilon estimate",
            "claim": "For every chain O with n>=2 and every terminal endpoint x in {a_{n-1}, b_{n-1}}, prove directly that Upsilon_O(u,x)=|P_O(u,x)|-lambda|D_O(u,x)|+eta L_n(u,x)<0. Do not reduce this to the old Phi-monotonicity comparison Phi_O<=Phi_{O_{1,n-1}}; that comparison can fail for L_n.",
            "status": "missing",
            "summary": "Replace the old terminal-disk monotonicity step by a direct endpoint-level bound that absorbs the positive L_n contribution.",
            "manual_certificate": "",
            "tool_requests": [],
        },
        {
            "id": "S3",
            "title": "Compensated obstructed split",
            "claim": "When D_O(u,v) is obstructed at p in {a_j,b_j}, split O into O^L=O_{1,j+1} with terminals u,p and O^R=O_{j+1,n} with terminals p,v. Prove the Upsilon-level split Upsilon_O(u,v)<=Upsilon_{O^L}(u,p)+Upsilon_{O^R}(p,v), equivalently the compensated split defect (|P_O|-|P_L|-|P_R|)+eta(L_n(u,v)-L_{j+1}(u,p)-L_n(p,v))<=0 after the D-length split.",
            "status": "missing",
            "summary": "Do not assert old Phi subadditivity in isolation. Either prove the compensated split inequality directly or supply a strict certificate for the explicit geometric balance.",
            "manual_certificate": "",
            "tool_requests": [],
        },
        {
            "id": "S4",
            "title": "Worst terminal point control",
            "claim": "For each non-endpoint, no-pivot, unobstructed terminal subarc Ahat on the boundary of O_n, establish sup_{v in Ahat} Upsilon_O(u,v)<0 using an endpoint reduction, a piecewise monotonic/pivot certificate, or a direct regional certificate. The certificate must state whether L_n is a full chord, a partial chord, or |uv| in the local case.",
            "status": "missing",
            "summary": "Replace the original endpoint/pivot localization argument by a strict certificate for the v-dependent terminal supremum.",
            "manual_certificate": "",
            "tool_requests": [],
        },
        {
            "id": "S5",
            "title": "Regional descent certificate",
            "claim": "In the terminal-disk transformation step, prove the directional derivative dUpsilon/dX=d|P|/dX-lambda d|D|/dX+eta dL_n/dX<0 on the full feasible region. The region and dL_n/dX must be specified explicitly, including any induced movement of v and gamma.",
            "status": "missing",
            "summary": "Produce an explicit multivariable regional descent certificate; do not compress the relaxed case to Xia's four old one-variable g_i functions unless that reduction is re-proved.",
            "manual_certificate": "",
            "tool_requests": [],
        },
        {
            "id": "S6",
            "title": "Extreme-chain lower bound",
            "claim": "On the extremal chain O* selected by the Lemma-3 extremal procedure, prove the positive terminal-penetration inequality L_n(u*,v*) >= (3/10)|P_{O*}(u*,v*)|, or the corresponding eta-scaled bound Phi_eta >= B|P| with B recorded in constant_chain.",
            "status": "missing",
            "summary": "Expose the geometric chord-versus-path lower bound that justifies B=3/10; the final lambda/(1+B) arithmetic alone does not close S6.",
            "manual_certificate": "",
            "tool_requests": [],
        },
    ],
}


SOURCE_STYLE_PROOF_SKELETON = (
    "Write the analytic certificate in the same proposition style used by proof_agent's Proof Writer:\n"
    "## Assumptions\n"
    "## Claim\n"
    "## Derivation\n"
    "## Boundary Cases\n"
    "## Verification Needs\n"
    "## Conclusion\n"
    "If the local proposition is closed, the Verification Needs section must be exactly `None`. "
    "If anything remains unresolved, list only line-by-line `- ...` items there and set status to missing/blocked. "
    "For the Section 6 segment potential, Phi depends on v through L_n(u,v). Do not use the old v-independent shortcuts "
    "`Phi_O <= Phi_{O_{1,n-1}}`, standalone Phi-subadditivity, endpoint-only localization, or compression to Xia's "
    "four one-variable g_i certificates unless the certificate re-proves the missing relaxed hypotheses explicitly."
)


def section6_s2_continuation_facts() -> str:
    return (
        "S2 continuation facts from the current packet and prior S2 exploration:\n"
        "- For O'=O_{1,n-1} and x in {a_{n-1}, b_{n-1}}, x is a terminal of the prefix chain O'.\n"
        "- The endpoint D-value is unchanged by appending O_n: |D_O(u,x)|=|D_{O'}(u,x)|.\n"
        "- Therefore the endpoint increment identity is\n"
        "  Upsilon_O(u,x)-Upsilon_{O'}(u,x)\n"
        "  = (|P_O(u,x)|-|P_{O'}(u,x)|) + (L_n(u,x)-L_{n-1}(u,x)).\n"
        "- These facts may be cited as established local lemmas in this continuation. Do not spend the main proof effort "
        "re-proving them unless a correction is needed.\n"
        "- They do not close S2 by themselves. The only remaining mathematical bottleneck is the endpoint compensation "
        "inequality for DeltaP+DeltaL, plus any explicitly stated source of strictness."
    )


def section6_continuation_context(oid: str) -> str:
    if str(oid or "").strip().upper() == "S2":
        return section6_s2_continuation_facts()
    return "[none]"


def section6_obligation_focus_prompt(oid: str) -> str:
    if str(oid or "").strip().upper() != "S2":
        return ""
    return (
        "Current S2 micro-target for this continuation:\n"
        "The broad S2 reduction is already known. In this run, do not output another broad S2 reduction as the main result.\n"
        "Use the established identity\n"
        "  Upsilon_O(u,x)-Upsilon_{O'}(u,x) = DeltaP + DeltaL,\n"
        "where DeltaP=|P_O(u,x)|-|P_{O'}(u,x)| and DeltaL=L_n(u,x)-L_{n-1}(u,x).\n\n"
        "Your task is exactly one of the following:\n"
        "A) Prove DeltaP+DeltaL <= 0 for x=a_{n-1} and x=b_{n-1}, with a separate geometric comparison of P_O and P_{O'} in both endpoint cases. "
        "If this closes S2, integrate it into a full S2 certificate and set `## Verification Needs` exactly to `None`.\n"
        "B) Disprove this segment-potential candidate by giving a concrete feasible geometric configuration or parameter regime where DeltaP+DeltaL > 0.\n"
        "C) If neither is possible from the supplied definitions, state the single missing geometric definition or lemma needed to decide the sign of DeltaP+DeltaL.\n\n"
        "Required Derivation structure for S2:\n"
        "1. Prefix terminal geometry: state exactly what part of P_{O'} ends at x.\n"
        "2. Appended-disk geometry: state exactly what part of P_O replaces it.\n"
        "3. Endpoint x=a_{n-1}: compute or bound DeltaP and DeltaL.\n"
        "4. Endpoint x=b_{n-1}: compute or bound DeltaP and DeltaL.\n"
        "5. Conclude the sign of DeltaP+DeltaL, or give the precise obstruction/counterexample.\n\n"
        "If strict negativity uses an induction hypothesis on the prefix chain, formulate that induction step explicitly. "
        "Do not treat the old v-independent Phi_O<=Phi_{O'} shortcut as the proof."
    )


def section6_web_search_instruction() -> str:
    if not SECTION6_WEB_SEARCH:
        return ""
    return (
        "Optional web-literature instruction:\n"
        "OpenRouter web search may be available for this request. Use it sparingly and only to locate relevant scholarly definitions, "
        "the original Xia paper context, or closely related geometric-spanner lemmas. The final proof certificate must remain "
        "self-contained; a citation may motivate a lemma but cannot replace the derivation. Keep any URLs inside JSON string fields, "
        "and do not output prose outside the requested tagged JSON object."
    )


INTERVAL_REGION_TOOL_SCHEMA = {
    "request_id": "s5_interval_region_certificate",
    "tool_name": "verification",
    "justification": "Explain exactly which displayed inequality this certifies.",
    "spec": {
        "mode": "interval_region",
        "variables": ["alpha", "beta"],
        "domain": {"alpha": ["0", "pi/2"], "beta": ["0", "pi/2"]},
        "constraints": [
            {
                "label": "optional_feasibility_constraint",
                "expression": "alpha - beta",
                "relation": ">=",
                "threshold": 0,
            }
        ],
        "inequalities": [
            {
                "label": "descent_negative",
                "expression": "sin(alpha) - alpha*cos(alpha)",
                "relation": "<",
                "threshold": 0,
            }
        ],
        "max_iterations": 20000,
        "min_width": 1e-5,
        "notes": "Replace the toy expression/domain with the actual Section 6 regional inequality.",
    },
}


def toy_example_packet() -> dict[str, Any]:
    obligations = [
        {
            "id": oid,
            "title": f"{oid} toy obligation",
            "claim": f"{oid} toy claim only.",
            "status": "proved",
            "manual_certificate": "Toy placeholder only. Replace with the real Section 6 proof text.",
            "tool_requests": [],
        }
        for oid in REQUIRED_OBLIGATIONS
    ]
    obligations[4] = {
        "id": "S5",
        "title": "S5 toy interval-region obligation",
        "claim": "Toy claim only: x**2 < 2 on [0,1].",
        "status": "verified",
        "manual_certificate": "Toy interval-region certificate only; replace expression/domain with the real S5 descent inequality.",
        "tool_requests": [
            {
                "request_id": "toy_interval_region",
                "tool_name": "verification",
                "justification": "Demonstrates the interval_region format; proves x**2 < 2 on [0,1].",
                "spec": {
                    "mode": "interval_region",
                    "variables": ["x"],
                    "domain": {"x": [0, 1]},
                    "constraints": [],
                    "inequalities": [
                        {
                            "label": "toy_x_sq_lt_2",
                            "expression": "x**2",
                            "relation": "<",
                            "threshold": 2,
                        }
                    ],
                    "max_iterations": 100,
                    "min_width": 1e-6,
                    "notes": "Toy example; not mathematical evidence for Xia Section 6.",
                },
            }
        ],
    }


def self_test_packet(*, numeric_region_only: bool = False) -> dict[str, Any]:
    certificate = (
        "## Assumptions\n"
        "This is an internal self-test obligation, not Section 6 evidence. The "
        "test assumes only that a substantive certificate with the fixed proof "
        "sections can be parsed by the local verifier.\n\n"
        "## Claim\n"
        "The self-test analytic obligation is syntactically closed.\n\n"
        "## Derivation\n"
        "The derivation is intentionally simple: the packet is designed to test "
        "the verifier gate, so the proof text is long enough to avoid the "
        "reserved-marker and minimum-length filters and includes an explicitly closed "
        "Verification Needs section.\n\n"
        "## Boundary Cases\n"
        "There are no mathematical boundary cases in this internal format test.\n\n"
        "## Verification Needs\n"
        "None\n\n"
        "## Conclusion\n"
        "The self-test certificate is closed for verifier-regression purposes only."
    )
    obligations = [
        {
            "id": oid,
            "title": f"{oid} self-test obligation",
            "claim": f"{oid} self-test local claim.",
            "status": "proved",
            "summary": f"{oid} self-test summary.",
            "manual_certificate": certificate,
            "tool_requests": [],
        }
        for oid in REQUIRED_OBLIGATIONS
    ]
    region_mode = "numeric_region" if numeric_region_only else "interval_region"
    obligations[4] = {
        "id": "S5",
        "title": "S5 self-test region obligation",
        "claim": "Self-test claim: x**2 < 2 on [0,1].",
        "status": "verified",
        "summary": "Self-test summary for the region verifier.",
        "manual_certificate": "",
        "tool_requests": [
            {
                "request_id": f"self_test_{region_mode}",
                "tool_name": "verification",
                "justification": "Internal self-test region request.",
                "spec": {
                    "mode": region_mode,
                    "variables": ["x"],
                    "domain": {"x": [0, 1]},
                    "constraints": [],
                    "inequalities": [
                        {
                            "label": "self_test_x_sq_lt_2",
                            "expression": "x**2",
                            "relation": "<",
                            "threshold": 2,
                        }
                    ],
                    "grid_points": 5,
                    "max_iterations": 100,
                    "min_width": 1e-6,
                    "notes": "Internal self-test only.",
                },
            }
        ],
    }
    return {
        "claim_id": "self_test_section6_packet",
        "paper_claim": SECTION6_TEMPLATE["paper_claim"],
        "potential": {
            "form": "Phi_eta(O,u,v) = eta * L_n(u,v)",
            "definition": "L_n(u,v) is the length of the segment of uv inside O_n.",
            "normalization_notes": "Self-test coefficient only.",
            "eta": "0.1",
        },
        "constant_chain": {
            "rho_target": "1.98",
            "lambda": "1.8",
            "phi_lower_bound_B": "0",
            "notes": "Self-test constants only.",
        },
        "obligations": obligations,
    }


def read_text_if_exists(path: Path, max_chars: int = 80000) -> str:
    if not path.exists() or not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    head = text[: int(max_chars * 0.65)]
    tail = text[-int(max_chars * 0.35) :]
    return f"{head}\n\n[...truncated...]\n\n{tail}"


def _merge_line_windows(windows: list[tuple[int, int]], *, gap: int = 8) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(windows):
        if not merged or start > merged[-1][1] + gap:
            merged.append((start, end))
            continue
        prev_start, prev_end = merged[-1]
        merged[-1] = (prev_start, max(prev_end, end))
    return merged


def _truncate_middle(text: str, max_chars: int) -> str:
    text = str(text or "").strip()
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    head = text[: int(max_chars * 0.65)]
    tail = text[-int(max_chars * 0.35) :]
    return f"{head}\n\n[...truncated...]\n\n{tail}"


def focused_paper_context(text: str, max_chars: int) -> str:
    """Build a Section-6-focused paper packet instead of sending the full paper."""
    lines = str(text or "").splitlines()
    if not lines or max_chars <= 0:
        return ""

    windows: list[tuple[int, int]] = []
    lowered_patterns = tuple(pattern.lower() for pattern in PAPER_CONTEXT_PATTERNS)
    for index, line in enumerate(lines):
        lowered = line.lower()
        if not any(pattern in lowered for pattern in lowered_patterns):
            continue
        windows.append((max(0, index - 22), min(len(lines), index + 78)))

    chunks: list[str] = []
    head = "\n".join(lines[:90]).strip()
    if head:
        chunks.append(f"[Paper head]\n{head}")

    for start, end in _merge_line_windows(windows):
        excerpt = "\n".join(lines[start:end]).strip()
        if excerpt:
            chunks.append(f"[Paper excerpt lines {start + 1}-{end}]\n{excerpt}")

    if not chunks:
        tail = "\n".join(lines[-260:]).strip()
        return _truncate_middle(tail or "\n".join(lines), max_chars)
    return _truncate_middle("\n\n---\n\n".join(chunks), max_chars)


def extract_pdf_context(max_chars: int = DEFAULT_PAPER_CONTEXT_CHARS, *, full_text: bool = False) -> str:
    pdf_path = next((path for path in PAPER_PDF_CANDIDATES if path.exists()), None)
    if pdf_path is None:
        return ""
    try:
        completed = subprocess.run(
            ["pdftotext", str(pdf_path), "-"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=30,
        )
    except Exception:
        return ""
    text = completed.stdout
    if full_text and len(text) <= max_chars:
        return text.strip()
    return focused_paper_context(text, max_chars=max_chars)


def build_auto_context(max_chars: int = DEFAULT_CONTEXT_CHARS) -> str:
    parts = []
    pdf_context = extract_pdf_context(max_chars=min(max_chars, DEFAULT_PAPER_CONTEXT_CHARS))
    if pdf_context:
        parts.append(f"[Primary paper extracted context]\n{pdf_context}")
    for path in CONTEXT_FILES:
        text = read_text_if_exists(path)
        if text:
            parts.append(f"[{path.relative_to(PROJECT_ROOT)}]\n{text}")
    context = "\n\n====\n\n".join(parts).strip()
    if len(context) <= max_chars:
        return context
    return context[:max_chars]


TASK_CONTEXT_PATTERNS: dict[str, tuple[str, ...]] = {
    "header": (
        "potential function",
        "length of the segment of uv inside",
        "upper bound to 1.98",
        "lambda",
        "rho",
        "Lemma 2",
        "Lemma 3",
        "Conclusions",
    ),
    "S1": (
        "n = 1",
        "single disk",
        "one disk",
        "base case",
        "shorter arc",
        "pi/2",
    ),
    "S2": (
        "Proposition 2",
        "terminal",
        "endpoint",
        "Upsilon",
        "segment",
        "L_n",
        "a_{n-1}",
        "b_{n-1}",
        "O_{n-1}",
    ),
    "S3": (
        "obstructed",
        "split",
        "Proposition 3",
        "compensated",
        "Upsilon",
        "segment",
        "pivot",
        "L_n",
    ),
    "S4": (
        "pivotal",
        "endpoint",
        "worst",
        "terminal arc",
        "segment length",
        "Proposition 4",
        "Proposition 6",
        "convex",
    ),
    "S5": (
        "Proposition 7",
        "functional analysis",
        "derivative",
        "multivariable",
        "interval_region",
        "L_n",
        "transformation",
        "descent",
    ),
    "S6": (
        "Lemma 3",
        "Proposition 8",
        "Proposition 9",
        "Proposition 10",
        "extreme",
        "extremal",
        "aligned",
        "terminal penetration",
        "chord",
        "segment length",
        "lower bound",
    ),
}


def truncate_for_prompt(text: str, max_chars: int) -> str:
    text = str(text or "").strip()
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip()


class TeeStream:
    """Write process output to both the original stream and a log file."""

    def __init__(self, primary: Any, log_handle: Any):
        self.primary = primary
        self.log_handle = log_handle

    def write(self, data: str) -> int:
        written = self.primary.write(data)
        self.log_handle.write(data)
        return written

    def flush(self) -> None:
        self.primary.flush()
        self.log_handle.flush()

    def isatty(self) -> bool:
        return bool(getattr(self.primary, "isatty", lambda: False)())

    @property
    def encoding(self) -> str:
        return getattr(self.primary, "encoding", "utf-8") or "utf-8"


def configure_output_log(log_path: Path | None, enabled: bool = True) -> Any | None:
    if not enabled or log_path is None:
        return None
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("a", encoding="utf-8", buffering=1)
    handle.write(
        "\n"
        + "=" * 80
        + f"\nsection6 run started {datetime.now(timezone.utc).isoformat()}\n"
    )
    sys.stdout = TeeStream(sys.stdout, handle)
    sys.stderr = TeeStream(sys.stderr, handle)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    file_handler.setLevel(logging.INFO)
    logging.getLogger().addHandler(file_handler)
    logging.getLogger("proof_agent.retry").setLevel(logging.INFO)
    return handle


def extract_relevant_context(context: str, task: str, max_chars: int) -> str:
    text = str(context or "").strip()
    if not text or max_chars <= 0 or len(text) <= max_chars:
        return text

    patterns = TASK_CONTEXT_PATTERNS.get(str(task or "").upper()) or TASK_CONTEXT_PATTERNS.get(str(task or "").lower()) or ()
    lowered_patterns = tuple(pattern.lower() for pattern in patterns)
    lines = text.splitlines()
    excerpts: list[str] = []
    seen: set[tuple[int, int]] = set()

    for index, line in enumerate(lines):
        lowered = line.lower()
        if "upper bound to 1.98" not in lowered and "length of the segment of uv inside" not in lowered:
            continue
        start = max(0, index - 20)
        end = min(len(lines), index + 45)
        excerpt = "\n".join(lines[start:end]).strip()
        if excerpt:
            excerpts.append(f"[Section 6 target claim]\n{excerpt}")
        break

    for index, line in enumerate(lines):
        lowered = line.lower()
        if not any(pattern in lowered for pattern in lowered_patterns):
            continue
        start = max(0, index - 28)
        end = min(len(lines), index + 80)
        key = (start, end)
        if key in seen:
            continue
        seen.add(key)
        excerpt = "\n".join(lines[start:end]).strip()
        if excerpt:
            excerpts.append(excerpt)

    anchors = []
    head = "\n".join(lines[:120]).strip()
    tail = "\n".join(lines[-220:]).strip()
    if head:
        anchors.append(f"[Context head]\n{head}")
    if tail:
        anchors.append(f"[Context tail]\n{tail}")
    if excerpts:
        anchors.append("[Task-matched excerpts]\n" + "\n\n---\n\n".join(excerpts))
    if not anchors:
        return truncate_for_prompt(text, max_chars)

    selected = "\n\n====\n\n".join(anchors)
    if len(selected) <= max_chars:
        return selected

    head_budget = max(2000, int(max_chars * 0.20))
    tail_budget = max(2000, int(max_chars * 0.20))
    excerpt_budget = max_chars - head_budget - tail_budget - 24
    compact_parts = [
        truncate_for_prompt(head, head_budget),
        truncate_for_prompt("\n\n---\n\n".join(excerpts), max(0, excerpt_budget)),
        truncate_for_prompt(tail, tail_budget),
    ]
    return "\n\n====\n\n".join(part for part in compact_parts if part).strip()[:max_chars]


def compact_json(data: Any, max_chars: int = 12000) -> str:
    text = json.dumps(data, ensure_ascii=False, indent=2)
    return truncate_for_prompt(text, max_chars)


def section6_candidate_header(packet: dict[str, Any], max_chars: int = 14000) -> str:
    """Compact source-pipeline style candidate block."""
    header = {
        "claim_id": (packet or {}).get("claim_id", ""),
        "paper_claim": (packet or {}).get("paper_claim", ""),
        "potential": (packet or {}).get("potential", {}),
        "constant_chain": (packet or {}).get("constant_chain", {}),
        "global_notes": (packet or {}).get("global_notes", ""),
    }
    return compact_json(header, max_chars=max_chars)


def section6_obligation_template_packet(max_chars: int = 8000) -> str:
    compact = [
        {
            "id": item.get("id", ""),
            "title": item.get("title", ""),
            "claim": item.get("claim", ""),
            "summary": item.get("summary", ""),
        }
        for item in SECTION6_TEMPLATE.get("obligations", [])
        if isinstance(item, dict)
    ]
    return compact_json(compact, max_chars=max_chars)


def section6_property_context(packet: dict[str, Any], oid: str, max_chars: int = 10000) -> str:
    oid = str(oid or "").strip().upper()
    potential = (packet or {}).get("potential") or {}
    chain = (packet or {}).get("constant_chain") or {}
    lines = [
        f"Candidate ID: {(packet or {}).get('claim_id', 'section6_candidate')}",
        f"Candidate form: {potential.get('form', '[missing]')}",
        f"Current property: {oid}",
        f"Source-property analogue: {SECTION6_SOURCE_PROPERTY_ANALOGUES.get(oid, '[missing]')}",
        f"Target rho: {chain.get('rho_target', '1.98')}",
        f"Lambda: {chain.get('lambda', '[missing]')}",
    ]
    if "phi_lower_bound_B" in chain:
        lines.append(f"Phi lower-bound B: {chain.get('phi_lower_bound_B')}")
    if "phi_lower_bound_C" in chain:
        lines.append(f"Phi lower-bound C: {chain.get('phi_lower_bound_C')}")
    definition = potential.get("definition")
    if definition:
        lines.append("Potential definition:")
        lines.append(truncate_for_prompt(compact_json(definition, max_chars=3000), 3000))
    notes = (packet or {}).get("global_notes", "")
    if notes:
        lines.append("Design/global notes:")
        lines.append(truncate_for_prompt(compact_json(notes, max_chars=3000), 3000))
    return truncate_for_prompt("\n".join(str(item) for item in lines), max_chars)


def section6_property_contract(packet: dict[str, Any], oid: str, max_chars: int = 8000) -> str:
    target = str(oid or "").strip().upper()
    plan = copy.deepcopy(get_section6_obligation_plan(packet, target))
    if target == "S2":
        plan["continuation_policy"] = (
            "This continuation may cite the already established S2 local lemmas in the Known continuation facts block. "
            "Those lemmas are auxiliary only; a passing S2 draft must still prove the endpoint compensation inequality "
            "or give a direct endpoint negativity proof with Verification Needs exactly None. "
            "If strictness uses induction on the prefix chain, the induction hypothesis must be stated as part of the proof structure, "
            "not as an already closed external obligation."
        )
    return compact_json(plan, max_chars=max_chars)


def section6_dependency_summary(packet: dict[str, Any], oid: str, max_chars: int = 7000) -> str:
    target = str(oid or "").strip().upper()
    plan = get_section6_obligation_plan(packet, target)
    dependencies = [
        str(dep or "").strip().upper()
        for dep in plan.get("dependencies") or []
        if str(dep or "").strip().upper() in REQUIRED_OBLIGATIONS
    ]
    if not dependencies:
        return "[none]"
    obligations = normalize_obligations(packet)
    chunks = []
    for dep in dependencies:
        item = obligations.get(dep) or {}
        chunks.append(
            "- {id}: status={status}; title={title}; summary={summary}".format(
                id=dep,
                status=str(item.get("status", "missing")).strip() or "missing",
                title=str(item.get("title", "")).strip(),
                summary=truncate_for_prompt(str(item.get("summary", "") or ""), 900),
            )
        )
    return truncate_for_prompt("\n".join(chunks), max_chars)


def extract_json_object(raw_text: str) -> dict[str, Any]:
    text = str(raw_text or "").strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    fenced = re.findall(r"```(?:json)?\s*([\s\S]*?)```", text, flags=re.IGNORECASE)
    for block in reversed(fenced):
        try:
            parsed = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    decoder = json.JSONDecoder()
    for match in reversed(list(re.finditer(r"{", text))):
        try:
            parsed, _ = decoder.raw_decode(text[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("could not parse a JSON object from LLM output")


class Section6JsonAgent(BaseAgent):
    """BaseAgent variant that tolerates empty OpenAI-compatible stream events."""

    def _web_search_enabled(self) -> bool:
        if not SECTION6_WEB_SEARCH or LLM_PROVIDER != "openrouter":
            return False
        allowed = {item.strip().lower() for item in SECTION6_WEB_SEARCH_AGENTS if item.strip()}
        if not allowed or "*" in allowed:
            return True
        return self.name.strip().lower() in allowed

    def _headers_and_payload(self, prompt):
        headers, payload = super()._headers_and_payload(prompt)
        if self._web_search_enabled():
            tool: dict[str, Any] = {"type": "openrouter:web_search"}
            parameters: dict[str, Any] = {}
            if SECTION6_WEB_SEARCH_ENGINE:
                parameters["engine"] = SECTION6_WEB_SEARCH_ENGINE
            if SECTION6_WEB_SEARCH_MAX_RESULTS is not None:
                parameters["max_results"] = max(1, SECTION6_WEB_SEARCH_MAX_RESULTS)
            if SECTION6_WEB_SEARCH_MAX_TOTAL_RESULTS is not None:
                parameters["max_total_results"] = max(1, SECTION6_WEB_SEARCH_MAX_TOTAL_RESULTS)
            if SECTION6_WEB_SEARCH_CONTEXT_SIZE:
                parameters["search_context_size"] = SECTION6_WEB_SEARCH_CONTEXT_SIZE
            if SECTION6_WEB_SEARCH_ALLOWED_DOMAINS:
                parameters["allowed_domains"] = list(SECTION6_WEB_SEARCH_ALLOWED_DOMAINS)
            if parameters:
                tool["parameters"] = parameters
            payload["tools"] = [tool]
        return headers, payload

    def call_llm(self, prompt, stream=None, print_stream=True):
        headers, payload = self._headers_and_payload(prompt)
        use_stream = bool(stream)
        print(
            f"[{self.name}] calling provider={LLM_PROVIDER} model={MODEL_NAME} "
            f"stream={1 if use_stream else 0} timeout={REQUEST_TIMEOUT_SECONDS}s "
            f"web={1 if self._web_search_enabled() else 0}",
            flush=True,
        )
        if not use_stream:
            return self._call_non_stream(headers, payload)
        try:
            return self._call_stream(headers, payload, print_stream)
        except Exception as exc:
            print(
                f"[{self.name}] streaming failed after retries: {type(exc).__name__}: {exc}; "
                "falling back to non-streaming request",
                flush=True,
            )
            return self._call_non_stream(headers, payload)

    @with_http_retry("section6_stream_openai")
    def _stream_openai_compatible(self, headers: dict[str, str], payload: dict[str, Any], print_stream: bool) -> str:
        stream_payload = dict(payload)
        stream_payload["stream"] = True
        response = requests.post(
            _openai_compatible_api_url(),
            headers=headers,
            json=stream_payload,
            stream=True,
            timeout=REQUEST_TIMEOUT_SECONDS,
            proxies=_request_proxies(),
        )
        response.raise_for_status()
        pieces: list[str] = []
        started = False
        saw_done = False
        for raw_line in response.iter_lines(decode_unicode=False):
            if not raw_line:
                continue
            line = (
                raw_line.decode("utf-8", errors="replace").strip()
                if isinstance(raw_line, bytes)
                else str(raw_line).strip()
            )
            if not line.startswith("data:"):
                continue
            data_text = line[5:].strip()
            if data_text == "[DONE]":
                saw_done = True
                break
            event = json.loads(data_text)
            choices = event.get("choices") or []
            if not choices:
                continue
            choice = choices[0] or {}
            text = self._stream_delta_text(choice)
            if not text:
                text = (choice.get("message") or {}).get("content", "")
            text = self._merge_openai_content(text)
            if not text:
                continue
            if print_stream and not started:
                print(f"[{self.name}] streaming: ", end="", flush=True)
                started = True
            if print_stream:
                print(text, end="", flush=True)
            pieces.append(text)
        if print_stream and started:
            print("")
        result = "".join(pieces).strip()
        if not saw_done:
            raise IncompleteStreamError(
                f"incomplete streaming response without [DONE] (received_chars={len(result)})",
                partial_text=result,
            )
        if not result:
            raise RuntimeError("empty streaming response")
        return result


SOURCE_AGENT_REGISTRY = {
    source_potential_designer.name: source_potential_designer,
    source_proof_planner.name: source_proof_planner,
    source_proof_writer.name: source_proof_writer,
    source_correctness_checker.name: source_correctness_checker,
}


def _llm_json_agent(name: str, role: str = "", temperature: float | None = None) -> BaseAgent:
    source_agent = SOURCE_AGENT_REGISTRY.get(str(name or "").strip())
    if source_agent is not None:
        return Section6JsonAgent(
            source_agent.name,
            source_agent.role,
            temperature=source_agent.temp if temperature is None else temperature,
        )
    return Section6JsonAgent(name, role, temperature=0.1 if temperature is None else temperature)


def _call_json_agent(
    agent: BaseAgent,
    prompt: str,
    tag_name: str,
    *,
    print_stream: bool,
    stream: bool | None,
) -> dict[str, Any]:
    raw = agent.call_llm_tagged(
        prompt,
        tag_name=tag_name,
        content_hint="The tag content must be a valid JSON object.",
        print_stream=print_stream,
        stream=stream,
    )
    return extract_json_object(raw)


def _section6_template_obligation(oid: str) -> dict[str, Any]:
    target = str(oid or "").strip().upper()
    for item in SECTION6_TEMPLATE.get("obligations", []):
        if str(item.get("id", "")).strip().upper() == target:
            return copy.deepcopy(item)
    return {
        "id": target,
        "title": f"{target} obligation",
        "claim": f"Establish {target}.",
        "summary": f"Proof obligation {target}.",
    }


def default_section6_obligation_plan(oid: str) -> dict[str, Any]:
    oid = str(oid or "").strip().upper()
    template = _section6_template_obligation(oid)
    requires_tool = oid == "S5"
    tool_plan = {"should_request": False}
    if requires_tool:
        tool_plan = {
            "should_request": True,
            "preferred_mode": "interval_region",
            "goal": "Provide an executable multivariable regional certificate for the local descent inequality.",
            "must_certify": True,
        }
    elif oid in TOOL_AUGMENTED_CERTIFICATE_ALLOWED:
        tool_plan = {
            "should_request": False,
            "preferred_mode": "interval_region",
            "goal": "If the proof draft reduces the remaining work to explicit inequalities over a stated region, a replayable interval_region certificate may cover those Verification Needs.",
            "must_certify": False,
        }
    return {
        "id": oid,
        "title": template.get("title", f"{oid} obligation"),
        "claim_refinement": template.get("claim", ""),
        "source_property_analogue": SECTION6_SOURCE_PROPERTY_ANALOGUES.get(oid, ""),
        "dependencies": [prev for prev in REQUIRED_OBLIGATIONS[: REQUIRED_OBLIGATIONS.index(oid)] if oid != "S1"],
        "verification_focus": template.get("summary", ""),
        "requires_tool": requires_tool,
        "tool_plan": tool_plan,
        "success_criterion": (
            "The local verifier must accept this obligation with no open Verification Needs; "
            "for S3/S4/S5/S6, explicit open Verification Needs may instead be covered by passing replayable tool certificates. "
            "Model-written status is advisory only."
        ),
    }


def normalize_section6_proof_plan(plan: dict[str, Any] | None) -> dict[str, Any]:
    raw = plan if isinstance(plan, dict) else {}
    order = []
    for item in raw.get("proof_order") or []:
        oid = str(item or "").strip().upper()
        if oid in REQUIRED_OBLIGATIONS and oid not in order:
            order.append(oid)
    for oid in REQUIRED_OBLIGATIONS:
        if oid not in order:
            order.append(oid)

    raw_obligations = raw.get("obligations") or raw.get("obligation_plans") or {}
    if isinstance(raw_obligations, list):
        raw_obligations = {
            str(item.get("id", "")).strip().upper(): item
            for item in raw_obligations
            if isinstance(item, dict)
        }
    if not isinstance(raw_obligations, dict):
        raw_obligations = {}

    obligations: dict[str, Any] = {}
    for oid in REQUIRED_OBLIGATIONS:
        merged = default_section6_obligation_plan(oid)
        raw_item = raw_obligations.get(oid) or raw_obligations.get(oid.lower()) or {}
        if isinstance(raw_item, dict):
            for key in (
                "title",
                "claim_refinement",
                "source_property_analogue",
                "verification_focus",
                "tool_plan",
                "success_criterion",
            ):
                if raw_item.get(key) not in (None, "", []):
                    merged[key] = raw_item[key]
            dependencies = raw_item.get("dependencies")
            if isinstance(dependencies, list):
                merged["dependencies"] = [
                    str(dep or "").strip().upper()
                    for dep in dependencies
                    if str(dep or "").strip().upper() in REQUIRED_OBLIGATIONS
                    and str(dep or "").strip().upper() != oid
                ]
            if "requires_tool" in raw_item:
                merged["requires_tool"] = str(raw_item.get("requires_tool")).strip().lower() in {
                    "1",
                    "true",
                    "yes",
                    "on",
                } or raw_item.get("requires_tool") is True
            if not isinstance(merged.get("tool_plan"), dict):
                merged["tool_plan"] = {"should_request": bool(merged.get("requires_tool"))}
        merged["id"] = oid
        obligations[oid] = merged

    return {
        "proof_order": order,
        "obligations": obligations,
        "risk_notes": str(raw.get("risk_notes", "") or "").strip(),
        "early_failure_checks": raw.get("early_failure_checks") if isinstance(raw.get("early_failure_checks"), list) else [],
        "planner_notes": str(raw.get("planner_notes", "") or raw.get("notes", "") or "").strip(),
    }


def get_section6_obligation_plan(packet: dict[str, Any], oid: str) -> dict[str, Any]:
    plan = normalize_section6_proof_plan((packet or {}).get("proof_plan"))
    return plan["obligations"].get(str(oid or "").strip().upper(), default_section6_obligation_plan(oid))


def section6_source_property(oid: str) -> str:
    analogue = SECTION6_SOURCE_PROPERTY_ANALOGUES.get(str(oid or "").strip().upper(), "")
    return analogue.split("/", 1)[0].strip()


def load_section6_local_memory_context(oid: str = "", max_chars: int = 6000) -> str:
    if not DEFAULT_SECTION6_MEMORY.exists():
        return ""
    target = str(oid or "").strip().upper()
    try:
        raw_lines = DEFAULT_SECTION6_MEMORY.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    rendered = []
    for line in raw_lines[-80:]:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        obligations = item.get("obligations") or {}
        if target and target not in obligations:
            continue
        if target:
            focus = obligations.get(target) or {}
            rendered.append(
                (
                    f"- {item.get('saved_at', '[unknown time]')} {target}: "
                    f"complete={focus.get('complete')} status={focus.get('status')} "
                    f"reason={focus.get('reason')}; verdict={item.get('verdict', '[unknown]')}"
                ).strip()
            )
        else:
            rendered.append(
                (
                    f"- {item.get('saved_at', '[unknown time]')}: verdict={item.get('verdict', '[unknown]')}; "
                    f"closed={', '.join(item.get('closed_obligations') or []) or '[none]'}; "
                    f"open={', '.join(item.get('open_obligations') or []) or '[none]'}"
                ).strip()
            )
    text = "\n".join(rendered[-30:]).strip()
    if max_chars > 0 and len(text) > max_chars:
        return text[-max_chars:]
    return text


def load_section6_exploration_summary_context(oid: str = "", max_chars: int = DEFAULT_EXPLORATION_SUMMARY_CONTEXT_CHARS) -> str:
    if max_chars <= 0 or not DEFAULT_SECTION6_EXPLORATION_SUMMARY.exists():
        return ""
    target = str(oid or "").strip().upper()
    try:
        raw_lines = DEFAULT_SECTION6_EXPLORATION_SUMMARY.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    items = []
    for line in raw_lines[-120:]:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        item_oid = str(item.get("obligation_id", "") or "").strip().upper()
        if target and item_oid != target:
            continue
        items.append(item)
    def render_summary_field(summary: dict[str, Any], key: str, limit: int = 900) -> str:
        return truncate_for_prompt(str(summary.get(key, "") or "[none]"), limit)

    rendered = []
    latest = items[-1] if items else None
    if latest:
        latest_summary = latest.get("summary")
        if not isinstance(latest_summary, dict):
            latest_summary = {}
        rendered.append(
            "\n".join(
                [
                    "[CURRENT EXPLORATION FOCUS - use before older attempts]",
                    f"obligation: {latest.get('obligation_id', '[unknown]')} round={latest.get('round', '[unknown]')} saved_at={latest.get('saved_at', '[unknown time]')}",
                    "proof_mode: First try to prove or refute current_target. Do not merely restate a blocked status unless a fresh attempt at the current target fails.",
                    f"current_target: {render_summary_field(latest_summary, 'remaining_target', 1400)}",
                    f"next_action: {render_summary_field(latest_summary, 'next_prompt_hint', 1400)}",
                    f"facts_to_reuse: {render_summary_field(latest_summary, 'key_facts', 1400)}",
                    f"dead_ends_to_avoid: {render_summary_field(latest_summary, 'failed_or_blocked', 1400)}",
                ]
            )
        )

    history_items = items[-9:-1] if latest else items[-8:]
    if history_items:
        rendered.append("[RECENT EXPLORATION HISTORY]")
    for item in history_items:
        summary = item.get("summary")
        if not isinstance(summary, dict):
            summary = {}
        parts = [
            f"- {item.get('saved_at', '[unknown time]')} {item.get('obligation_id', '[unknown]')} "
            f"round={item.get('round', '[unknown]')} complete={item.get('complete')} status={item.get('status')}:",
            f"  key_facts: {render_summary_field(summary, 'key_facts')}",
            f"  failed_or_blocked: {render_summary_field(summary, 'failed_or_blocked')}",
            f"  remaining_target: {render_summary_field(summary, 'remaining_target')}",
            f"  next_prompt_hint: {render_summary_field(summary, 'next_prompt_hint')}",
        ]
        rendered.append("\n".join(parts))
    text = "\n".join(rendered).strip()
    if len(text) > max_chars:
        return text[:max_chars]
    return text


def build_section6_memory_context(packet: dict[str, Any], oid: str = "", max_chars: int = 12000) -> str:
    local_memory = load_section6_local_memory_context(oid, max_chars=max(1000, max_chars // 3))
    exploration_memory = load_section6_exploration_summary_context(
        oid,
        max_chars=min(DEFAULT_EXPLORATION_SUMMARY_CONTEXT_CHARS, max(1000, max_chars // 2)),
    )
    memory_path = Path(CANDIDATE_MEMORY_FILE)
    if not memory_path.exists():
        return "\n\n".join(
            part
            for part in [
                f"[Section 6 focused exploration summaries]\n{exploration_memory}" if exploration_memory else "",
                f"[Section 6 local run memory]\n{local_memory}" if local_memory else "",
                "[source candidate memory unavailable: memory store does not exist]",
            ]
            if part
        )
    try:
        memory = MemoryManager(str(memory_path))
        potential = (packet or {}).get("potential") or {}
        form_text = str(potential.get("form", "") or "").strip()
        target_oid = str(oid or "").strip().upper()
        source_props = [section6_source_property(target_oid)] if target_oid else [
            section6_source_property(item) for item in REQUIRED_OBLIGATIONS
        ]
        source_props = [prop for prop in source_props if prop]
        chunks = []
        per_packet_chars = max(1000, int(max_chars / max(1, len(source_props) * 3)))
        for prop in source_props:
            learning = memory.property_learning_packet(prop, form_text=form_text, max_chars=per_packet_chars)
            reuse = memory.proposition_reuse_packet(prop, form_text=form_text, max_chars=per_packet_chars)
            tool_reuse = memory.tool_request_reuse_packet(prop, form_text=form_text, max_chars=per_packet_chars)
            section = "\n".join(
                part
                for part in [
                    f"[Mapped source property {prop}]",
                    f"property_learning:\n{learning}" if learning else "",
                    f"reusable_propositions:\n{reuse}" if reuse else "",
                    f"reusable_tool_requests:\n{tool_reuse}" if tool_reuse else "",
                ]
                if part
            ).strip()
            if section:
                chunks.append(section)
        rendered = "\n\n".join(chunks).strip()
        if not rendered:
            rendered = "[source candidate memory available but no relevant reusable packets were found]"
        combined = "\n\n".join(
            part
            for part in [
                f"[Section 6 focused exploration summaries]\n{exploration_memory}" if exploration_memory else "",
                f"[Section 6 local run memory]\n{local_memory}" if local_memory else "",
                rendered,
            ]
            if part
        ).strip()
        return combined[:max_chars]
    except Exception as exc:
        return "\n\n".join(
            part
            for part in [
                f"[Section 6 focused exploration summaries]\n{exploration_memory}" if exploration_memory else "",
                f"[Section 6 local run memory]\n{local_memory}" if local_memory else "",
                f"[source candidate memory unavailable: {exc}]",
            ]
            if part
        )


def plan_section6_proof(
    context: str,
    packet: dict[str, Any],
    *,
    print_stream: bool,
    stream: bool | None,
) -> dict[str, Any]:
    planner = _llm_json_agent("Proof Strategy Planner")
    planner_context = extract_relevant_context(
        context,
        "planner",
        max_chars=min(len(context), DEFAULT_PLANNER_CONTEXT_CHARS),
    )
    memory_context = build_section6_memory_context(packet, max_chars=DEFAULT_MEMORY_CONTEXT_CHARS)
    prompt = (
        "Research goal: verify Xia Section 6's rho <= 1.98 claim using the stated candidate potential.\n"
        "Plan the Section 6 verification in the same input/output shape as proof_agent's property proposition planner.\n"
        "Return exactly one JSON object with fields: proof_order, obligations, risk_notes, early_failure_checks, planner_notes.\n"
        "The obligations field must be an object keyed by S1,S2,S3,S4,S5,S6. Each value must contain:\n"
        "id, title, claim_refinement, source_property_analogue, dependencies, verification_focus, requires_tool, tool_plan, success_criterion.\n\n"
        "Important constraints:\n"
        "1) Use the Section 6 names S1-S6, not N1/N2/N3/D4/Q5/Q6, but include the source-property analogue for memory consistency.\n"
        "2) The planner does not prove anything and must not mark anything verified.\n"
        "3) Make the proof obligations narrow enough for a single Proof Writer proposition.\n"
        "4) For S5, prefer interval_region when the inequality is explicit; numeric_region may only search for counterexamples and cannot close the proof.\n"
        "5) For S3/S6, a tool may assist only after the draft states the analytic reduction and the exact inequality/region to certify.\n"
        "6) For S6, the success criterion must mention the exact positive lower bound Phi >= B*|P| needed by constant_chain.\n"
        "7) For S2/S3/S4, identify the endpoint, compensated split, or terminal-localization lemma that would close the local step.\n"
        "8) Do not phrase S2 as old Phi-monotonicity or S3 as standalone old Phi-subadditivity; the segment potential depends on v.\n\n"
        "Source-property analogue map:\n"
        f"{json.dumps(SECTION6_SOURCE_PROPERTY_ANALOGUES, ensure_ascii=False, indent=2)}\n\n"
        "Current candidate header:\n"
        f"{section6_candidate_header(packet)}\n\n"
        "Property learning memory:\n"
        f"{memory_context or '[none]'}\n\n"
        "Current property set:\n"
        f"{section6_obligation_template_packet()}\n\n"
        "Key literature context:\n"
        f"{planner_context}"
    )
    raw_plan = _call_json_agent(
        planner,
        prompt,
        "SECTION6_PROOF_PLAN",
        print_stream=print_stream,
        stream=stream,
    )
    return normalize_section6_proof_plan(raw_plan)


TOOL_REQUEST_NEED_MARKERS = (
    "interval_region",
    "numeric_region",
    "numeric_1d",
    "symbolic_multivar",
    "tool request",
    "tool_requests",
    "executable",
    "interval certificate",
    "numeric certificate",
    "symbolic certificate",
    "region certificate",
    "regional certificate",
    "branch-and-bound",
    "box certificate",
)


def obligation_should_request_tools(obligation: dict[str, Any], obligation_plan: dict[str, Any]) -> bool:
    existing_requests = (obligation or {}).get("tool_requests") or []
    if isinstance(existing_requests, list) and existing_requests:
        return True
    if bool((obligation_plan or {}).get("requires_tool")):
        return True
    manual_certificate = str((obligation or {}).get("manual_certificate", "") or "")
    if verification_needs_closed(manual_certificate):
        return False
    search_text = "\n".join(
        [
            extract_verification_needs(manual_certificate),
            str((obligation_plan or {}).get("tool_plan", "") or ""),
            str((obligation_plan or {}).get("verification_focus", "") or ""),
        ]
    ).lower()
    return any(marker in search_text for marker in TOOL_REQUEST_NEED_MARKERS)


def request_obligation_tool_requests(
    packet: dict[str, Any],
    obligation: dict[str, Any],
    oid: str,
    *,
    context: str,
    print_stream: bool,
    stream: bool | None,
) -> dict[str, Any]:
    if not _active_api_key():
        return obligation
    oid = str(oid or "").strip().upper()
    if oid not in REQUIRED_OBLIGATIONS:
        return obligation
    updated = copy.deepcopy(obligation or {})
    existing_requests = updated.get("tool_requests") or []
    if not isinstance(existing_requests, list):
        existing_requests = []
    manual_certificate = str(updated.get("manual_certificate", "") or "")
    obligation_plan = get_section6_obligation_plan(packet, oid)
    needs = extract_verification_needs(manual_certificate)
    if not obligation_should_request_tools(updated, obligation_plan):
        updated["tool_requests"] = existing_requests
        return updated

    tool_context = extract_relevant_context(
        context,
        oid,
        max_chars=min(len(context), DEFAULT_TOOL_CONTEXT_CHARS),
    )
    memory_context = build_section6_memory_context(packet, oid, max_chars=DEFAULT_MEMORY_CONTEXT_CHARS)
    agent = _llm_json_agent(
        "Tool Requester",
        (
            "You are the proof_agent proposition tool-request phase. "
            "You do not rewrite the proof. You output executable verification tool_requests only for concrete unresolved Verification Needs."
        ),
        temperature=0.05,
    )
    prompt = (
        f"Create tool_requests for Section 6 obligation {oid}. Return exactly one JSON object with field tool_requests.\n\n"
        "Rules:\n"
        "1) Do not modify the proof draft or candidate constants.\n"
        "2) Output [] if the remaining need is analytic/geometric and no concrete executable inequality is present.\n"
        "3) numeric_region is counterexample search only and cannot close a proof.\n"
        "4) interval_region can close explicit multivariable regional inequalities; use the exact schema below.\n"
        "5) Existing valid requests may be preserved, but malformed shorthand requests must be replaced.\n"
        "6) Every request must contain request_id, tool_name='verification', justification, and spec.\n"
        "7) For S3/S4/S5/S6, prefer interval_region when the draft contains explicit expressions, domains, and constraints.\n"
        "8) For S2, output [] unless the draft gives a concrete finite-dimensional endpoint inequality; S2 is expected to be analytic.\n\n"
        f"{section6_property_context(packet, oid)}\n\n"
        "interval_region request schema:\n"
        f"{json.dumps(INTERVAL_REGION_TOOL_SCHEMA, ensure_ascii=False, indent=2)}\n\n"
        "Current property contract:\n"
        f"{section6_property_contract(packet, oid)}\n\n"
        "Verification Needs extracted from the current draft:\n"
        f"{needs or '[not explicitly stated]'}\n\n"
        "Current obligation draft JSON:\n"
        f"{json.dumps(updated, ensure_ascii=False, indent=2)}\n\n"
        "Mapped source memory for this obligation:\n"
        f"{memory_context}\n\n"
        "Key literature context:\n"
        f"{tool_context}"
    )
    payload = _call_json_agent(
        agent,
        prompt,
        "SECTION6_TOOL_REQUESTS",
        print_stream=print_stream,
        stream=stream,
    )
    requests = payload.get("tool_requests")
    if isinstance(requests, list):
        updated["tool_requests"] = requests
    else:
        updated["tool_requests"] = existing_requests
    return updated


def review_obligation_with_correctness_checker(
    packet: dict[str, Any],
    obligation: dict[str, Any],
    oid: str,
    *,
    context: str,
    print_stream: bool,
    stream: bool | None,
) -> dict[str, Any]:
    oid = str(oid or "").strip().upper()
    review_context = extract_relevant_context(
        context,
        oid,
        max_chars=min(len(context), DEFAULT_REVIEW_CONTEXT_CHARS),
    )
    agent = _llm_json_agent("Correctness Checker", temperature=0.0)
    prompt = (
        f"Review this Section 6 proof-agent proposition draft for obligation {oid}.\n"
        "Return exactly one JSON object with fields: verdict, fatal_issues, repair_instructions, rationale.\n"
        "verdict must be PASS or REJECT.\n\n"
        "Review policy:\n"
        "1) Review only this local proposition draft and the provided Section 6 contract.\n"
        "2) PASS is allowed when the draft honestly leaves a narrow unresolved item in Verification Needs and does not overclaim closure.\n"
        "3) REJECT for concrete algebraic/geometric errors, contradiction with the candidate potential/constants, unsupported closure, broad handwaving, or claiming the global 1.98 theorem from a local step.\n"
        "4) REJECT S2/S3 drafts that rely on old v-independent Phi monotonicity/subadditivity without proving the relaxed replacement.\n"
        "5) Do not reject merely because a tool certificate is still pending; Tool Requester and Local Verifier run after this review.\n"
        "6) If rejected, repair_instructions must be specific enough for Proof Writer to revise the draft.\n\n"
        f"{section6_property_context(packet, oid)}\n\n"
        "Current property contract:\n"
        f"{section6_property_contract(packet, oid)}\n\n"
        "Known continuation facts for this obligation:\n"
        f"{section6_continuation_context(oid)}\n\n"
        "Reviewer note: do not reject solely because the draft cites a listed continuation fact instead of re-proving it. "
        "Do reject if the draft uses an unlisted fact, applies a listed fact incorrectly, or claims S2 closure without proving "
        "the endpoint compensation/strictness required by the contract.\n\n"
        "Draft obligation JSON:\n"
        f"{json.dumps(obligation, ensure_ascii=False, indent=2)}\n\n"
        "Key literature context:\n"
        f"{review_context}"
    )
    review = _call_json_agent(
        agent,
        prompt,
        "SECTION6_CORRECTNESS_REVIEW",
        print_stream=print_stream,
        stream=stream,
    )
    verdict = str(review.get("verdict", "")).strip().upper()
    if verdict not in {"PASS", "REJECT"}:
        review["verdict"] = "REJECT"
        review["fatal_issues"] = list(review.get("fatal_issues") or []) + [
            "Correctness Checker did not return verdict PASS or REJECT."
        ]
        review["repair_instructions"] = str(review.get("repair_instructions", "") or "Return a valid local proposition draft with precise scope and explicit Verification Needs.")
    return review


def write_obligation_with_review(
    writer_agent: BaseAgent,
    prompt: str,
    tag_name: str,
    packet: dict[str, Any],
    oid: str,
    *,
    context: str,
    print_stream: bool,
    stream: bool | None,
    max_review_rounds: int = DEFAULT_CORRECTNESS_REVIEW_ROUNDS,
) -> dict[str, Any]:
    oid = str(oid or "").strip().upper()
    obligation = _call_json_agent(
        writer_agent,
        prompt,
        tag_name,
        print_stream=print_stream,
        stream=stream,
    )
    obligation["id"] = str(obligation.get("id", oid)).strip().upper() or oid
    if obligation["id"] != oid:
        obligation["id"] = oid

    last_review: dict[str, Any] | None = None
    for round_index in range(1, max(1, int(max_review_rounds)) + 1):
        review = review_obligation_with_correctness_checker(
            packet,
            obligation,
            oid,
            context=context,
            print_stream=print_stream,
            stream=stream,
        )
        last_review = review
        verdict = str(review.get("verdict", "")).strip().upper()
        obligation["correctness_review"] = review
        if verdict == "PASS":
            return obligation

        revision_prompt = (
            f"{prompt}\n\n"
            "The source proof_agent Correctness Checker rejected the current draft. "
            "Revise the same local proposition only. Do not change unrelated obligations, eta, lambda, B, or rho_target. "
            "If the rejected draft overclaimed closure, narrow the conclusion and put the exact remaining items in `## Verification Needs`.\n\n"
            "Rejected draft JSON:\n"
            f"{json.dumps(obligation, ensure_ascii=False, indent=2)}\n\n"
            "Correctness Checker feedback:\n"
            f"{json.dumps(review, ensure_ascii=False, indent=2)}\n\n"
            f"Revision round: {round_index}\n"
            "Return exactly one JSON object with fields id, title, claim, status, summary, manual_certificate, tool_requests."
        )
        obligation = _call_json_agent(
            writer_agent,
            revision_prompt,
            f"{tag_name}_REVISION_{round_index}",
            print_stream=print_stream,
            stream=stream,
        )
        obligation["id"] = str(obligation.get("id", oid)).strip().upper() or oid
        if obligation["id"] != oid:
            obligation["id"] = oid

    if last_review is not None:
        obligation["correctness_review"] = last_review
        if str(last_review.get("verdict", "")).strip().upper() == "REJECT":
            obligation["status"] = "blocked"
            feedback = str(last_review.get("repair_instructions", "") or last_review.get("rationale", "") or "").strip()
            if feedback:
                obligation["summary"] = (str(obligation.get("summary", "")).strip() + f" Correctness review remains rejected: {feedback}").strip()
    return obligation


def auto_generate_packet_split(
    context: str,
    print_stream: bool = True,
    stream: bool | None = False,
    review_rounds: int = DEFAULT_CORRECTNESS_REVIEW_ROUNDS,
) -> dict[str, Any]:
    if not _active_api_key():
        raise RuntimeError(
            "auto verification requires an LLM API key because the missing Section 6 derivation must be reconstructed from text"
        )

    header_agent = _llm_json_agent("Potential Function Designer")
    header_context = extract_relevant_context(
        context,
        "header",
        max_chars=min(len(context), DEFAULT_HEADER_CONTEXT_CHARS),
    )
    header_prompt = (
        "Return a JSON object with fields claim_id, paper_claim, potential, constant_chain, and global_notes.\n"
        "The potential object must include form, definition, normalization_notes, eta.\n"
        "The constant_chain object must include rho_target, lambda, and either phi_lower_bound_B or phi_lower_bound_C.\n"
        "Use rho_target='1.98'. Build a candidate constant chain suitable for downstream proof writing.\n\n"
        "Important design rules, matching proof_agent's candidate-centric source pipeline:\n"
        "- The literal Section 6 sentence suggests the unscaled convention Phi_O(u,v)=L(uv intersect O_n), i.e. eta=1, unless you "
        "explicitly justify a different sign or scaling.\n"
        "- Lambda is the coefficient in Upsilon=|P|-lambda|D|+Phi. It does not need to be <=1.98 if the final lower bound is "
        "Phi >= B*|P| with B>0; the final check is lambda/(1+B) <= 1.98.\n"
        "- For the one-disk base case under Phi_eta=eta*L_n, record the necessary condition lambda > pi/2 + eta.\n"
        "- Choose constants with strict slack: lambda must be strictly greater than pi/2+eta, and lambda/(1+B) must be strictly less than 1.98 after decimal parsing. "
        "For example, if lambda is near pi/2+1, choose B slightly larger than lambda/1.98-1 rather than exactly equal.\n"
        "- If you choose lambda or B by optimization rather than quotation, say so in normalization_notes/global_notes and give "
        "the algebraic reason.\n"
        "- Do not present guessed values as paper-quoted values. Label them candidate constants and specify which proof properties must establish them.\n\n"
        "Relevant context:\n"
        f"{header_context}"
    )
    header = _call_json_agent(
        header_agent,
        header_prompt,
        "SECTION6_HEADER",
        print_stream=print_stream,
        stream=stream,
    )

    packet = copy.deepcopy(SECTION6_TEMPLATE)
    for key in ("claim_id", "paper_claim", "global_notes"):
        if key in header:
            packet[key] = header[key]
    if isinstance(header.get("potential"), dict):
        packet["potential"].update(header["potential"])
    if isinstance(header.get("constant_chain"), dict):
        packet["constant_chain"].update(header["constant_chain"])

    proof_plan = plan_section6_proof(
        context,
        packet,
        print_stream=print_stream,
        stream=stream,
    )
    packet["proof_plan"] = proof_plan

    obligation_templates = {item["id"]: item for item in SECTION6_TEMPLATE["obligations"]}
    obligation_agent = _llm_json_agent("Proof Writer")
    obligations = []
    for oid in proof_plan.get("proof_order") or REQUIRED_OBLIGATIONS:
        if oid not in REQUIRED_OBLIGATIONS:
            continue
        obligation_context = extract_relevant_context(
            context,
            oid,
            max_chars=min(len(context), DEFAULT_OBLIGATION_CONTEXT_CHARS),
        )
        memory_context = build_section6_memory_context(packet, oid, max_chars=DEFAULT_MEMORY_CONTEXT_CHARS)
        focus_prompt = section6_obligation_focus_prompt(oid)
        web_instruction = section6_web_search_instruction()
        obligation_prompt = (
            "Overall goal: verify Xia Section 6's rho <= 1.98 claim using the stated candidate potential.\n"
            f"Return exactly one JSON object for obligation {oid}.\n"
            "Required fields: id, title, claim, status, summary, manual_certificate, tool_requests.\n"
            "Treat this obligation as one proof_agent proposition. Allowed status values for a real proof are proved/verified/pass/closed; use missing/blocked only when the proposition still has nonempty Verification Needs.\n"
            "The model-written status is only a claim; the local verifier will recompute closure from Verification Needs, tool execution, and the constant chain.\n"
            "For S1/S2, manual_certificate must close analytically using the six-section skeleton, not just a summary.\n"
            "For S3/S6, manual_certificate must contain the analytic reduction; if a remaining explicit inequality needs a strict certificate, list it precisely in `## Verification Needs` for tool coverage. "
            "For S4/S5, describe any needed executable certificate in `## Verification Needs`; the separate Tool Requester will convert explicit inequalities into tool_requests after the correctness review. "
            "Leave tool_requests as [] unless a complete executable request is already unavoidable.\n"
            "If the header is a candidate rather than a paper-quoted theorem, still analyze it: prove the obligation conditionally on "
            "the stated eta/lambda/B/C when possible, and make every extra assumption explicit in manual_certificate.\n"
            "Do not lead with counterexamples or failure analysis. First try to establish the proposition in the style of the source Proof Writer. "
            "If a required lemma is missing, state the narrow lemma as a Verification Need rather than replacing the proof with broad skepticism.\n"
            "For S1, explicitly use the one-disk inequality |P| <= (pi/2)|D| and the resulting condition on lambda and eta.\n"
            "For S2, prove the endpoint Upsilon estimate directly; do not present Phi_O <= Phi_{O_{1,n-1}} as the proof.\n"
            "For S3, keep the path/D split algebra separate from the new compensated segment-length defect.\n"
            "For S6, explicitly derive the B used in constant_chain or mark the obligation blocked; do not merely repeat the desired bound.\n"
            "Do not use toy examples. Do not claim the whole theorem in this local obligation.\n\n"
            f"{focus_prompt + chr(10) + chr(10) if focus_prompt else ''}"
            f"{web_instruction + chr(10) + chr(10) if web_instruction else ''}"
            f"{section6_property_context(packet, oid)}\n\n"
            "Known continuation facts for this obligation:\n"
            f"{section6_continuation_context(oid)}\n\n"
            "Property learning memory:\n"
            f"{memory_context or '[none]'}\n\n"
            "Current property contract:\n"
            f"{section6_property_contract(packet, oid)}\n\n"
            "Dependent proposition summary inside the current candidate:\n"
            f"{section6_dependency_summary(packet, oid)}\n\n"
            f"Proof Writer skeleton for manual_certificate:\n{SOURCE_STYLE_PROOF_SKELETON}\n\n"
            "Obligation template:\n"
            f"{json.dumps(obligation_templates[oid], ensure_ascii=False, indent=2)}\n\n"
            "Key literature context:\n"
            f"{obligation_context}"
        )
        obligation = write_obligation_with_review(
            obligation_agent,
            obligation_prompt,
            f"SECTION6_{oid}",
            packet,
            oid,
            context=context,
            print_stream=print_stream,
            stream=stream,
            max_review_rounds=review_rounds,
        )
        obligation = request_obligation_tool_requests(
            packet,
            obligation,
            oid,
            context=context,
            print_stream=print_stream,
            stream=stream,
        )
        obligations.append(obligation)

    packet["obligations"] = obligations
    packet.pop("toy_only", None)
    return packet


def auto_generate_packet(
    context: str,
    print_stream: bool = True,
    stream: bool | None = False,
    strategy: str = "split",
    review_rounds: int = DEFAULT_CORRECTNESS_REVIEW_ROUNDS,
) -> dict[str, Any]:
    strategy = str(strategy or "split").strip().lower()
    if strategy != "split":
        raise ValueError("only the planner-driven split strategy is supported")
    return auto_generate_packet_split(
        context,
        print_stream=print_stream,
        stream=stream,
        review_rounds=review_rounds,
    )


def audit_verified_packet(packet: dict[str, Any], report: str, print_stream: bool = True, stream: bool | None = False) -> tuple[bool, str]:
    if not _active_api_key():
        return False, "LLM audit unavailable: missing API key"
    agent = _llm_json_agent("Correctness Checker", temperature=0.0)
    prompt = (
        "Apply the source proof_agent Correctness Checker role to this Section 6 proof packet and verifier report. "
        "Return PASS only if the analytic certificates, tool certificates, "
        "and constant chain genuinely support rho <= 1.98 under the stated potential. Reject for missing derivations, vague certificates, "
        "unsupported S1/S2/S3/S6 arguments, incorrect use of the endpoint-dependent potential, old Phi-monotonicity shortcuts in S2/S3, "
        "or any mismatch between B/C/lambda/rho.\n\n"
        "Return JSON with fields: verdict, fatal_issues, rationale. verdict must be PASS or REJECT.\n\n"
        "Proof packet:\n"
        f"{json.dumps(packet, ensure_ascii=False, indent=2)}\n\n"
        "Verifier report:\n"
        f"{report}"
    )
    raw = agent.call_llm_tagged(
        prompt,
        tag_name="SECTION6_AUDIT",
        content_hint="The tag content must be a valid JSON object.",
        print_stream=print_stream,
        stream=stream,
    )
    audit = extract_json_object(raw)
    verdict = str(audit.get("verdict", "")).strip().upper()
    audit_text = json.dumps(audit, ensure_ascii=False, indent=2)
    return verdict == "PASS", audit_text


@dataclass
class ToolRun:
    request_id: str
    status: str
    mode: str
    passing: bool
    summary: str
    rendered: str


@dataclass
class ObligationRun:
    obligation_id: str
    status: str
    complete: bool
    reason: str
    tools: list[ToolRun]


def parse_decimal(value: Any, field_name: str) -> Decimal:
    if value is None:
        raise ValueError(f"{field_name} is missing")
    text = str(value).strip()
    if not text:
        raise ValueError(f"{field_name} is empty")
    if "/" in text:
        numerator, denominator = [part.strip() for part in text.split("/", 1)]
        try:
            return Decimal(numerator) / Decimal(denominator)
        except (InvalidOperation, ZeroDivisionError) as exc:
            raise ValueError(f"{field_name} is not a valid fraction: {text}") from exc
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"{field_name} is not a valid decimal: {text}") from exc


def parse_float_expr(value: Any, field_name: str) -> float:
    if isinstance(value, (int, float)):
        parsed = float(value)
        if math.isfinite(parsed):
            return parsed
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is empty")
    evaluator, _ = compile_expression(text, [])
    parsed = float(evaluator())
    if not math.isfinite(parsed):
        raise ValueError(f"{field_name} is not finite")
    return parsed


@dataclass(frozen=True)
class Interval:
    lo: float
    hi: float

    @property
    def width(self) -> float:
        return self.hi - self.lo

    @property
    def midpoint(self) -> float:
        return 0.5 * (self.lo + self.hi)

    def is_finite(self) -> bool:
        return math.isfinite(self.lo) and math.isfinite(self.hi)


def interval_add(a: Interval, b: Interval) -> Interval:
    return Interval(a.lo + b.lo, a.hi + b.hi)


def interval_sub(a: Interval, b: Interval) -> Interval:
    return Interval(a.lo - b.hi, a.hi - b.lo)


def interval_mul(a: Interval, b: Interval) -> Interval:
    values = (a.lo * b.lo, a.lo * b.hi, a.hi * b.lo, a.hi * b.hi)
    return Interval(min(values), max(values))


def interval_div(a: Interval, b: Interval) -> Interval:
    if b.lo <= 0.0 <= b.hi:
        return Interval(float("-inf"), float("inf"))
    values = (a.lo / b.lo, a.lo / b.hi, a.hi / b.lo, a.hi / b.hi)
    return Interval(min(values), max(values))


def interval_pow(a: Interval, exponent: float) -> Interval:
    rounded = round(exponent)
    if abs(exponent - rounded) > 1e-12 or rounded < 0:
        return Interval(float("-inf"), float("inf"))
    n = int(rounded)
    if n == 0:
        return Interval(1.0, 1.0)
    if n % 2 == 1:
        return Interval(a.lo**n, a.hi**n)
    if a.lo <= 0.0 <= a.hi:
        return Interval(0.0, max(a.lo**n, a.hi**n))
    values = (a.lo**n, a.hi**n)
    return Interval(min(values), max(values))


def _critical_values(lo: float, hi: float, base: float, period: float) -> list[float]:
    start = math.ceil((lo - base) / period)
    end = math.floor((hi - base) / period)
    return [base + k * period for k in range(start, end + 1)]


def interval_sin(a: Interval) -> Interval:
    if not a.is_finite():
        return Interval(-1.0, 1.0)
    if a.width >= 2.0 * math.pi:
        return Interval(-1.0, 1.0)
    points = [a.lo, a.hi]
    points.extend(_critical_values(a.lo, a.hi, math.pi / 2.0, 2.0 * math.pi))
    points.extend(_critical_values(a.lo, a.hi, -math.pi / 2.0, 2.0 * math.pi))
    values = [math.sin(x) for x in points if a.lo <= x <= a.hi]
    return Interval(min(values), max(values))


def interval_cos(a: Interval) -> Interval:
    if not a.is_finite():
        return Interval(-1.0, 1.0)
    if a.width >= 2.0 * math.pi:
        return Interval(-1.0, 1.0)
    points = [a.lo, a.hi]
    points.extend(_critical_values(a.lo, a.hi, 0.0, 2.0 * math.pi))
    points.extend(_critical_values(a.lo, a.hi, math.pi, 2.0 * math.pi))
    values = [math.cos(x) for x in points if a.lo <= x <= a.hi]
    return Interval(min(values), max(values))


def interval_tan(a: Interval) -> Interval:
    if not a.is_finite():
        return Interval(float("-inf"), float("inf"))
    poles = _critical_values(a.lo, a.hi, math.pi / 2.0, math.pi)
    if any(a.lo <= pole <= a.hi for pole in poles):
        return Interval(float("-inf"), float("inf"))
    values = (math.tan(a.lo), math.tan(a.hi))
    return Interval(min(values), max(values))


def interval_call(name: str, arg: Interval) -> Interval:
    if name == "abs":
        if arg.lo <= 0.0 <= arg.hi:
            return Interval(0.0, max(abs(arg.lo), abs(arg.hi)))
        return Interval(min(abs(arg.lo), abs(arg.hi)), max(abs(arg.lo), abs(arg.hi)))
    if name == "sin":
        return interval_sin(arg)
    if name == "cos":
        return interval_cos(arg)
    if name == "tan":
        return interval_tan(arg)
    if name == "sqrt":
        if arg.lo < 0.0:
            return Interval(float("-inf"), float("inf"))
        return Interval(math.sqrt(arg.lo), math.sqrt(arg.hi))
    if name == "exp":
        return Interval(math.exp(arg.lo), math.exp(arg.hi))
    if name == "log":
        if arg.lo <= 0.0:
            return Interval(float("-inf"), float("inf"))
        return Interval(math.log(arg.lo), math.log(arg.hi))
    if name == "asin":
        if arg.lo < -1.0 or arg.hi > 1.0:
            return Interval(float("-inf"), float("inf"))
        return Interval(math.asin(arg.lo), math.asin(arg.hi))
    if name == "acos":
        if arg.lo < -1.0 or arg.hi > 1.0:
            return Interval(float("-inf"), float("inf"))
        return Interval(math.acos(arg.hi), math.acos(arg.lo))
    if name == "atan":
        return Interval(math.atan(arg.lo), math.atan(arg.hi))
    return Interval(float("-inf"), float("inf"))


def interval_eval_node(node: ast.AST, box: dict[str, Interval]) -> Interval:
    if isinstance(node, ast.Expression):
        return interval_eval_node(node.body, box)
    if isinstance(node, ast.Constant):
        return Interval(float(node.value), float(node.value))
    if isinstance(node, ast.Name):
        if node.id in box:
            return box[node.id]
        if node.id in SAFE_REGION_CONSTANTS:
            value = SAFE_REGION_CONSTANTS[node.id]
            return Interval(value, value)
        raise ValueError(f"unsupported name in interval expression: {node.id}")
    if isinstance(node, ast.Attribute):
        if (
            isinstance(node.value, ast.Name)
            and node.value.id == "math"
            and node.attr in SAFE_REGION_CONSTANTS
        ):
            value = SAFE_REGION_CONSTANTS[node.attr]
            return Interval(value, value)
        raise ValueError("unsupported attribute in interval expression")
    if isinstance(node, ast.UnaryOp):
        value = interval_eval_node(node.operand, box)
        if isinstance(node.op, ast.USub):
            return Interval(-value.hi, -value.lo)
        if isinstance(node.op, ast.UAdd):
            return value
    if isinstance(node, ast.BinOp):
        left = interval_eval_node(node.left, box)
        right = interval_eval_node(node.right, box)
        if isinstance(node.op, ast.Add):
            return interval_add(left, right)
        if isinstance(node.op, ast.Sub):
            return interval_sub(left, right)
        if isinstance(node.op, ast.Mult):
            return interval_mul(left, right)
        if isinstance(node.op, ast.Div):
            return interval_div(left, right)
        if isinstance(node.op, ast.Pow) and right.lo == right.hi:
            return interval_pow(left, right.lo)
    if isinstance(node, ast.Call):
        if len(node.args) != 1:
            return Interval(float("-inf"), float("inf"))
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == "math":
            name = node.func.attr
        else:
            name = ""
        if name not in SAFE_REGION_FUNCTIONS:
            return Interval(float("-inf"), float("inf"))
        return interval_call(name, interval_eval_node(node.args[0], box))
    raise ValueError(f"unsupported interval expression node: {type(node).__name__}")


def parse_interval_expression(expression: str, variables: list[str]) -> ast.Expression:
    normalized = str(expression or "0").strip().replace("^", "**").replace("π", "pi").replace("−", "-")
    node = ast.parse(normalized, mode="eval")
    allowed_names = set(variables) | SAFE_REGION_FUNCTIONS | set(SAFE_REGION_CONSTANTS) | {"math"}
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id not in allowed_names:
            raise ValueError(f"unsupported name: {child.id}")
        if isinstance(child, ast.Attribute):
            if not (
                isinstance(child.value, ast.Name)
                and child.value.id == "math"
                and (child.attr in SAFE_REGION_FUNCTIONS or child.attr in SAFE_REGION_CONSTANTS)
            ):
                raise ValueError("unsupported attribute access")
        if isinstance(child, ast.Call):
            if isinstance(child.func, ast.Name):
                if child.func.id not in SAFE_REGION_FUNCTIONS:
                    raise ValueError(f"unsupported function: {child.func.id}")
            elif isinstance(child.func, ast.Attribute):
                if not (
                    isinstance(child.func.value, ast.Name)
                    and child.func.value.id == "math"
                    and child.func.attr in SAFE_REGION_FUNCTIONS
                ):
                    raise ValueError("unsupported function call")
    return node


def normalize_variables(raw: Any) -> list[str]:
    if isinstance(raw, str):
        items = [raw]
    else:
        items = list(raw or [])
    variables = []
    for item in items:
        name = str(item or "").strip()
        if not name:
            continue
        if not name.replace("_", "a").isalnum() or not (name[0].isalpha() or name[0] == "_"):
            raise ValueError(f"invalid variable name: {name}")
        if name not in variables:
            variables.append(name)
    if not variables:
        raise ValueError("region spec requires non-empty variables")
    return variables


def parse_domain_interval(raw: Any, label: str) -> Interval:
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        raise ValueError(f"{label} must be a [lo, hi] interval")
    lo = parse_float_expr(raw[0], f"{label}.lo")
    hi = parse_float_expr(raw[1], f"{label}.hi")
    if not lo < hi:
        raise ValueError(f"{label} must satisfy lo < hi")
    return Interval(lo, hi)


def normalize_region_boxes(domain: Any, variables: list[str]) -> list[dict[str, Interval]]:
    if isinstance(domain, list) and domain and all(isinstance(item, dict) for item in domain):
        raw_boxes = domain
    else:
        raw_boxes = [domain]

    boxes: list[dict[str, Interval]] = []
    for raw_box in raw_boxes:
        if not isinstance(raw_box, dict):
            raise ValueError("multivariable region domain must be an object or a list of objects")
        box = {}
        for variable in variables:
            if variable not in raw_box:
                raise ValueError(f"domain is missing interval for variable {variable}")
            box[variable] = parse_domain_interval(raw_box[variable], f"domain.{variable}")
        boxes.append(box)
    return boxes


def box_center(box: dict[str, Interval]) -> dict[str, float]:
    return {name: interval.midpoint for name, interval in box.items()}


def split_box(box: dict[str, Interval]) -> tuple[dict[str, Interval], dict[str, Interval]]:
    left, right, _, _ = split_box_with_meta(box)
    return left, right


def split_box_with_meta(box: dict[str, Interval]) -> tuple[dict[str, Interval], dict[str, Interval], str, float]:
    variable = max(box, key=lambda name: box[name].width)
    midpoint = box[variable].midpoint
    left = dict(box)
    right = dict(box)
    left[variable] = Interval(box[variable].lo, midpoint)
    right[variable] = Interval(midpoint, box[variable].hi)
    return left, right, variable, midpoint


def format_box(box: dict[str, Interval]) -> dict[str, list[float]]:
    return {name: [interval.lo, interval.hi] for name, interval in box.items()}


def format_interval(interval: Interval) -> list[float]:
    return [interval.lo, interval.hi]


def canonical_json_hash(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def parse_trace_box(raw: Any, variables: list[str]) -> dict[str, Interval]:
    if not isinstance(raw, dict):
        raise ValueError("trace event box must be an object")
    box: dict[str, Interval] = {}
    for variable in variables:
        if variable not in raw:
            raise ValueError(f"trace event box missing variable {variable}")
        value = raw[variable]
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise ValueError(f"trace event box interval for {variable} must be [lo, hi]")
        lo = float(value[0])
        hi = float(value[1])
        if not lo < hi:
            raise ValueError(f"trace event box interval for {variable} must satisfy lo < hi")
        box[variable] = Interval(lo, hi)
    return box


def boxes_match(left: dict[str, Interval], right: dict[str, Interval], variables: list[str], *, tol: float = 1e-12) -> bool:
    for variable in variables:
        a = left[variable]
        b = right[variable]
        if not (math.isclose(a.lo, b.lo, rel_tol=0.0, abs_tol=tol) and math.isclose(a.hi, b.hi, rel_tol=0.0, abs_tol=tol)):
            return False
    return True


def interval_certificate_condition(upper: float, relation: str) -> bool:
    if strict_relation(relation):
        return math.isfinite(upper) and upper < 0.0
    return math.isfinite(upper) and upper <= 0.0


def relation_gap_value(value: float, relation: str, threshold: float) -> float:
    if relation in {"<", "<="}:
        return value - threshold
    if relation in {">", ">="}:
        return threshold - value
    raise ValueError(f"unsupported relation: {relation}")


def relation_gap_interval(interval: Interval, relation: str, threshold: float) -> Interval:
    target = Interval(threshold, threshold)
    if relation in {"<", "<="}:
        return interval_sub(interval, target)
    if relation in {">", ">="}:
        return interval_sub(target, interval)
    raise ValueError(f"unsupported relation: {relation}")


def strict_relation(relation: str) -> bool:
    return relation in {"<", ">"}


def gap_violates(gap: float, relation: str) -> bool:
    return gap >= 0.0 if strict_relation(relation) else gap > 0.0


def grid_for_interval(interval: Interval, points: int) -> list[float]:
    if points <= 1:
        return [interval.midpoint]
    step = interval.width / (points - 1)
    return [interval.lo + i * step for i in range(points)]


def normalize_constraints(raw: Any) -> list[dict[str, Any]]:
    constraints = list(raw or [])
    return [item for item in constraints if isinstance(item, dict)]


def normalize_inequalities(raw: Any) -> list[dict[str, Any]]:
    inequalities = list(raw or [])
    if not inequalities:
        raise ValueError("region spec requires non-empty inequalities")
    return [item for item in inequalities if isinstance(item, dict)]


def numeric_constraints_satisfied(
    constraints: list[dict[str, Any]],
    evaluators: list[tuple[dict[str, Any], Any]],
    assignment: dict[str, float],
) -> bool:
    for item, evaluator in evaluators:
        relation = str(item.get("relation", "<=")).strip() or "<="
        threshold = parse_float_expr(item.get("threshold", 0), "constraint.threshold")
        try:
            value = evaluator(**assignment)
        except Exception:
            return False
        if not math.isfinite(value):
            return False
        if gap_violates(relation_gap_value(value, relation, threshold), relation):
            return False
    return True


def interval_constraint_state(
    constraints: list[dict[str, Any]],
    nodes: list[tuple[dict[str, Any], ast.Expression]],
    box: dict[str, Interval],
) -> str:
    if not constraints:
        return "fully_feasible"

    saw_partial = False
    for item, node in nodes:
        relation = str(item.get("relation", "<=")).strip() or "<="
        threshold = parse_float_expr(item.get("threshold", 0), "constraint.threshold")
        gap = relation_gap_interval(interval_eval_node(node, box), relation, threshold)
        if (strict_relation(relation) and gap.lo >= 0.0) or (
            not strict_relation(relation) and gap.lo > 0.0
        ):
            return "infeasible"
        if gap.hi > 0.0 or (strict_relation(relation) and gap.hi >= 0.0):
            saw_partial = True
    return "partial" if saw_partial else "fully_feasible"


def center_counterexample(
    inequality: dict[str, Any],
    evaluator: Any,
    constraints_eval: list[tuple[dict[str, Any], Any]],
    assignment: dict[str, float],
) -> dict[str, Any] | None:
    if not numeric_constraints_satisfied([item for item, _ in constraints_eval], constraints_eval, assignment):
        return None
    relation = str(inequality.get("relation", "<")).strip() or "<"
    threshold = parse_float_expr(inequality.get("threshold", 0), "inequality.threshold")
    try:
        value = evaluator(**assignment)
    except Exception:
        return None
    if not math.isfinite(value):
        return None
    gap = relation_gap_value(value, relation, threshold)
    if gap_violates(gap, relation):
        return {
            "assignment": assignment,
            "value": value,
            "gap": gap,
            "relation": relation,
            "threshold": threshold,
        }
    return None


def run_numeric_region_spec(spec: dict[str, Any]) -> dict[str, Any]:
    variables = normalize_variables(spec.get("variables"))
    boxes = normalize_region_boxes(spec.get("domain") or {}, variables)
    constraints = normalize_constraints(spec.get("constraints"))
    inequalities = normalize_inequalities(spec.get("inequalities"))
    grid_points = max(2, int(spec.get("grid_points", 5)))
    random_samples = max(0, int(spec.get("random_samples", 0)))
    max_evaluations = max(1, int(spec.get("max_evaluations", 250000)))
    seed = int(spec.get("seed", 198))

    constraint_evaluators = [
        (
            item,
            compile_expression(str(item.get("expression", "0")), variables)[0],
        )
        for item in constraints
    ]
    inequality_evaluators = [
        (
            item,
            compile_expression(str(item.get("expression", "0")), variables)[0],
        )
        for item in inequalities
    ]

    details = []
    found_counterexample = None
    rng = random.Random(seed)

    for ineq_index, (inequality, evaluator) in enumerate(inequality_evaluators, start=1):
        label = str(inequality.get("label", "")).strip() or f"ineq_{ineq_index}"
        relation = str(inequality.get("relation", "<")).strip() or "<"
        threshold = parse_float_expr(inequality.get("threshold", 0), f"{label}.threshold")
        worst_gap = float("-inf")
        worst_assignment: dict[str, float] | None = None
        checked = 0
        feasible = 0
        invalid = 0

        for box in boxes:
            grids = [grid_for_interval(box[variable], grid_points) for variable in variables]
            for values in itertools.product(*grids):
                if checked >= max_evaluations:
                    break
                checked += 1
                assignment = dict(zip(variables, values, strict=True))
                if not numeric_constraints_satisfied(constraints, constraint_evaluators, assignment):
                    continue
                feasible += 1
                try:
                    value = evaluator(**assignment)
                except Exception:
                    invalid += 1
                    continue
                if not math.isfinite(value):
                    invalid += 1
                    continue
                gap = relation_gap_value(value, relation, threshold)
                if gap > worst_gap:
                    worst_gap = gap
                    worst_assignment = dict(assignment)
                if gap_violates(gap, relation):
                    found_counterexample = {
                        "label": label,
                        "assignment": assignment,
                        "value": value,
                        "gap": gap,
                    }
                    break
            if checked >= max_evaluations or found_counterexample:
                break

            for _ in range(random_samples):
                if checked >= max_evaluations:
                    break
                checked += 1
                assignment = {
                    variable: rng.uniform(box[variable].lo, box[variable].hi)
                    for variable in variables
                }
                if not numeric_constraints_satisfied(constraints, constraint_evaluators, assignment):
                    continue
                feasible += 1
                try:
                    value = evaluator(**assignment)
                except Exception:
                    invalid += 1
                    continue
                if not math.isfinite(value):
                    invalid += 1
                    continue
                gap = relation_gap_value(value, relation, threshold)
                if gap > worst_gap:
                    worst_gap = gap
                    worst_assignment = dict(assignment)
                if gap_violates(gap, relation):
                    found_counterexample = {
                        "label": label,
                        "assignment": assignment,
                        "value": value,
                        "gap": gap,
                    }
                    break
            if checked >= max_evaluations or found_counterexample:
                break

        details.append(
            {
                "label": label,
                "status": "fail" if found_counterexample and found_counterexample["label"] == label else "search_complete",
                "relation_target": f"{relation} {threshold:g}",
                "worst_gap": None if worst_gap == float("-inf") else worst_gap,
                "worst_assignment": worst_assignment,
                "feasible_samples": feasible,
                "invalid_samples": invalid,
                "evaluations": checked,
            }
        )
        if found_counterexample:
            break

    if found_counterexample:
        return {
            "status": "verified_fail",
            "mode": "numeric_region",
            "summary": "numeric region search found a feasible counterexample sample",
            "details": details,
            "counterexample": found_counterexample,
            "notes": str(spec.get("notes", "")).strip(),
        }
    return {
        "status": "analysis_complete",
        "mode": "numeric_region",
        "summary": "numeric region search found no sampled counterexample; this is not a proof certificate",
        "details": details,
        "notes": str(spec.get("notes", "")).strip(),
    }


def verify_inequality_interval(
    inequality: dict[str, Any],
    variables: list[str],
    boxes: list[dict[str, Interval]],
    constraints: list[dict[str, Any]],
    max_iterations: int,
    min_width: float,
) -> dict[str, Any]:
    label = str(inequality.get("label", "")).strip() or "ineq"
    relation = str(inequality.get("relation", "<")).strip() or "<"
    threshold = parse_float_expr(inequality.get("threshold", 0), f"{label}.threshold")
    expr_node = parse_interval_expression(str(inequality.get("expression", "0")), variables)
    expr_eval = compile_expression(str(inequality.get("expression", "0")), variables)[0]
    constraint_nodes = [
        (item, parse_interval_expression(str(item.get("expression", "0")), variables))
        for item in constraints
    ]
    constraint_evals = [
        (item, compile_expression(str(item.get("expression", "0")), variables)[0])
        for item in constraints
    ]

    stack = list(boxes)
    iterations = 0
    closed_boxes = 0
    skipped_infeasible = 0
    partial_constraint_boxes = 0
    worst_upper = float("-inf")
    worst_box = None
    trace_events: list[dict[str, Any]] = []

    while stack and iterations < max_iterations:
        box = stack.pop()
        iterations += 1

        feasibility = interval_constraint_state(constraints, constraint_nodes, box)
        if feasibility == "infeasible":
            skipped_infeasible += 1
            trace_events.append(
                {
                    "step": iterations,
                    "action": "infeasible",
                    "box": format_box(box),
                    "feasibility": feasibility,
                }
            )
            continue
        if feasibility == "partial":
            partial_constraint_boxes += 1

        counterexample = center_counterexample(
            inequality,
            expr_eval,
            constraint_evals,
            box_center(box),
        )
        if counterexample is not None:
            trace_events.append(
                {
                    "step": iterations,
                    "action": "counterexample",
                    "box": format_box(box),
                    "feasibility": feasibility,
                    "counterexample": counterexample,
                }
            )
            return {
                "label": label,
                "status": "fail",
                "reason": "box center is a feasible counterexample",
                "counterexample": counterexample,
                "iterations": iterations,
                "closed_boxes": closed_boxes,
                "skipped_infeasible": skipped_infeasible,
                "partial_constraint_boxes": partial_constraint_boxes,
                "worst_upper": worst_upper if worst_upper != float("-inf") else None,
                "worst_box": worst_box,
                "certificate_trace": trace_events,
            }

        value_interval = interval_eval_node(expr_node, box)
        gap_interval = relation_gap_interval(value_interval, relation, threshold)
        if not gap_interval.is_finite():
            upper = float("inf")
        else:
            upper = gap_interval.hi
        if upper > worst_upper:
            worst_upper = upper
            worst_box = format_box(box)

        if interval_certificate_condition(upper, relation):
            closed_boxes += 1
            trace_events.append(
                {
                    "step": iterations,
                    "action": "certified",
                    "box": format_box(box),
                    "feasibility": feasibility,
                    "value_interval": format_interval(value_interval),
                    "gap_interval": format_interval(gap_interval),
                    "upper": upper,
                    "relation": relation,
                    "threshold": threshold,
                }
            )
            continue

        widest = max(interval.width for interval in box.values())
        if widest <= min_width:
            trace_events.append(
                {
                    "step": iterations,
                    "action": "inconclusive_min_width",
                    "box": format_box(box),
                    "feasibility": feasibility,
                    "value_interval": format_interval(value_interval),
                    "gap_interval": format_interval(gap_interval),
                    "upper": upper,
                    "widest": widest,
                    "min_width": min_width,
                }
            )
            return {
                "label": label,
                "status": "inconclusive",
                "reason": "min_width reached before all boxes were certified",
                "iterations": iterations,
                "closed_boxes": closed_boxes,
                "skipped_infeasible": skipped_infeasible,
                "partial_constraint_boxes": partial_constraint_boxes,
                "worst_upper": worst_upper,
                "worst_box": worst_box,
                "certificate_trace": trace_events,
            }

        left, right, split_variable, midpoint = split_box_with_meta(box)
        trace_events.append(
            {
                "step": iterations,
                "action": "split",
                "box": format_box(box),
                "feasibility": feasibility,
                "value_interval": format_interval(value_interval),
                "gap_interval": format_interval(gap_interval),
                "upper": upper,
                "split_variable": split_variable,
                "midpoint": midpoint,
                "children": [format_box(left), format_box(right)],
            }
        )
        stack.append(right)
        stack.append(left)

    if stack:
        trace_events.append(
            {
                "step": iterations,
                "action": "inconclusive_max_iterations",
                "remaining_boxes": len(stack),
            }
        )
        return {
            "label": label,
            "status": "inconclusive",
            "reason": "max_iterations reached before all boxes were certified",
            "iterations": iterations,
            "remaining_boxes": len(stack),
            "closed_boxes": closed_boxes,
            "skipped_infeasible": skipped_infeasible,
            "partial_constraint_boxes": partial_constraint_boxes,
            "worst_upper": worst_upper if worst_upper != float("-inf") else None,
            "worst_box": worst_box,
            "certificate_trace": trace_events,
        }

    trace_hash = canonical_json_hash(trace_events)
    return {
        "label": label,
        "status": "pass",
        "reason": "all feasible boxes certified by replayable interval certificate trace",
        "iterations": iterations,
        "closed_boxes": closed_boxes,
        "skipped_infeasible": skipped_infeasible,
        "partial_constraint_boxes": partial_constraint_boxes,
        "worst_upper": worst_upper if worst_upper != float("-inf") else None,
        "worst_box": worst_box,
        "certificate_trace_version": "interval_region_trace_v1",
        "certificate_trace_hash": trace_hash,
        "certificate_trace_events": len(trace_events),
        "certificate_trace": trace_events,
    }


def replay_interval_certificate_trace(
    inequality: dict[str, Any],
    variables: list[str],
    boxes: list[dict[str, Interval]],
    constraints: list[dict[str, Any]],
    trace_events: list[dict[str, Any]],
    *,
    min_width: float,
) -> dict[str, Any]:
    label = str(inequality.get("label", "")).strip() or "ineq"
    relation = str(inequality.get("relation", "<")).strip() or "<"
    threshold = parse_float_expr(inequality.get("threshold", 0), f"{label}.threshold")
    expr_node = parse_interval_expression(str(inequality.get("expression", "0")), variables)
    constraint_nodes = [
        (item, parse_interval_expression(str(item.get("expression", "0")), variables))
        for item in constraints
    ]
    stack = list(boxes)
    certified = 0
    infeasible = 0
    splits = 0

    if not isinstance(trace_events, list) or not trace_events:
        return {
            "status": "trace_reject",
            "reason": "certificate trace is missing or empty",
            "label": label,
        }

    for index, event in enumerate(trace_events, start=1):
        if not isinstance(event, dict):
            return {"status": "trace_reject", "reason": f"trace event {index} is not an object", "label": label}
        action = str(event.get("action", "")).strip()
        if action.startswith("inconclusive") or action == "counterexample":
            return {"status": "trace_reject", "reason": f"trace contains non-proof action {action!r}", "label": label}
        if not stack:
            return {"status": "trace_reject", "reason": f"trace event {index} has no matching pending box", "label": label}
        current = stack.pop()
        try:
            event_box = parse_trace_box(event.get("box"), variables)
        except Exception as exc:
            return {"status": "trace_reject", "reason": f"trace event {index} has invalid box: {exc}", "label": label}
        if not boxes_match(current, event_box, variables):
            return {
                "status": "trace_reject",
                "reason": f"trace event {index} box does not match replay stack",
                "label": label,
                "expected_box": format_box(current),
                "actual_box": format_box(event_box),
            }

        feasibility = interval_constraint_state(constraints, constraint_nodes, current)
        if action == "infeasible":
            if feasibility != "infeasible":
                return {
                    "status": "trace_reject",
                    "reason": f"trace event {index} marks a box infeasible but interval constraints replay as {feasibility}",
                    "label": label,
                    "box": format_box(current),
                }
            infeasible += 1
            continue

        if feasibility == "infeasible":
            return {
                "status": "trace_reject",
                "reason": f"trace event {index} should have used infeasible action",
                "label": label,
                "box": format_box(current),
            }

        value_interval = interval_eval_node(expr_node, current)
        gap_interval = relation_gap_interval(value_interval, relation, threshold)
        upper = gap_interval.hi if gap_interval.is_finite() else float("inf")

        if action == "certified":
            if not interval_certificate_condition(upper, relation):
                return {
                    "status": "trace_reject",
                    "reason": f"trace event {index} certified a box whose replayed interval upper bound is not negative",
                    "label": label,
                    "box": format_box(current),
                    "gap_interval": format_interval(gap_interval),
                    "upper": upper,
                }
            certified += 1
            continue

        if action == "split":
            widest = max(interval.width for interval in current.values())
            if widest <= min_width:
                return {
                    "status": "trace_reject",
                    "reason": f"trace event {index} split a box at or below min_width",
                    "label": label,
                    "box": format_box(current),
                    "widest": widest,
                    "min_width": min_width,
                }
            left, right, split_variable, midpoint = split_box_with_meta(current)
            children = event.get("children")
            if not isinstance(children, list) or len(children) != 2:
                return {"status": "trace_reject", "reason": f"trace event {index} split children must be a two-item list", "label": label}
            try:
                left_event = parse_trace_box(children[0], variables)
                right_event = parse_trace_box(children[1], variables)
            except Exception as exc:
                return {"status": "trace_reject", "reason": f"trace event {index} has invalid split children: {exc}", "label": label}
            if not boxes_match(left, left_event, variables) or not boxes_match(right, right_event, variables):
                return {
                    "status": "trace_reject",
                    "reason": f"trace event {index} split children do not match deterministic widest-box bisection",
                    "label": label,
                    "expected_children": [format_box(left), format_box(right)],
                    "actual_children": [format_box(left_event), format_box(right_event)],
                }
            if str(event.get("split_variable", split_variable)) != split_variable:
                return {
                    "status": "trace_reject",
                    "reason": f"trace event {index} split variable does not match replay",
                    "label": label,
                    "expected_split_variable": split_variable,
                    "actual_split_variable": event.get("split_variable"),
                }
            if not math.isclose(float(event.get("midpoint", midpoint)), midpoint, rel_tol=0.0, abs_tol=1e-12):
                return {
                    "status": "trace_reject",
                    "reason": f"trace event {index} split midpoint does not match replay",
                    "label": label,
                    "expected_midpoint": midpoint,
                    "actual_midpoint": event.get("midpoint"),
                }
            stack.append(right)
            stack.append(left)
            splits += 1
            continue

        return {"status": "trace_reject", "reason": f"trace event {index} has unsupported action {action!r}", "label": label}

    if stack:
        return {
            "status": "trace_reject",
            "reason": "trace ended with uncovered pending boxes",
            "label": label,
            "remaining_boxes": len(stack),
        }
    return {
        "status": "trace_verified",
        "reason": "trace replay covered the initial domain with certified or infeasible boxes",
        "label": label,
        "events": len(trace_events),
        "certified_boxes": certified,
        "infeasible_boxes": infeasible,
        "split_boxes": splits,
        "trace_hash": canonical_json_hash(trace_events),
    }


def run_interval_region_spec(spec: dict[str, Any]) -> dict[str, Any]:
    variables = normalize_variables(spec.get("variables"))
    boxes = normalize_region_boxes(spec.get("domain") or {}, variables)
    constraints = normalize_constraints(spec.get("constraints"))
    inequalities = normalize_inequalities(spec.get("inequalities"))
    max_iterations = max(10, int(spec.get("max_iterations", 20000)))
    min_width = abs(float(spec.get("min_width", 1e-5)))

    details = [
        verify_inequality_interval(
            inequality,
            variables,
            boxes,
            constraints,
            max_iterations=max_iterations,
            min_width=min_width,
        )
        for inequality in inequalities
    ]
    replay_reports = []
    for inequality, detail in zip(inequalities, details, strict=True):
        trace = detail.get("certificate_trace") or []
        if detail.get("status") == "pass":
            replay = replay_interval_certificate_trace(
                inequality,
                variables,
                boxes,
                constraints,
                trace,
                min_width=min_width,
            )
            detail["certificate_replay"] = replay
            if replay.get("status") == "trace_verified":
                detail["certificate_trace_hash"] = replay.get("trace_hash")
            else:
                detail["status"] = "inconclusive"
                detail["reason"] = f"certificate trace replay failed: {replay.get('reason', '[unknown]')}"
            replay_reports.append(replay)
        elif trace:
            detail["certificate_trace_hash"] = canonical_json_hash(trace)
    statuses = [item.get("status") for item in details]
    if any(status == "fail" for status in statuses):
        status = "verified_fail"
        summary = "interval region verifier found a feasible counterexample"
    elif statuses and all(status == "pass" for status in statuses):
        status = "verified_pass"
        summary = "all region inequalities were certified by replayed interval certificate traces"
    else:
        status = "inconclusive"
        summary = "interval region verifier could not certify all inequalities"

    trace_bundle = {
        "trace_version": "interval_region_trace_v1",
        "spec_hash": canonical_json_hash(
            {
                "mode": "interval_region",
                "variables": variables,
                "domain": spec.get("domain") or {},
                "constraints": constraints,
                "inequalities": inequalities,
                "min_width": min_width,
                "max_iterations": max_iterations,
            }
        ),
        "inequality_traces": [
            {
                "label": detail.get("label"),
                "status": detail.get("status"),
                "trace_hash": detail.get("certificate_trace_hash"),
                "replay": detail.get("certificate_replay"),
                "events": detail.get("certificate_trace_events", len(detail.get("certificate_trace") or [])),
                "trace": detail.get("certificate_trace") or [],
            }
            for detail in details
        ],
    }
    trace_bundle["bundle_hash"] = canonical_json_hash(trace_bundle)

    return {
        "status": status,
        "mode": "interval_region",
        "summary": summary,
        "details": details,
        "certificate_trace": trace_bundle,
        "certificate_trace_hash": trace_bundle["bundle_hash"],
        "certificate_replay_status": "trace_verified" if status == "verified_pass" else "not_applicable",
        "replay_reports": replay_reports,
        "notes": str(spec.get("notes", "")).strip(),
    }


def run_region_spec(spec: dict[str, Any]) -> dict[str, Any]:
    mode = str(spec.get("mode", "")).strip().lower()
    if mode == "numeric_region":
        return run_numeric_region_spec(spec)
    if mode == "interval_region":
        return run_interval_region_spec(spec)
    raise ValueError(f"unsupported region mode: {mode}")


def render_region_report(spec: dict[str, Any], report: dict[str, Any]) -> str:
    lines = [
        "## Verification Report",
        f"- Requested Mode: {spec.get('mode', 'unknown')}",
        f"- Executed Mode: {report.get('mode', 'unknown')}",
        f"- Status: {report.get('status', 'unknown')}",
        f"- Summary: {report.get('summary', '[missing]')}",
    ]
    if report.get("certificate_trace_hash"):
        lines.append(f"- Certificate Trace Hash: {report.get('certificate_trace_hash')}")
    if report.get("certificate_replay_status"):
        lines.append(f"- Certificate Replay Status: {report.get('certificate_replay_status')}")
    notes = str(report.get("notes", "")).strip()
    if notes:
        lines.append(f"- Notes: {notes}")
    counterexample = report.get("counterexample")
    if counterexample:
        lines.extend(["", "### Counterexample"])
        lines.append(json.dumps(counterexample, ensure_ascii=False, indent=2))
    details = report.get("details") or []
    if details:
        lines.extend(["", "### Region Checks"])
        for item in details:
            lines.append(
                f"- {item.get('label', 'ineq')}: status={item.get('status', 'unknown')}; "
                f"reason={item.get('reason', '[none]')}; "
                f"worst_upper={item.get('worst_upper', item.get('worst_gap'))}; "
                f"iterations={item.get('iterations', item.get('evaluations'))}; "
                f"closed_boxes={item.get('closed_boxes')}; "
                f"feasible_samples={item.get('feasible_samples')}; "
                f"trace_events={item.get('certificate_trace_events', len(item.get('certificate_trace') or []))}; "
                f"trace_hash={item.get('certificate_trace_hash', '[none]')}"
            )
            replay = item.get("certificate_replay") or {}
            if replay:
                lines.append(
                    f"  replay_status={replay.get('status')}; "
                    f"replay_reason={replay.get('reason')}; "
                    f"replay_events={replay.get('events')}"
                )
            if item.get("worst_box"):
                lines.append(f"  worst_box={item.get('worst_box')}")
            if item.get("worst_assignment"):
                lines.append(f"  worst_assignment={item.get('worst_assignment')}")
            if item.get("counterexample"):
                lines.append(f"  counterexample={item.get('counterexample')}")
    certificate_trace = report.get("certificate_trace")
    if certificate_trace:
        lines.extend(["", "### Formal Certificate Trace"])
        lines.append(
            "The JSON below is a replayable certificate: an auditor can start from the stated domain, "
            "follow each split, and check that every terminal box is either infeasible under the constraints "
            "or has a certified interval upper bound for the requested inequality."
        )
        lines.append("```json")
        lines.append(json.dumps(certificate_trace, ensure_ascii=False, indent=2, sort_keys=True))
        lines.append("```")
    return "\n".join(lines).strip()


def load_packet(path: Path | None) -> dict[str, Any]:
    if path is None:
        return json.loads(json.dumps(SECTION6_TEMPLATE))
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("proof packet must be a JSON object")
    return payload


def normalize_obligations(packet: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw_items = packet.get("obligations") or []
    if isinstance(raw_items, dict):
        raw_items = [{"id": key, **(value or {})} for key, value in raw_items.items()]
    if not isinstance(raw_items, list):
        raise ValueError("obligations must be a list or object")

    obligations: dict[str, dict[str, Any]] = {}
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        oid = str(item.get("id", "")).strip().upper()
        if oid:
            obligations[oid] = item
    return obligations


def obligation_template(oid: str) -> dict[str, Any]:
    normalized = str(oid or "").strip().upper()
    for item in SECTION6_TEMPLATE["obligations"]:
        if str(item.get("id", "")).strip().upper() == normalized:
            return copy.deepcopy(item)
    return {
        "id": normalized,
        "title": f"{normalized} obligation",
        "claim": "",
        "status": "missing",
        "summary": "",
        "manual_certificate": "",
        "tool_requests": [],
    }


def extract_verification_needs(certificate: Any) -> str:
    text = str(certificate or "")
    match = re.search(
        r"(?ims)^##\s*Verification Needs\s*$([\s\S]*?)(?=^##\s+|\Z)",
        text,
    )
    if not match:
        return ""
    return match.group(1).strip()


def verification_needs_closed(certificate: Any) -> bool:
    needs = re.sub(r"\s+", "", extract_verification_needs(certificate)).lower()
    return bool(needs) and needs in {"none", "[none]", "n/a", "na", "closed", "无", "无需验证"}


VERIFICATION_NEED_STOPWORDS = {
    "additional",
    "analytic",
    "certificate",
    "certify",
    "check",
    "checks",
    "condition",
    "conditions",
    "exact",
    "explicit",
    "lemma",
    "local",
    "needs",
    "proof",
    "prove",
    "required",
    "remaining",
    "section",
    "statement",
    "verify",
    "verification",
}


def verification_need_items(certificate: Any) -> list[str]:
    needs = extract_verification_needs(certificate)
    if not needs or verification_needs_closed(certificate):
        return []
    items = []
    for line in needs.splitlines():
        cleaned = re.sub(r"^\s*[-*]\s*", "", line).strip()
        if cleaned:
            items.append(cleaned)
    return items or [needs.strip()]


def _need_keywords(text: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]{4,}", str(text or "").lower())
    keywords = []
    for token in tokens:
        if token in VERIFICATION_NEED_STOPWORDS or token in keywords:
            continue
        keywords.append(token)
    return keywords[:8]


def tool_coverage_issue(
    certificate: Any,
    raw_requests: list[dict[str, Any]],
    passing_tools: list[ToolRun],
) -> str:
    items = verification_need_items(certificate)
    if not items:
        return ""
    if not passing_tools:
        return "open Verification Needs require passing tool certificates"
    passing_ids = {tool.request_id for tool in passing_tools}
    coverage_chunks = []
    for request in raw_requests:
        if not isinstance(request, dict):
            continue
        request_id = str(request.get("request_id", "")).strip()
        if request_id and request_id not in passing_ids:
            continue
        coverage_chunks.append(json.dumps(request, ensure_ascii=False, sort_keys=True))
    for tool in passing_tools:
        coverage_chunks.extend([tool.request_id, tool.mode, tool.summary])
    coverage_text = "\n".join(coverage_chunks).lower()

    uncovered = []
    for item in items:
        keywords = _need_keywords(item)
        if not keywords:
            uncovered.append(item)
            continue
        hits = sum(1 for keyword in keywords if keyword in coverage_text)
        if hits == 0 or (len(keywords) >= 3 and hits < 2):
            uncovered.append(item)
    if not uncovered:
        return ""
    return (
        "open Verification Needs are not explicitly covered by passing tool certificates: "
        + "; ".join(uncovered[:3])
    )


def manual_certificate_issue(text: str) -> str:
    certificate = str(text or "").strip()
    if not certificate:
        return "manual_certificate is missing"
    lowered = certificate.lower()
    for marker in PLACEHOLDER_MARKERS:
        if marker in lowered:
            return f"manual_certificate still contains placeholder marker: {marker}"
    if len(certificate) < MIN_MANUAL_CERTIFICATE_CHARS:
        return (
            "manual_certificate is too short to be treated as analytic evidence "
            f"({len(certificate)} < {MIN_MANUAL_CERTIFICATE_CHARS} chars)"
        )
    return ""


def packet_preflight(packet: dict[str, Any], obligations: dict[str, dict[str, Any]]) -> list[str]:
    issues: list[str] = []
    claim_id = str(packet.get("claim_id", "")).strip()
    if not claim_id:
        issues.append("claim_id is missing")
    if claim_id.lower().startswith("toy"):
        issues.append("claim_id is marked as a toy/example packet")

    potential = packet.get("potential") or {}
    if not isinstance(potential, dict):
        issues.append("potential must be an object")
        potential = {}
    if not str(potential.get("form", "")).strip():
        issues.append("potential.form is missing")
    if not str(potential.get("definition", "")).strip():
        issues.append("potential.definition is missing")
    if not str(potential.get("eta", "")).strip():
        issues.append("potential.eta must be filled with the Section 6 coefficient/sign convention")
    else:
        chain = packet.get("constant_chain") or {}
        if isinstance(chain, dict) and str(chain.get("lambda", "")).strip():
            try:
                eta = parse_decimal(potential.get("eta"), "potential.eta")
                lam = parse_decimal(chain.get("lambda"), "constant_chain.lambda")
                base_threshold = Decimal(str(math.pi / 2.0)) + eta
                if lam <= base_threshold:
                    issues.append(
                        "base-case necessary condition failed for Phi_eta=eta*L_n: "
                        f"lambda must be > pi/2 + eta = {base_threshold}, got {lam}"
                    )
            except ValueError:
                issues.append("potential.eta and constant_chain.lambda must be numeric for the base-case sanity check")

    for oid in REQUIRED_OBLIGATIONS:
        obligation = obligations.get(oid) or {}
        if not obligation:
            issues.append(f"{oid} obligation is missing")
            continue
        if not str(obligation.get("title", "")).strip():
            issues.append(f"{oid}.title is missing")
        if not str(obligation.get("claim", "")).strip():
            issues.append(f"{oid}.claim is missing")
        if not str(obligation.get("summary", obligation.get("manual_certificate", ""))).strip():
            issues.append(f"{oid}.summary or manual_certificate is missing")
    return issues


def run_tool_request(raw_request: dict[str, Any], fallback_id: str) -> ToolRun:
    request_id = str(raw_request.get("request_id", "")).strip() or fallback_id
    tool_name = str(raw_request.get("tool_name", "verification")).strip().lower()
    if tool_name != "verification":
        return ToolRun(
            request_id=request_id,
            status="unavailable",
            mode="none",
            passing=False,
            summary=f"unsupported tool_name={tool_name}",
            rendered="",
        )

    spec = raw_request.get("spec") or {}
    if not isinstance(spec, dict):
        return ToolRun(
            request_id=request_id,
            status="tool_error",
            mode="unknown",
            passing=False,
            summary="spec must be a JSON object",
            rendered="",
        )

    try:
        requested_mode = str(spec.get("mode", "")).strip().lower()
        if requested_mode in REGION_MODES:
            payload = run_region_spec(spec)
            status = str(payload.get("status", "unknown")).strip() or "unknown"
            mode = str(payload.get("mode", requested_mode)).strip() or requested_mode
            rendered = render_region_report(spec, payload)
        else:
            report = run_verification_spec(spec)
            payload = report.to_dict() if hasattr(report, "to_dict") else dict(report or {})
            status = str(payload.get("status", "unknown")).strip() or "unknown"
            mode = str(payload.get("mode", spec.get("mode", "unknown"))).strip() or "unknown"
            rendered = render_verification_report(spec, report)
        return ToolRun(
            request_id=request_id,
            status=status,
            mode=mode,
            passing=status in PASSING_TOOL_STATUSES,
            summary=str(payload.get("summary", "")).strip(),
            rendered=rendered,
        )
    except Exception as exc:
        return ToolRun(
            request_id=request_id,
            status="tool_error",
            mode=str(spec.get("mode", "unknown")).strip() or "unknown",
            passing=False,
            summary=str(exc),
            rendered="",
        )


def run_obligation(oid: str, obligation: dict[str, Any] | None) -> ObligationRun:
    if not obligation:
        return ObligationRun(
            obligation_id=oid,
            status="missing",
            complete=False,
            reason="obligation is absent from the proof packet",
            tools=[],
        )

    status = str(obligation.get("status", "")).strip().lower() or "missing"
    manual_certificate = str(obligation.get("manual_certificate", "")).strip()
    raw_requests = obligation.get("tool_requests") or []
    if not isinstance(raw_requests, list):
        raw_requests = []

    tools = [
        run_tool_request(request, fallback_id=f"{oid}_tool_{index}")
        for index, request in enumerate(raw_requests, start=1)
        if isinstance(request, dict)
    ]
    failed_tools = [tool for tool in tools if not tool.passing]
    status_closed = status in PROVED_STATUSES
    passing_tools = [tool for tool in tools if tool.passing]
    manual_issue = manual_certificate_issue(manual_certificate)
    manual_needs_closed = verification_needs_closed(manual_certificate)
    manual_closed_evidence = bool(manual_certificate and not manual_issue and manual_needs_closed)
    tool_coverage = ""
    if (
        oid in TOOL_AUGMENTED_CERTIFICATE_ALLOWED
        and manual_certificate
        and not manual_needs_closed
        and passing_tools
    ):
        tool_coverage = tool_coverage_issue(manual_certificate, raw_requests, passing_tools)
    has_evidence = bool(manual_closed_evidence or passing_tools)

    if failed_tools:
        return ObligationRun(
            obligation_id=oid,
            status=status,
            complete=False,
            reason="one or more tool requests did not return verified_pass",
            tools=tools,
        )
    if oid in MANUAL_CERTIFICATE_REQUIRED and manual_issue:
        return ObligationRun(
            obligation_id=oid,
            status=status,
            complete=False,
            reason=manual_issue,
            tools=tools,
        )
    if oid in ANALYTIC_CERTIFICATE_REQUIRED and not manual_needs_closed:
        return ObligationRun(
            obligation_id=oid,
            status=status,
            complete=False,
            reason="manual_certificate Verification Needs must be exactly None for an analytic obligation to close",
            tools=tools,
        )
    if oid in MANUAL_CERTIFICATE_REQUIRED and not manual_needs_closed and not passing_tools:
        return ObligationRun(
            obligation_id=oid,
            status=status,
            complete=False,
            reason=(
                "manual_certificate has open Verification Needs and no passing tool certificate "
                "covering the relaxed auxiliary checks"
            ),
            tools=tools,
        )
    if manual_certificate and manual_issue and not passing_tools:
        return ObligationRun(
            obligation_id=oid,
            status=status,
            complete=False,
            reason=manual_issue,
            tools=tools,
        )
    if manual_certificate and not manual_needs_closed and not passing_tools:
        return ObligationRun(
            obligation_id=oid,
            status=status,
            complete=False,
            reason="manual_certificate has open Verification Needs and no passing tool certificate",
            tools=tools,
        )
    if tool_coverage:
        return ObligationRun(
            obligation_id=oid,
            status=status,
            complete=False,
            reason=tool_coverage,
            tools=tools,
        )
    if not has_evidence:
        return ObligationRun(
            obligation_id=oid,
            status=status,
            complete=False,
            reason="closed status has no manual_certificate or tool_requests evidence",
            tools=tools,
        )
    if not status_closed:
        return ObligationRun(
            obligation_id=oid,
            status="verified",
            complete=True,
            reason=f"closed by local verifier; model status was {status!r}",
            tools=tools,
        )
    return ObligationRun(
        obligation_id=oid,
        status=status,
        complete=True,
        reason="closed",
        tools=tools,
    )


def repair_failed_tool_requests(
    packet: dict[str, Any],
    *,
    context: str,
    print_stream: bool,
    stream: bool | None,
    max_rounds: int = 1,
) -> tuple[dict[str, Any], list[str]]:
    if max_rounds <= 0 or not _active_api_key():
        return packet, []

    repaired_packet = copy.deepcopy(packet)
    obligations = normalize_obligations(repaired_packet)
    notes: list[str] = []
    repairable_statuses = {"tool_error", "unavailable", "inconclusive", "analysis_complete"}

    for oid in REQUIRED_OBLIGATIONS:
        obligation = obligations.get(oid)
        if not obligation:
            continue
        raw_requests = obligation.get("tool_requests") or []
        if not isinstance(raw_requests, list) or not raw_requests:
            continue

        for round_index in range(1, max_rounds + 1):
            runs = [
                run_tool_request(request, fallback_id=f"{oid}_tool_{index}")
                for index, request in enumerate(raw_requests, start=1)
                if isinstance(request, dict)
            ]
            failed = [item for item in runs if not item.passing]
            repairable = [item for item in failed if item.status in repairable_statuses]
            if not repairable:
                break

            agent = _llm_json_agent(
                "Tool Request Repairer",
                (
                    "You repair proof_agent verification tool requests. "
                    "Keep the mathematical proof draft unchanged and fix only executable JSON specs. "
                    "Do not invent a new theorem; target the displayed Verification Needs."
                ),
                temperature=0.05,
            )
            feedback = [
                {
                    "request_id": item.request_id,
                    "status": item.status,
                    "mode": item.mode,
                    "summary": item.summary,
                }
                for item in runs
            ]
            repair_context = extract_relevant_context(
                context,
                oid,
                max_chars=min(len(context), DEFAULT_REPAIR_CONTEXT_CHARS),
            )
            prompt = (
                f"Repair tool_requests for obligation {oid}. Return exactly one JSON object with field tool_requests.\n"
                "Do not change the proof text, status, title, claim, or summary. Only repair executable tool request JSON.\n\n"
                f"{section6_property_context(repaired_packet, oid)}\n\n"
                "Supported request shape:\n"
                f"{json.dumps(INTERVAL_REGION_TOOL_SCHEMA, ensure_ascii=False, indent=2)}\n\n"
                "Rules:\n"
                "1) Each request must contain request_id, tool_name, justification, spec.\n"
                "2) tool_name must be \"verification\".\n"
                "3) For interval_region, spec must contain mode, variables, domain, constraints, inequalities, max_iterations, min_width, notes.\n"
                "4) Do not output shorthand fields such as type, purpose, expression, verify outside spec.\n"
                "5) If the current proof text does not contain a concrete executable inequality, return an empty tool_requests array rather than inventing one.\n\n"
                "Current obligation:\n"
                f"{json.dumps(obligation, ensure_ascii=False, indent=2)}\n\n"
                "Tool execution feedback:\n"
                f"{json.dumps(feedback, ensure_ascii=False, indent=2)}\n\n"
                "Key literature context:\n"
                f"{repair_context}"
            )
            payload = _call_json_agent(
                agent,
                prompt,
                f"SECTION6_{oid}_TOOL_REPAIR",
                print_stream=print_stream,
                stream=stream,
            )
            candidate_requests = payload.get("tool_requests")
            if not isinstance(candidate_requests, list):
                notes.append(f"{oid}: tool repair round {round_index} returned no tool_requests array")
                break
            obligation["tool_requests"] = candidate_requests
            raw_requests = candidate_requests
            notes.append(f"{oid}: repaired tool_requests round {round_index}")

    repaired_packet["obligations"] = [
        obligations.get(oid, obligation_template(oid))
        for oid in REQUIRED_OBLIGATIONS
    ]
    return repaired_packet, notes


def constant_chain(packet: dict[str, Any]) -> tuple[bool, list[str]]:
    chain = packet.get("constant_chain") or {}
    if not isinstance(chain, dict):
        chain = {}

    messages: list[str] = []
    try:
        rho_target = parse_decimal(chain.get("rho_target", packet.get("rho_target", "1.98")), "rho_target")
        lam = parse_decimal(chain.get("lambda", packet.get("lambda")), "lambda")
        if str(chain.get("phi_lower_bound_B", "")).strip():
            lower_b = parse_decimal(chain.get("phi_lower_bound_B"), "phi_lower_bound_B")
            convention = f"Phi >= B*|P| with B={lower_b}"
        elif str(chain.get("phi_lower_bound_C", "")).strip():
            lower_c = parse_decimal(chain.get("phi_lower_bound_C"), "phi_lower_bound_C")
            lower_b = -lower_c
            convention = f"Phi >= -C*|P| with C={lower_c}; converted B={lower_b}"
        else:
            raise ValueError("phi_lower_bound_B or phi_lower_bound_C is required")
    except ValueError as exc:
        return False, [f"constant chain missing/invalid: {exc}"]

    denominator = Decimal(1) + lower_b
    if denominator <= 0:
        return False, [f"constant chain invalid: 1+B must be positive, got {denominator}"]

    rho_bound = lam / denominator
    passed = rho_bound <= rho_target
    messages.append(convention)
    messages.append(f"lambda={lam}")
    messages.append(f"rho_bound=lambda/(1+B)={rho_bound}")
    messages.append(f"rho_target={rho_target}")
    messages.append("constant_chain=PASS" if passed else "constant_chain=FAIL")
    return passed, messages


def build_report(packet: dict[str, Any]) -> tuple[bool, str]:
    obligations = normalize_obligations(packet)
    preflight_issues = packet_preflight(packet, obligations)
    obligation_runs = [run_obligation(oid, obligations.get(oid)) for oid in REQUIRED_OBLIGATIONS]
    constants_ok, constant_messages = constant_chain(packet)
    obligations_ok = all(item.complete for item in obligation_runs)
    toy_only = bool(packet.get("toy_only"))
    verified = obligations_ok and constants_ok and not preflight_issues and not toy_only

    lines = [
        "# Section 6 rho <= 1.98 Verification Report",
        "",
        f"Claim ID: {packet.get('claim_id', '[missing]')}",
        f"Potential: {((packet.get('potential') or {}).get('form') if isinstance(packet.get('potential'), dict) else packet.get('potential')) or '[missing]'}",
        f"Verdict: {'VERIFIED' if verified else 'NOT VERIFIED'}",
    ]
    if toy_only:
        lines.append("Toy Packet: true; this packet demonstrates format only and is never accepted as Section 6 evidence.")
    closed_ids = [item.obligation_id for item in obligation_runs if item.complete]
    open_ids = [item.obligation_id for item in obligation_runs if not item.complete]
    next_target = open_ids[0] if open_ids else "[none]"
    lines.extend(
        [
            "",
            "## Progress Summary",
            f"- constant_chain: {'PASS' if constants_ok else 'FAIL'}",
            f"- closed_obligations: {', '.join(closed_ids) if closed_ids else '[none]'}",
            f"- open_lemmas: {', '.join(open_ids) if open_ids else '[none]'}",
            f"- next_target: {next_target}",
        ]
    )
    lines.extend(["", "## Packet Preflight"])
    if preflight_issues:
        lines.extend(f"- FAIL: {issue}" for issue in preflight_issues)
    else:
        lines.append("- PASS")
    lines.extend(["", "## Constant Chain"])
    lines.extend(f"- {message}" for message in constant_messages)
    lines.extend(["", "## Obligations"])

    for item in obligation_runs:
        marker = "PASS" if item.complete else "FAIL"
        lines.append(f"- {item.obligation_id}: {marker}; status={item.status}; reason={item.reason}")
        for tool in item.tools:
            tool_marker = "PASS" if tool.passing else "FAIL"
            lines.append(
                f"  - tool {tool.request_id}: {tool_marker}; "
                f"mode={tool.mode}; status={tool.status}; summary={tool.summary or '[none]'}"
            )

    rendered_tools = [
        (item.obligation_id, tool)
        for item in obligation_runs
        for tool in item.tools
        if tool.rendered
    ]
    if rendered_tools:
        lines.extend(["", "## Tool Details"])
        for oid, tool in rendered_tools:
            lines.extend(
                [
                    f"### {oid} / {tool.request_id}",
                    "",
                    tool.rendered,
                    "",
                ]
            )

    if not verified:
        lines.extend(
            [
                "",
                "## Next Required Evidence",
                "- Close every S1-S6 obligation according to the local verifier; model-written status is advisory.",
                "- Attach manual_certificate text with `## Verification Needs` exactly `None` for S1/S2 and any fully analytic obligations.",
                "- For S3/S4/S5/S6, attach executable verification tool_requests when explicit relaxed-region or geometric inequalities remain.",
                "- Fill constant_chain.lambda and phi_lower_bound_B or phi_lower_bound_C so lambda/(1+B) <= 1.98.",
                "- Fill potential.eta and each obligation's title/claim/summary fields.",
            ]
        )
    return verified, "\n".join(lines).rstrip() + "\n"


def record_section6_memory_checkpoint(packet: dict[str, Any], verified: bool, notes: list[str] | None = None) -> None:
    try:
        obligations = normalize_obligations(packet)
        obligation_runs = {
            oid: run_obligation(oid, obligations.get(oid))
            for oid in REQUIRED_OBLIGATIONS
        }
        payload = {
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "claim_id": str(packet.get("claim_id", "")).strip(),
            "potential_form": str(((packet.get("potential") or {}).get("form", "")) if isinstance(packet.get("potential"), dict) else packet.get("potential", "")).strip(),
            "verdict": "VERIFIED" if verified else "NOT VERIFIED",
            "closed_obligations": [oid for oid, run in obligation_runs.items() if run.complete],
            "open_obligations": [oid for oid, run in obligation_runs.items() if not run.complete],
            "obligations": {
                oid: {
                    "status": run.status,
                    "complete": run.complete,
                    "reason": run.reason,
                    "tool_statuses": [
                        {
                            "request_id": tool.request_id,
                            "mode": tool.mode,
                            "status": tool.status,
                            "passing": tool.passing,
                            "summary": tool.summary,
                        }
                        for tool in run.tools
                    ],
                }
                for oid, run in obligation_runs.items()
            },
            "notes": list(notes or [])[-20:],
        }
        DEFAULT_SECTION6_MEMORY.parent.mkdir(parents=True, exist_ok=True)
        with DEFAULT_SECTION6_MEMORY.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception:
        return


def load_development_packet(spec_path: Path | None) -> tuple[dict[str, Any], str]:
    if spec_path is not None:
        return load_packet(spec_path), str(spec_path)
    if DEFAULT_AUTO_PACKET.exists():
        return load_packet(DEFAULT_AUTO_PACKET), str(DEFAULT_AUTO_PACKET)
    return load_packet(None), "template"


def replace_obligation(packet: dict[str, Any], obligation: dict[str, Any]) -> dict[str, Any]:
    updated = copy.deepcopy(packet)
    oid = str(obligation.get("id", "")).strip().upper()
    if not oid:
        raise ValueError("developed obligation is missing id")
    obligations = normalize_obligations(updated)
    obligations[oid] = obligation
    updated["obligations"] = [
        obligations.get(required, obligation_template(required))
        for required in REQUIRED_OBLIGATIONS
    ]
    return updated


def open_obligation_ids(packet: dict[str, Any]) -> list[str]:
    obligations = normalize_obligations(packet)
    return [
        oid
        for oid in REQUIRED_OBLIGATIONS
        if not run_obligation(oid, obligations.get(oid)).complete
    ]


def summarize_section6_exploration(
    packet: dict[str, Any],
    oid: str,
    *,
    round_index: int,
    output_path: Path = DEFAULT_SECTION6_EXPLORATION_SUMMARY,
    print_stream: bool,
    stream: bool | None,
) -> dict[str, Any]:
    oid = str(oid or "").strip().upper()
    obligations = normalize_obligations(packet)
    current = obligations.get(oid) or {}
    current_run = run_obligation(oid, current)
    review = current.get("correctness_review") if isinstance(current, dict) else {}
    if not isinstance(review, dict):
        review = {}

    fallback_summary = {
        "key_facts": truncate_for_prompt(str(current.get("summary", "") or ""), 900),
        "failed_or_blocked": current_run.reason,
        "remaining_target": truncate_for_prompt(extract_verification_needs(str(current.get("manual_certificate", "") or "")) or current_run.reason, 900),
        "next_prompt_hint": "Continue from the remaining Verification Needs; avoid repeating already established reductions.",
        "should_continue": not current_run.complete,
    }

    summary = fallback_summary
    if _active_api_key():
        try:
            agent = _llm_json_agent("Exploration Summarizer", temperature=0.05)
            prompt = (
                f"Summarize the latest Section 6 exploration for obligation {oid}.\n"
                "Return exactly one JSON object with fields: key_facts, failed_or_blocked, remaining_target, next_prompt_hint, should_continue.\n\n"
                "Rules:\n"
                "1) Do not try to prove new mathematics. Summarize only what the latest draft and review established or rejected.\n"
                "2) Keep each string concise and useful for the next Proof Writer prompt.\n"
                "3) For S2, preserve any useful endpoint-compensation information about DeltaP+DeltaL and avoid re-listing generic history.\n"
                "4) `next_prompt_hint` should tell the next round what newest lemma to prove or refute. Do not make it merely 'write a blocked draft' unless a fresh proof/refutation attempt has already failed.\n"
                "5) Set should_continue=false only if the local verifier status is complete=true.\n\n"
                f"{section6_property_context(packet, oid)}\n\n"
                "Known continuation facts:\n"
                f"{section6_continuation_context(oid)}\n\n"
                "Local verifier status:\n"
                f"{json.dumps({'status': current_run.status, 'complete': current_run.complete, 'reason': current_run.reason, 'tools': [tool.__dict__ for tool in current_run.tools]}, ensure_ascii=False, indent=2)}\n\n"
                "Latest obligation draft:\n"
                f"{truncate_for_prompt(json.dumps(current, ensure_ascii=False, indent=2), 18000)}\n\n"
                "Latest correctness review:\n"
                f"{truncate_for_prompt(json.dumps(review, ensure_ascii=False, indent=2), 8000)}"
            )
            candidate = _call_json_agent(
                agent,
                prompt,
                "SECTION6_EXPLORATION_SUMMARY",
                print_stream=print_stream,
                stream=stream,
            )
            if isinstance(candidate, dict):
                summary = {
                    "key_facts": truncate_for_prompt(str(candidate.get("key_facts", "") or fallback_summary["key_facts"]), 1200),
                    "failed_or_blocked": truncate_for_prompt(str(candidate.get("failed_or_blocked", "") or fallback_summary["failed_or_blocked"]), 1200),
                    "remaining_target": truncate_for_prompt(str(candidate.get("remaining_target", "") or fallback_summary["remaining_target"]), 1200),
                    "next_prompt_hint": truncate_for_prompt(str(candidate.get("next_prompt_hint", "") or fallback_summary["next_prompt_hint"]), 1200),
                    "should_continue": bool(candidate.get("should_continue", fallback_summary["should_continue"])),
                }
        except Exception as exc:
            summary = dict(fallback_summary)
            summary["failed_or_blocked"] = truncate_for_prompt(
                f"{fallback_summary['failed_or_blocked']} [summarizer fallback: {type(exc).__name__}: {exc}]",
                1200,
            )

    payload = {
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "obligation_id": oid,
        "round": int(round_index),
        "status": current_run.status,
        "complete": current_run.complete,
        "reason": current_run.reason,
        "summary": summary,
    }
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        pass
    return payload


def develop_single_obligation(
    packet: dict[str, Any],
    oid: str,
    *,
    context: str,
    print_stream: bool,
    stream: bool | None,
    review_rounds: int = DEFAULT_CORRECTNESS_REVIEW_ROUNDS,
) -> dict[str, Any]:
    if not _active_api_key():
        raise RuntimeError("develop-obligation requires an LLM API key")
    oid = str(oid or "").strip().upper()
    if oid not in REQUIRED_OBLIGATIONS:
        raise ValueError(f"unsupported obligation id: {oid}")

    obligations = normalize_obligations(packet)
    current = obligations.get(oid) or obligation_template(oid)
    current_run = run_obligation(oid, current)
    target_context = extract_relevant_context(
        context,
        oid,
        max_chars=min(len(context), DEFAULT_OBLIGATION_CONTEXT_CHARS),
    )
    memory_context = build_section6_memory_context(packet, oid, max_chars=DEFAULT_MEMORY_CONTEXT_CHARS)
    agent = _llm_json_agent("Proof Writer")
    focus_prompt = section6_obligation_focus_prompt(oid)
    web_instruction = section6_web_search_instruction()
    prompt = (
        "Overall goal: verify Xia Section 6's rho <= 1.98 claim using the stated candidate potential.\n"
        f"Develop obligation {oid} for the Section 6 verification packet.\n"
        "Return exactly one JSON object with fields: id, title, claim, status, summary, manual_certificate, tool_requests.\n\n"
        "Core rules copied from proof_agent's proposition pipeline:\n"
        "1) Treat this as one local proposition, not the whole theorem.\n"
        "2) Try to close the smallest unresolved lemma in the current Verification Needs.\n"
        "3) If the proposition is closed, status must be proved/verified/pass/closed and `## Verification Needs` must be exactly `None`.\n"
        "4) If it is not closed, status must be missing/blocked and `## Verification Needs` must list the narrow remaining checks line by line.\n"
        "4.1) The model-written status is advisory only; the local verifier will recompute closure from the draft and tools.\n"
        "5) Do not replace the draft with broad skepticism. Either prove the local lemma, narrow it, or identify a precise fatal local obstruction.\n"
        "6) For S5, describe needed executable interval_region certificates in Verification Needs; Tool Requester runs separately after review.\n"
        "7) For S3/S6, include the analytic reduction first; an explicit remaining inequality may be left for a replayable tool certificate.\n"
        "8) For S2, prove the endpoint Upsilon estimate directly and do not rely on old Phi-monotonicity.\n"
        "9) For S6, focus on the extremal-chain terminal-penetration lower bound needed for B, not the already-finished constant arithmetic.\n"
        "10) Do not alter eta, lambda, B, rho_target, or any other obligation.\n\n"
        f"{focus_prompt + chr(10) + chr(10) if focus_prompt else ''}"
        f"{web_instruction + chr(10) + chr(10) if web_instruction else ''}"
        f"{section6_property_context(packet, oid)}\n\n"
        "Known continuation facts for this obligation:\n"
        f"{section6_continuation_context(oid)}\n\n"
        "Property learning memory:\n"
        f"{memory_context or '[none]'}\n\n"
        "Current property contract:\n"
        f"{section6_property_contract(packet, oid)}\n\n"
        "Dependent proposition summary inside the current candidate:\n"
        f"{section6_dependency_summary(packet, oid)}\n\n"
        f"Proof Writer skeleton:\n{SOURCE_STYLE_PROOF_SKELETON}\n\n"
        "Current target status from local verifier:\n"
        f"{json.dumps({'status': current_run.status, 'complete': current_run.complete, 'reason': current_run.reason, 'tools': [tool.__dict__ for tool in current_run.tools]}, ensure_ascii=False, indent=2)}\n\n"
        "Current obligation draft:\n"
        f"{json.dumps(current, ensure_ascii=False, indent=2)}\n\n"
        "Obligation template:\n"
        f"{json.dumps(obligation_template(oid), ensure_ascii=False, indent=2)}\n\n"
        "Key literature context:\n"
        f"{target_context}"
    )
    developed = write_obligation_with_review(
        agent,
        prompt,
        f"SECTION6_{oid}_DEVELOPED",
        packet,
        oid,
        context=context,
        print_stream=print_stream,
        stream=stream,
        max_review_rounds=review_rounds,
    )
    developed = request_obligation_tool_requests(
        packet,
        developed,
        oid,
        context=context,
        print_stream=print_stream,
        stream=stream,
    )
    return developed


@dataclass
class Section6AgentConfig:
    packet_out: Path = DEFAULT_AUTO_PACKET
    report_out: Path = DEFAULT_AUTO_REPORT
    log_out: Path | None = DEFAULT_AUTO_LOG
    print_stream: bool = True
    stream: bool | None = True
    context_chars: int = DEFAULT_CONTEXT_CHARS
    strategy: str = "split"
    audit: bool = True
    repair_tools: bool = True
    tool_repair_rounds: int = 1
    correctness_review_rounds: int = DEFAULT_CORRECTNESS_REVIEW_ROUNDS
    summarize_exploration: bool = False


class Section6ProofAgent:
    """Stateful proof-agent wrapper for the Section 6 verification workflow."""

    property_order = REQUIRED_OBLIGATIONS

    def __init__(self, config: Section6AgentConfig):
        self.config = config
        self.context = ""
        self.packet: dict[str, Any] | None = None
        self.source_packet = ""
        self.notes: list[str] = []

    def _header(self, title: str, extra: list[str] | None = None) -> list[str]:
        lines = [
            title,
            "",
            f"- provider={LLM_PROVIDER}",
            f"- model={MODEL_NAME}",
            f"- packet_out={self.config.packet_out}",
            f"- report_out={self.config.report_out}",
            f"- llm_retries={os.getenv('LLM_RETRY_MAX_ATTEMPTS', '5')}",
            f"- llm_retry_max_seconds={os.getenv('LLM_RETRY_MAX_SECONDS', '60')}",
        ]
        if self.config.log_out is not None:
            lines.append(f"- log_out={self.config.log_out}")
        if extra:
            lines.extend(extra)
        lines.append("")
        return lines

    def _ensure_context(self) -> str:
        if not self.context:
            self.context = build_auto_context(max_chars=self.config.context_chars)
            if not self.context:
                raise RuntimeError("no local paper/docs context found")
        return self.context

    def load_packet(self, spec_path: Path | None) -> dict[str, Any]:
        self.packet, self.source_packet = load_development_packet(spec_path)
        self.packet["proof_plan"] = normalize_section6_proof_plan(self.packet.get("proof_plan"))
        self.notes.append(f"source_packet={self.source_packet}")
        return self.packet

    def generate_packet(self) -> dict[str, Any]:
        self._ensure_context()
        self.packet = auto_generate_packet(
            self.context,
            print_stream=self.config.print_stream,
            stream=self.config.stream,
            strategy=self.config.strategy,
            review_rounds=self.config.correctness_review_rounds,
        )
        self.notes.append(f"generated_packet strategy={self.config.strategy}")
        self.repair_tool_requests()
        return self.packet

    def repair_tool_requests(self) -> None:
        if not self.config.repair_tools or self.packet is None:
            return
        self._ensure_context()
        self.packet, repair_notes = repair_failed_tool_requests(
            self.packet,
            context=self.context,
            print_stream=self.config.print_stream,
            stream=self.config.stream,
            max_rounds=self.config.tool_repair_rounds,
        )
        self.notes.extend(repair_notes)

    def develop_obligation(self, oid: str, rounds: int = 1) -> None:
        if self.packet is None:
            raise RuntimeError("no packet loaded")
        self._ensure_context()
        oid = str(oid or "").strip().upper()
        if oid not in self.property_order:
            raise ValueError(f"unsupported obligation id: {oid}")

        before = run_obligation(oid, normalize_obligations(self.packet).get(oid))
        self.notes.append(
            f"{oid}: before status={before.status} complete={before.complete} reason={before.reason}"
        )
        for round_index in range(1, max(1, int(rounds)) + 1):
            try:
                developed = develop_single_obligation(
                    self.packet,
                    oid,
                    context=self.context,
                    print_stream=self.config.print_stream,
                    stream=self.config.stream,
                    review_rounds=self.config.correctness_review_rounds,
                )
                self.packet = replace_obligation(self.packet, developed)
                self.repair_tool_requests()
            except Exception as exc:
                self.notes.append(
                    f"{oid} round {round_index}: develop_failed={type(exc).__name__}: {exc}"
                )
            after = run_obligation(oid, normalize_obligations(self.packet).get(oid))
            self.notes.append(
                f"{oid} round {round_index}: status={after.status} complete={after.complete} reason={after.reason}"
            )
            if self.config.summarize_exploration:
                summary_payload = summarize_section6_exploration(
                    self.packet,
                    oid,
                    round_index=round_index,
                    print_stream=self.config.print_stream,
                    stream=self.config.stream,
                )
                summary = summary_payload.get("summary") if isinstance(summary_payload, dict) else {}
                next_hint = ""
                if isinstance(summary, dict):
                    next_hint = truncate_for_prompt(str(summary.get("next_prompt_hint", "") or ""), 220)
                self.notes.append(
                    f"{oid} round {round_index}: exploration_summary_saved={DEFAULT_SECTION6_EXPLORATION_SUMMARY}"
                    + (f" next={next_hint}" if next_hint else "")
                )
            self.write_checkpoint(
                title=f"# Section 6 Proof Agent: {oid}",
                extra=[f"- stage=develop_obligation", f"- obligation={oid}", f"- round={round_index}"],
                audit=False,
            )
            if after.complete:
                break

    def develop_all_open(self, *, passes: int = 1, rounds_per_obligation: int = 1) -> None:
        if self.packet is None:
            raise RuntimeError("no packet loaded")
        for pass_index in range(1, max(1, int(passes)) + 1):
            open_ids = open_obligation_ids(self.packet)
            self.notes.append(
                f"pass {pass_index}: open={', '.join(open_ids) if open_ids else '[none]'}"
            )
            if not open_ids:
                break
            for oid in open_ids:
                self.develop_obligation(oid, rounds=rounds_per_obligation)

    def report_text(self, title: str, extra: list[str] | None = None, *, audit: bool = False) -> tuple[bool, str]:
        if self.packet is None:
            raise RuntimeError("no packet loaded")
        hard_verified, hard_report = build_report(self.packet)
        final_verified = hard_verified
        audit_section = ""
        if hard_verified and audit:
            audit_passed, audit_text = audit_verified_packet(
                self.packet,
                hard_report,
                print_stream=self.config.print_stream,
                stream=self.config.stream,
            )
            final_verified = audit_passed
            audit_section = (
                "\n## LLM Audit\n"
                f"- Status: {'PASS' if audit_passed else 'REJECT'}\n\n"
                "```json\n"
                f"{audit_text}\n"
                "```\n"
            )
            if not audit_passed:
                hard_report = hard_report.replace("Verdict: VERIFIED", "Verdict: NOT VERIFIED", 1)
        notes_section = ""
        if self.notes:
            notes_section = "\n## Agent Notes\n" + "\n".join(f"- {note}" for note in self.notes) + "\n"
        report = "\n".join(self._header(title, extra)).rstrip() + "\n\n" + hard_report.rstrip() + "\n" + notes_section + audit_section
        return final_verified, report

    def write_checkpoint(self, title: str, extra: list[str] | None = None, *, audit: bool = False) -> tuple[bool, str]:
        if self.packet is None:
            raise RuntimeError("no packet loaded")
        self.config.packet_out.parent.mkdir(parents=True, exist_ok=True)
        self.config.packet_out.write_text(json.dumps(self.packet, ensure_ascii=False, indent=2), encoding="utf-8")
        verified, report = self.report_text(title, extra, audit=audit)
        record_section6_memory_checkpoint(self.packet, verified, self.notes)
        self.config.report_out.parent.mkdir(parents=True, exist_ok=True)
        self.config.report_out.write_text(report, encoding="utf-8")
        return verified, report

    def failure_report(self, title: str, heading: str, exc: BaseException, extra: list[str] | None = None) -> str:
        if self.packet is not None:
            self.config.packet_out.parent.mkdir(parents=True, exist_ok=True)
            self.config.packet_out.write_text(json.dumps(self.packet, ensure_ascii=False, indent=2), encoding="utf-8")
            record_section6_memory_checkpoint(self.packet, False, self.notes + [f"{heading}: {exc}"])
        report = "\n".join(
            self._header(title, extra)
            + [
                "Verdict: NOT VERIFIED",
                "",
                f"## {heading}",
                f"- {exc}",
            ]
        ).rstrip() + "\n"
        self.config.report_out.parent.mkdir(parents=True, exist_ok=True)
        self.config.report_out.write_text(report, encoding="utf-8")
        return report

    def execute(
        self,
        *,
        spec_path: Path | None = None,
        auto: bool = False,
        develop_obligation_id: str | None = None,
        develop_all_open: bool = False,
        passes: int = 1,
        rounds: int = 1,
    ) -> tuple[bool, str]:
        title = "# Section 6 Proof Agent"
        extra = [
            f"- auto={1 if auto else 0}",
            f"- develop_all_open={1 if develop_all_open else 0}",
            f"- develop_obligation={develop_obligation_id or '[none]'}",
            f"- strategy={self.config.strategy}",
            f"- summarize_exploration={1 if self.config.summarize_exploration else 0}",
        ]
        try:
            if auto:
                self.generate_packet()
            else:
                self.load_packet(spec_path)

            if develop_obligation_id:
                self.develop_obligation(develop_obligation_id, rounds=rounds)
            if develop_all_open:
                self.develop_all_open(passes=passes, rounds_per_obligation=rounds)
            return self.write_checkpoint(title, extra, audit=self.config.audit)
        except Exception as exc:
            return False, self.failure_report(title, "Agent Failure", exc, extra)


def run_self_test() -> tuple[bool, str]:
    checks = []

    checks.append(
        (
            "proof prompt context uses no external Markdown design notes",
            CONTEXT_FILES == (),
        )
    )

    verified, _ = build_report(self_test_packet(numeric_region_only=False))
    checks.append(("interval_region can close an obligation", verified is True))

    hybrid_packet = self_test_packet(numeric_region_only=False)
    hybrid_need_certificate = (
        "## Assumptions\n"
        "This is an internal relaxed-hybrid self-test, not Section 6 evidence.\n\n"
        "## Claim\n"
        "The S3-style obligation may combine analytic reduction text with a replayable tool certificate.\n\n"
        "## Derivation\n"
        "The derivation intentionally reduces the only remaining check to the same explicit interval-region inequality used by the self-test tool request.\n\n"
        "## Boundary Cases\n"
        "There are no mathematical boundary cases in this internal format test.\n\n"
        "## Verification Needs\n"
        "- certify self_test_x_sq_lt_2 interval_region\n\n"
        "## Conclusion\n"
        "The manual part is intentionally open, and must be covered by the passing tool certificate."
    )
    hybrid_packet["obligations"][2]["manual_certificate"] = hybrid_need_certificate
    hybrid_packet["obligations"][2]["tool_requests"] = copy.deepcopy(hybrid_packet["obligations"][4]["tool_requests"])
    verified, _ = build_report(hybrid_packet)
    checks.append(("S3 hybrid manual-plus-interval certificate can close", verified is True))

    verified, _ = build_report(self_test_packet(numeric_region_only=True))
    checks.append(("numeric_region search cannot close an obligation", verified is False))

    verified, _ = build_report(SECTION6_TEMPLATE)
    checks.append(("empty Section 6 template is not verified", verified is False))

    lines = ["# verify_section6_198 self-test", ""]
    all_passed = True
    for label, passed in checks:
        all_passed = all_passed and passed
        lines.append(f"- {'PASS' if passed else 'FAIL'}: {label}")
    return all_passed, "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--spec", type=Path, help="JSON proof packet to verify")
    parser.add_argument("--template", action="store_true", help="print a JSON proof-packet template")
    parser.add_argument("--example", action="store_true", help="print a toy proof packet demonstrating the accepted format")
    parser.add_argument("--self-test", action="store_true", help="run internal verifier sanity checks")
    parser.add_argument("-a", "--auto", action="store_true", help="auto-generate a Section 6 proof packet with the configured LLM, then verify it; this is also the no-argument default")
    parser.add_argument("-f", "--full", action="store_true", help="auto-generate a fresh packet and then develop every open S-obligation")
    parser.add_argument("--dev", "--develop-all-open", dest="develop_all_open", action="store_true", help="automatically develop every currently open S-obligation; combine with --auto to generate first")
    parser.add_argument("--passes", "--develop-passes", dest="develop_passes", type=int, default=1, help="passes over all open obligations for --dev")
    parser.add_argument("--do", "--develop-obligation", dest="develop_obligation", choices=list(REQUIRED_OBLIGATIONS), help="develop only one S-obligation from an existing packet")
    parser.add_argument("--rounds", "--develop-rounds", dest="develop_rounds", type=int, default=1, help="proof-writer rounds per obligation for development modes")
    parser.add_argument("--no-audit", action="store_true", help="skip the final LLM audit gate in --auto mode")
    parser.add_argument("--no-tool-repair", action="store_true", help="disable LLM repair of malformed or inconclusive tool requests")
    parser.add_argument("--tool-repair-rounds", type=int, default=1, help="tool-request repair rounds for --auto or --develop-obligation")
    parser.add_argument("--review-rounds", type=int, default=DEFAULT_CORRECTNESS_REVIEW_ROUNDS, help="Correctness Checker review/rewrite rounds after each proof draft")
    parser.add_argument("--summarize-exploration", action="store_true", help="after each development round, summarize the draft/review into a compact JSONL memory used by later rounds")
    parser.add_argument("--quiet", "--quiet-llm", dest="quiet_llm", action="store_true", help="do not print LLM tokens while generating/auditing the packet")
    parser.add_argument("--stream-llm", dest="stream_llm", action="store_true", default=True, help="use streaming LLM calls; enabled by default")
    parser.add_argument("--no-stream", "--no-stream-llm", dest="stream_llm", action="store_false", help="disable streaming LLM calls")
    parser.add_argument("--context-chars", type=int, default=DEFAULT_CONTEXT_CHARS, help="maximum local paper/docs context chars passed to the proof-packet builder")
    parser.add_argument("--strategy", "--auto-strategy", dest="auto_strategy", choices=["split"], default="split", help="LLM proof-packet generation strategy for --auto")
    parser.add_argument("--packet-out", type=Path, default=DEFAULT_AUTO_PACKET, help="where --auto writes the generated proof packet")
    parser.add_argument("--report-out", type=Path, default=DEFAULT_AUTO_REPORT, help="where --auto writes the generated Markdown report")
    parser.add_argument("--log-out", type=Path, default=DEFAULT_AUTO_LOG, help="append terminal output and retry logs to this file")
    parser.add_argument("--no-log", action="store_true", help="disable run-output logging")
    parser.add_argument("--llm-retries", type=int, default=int(os.getenv("LLM_RETRY_MAX_ATTEMPTS", "12")), help="LLM HTTP retry attempts for this run")
    parser.add_argument("--llm-retry-base-seconds", type=float, default=None, help="override LLM_RETRY_BASE_SECONDS for this run")
    parser.add_argument("--llm-retry-max-seconds", type=float, default=None, help="override LLM_RETRY_MAX_SECONDS for this run")
    parser.add_argument("--out", type=Path, help="optional path to write the Markdown report")
    args = parser.parse_args(argv)

    os.environ["LLM_RETRY_MAX_ATTEMPTS"] = str(max(1, int(args.llm_retries)))
    if args.llm_retry_base_seconds is not None:
        os.environ["LLM_RETRY_BASE_SECONDS"] = str(max(0.1, float(args.llm_retry_base_seconds)))
    if args.llm_retry_max_seconds is not None:
        os.environ["LLM_RETRY_MAX_SECONDS"] = str(max(1.0, float(args.llm_retry_max_seconds)))
    log_handle = configure_output_log(args.log_out, enabled=not args.no_log)

    if args.template:
        print(json.dumps(SECTION6_TEMPLATE, ensure_ascii=False, indent=2))
        return 0
    if args.example:
        print(json.dumps(toy_example_packet(), ensure_ascii=False, indent=2))
        return 0
    if args.self_test:
        passed, report = run_self_test()
        print(report, end="")
        return 0 if passed else 1
    if args.full:
        args.auto = True
        args.develop_all_open = True

    if args.auto or args.develop_all_open or args.develop_obligation or args.spec is None:
        agent = Section6ProofAgent(
            Section6AgentConfig(
                packet_out=args.packet_out,
                report_out=args.report_out,
                log_out=None if args.no_log else args.log_out,
                print_stream=not args.quiet_llm,
                stream=True if args.stream_llm else False,
                context_chars=max(4000, int(args.context_chars)),
                strategy=args.auto_strategy,
                audit=not args.no_audit,
                repair_tools=not args.no_tool_repair,
                tool_repair_rounds=max(0, int(args.tool_repair_rounds)),
                correctness_review_rounds=max(1, int(args.review_rounds)),
                summarize_exploration=bool(args.summarize_exploration),
            )
        )
        should_auto = bool(args.auto or (args.spec is None and not args.develop_all_open and not args.develop_obligation))
        verified, report = agent.execute(
            spec_path=args.spec,
            auto=should_auto,
            develop_obligation_id=args.develop_obligation,
            develop_all_open=bool(args.develop_all_open),
            passes=max(1, int(args.develop_passes)),
            rounds=max(1, int(args.develop_rounds)),
        )
        print(report, end="")
        return 0 if verified else 1

    packet = load_packet(args.spec)
    verified, report = build_report(packet)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
    else:
        print(report, end="")
    return 0 if verified else 1


if __name__ == "__main__":
    raise SystemExit(main())
