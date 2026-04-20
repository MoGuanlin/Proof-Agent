#!/usr/bin/env python3
"""Export candidate-memory snapshots into per-candidate Markdown reports.

Reads .cache/candidate_memory.sqlite (or another path via --db) and writes
one Markdown file per candidate covering: candidate metadata, proof plan,
each property's proposition plan + proofs, the terminal report, and the
post-terminal / planner decisions. An index.md links the per-candidate files.

Examples:
    python scripts/export_candidates.py
    python scripts/export_candidates.py --namespace candidate::33c6577604c0525d
    python scripts/export_candidates.py --candidate C1_symmetric_coefficients
    python scripts/export_candidates.py --include-history --all-namespaces
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_DB = Path(".cache/candidate_memory.sqlite")
DEFAULT_OUT = Path("artifacts/reports/candidates")

SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize(name: str) -> str:
    return SAFE_NAME.sub("_", name).strip("_") or "unnamed"


def short_ns(ns: str) -> str:
    return sanitize(ns.replace("::", "_"))


def load_json(value: Any) -> Any:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


def fence_json(value: Any) -> str:
    parsed = load_json(value)
    if parsed is None:
        return "_(empty)_"
    if isinstance(parsed, str):
        return parsed
    return "```json\n" + json.dumps(parsed, ensure_ascii=False, indent=2) + "\n```"


def md_quote(text: str) -> str:
    return "\n".join(("> " + line) if line else ">" for line in text.splitlines())


def bullet_list(items: list[Any]) -> str:
    if not items:
        return "_(none)_"
    out = []
    for item in items:
        if isinstance(item, (dict, list)):
            out.append("- " + json.dumps(item, ensure_ascii=False))
        else:
            out.append(f"- {item}")
    return "\n".join(out)


def fetch_candidates(
    conn: sqlite3.Connection, namespace: str | None
) -> list[sqlite3.Row]:
    sql = (
        "SELECT memory_namespace, candidate_id, snapshot_id, status "
        "FROM candidate_latest"
    )
    params: tuple = ()
    if namespace:
        sql += " WHERE memory_namespace = ?"
        params = (namespace,)
    sql += " ORDER BY memory_namespace, candidate_id"
    return list(conn.execute(sql, params))


def fetch_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM candidate_snapshots WHERE snapshot_id = ?",
        (snapshot_id,),
    ).fetchone()


def fetch_artifacts(conn: sqlite3.Connection, snapshot_id: int) -> dict[str, str]:
    rows = conn.execute(
        "SELECT artifact_key, content FROM artifacts WHERE snapshot_id = ?",
        (snapshot_id,),
    )
    return {row["artifact_key"]: row["content"] for row in rows}


def fetch_property_states(
    conn: sqlite3.Connection, snapshot_id: int
) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT property_name, status, note, artifact_key "
            "FROM property_states WHERE snapshot_id = ? ORDER BY property_name",
            (snapshot_id,),
        )
    )


def fetch_proposition_states(
    conn: sqlite3.Connection, snapshot_id: int
) -> dict[str, list[sqlite3.Row]]:
    rows = conn.execute(
        "SELECT property_name, proposition_id, position, title, claim, "
        "dependencies_json, verification_focus, requires_tool, status, note, "
        "artifact_key, signature "
        "FROM proposition_states WHERE snapshot_id = ? "
        "ORDER BY property_name, position",
        (snapshot_id,),
    )
    grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        grouped[row["property_name"]].append(row)
    return grouped


def fetch_tool_requests(
    conn: sqlite3.Connection, snapshot_id: int
) -> dict[tuple[str, str], list[sqlite3.Row]]:
    rows = conn.execute(
        "SELECT property_name, proposition_id, request_id, position, tool_name, "
        "justification, spec_json, report_json, report_status, artifact_key "
        "FROM tool_request_states WHERE snapshot_id = ? "
        "ORDER BY property_name, proposition_id, position",
        (snapshot_id,),
    )
    grouped: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        grouped[(row["property_name"], row["proposition_id"])].append(row)
    return grouped


def fetch_history(
    conn: sqlite3.Connection, namespace: str, candidate_id: str
) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT snapshot_id, saved_at, status FROM candidate_snapshots "
            "WHERE memory_namespace = ? AND candidate_id = ? "
            "ORDER BY snapshot_id",
            (namespace, candidate_id),
        )
    )


def render_proof_plan(content: str | None) -> str:
    if not content:
        return "_(no proof plan recorded)_"
    plan = load_json(content)
    if not isinstance(plan, dict):
        return "```\n" + str(content) + "\n```"
    parts: list[str] = []
    if plan.get("reusable_props"):
        parts.append("**Reusable propositions**\n\n" + bullet_list(plan["reusable_props"]))
    if plan.get("needs_redo"):
        parts.append("**Needs redo**\n\n" + bullet_list(plan["needs_redo"]))
    if plan.get("priority"):
        parts.append("**Priority**\n\n" + bullet_list(plan["priority"]))
    obvious = plan.get("obvious_failure")
    if isinstance(obvious, dict):
        status = obvious.get("status")
        prop = obvious.get("property")
        reason = obvious.get("reason", "")
        parts.append(
            f"**Obvious failure?** `{status}`"
            + (f" (property `{prop}`)" if prop else "")
            + (f"\n\n{reason}" if reason else "")
        )
    if plan.get("risk_notes"):
        parts.append("**Risk notes**\n\n" + bullet_list(plan["risk_notes"]))
    if plan.get("estimated_c"):
        parts.append("**Estimated C**\n\n" + fence_json(plan["estimated_c"]))
    leftover = {
        k: v
        for k, v in plan.items()
        if k
        not in {
            "reusable_props",
            "needs_redo",
            "priority",
            "obvious_failure",
            "risk_notes",
            "estimated_c",
        }
    }
    if leftover:
        parts.append("**Other**\n\n" + fence_json(leftover))
    return "\n\n".join(parts)


def render_proposition_plan(content: str | None) -> str:
    if not content:
        return "_(no proposition plan)_"
    plan = load_json(content)
    if not isinstance(plan, list):
        return fence_json(content)
    if not plan:
        return "_(empty list)_"
    out: list[str] = []
    for item in plan:
        if not isinstance(item, dict):
            out.append(f"- {item}")
            continue
        head = f"- **{item.get('id', '?')}** — {item.get('title', '')}"
        out.append(head)
        if claim := item.get("claim"):
            out.append(f"  - claim: {claim}")
        if deps := item.get("dependencies"):
            out.append(f"  - dependencies: {deps}")
        if focus := item.get("verification_focus"):
            out.append(f"  - verification focus: {focus}")
        if item.get("requires_tool"):
            out.append("  - requires tool: yes")
    return "\n".join(out)


def render_proposition(row: sqlite3.Row, proof: str | None) -> str:
    parts = [f"#### {row['proposition_id']} — `{row['status']}`"]
    if row["title"]:
        parts.append(f"**{row['title']}**")
    meta: list[str] = []
    if row["requires_tool"]:
        meta.append("requires tool")
    if row["signature"]:
        meta.append(f"sig=`{row['signature']}`")
    if meta:
        parts.append("_" + ", ".join(meta) + "_")
    if row["claim"]:
        parts.append(f"**Claim:** {row['claim']}")
    if row["verification_focus"]:
        parts.append(f"**Verification focus:** {row['verification_focus']}")
    deps = load_json(row["dependencies_json"])
    if deps:
        parts.append("**Dependencies:** " + json.dumps(deps, ensure_ascii=False))
    if row["note"]:
        parts.append("**Note:**\n\n" + md_quote(row["note"]))
    if proof:
        parts.append("**Proof draft:**\n\n" + proof)
    return "\n\n".join(parts)


def render_tool_request(row: sqlite3.Row) -> str:
    parts = [
        f"- **{row['tool_name']}** (`{row['report_status'] or 'no report'}`)"
        f" — request `{row['request_id']}`"
    ]
    if row["justification"]:
        parts.append(f"  - justification: {row['justification']}")
    parts.append("  - spec:\n" + fence_json(row["spec_json"]))
    parts.append("  - report:\n" + fence_json(row["report_json"]))
    return "\n".join(parts)


def render_candidate(
    conn: sqlite3.Connection,
    namespace: str,
    candidate_id: str,
    snapshot_id: int,
    include_history: bool,
) -> str:
    snap = fetch_snapshot(conn, snapshot_id)
    if snap is None:
        return f"# {candidate_id}\n\n_(snapshot {snapshot_id} not found)_\n"
    artifacts = fetch_artifacts(conn, snapshot_id)
    prop_states = fetch_property_states(conn, snapshot_id)
    proposition_states = fetch_proposition_states(conn, snapshot_id)
    tool_requests = fetch_tool_requests(conn, snapshot_id)
    payload = load_json(snap["payload_json"]) or {}

    lines: list[str] = []
    lines.append(f"# {candidate_id}")
    lines.append("")
    lines.append(f"- **Namespace:** `{namespace}`")
    lines.append(f"- **Latest snapshot:** {snapshot_id} @ {snap['saved_at']}")
    lines.append(f"- **Status:** `{snap['status']}`")
    if snap["architecture_mode"]:
        lines.append(f"- **Architecture mode:** `{snap['architecture_mode']}`")
    if snap["derived_from"]:
        lines.append(f"- **Derived from:** {snap['derived_from']}")
    if snap["source_direction"]:
        lines.append(f"- **Source direction:** {snap['source_direction']}")
    if snap["form"]:
        lines.append("")
        lines.append("## Form")
        lines.append("")
        lines.append(md_quote(snap["form"]))

    if intuition := payload.get("intuition"):
        lines.append("")
        lines.append("## Intuition")
        lines.append("")
        lines.append(intuition)

    if risk := payload.get("risk_notes"):
        risk_parsed = load_json(risk) if isinstance(risk, str) else risk
        if risk_parsed:
            lines.append("")
            lines.append("## Risk notes")
            lines.append("")
            if isinstance(risk_parsed, list):
                lines.append(bullet_list(risk_parsed))
            else:
                lines.append(str(risk_parsed))

    if est := snap["estimated_c"] or payload.get("estimated_c"):
        lines.append("")
        lines.append("## Estimated C")
        lines.append("")
        lines.append(fence_json(est))

    lines.append("")
    lines.append("## Proof plan")
    lines.append("")
    lines.append(render_proof_plan(artifacts.get("proof_plan")))

    property_order = [r["property_name"] for r in prop_states]
    seen = set(property_order)
    for key in artifacts:
        if key.startswith("proposition_plan_"):
            name = key[len("proposition_plan_"):]
            if name not in seen:
                property_order.append(name)
                seen.add(name)
    property_lookup = {r["property_name"]: r for r in prop_states}

    for prop_name in property_order:
        ps = property_lookup.get(prop_name)
        lines.append("")
        if ps:
            lines.append(f"## Property {prop_name} — `{ps['status']}`")
            if ps["note"]:
                lines.append("")
                lines.append(md_quote(ps["note"]))
        else:
            lines.append(f"## Property {prop_name}")

        plan_key = f"proposition_plan_{prop_name}"
        if plan_key in artifacts:
            lines.append("")
            lines.append("### Proposition plan")
            lines.append("")
            lines.append(render_proposition_plan(artifacts[plan_key]))

        for prow in proposition_states.get(prop_name, []):
            lines.append("")
            proof_key = f"property_{prop_name}_{prow['proposition_id']}"
            lines.append(render_proposition(prow, artifacts.get(proof_key)))
            for trow in tool_requests.get((prop_name, prow["proposition_id"]), []):
                lines.append("")
                lines.append(render_tool_request(trow))

        agg_key = f"property_{prop_name}"
        if agg_key in artifacts:
            lines.append("")
            lines.append("### Aggregated property write-up")
            lines.append("")
            lines.append(artifacts[agg_key])

    if pruned := payload.get("pruned_reason"):
        lines.append("")
        lines.append("## Pruned reason")
        lines.append("")
        lines.append(md_quote(str(pruned)))

    if "terminal_report" in artifacts:
        lines.append("")
        lines.append("## Terminal report")
        lines.append("")
        lines.append(artifacts["terminal_report"])

    if "post_terminal_decision" in artifacts:
        lines.append("")
        lines.append("## Post-terminal decision")
        lines.append("")
        lines.append(fence_json(artifacts["post_terminal_decision"]))

    planner_keys = sorted(
        k for k in artifacts if k.startswith("local_planner_decision_")
    )
    for key in planner_keys:
        lines.append("")
        lines.append(f"## {key.replace('_', ' ').title()}")
        lines.append("")
        lines.append(fence_json(artifacts[key]))

    leftover = {
        k: v
        for k, v in artifacts.items()
        if k not in {"proof_plan", "terminal_report", "post_terminal_decision"}
        and not k.startswith("proposition_plan_")
        and not k.startswith("property_")
        and not k.startswith("local_planner_decision_")
    }
    if leftover:
        lines.append("")
        lines.append("## Other artifacts")
        for key, content in leftover.items():
            lines.append("")
            lines.append(f"### {key}")
            lines.append("")
            lines.append(content)

    if include_history:
        history = fetch_history(conn, namespace, candidate_id)
        lines.append("")
        lines.append("## Iteration history")
        lines.append("")
        lines.append("| snapshot | saved_at | status |")
        lines.append("|---:|---|---|")
        for h in history:
            lines.append(f"| {h['snapshot_id']} | {h['saved_at']} | {h['status']} |")

    lines.append("")
    return "\n".join(lines)


def render_index(
    candidates: list[tuple[str, str, int, str]],
    when: datetime,
) -> str:
    lines = [
        "# Candidate memory export",
        "",
        f"Generated: {when.isoformat(timespec='seconds')}",
        "",
        "| Candidate | Namespace | Status | Snapshot | File |",
        "|---|---|---|---:|---|",
    ]
    for ns, cid, snap, status in candidates:
        fname = f"{short_ns(ns)}__{sanitize(cid)}.md"
        lines.append(
            f"| {cid} | `{ns}` | `{status}` | {snap} | [{fname}]({fname}) |"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--namespace",
        help="Restrict to this memory_namespace. Default: latest active namespace.",
    )
    parser.add_argument(
        "--all-namespaces",
        action="store_true",
        help="Export every namespace (overrides --namespace).",
    )
    parser.add_argument(
        "--candidate",
        action="append",
        help="Only export the given candidate id(s). May be repeated.",
    )
    parser.add_argument(
        "--include-history",
        action="store_true",
        help="Append per-snapshot iteration table.",
    )
    args = parser.parse_args(argv)

    if not args.db.exists():
        print(f"db not found: {args.db}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    if args.all_namespaces:
        namespace = None
    elif args.namespace:
        namespace = args.namespace
    else:
        row = conn.execute(
            "SELECT memory_namespace FROM candidate_snapshots "
            "ORDER BY snapshot_id DESC LIMIT 1"
        ).fetchone()
        namespace = row["memory_namespace"] if row else None
        if namespace:
            print(f"using namespace: {namespace}", file=sys.stderr)

    rows = fetch_candidates(conn, namespace)
    if args.candidate:
        wanted = set(args.candidate)
        rows = [r for r in rows if r["candidate_id"] in wanted]
    if not rows:
        print("no candidates matched", file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    summary: list[tuple[str, str, int, str]] = []

    for row in rows:
        ns = row["memory_namespace"]
        cid = row["candidate_id"]
        snap = row["snapshot_id"]
        status = row["status"]
        markdown = render_candidate(conn, ns, cid, snap, args.include_history)
        fname = f"{short_ns(ns)}__{sanitize(cid)}.md"
        path = args.out / fname
        path.write_text(markdown, encoding="utf-8")
        summary.append((ns, cid, snap, status))
        print(f"wrote {path}")

    index_path = args.out / "index.md"
    index_path.write_text(render_index(summary, datetime.now()), encoding="utf-8")
    print(f"wrote {index_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
