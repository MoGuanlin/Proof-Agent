"""Obligation gate + S2 contract for Phase 1.

Zero-copy rewrite of the verdict semantics from the archived strict script.
The verdict logic is intentionally narrower than the strict era: Phase 1
targets S2 only, which is in ANALYTIC_CERTIFICATE_REQUIRED, so a closing
certificate must be a manual proof with `## Verification Needs` exactly None
and no pending tool_requests. Region-tool certificates (interval/numeric
region) will be re-introduced when S3+ are scoped in.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Gate constants (semantics frozen; mirror the strict script's verdict policy).
ANALYTIC_CERTIFICATE_REQUIRED = frozenset({"S1", "S2"})
MANUAL_CERTIFICATE_REQUIRED = frozenset({"S1", "S2", "S3", "S6"})
PROVED_STATUSES = frozenset({"proved", "verified", "pass", "closed"})

# Skeleton headings the writer must populate (kept verbatim from strict
# convention so the gate string-checks remain honest).
SKELETON_HEADINGS = (
    "## Assumptions",
    "## Claim",
    "## Derivation",
    "## Boundary Cases",
    "## Verification Needs",
    "## Conclusion",
)
MIN_MANUAL_CERTIFICATE_CHARS = 120
PLACEHOLDER_MARKERS = (
    "todo",
    "tbd",
    "to be filled",
    "fill in",
    "...",
    "<placeholder>",
)


# --- S2 contract (Phase 1 target) -------------------------------------------

S2_CONTRACT = {
    "id": "S2",
    "title": "Endpoint case of Lemma 2 with segment potential",
    "claim": (
        "For every chain O with n >= 2 and every endpoint x in {a_{n-1}, b_{n-1}}, "
        "Upsilon_O(u, x) = |P_O(u, x)| - lambda * |D_O(u, x)| + eta * L_n(u, x) < 0."
    ),
    "constants": {
        "lambda": "643/250",  # 2.572
        "eta": "1",
        "B_target": "3/10",
        "rho_upper": "1.978...",
    },
    "forbidden_shortcuts": [
        "Do NOT reduce to Phi_O <= Phi_{O_{1,n-1}} (the v-independent monotonicity step). "
        "It fails for the segment potential because adding the terminal disk can introduce "
        "L_n(u, x) > L_{n-1}(u, x) = 0 with no compensating decrease in |P| or |D|.",
        "Do NOT compress to four single-variable g_i(alpha) < 0 certificates; that compression "
        "depends on Phi being v-independent.",
    ],
    "must_treat_as_three_term_balance": True,
    "context_notes": [
        "L_n(u, x) is the chord length [u, x] cap O_n; treat as a *positive* contribution "
        "that must be absorbed by the slack in |P_O| - lambda * |D_O| at the two endpoint candidates.",
        "Endpoints are x = a_{n-1} (rightmost on previous disk) and x = b_{n-1} (leftmost on previous disk).",
        "x lies on partial O_{n-1}; the segment [u, x] crosses partial O_n at most twice; |D_O(u, x)| = |x - u| (Euclidean).",
    ],
}

OBLIGATION_CONTRACTS = {"S2": S2_CONTRACT}


def get_contract(oid: str) -> dict:
    key = str(oid or "").strip().upper()
    if key not in OBLIGATION_CONTRACTS:
        raise KeyError(
            f"obligation {key!r} has no contract registered in Phase 1 (only S2 is in scope)"
        )
    return OBLIGATION_CONTRACTS[key]


# --- Certificate inspection -------------------------------------------------

def _section_text(certificate: str, heading: str) -> str:
    """Return the body of a `## Heading` section in `certificate`, or ''."""
    if not isinstance(certificate, str):
        return ""
    pattern = re.compile(
        rf"^{re.escape(heading)}\s*\n(.*?)(?=^##\s|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(certificate)
    return (match.group(1).strip() if match else "")


def extract_verification_needs(certificate: str) -> str:
    return _section_text(certificate, "## Verification Needs")


def verification_needs_closed(certificate: str) -> bool:
    """True iff `## Verification Needs` is exactly the literal `None` (case-insensitive)."""
    body = extract_verification_needs(certificate)
    if not body:
        return False
    normalized = body.strip().rstrip(".").strip().lower()
    return normalized in {"none", "n/a", "na"}


def manual_certificate_issue(certificate: str) -> str:
    """Return '' if the certificate looks well-formed; else a one-line reason."""
    if not isinstance(certificate, str) or not certificate.strip():
        return "manual_certificate is empty"
    if len(certificate.strip()) < MIN_MANUAL_CERTIFICATE_CHARS:
        return f"manual_certificate too short (<{MIN_MANUAL_CERTIFICATE_CHARS} chars)"
    lower = certificate.lower()
    for marker in PLACEHOLDER_MARKERS:
        if marker in lower:
            # `...` is too common in math notation (e.g. a_1, ..., a_n); allow it
            if marker == "...":
                continue
            return f"manual_certificate contains placeholder marker {marker!r}"
    missing = [h for h in SKELETON_HEADINGS if h not in certificate]
    if missing:
        return f"manual_certificate missing skeleton headings: {', '.join(missing)}"
    return ""


# --- Gate verdict -----------------------------------------------------------

@dataclass
class GateVerdict:
    oid: str
    closed: bool
    reason: str
    status: str = ""
    needs_closed: bool = False
    has_passing_tools: bool = False
    tool_results: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "oid": self.oid,
            "closed": self.closed,
            "reason": self.reason,
            "status": self.status,
            "needs_closed": self.needs_closed,
            "has_passing_tools": self.has_passing_tools,
            "tool_results": list(self.tool_results),
        }


def gate_obligation(oid: str, draft: dict[str, Any] | None) -> GateVerdict:
    """Decide whether a finished obligation draft passes the closure gate.

    Mirrors the strict script's verdict semantics for Phase 1's analytic-only
    track: certificate must be well-formed, Verification Needs must be exactly
    None, and the model-claimed status must lie in PROVED_STATUSES (or we
    upgrade it ourselves if the evidence is solid).
    """
    oid = str(oid or "").strip().upper()
    if not draft:
        return GateVerdict(oid=oid, closed=False, reason="draft is missing")

    status = str(draft.get("status", "")).strip().lower() or "missing"
    manual_certificate = str(draft.get("manual_certificate", "") or "").strip()
    raw_requests = draft.get("tool_requests") or []
    if not isinstance(raw_requests, list):
        raw_requests = []

    needs_closed = verification_needs_closed(manual_certificate)
    issue = manual_certificate_issue(manual_certificate)

    if oid in MANUAL_CERTIFICATE_REQUIRED and issue:
        return GateVerdict(
            oid=oid, closed=False, reason=issue, status=status,
            needs_closed=needs_closed,
        )
    if oid in ANALYTIC_CERTIFICATE_REQUIRED and not needs_closed:
        return GateVerdict(
            oid=oid, closed=False, status=status, needs_closed=needs_closed,
            reason=(
                "manual_certificate `## Verification Needs` must be exactly `None` "
                "for an analytic obligation to close"
            ),
        )
    if raw_requests:
        # Phase 1 has no tool runner; tool_requests on an analytic obligation
        # are by definition unresolved evidence.
        return GateVerdict(
            oid=oid, closed=False, status=status, needs_closed=needs_closed,
            reason=(
                "Phase 1 only closes analytic obligations; pending tool_requests cannot be "
                "discharged yet"
            ),
        )

    return GateVerdict(
        oid=oid, closed=True, status=status, needs_closed=needs_closed,
        reason=("closed" if status in PROVED_STATUSES
                else f"closed by gate; model status was {status!r}"),
    )
