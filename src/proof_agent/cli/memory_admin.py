import argparse
import json
import os
import sqlite3
import sys

from proof_agent.app_config import CANDIDATE_MEMORY_FILE
from proof_agent.candidate_memory import CandidateRecord, DEFAULT_PROPERTIES
from proof_agent.paths import resolve_from_project


def resolve_db_path(path_arg: str) -> str:
    path = path_arg or CANDIDATE_MEMORY_FILE
    return resolve_from_project(path)


def connect_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def candidate_from_snapshot_row(row: sqlite3.Row) -> CandidateRecord:
    return CandidateRecord.from_dict(json.loads(str(row["payload_json"])))


def fetch_latest_candidates(
    conn: sqlite3.Connection,
    status: str = "",
    limit: int = 0,
    namespace: str | None = None,
):
    clauses = []
    params: list = []
    sql = [
        "SELECT cs.snapshot_id, cs.saved_at, cs.status, cs.derived_from, cs.form, cs.payload_json, cs.memory_namespace",
        "FROM candidate_latest cl",
        "JOIN candidate_snapshots cs ON cs.snapshot_id = cl.snapshot_id",
    ]
    if namespace is not None:
        clauses.append("cl.memory_namespace = ?")
        params.append(namespace)
    if status:
        clauses.append("LOWER(cs.status) = ?")
        params.append(status.strip().lower())
    if clauses:
        sql.append("WHERE " + " AND ".join(clauses))
    sql.append("ORDER BY cs.snapshot_id DESC")
    if limit > 0:
        sql.append("LIMIT ?")
        params.append(limit)
    return conn.execute("\n".join(sql), params).fetchall()


def fetch_snapshot(conn: sqlite3.Connection, snapshot_id: int):
    return conn.execute(
        "SELECT snapshot_id, saved_at, status, derived_from, form, payload_json, memory_namespace "
        "FROM candidate_snapshots WHERE snapshot_id = ?",
        (snapshot_id,),
    ).fetchone()


def fetch_latest_snapshot_for_candidate(
    conn: sqlite3.Connection,
    candidate_id: str,
    namespace: str | None = None,
):
    params: list = [candidate_id]
    ns_clause = ""
    if namespace is not None:
        ns_clause = "AND cl.memory_namespace = ? "
        params.append(namespace)
    return conn.execute(
        f"""
        SELECT cs.snapshot_id, cs.saved_at, cs.status, cs.derived_from, cs.form, cs.payload_json, cs.memory_namespace
        FROM candidate_latest cl
        JOIN candidate_snapshots cs ON cs.snapshot_id = cl.snapshot_id
        WHERE cl.candidate_id = ? {ns_clause}
        ORDER BY cs.snapshot_id DESC
        LIMIT 1
        """,
        params,
    ).fetchone()


def fetch_all_snapshots_for_candidate(
    conn: sqlite3.Connection,
    candidate_id: str,
    namespace: str | None = None,
):
    params: list = [candidate_id]
    ns_clause = ""
    if namespace is not None:
        ns_clause = "AND memory_namespace = ? "
        params.append(namespace)
    return conn.execute(
        f"""
        SELECT snapshot_id, saved_at, status, derived_from, form, payload_json, memory_namespace
        FROM candidate_snapshots
        WHERE candidate_id = ? {ns_clause}
        ORDER BY snapshot_id DESC
        """,
        params,
    ).fetchall()


def list_namespaces(conn: sqlite3.Connection):
    """Return [(namespace, candidate_count, latest_snapshot_id, latest_saved_at), ...]
    sorted by latest_snapshot_id desc.
    """
    rows = conn.execute(
        """
        SELECT cl.memory_namespace AS ns,
               COUNT(*) AS cnt,
               MAX(cs.snapshot_id) AS latest_snap,
               MAX(cs.saved_at) AS latest_ts
        FROM candidate_latest cl
        JOIN candidate_snapshots cs ON cs.snapshot_id = cl.snapshot_id
        GROUP BY cl.memory_namespace
        ORDER BY latest_snap DESC
        """
    ).fetchall()
    return [(str(r["ns"] or ""), int(r["cnt"] or 0), int(r["latest_snap"] or 0), str(r["latest_ts"] or "")) for r in rows]


def format_property_snapshot(record: CandidateRecord) -> str:
    parts = []
    for prop in DEFAULT_PROPERTIES:
        detail = record.property_status.get(prop) or {}
        status = str(detail.get("status", "")).strip() or "untested"
        note = str(detail.get("note", "")).strip()
        item = f"{prop}={status}"
        if note:
            item += f" ({note[:120]})"
        parts.append(item)
    return "; ".join(parts)


def print_stats(conn: sqlite3.Connection):
    tables = [
        "candidate_latest",
        "candidate_snapshots",
        "property_states",
        "proposition_states",
        "artifacts",
        "tool_request_states",
    ]
    for table in tables:
        count = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] or 0)
        print(f"{table}: {count}")


def print_candidate_list(conn: sqlite3.Connection, status: str = "", limit: int = 0):
    rows = fetch_latest_candidates(conn, status=status, limit=limit)
    if not rows:
        print("No candidates found.")
        return
    for row in rows:
        record = candidate_from_snapshot_row(row)
        print(
            f"{record.candidate_id} | snapshot={row['snapshot_id']} | status={row['status']} | "
            f"derived_from={row['derived_from'] or '[none]'}"
        )
        print(f"  form={row['form']}")
        print(f"  properties={format_property_snapshot(record)}")
        if record.pruned_reason:
            print(f"  pruned_reason={record.pruned_reason}")
        if record.terminal_decision.get('action'):
            print(f"  decision={record.terminal_decision.get('action')}")


def print_candidate_detail(conn: sqlite3.Connection, candidate_id: str = "", snapshot_id: int = 0):
    if snapshot_id:
        row = fetch_snapshot(conn, snapshot_id)
    else:
        row = fetch_latest_snapshot_for_candidate(conn, candidate_id)
    if not row:
        print("Record not found.", file=sys.stderr)
        raise SystemExit(1)
    record = candidate_from_snapshot_row(row)
    payload = record.to_dict()
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def require_confirm(args):
    if getattr(args, "yes", False):
        return
    raise SystemExit("Refusing to delete without --yes.")


def delete_candidate(conn: sqlite3.Connection, candidate_id: str, namespace: str | None = None):
    clauses = ["candidate_id = ?"]
    params: list = [candidate_id]
    if namespace is not None:
        clauses.append("memory_namespace = ?")
        params.append(namespace)
    rows = conn.execute(
        "SELECT snapshot_id FROM candidate_snapshots WHERE " + " AND ".join(clauses),
        params,
    ).fetchall()
    snapshot_ids = [int(row["snapshot_id"]) for row in rows]
    count = len(snapshot_ids)
    if count <= 0:
        print(f"Candidate not found: {candidate_id}", file=sys.stderr)
        raise SystemExit(1)
    for snapshot_id in snapshot_ids:
        conn.execute(
            "DELETE FROM tool_request_states WHERE snapshot_id = ? AND candidate_id = ?",
            (snapshot_id, candidate_id),
        )
        conn.execute(
            "DELETE FROM artifacts WHERE snapshot_id = ? AND candidate_id = ?",
            (snapshot_id, candidate_id),
        )
        conn.execute(
            "DELETE FROM proposition_states WHERE snapshot_id = ? AND candidate_id = ?",
            (snapshot_id, candidate_id),
        )
        conn.execute(
            "DELETE FROM property_states WHERE snapshot_id = ? AND candidate_id = ?",
            (snapshot_id, candidate_id),
        )
    if namespace is None:
        conn.execute("DELETE FROM candidate_latest WHERE candidate_id = ?", (candidate_id,))
    else:
        conn.execute(
            "DELETE FROM candidate_latest WHERE candidate_id = ? AND memory_namespace = ?",
            (candidate_id, namespace),
        )
    for snapshot_id in snapshot_ids:
        conn.execute(
            "DELETE FROM candidate_snapshots WHERE snapshot_id = ? AND candidate_id = ?",
            (snapshot_id, candidate_id),
        )
    conn.commit()
    suffix = f" in namespace {namespace}" if namespace is not None else ""
    print(f"Deleted candidate {candidate_id}{suffix} and {count} snapshot(s).")


def delete_candidates_by_status(conn: sqlite3.Connection, status: str, namespace: str | None = None):
    normalized = str(status or "").strip().lower()
    if not normalized:
        raise SystemExit("Missing status.")
    rows = fetch_latest_candidates(conn, status=normalized, limit=0, namespace=namespace)
    scoped_candidates = []
    seen = set()
    for row in rows:
        record = candidate_from_snapshot_row(row)
        if record.candidate_id:
            row_namespace = str(row["memory_namespace"] or "") if "memory_namespace" in row.keys() else None
            key = (row_namespace, record.candidate_id) if namespace is not None else record.candidate_id
            if key in seen:
                continue
            seen.add(key)
            scoped_candidates.append((record.candidate_id, row_namespace if namespace is not None else None))
    if not scoped_candidates:
        print(f"No candidates found with status={normalized}.")
        return []
    deleted = []
    for candidate_id, row_namespace in scoped_candidates:
        delete_candidate(conn, candidate_id, namespace=row_namespace)
        deleted.append(candidate_id)
    return deleted


def delete_snapshot(conn: sqlite3.Connection, snapshot_id: int):
    row = conn.execute(
        "SELECT snapshot_id, candidate_id, memory_namespace FROM candidate_snapshots WHERE snapshot_id = ?",
        (snapshot_id,),
    ).fetchone()
    if not row:
        print(f"Snapshot not found: {snapshot_id}", file=sys.stderr)
        raise SystemExit(1)
    candidate_id = str(row["candidate_id"])
    namespace = str(row["memory_namespace"] or "")
    conn.execute(
        "DELETE FROM tool_request_states WHERE snapshot_id = ? AND candidate_id = ?",
        (snapshot_id, candidate_id),
    )
    conn.execute(
        "DELETE FROM artifacts WHERE snapshot_id = ? AND candidate_id = ?",
        (snapshot_id, candidate_id),
    )
    conn.execute(
        "DELETE FROM proposition_states WHERE snapshot_id = ? AND candidate_id = ?",
        (snapshot_id, candidate_id),
    )
    conn.execute(
        "DELETE FROM property_states WHERE snapshot_id = ? AND candidate_id = ?",
        (snapshot_id, candidate_id),
    )
    latest = conn.execute(
        "SELECT snapshot_id FROM candidate_latest WHERE candidate_id = ? AND memory_namespace = ?",
        (candidate_id, namespace),
    ).fetchone()
    replacement = conn.execute(
        "SELECT snapshot_id, status, derived_from, form FROM candidate_snapshots "
        "WHERE candidate_id = ? AND memory_namespace = ? AND snapshot_id != ? "
        "ORDER BY snapshot_id DESC LIMIT 1",
        (candidate_id, namespace, snapshot_id),
    ).fetchone()
    if latest and int(latest["snapshot_id"]) == int(snapshot_id):
        if replacement:
            conn.execute(
                """
                UPDATE candidate_latest
                SET snapshot_id = ?, status = ?, derived_from = ?, form = ?
                WHERE candidate_id = ? AND memory_namespace = ?
                """,
                (
                    int(replacement["snapshot_id"]),
                    str(replacement["status"] or ""),
                    str(replacement["derived_from"] or ""),
                    str(replacement["form"] or ""),
                    candidate_id,
                    namespace,
                ),
            )
        else:
            conn.execute(
                "DELETE FROM candidate_latest WHERE candidate_id = ? AND memory_namespace = ?",
                (candidate_id, namespace),
            )
    conn.execute("DELETE FROM candidate_snapshots WHERE snapshot_id = ?", (snapshot_id,))
    conn.commit()
    print(f"Deleted snapshot {snapshot_id} for candidate {candidate_id} in namespace {namespace}.")


def print_candidate_history(conn: sqlite3.Connection, candidate_id: str):
    rows = fetch_all_snapshots_for_candidate(conn, candidate_id)
    if not rows:
        print("Candidate not found.", file=sys.stderr)
        raise SystemExit(1)
    for row in rows:
        record = candidate_from_snapshot_row(row)
        print(
            f"snapshot={row['snapshot_id']} | saved_at={row['saved_at']} | status={row['status']} | "
            f"derived_from={row['derived_from'] or '[none]'}"
        )
        if record.pruned_reason:
            print(f"  pruned_reason={record.pruned_reason}")
        if record.terminal_decision.get("action"):
            print(f"  decision={record.terminal_decision.get('action')}")


def build_parser():
    parser = argparse.ArgumentParser(description="Inspect and delete candidate memory records.")
    parser.add_argument("--db", default=CANDIDATE_MEMORY_FILE, help="Path to candidate memory sqlite file.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("stats", help="Show table counts.")

    list_parser = subparsers.add_parser("list", help="List latest candidate records.")
    list_parser.add_argument("--status", default="", help="Filter by latest candidate status.")
    list_parser.add_argument("--limit", type=int, default=20, help="Max number of candidates to print.")

    show_parser = subparsers.add_parser("show", help="Show one candidate snapshot as JSON.")
    show_group = show_parser.add_mutually_exclusive_group(required=True)
    show_group.add_argument("--candidate-id", help="Candidate id to inspect (latest snapshot).")
    show_group.add_argument("--snapshot-id", type=int, help="Snapshot id to inspect.")

    history_parser = subparsers.add_parser("history", help="Show snapshot history for one candidate.")
    history_parser.add_argument("--candidate-id", required=True, help="Candidate id to inspect.")

    delete_candidate_parser = subparsers.add_parser("delete-candidate", help="Delete all snapshots for a candidate.")
    delete_candidate_parser.add_argument("--candidate-id", required=True, help="Candidate id to delete.")
    delete_candidate_parser.add_argument("--yes", action="store_true", help="Confirm deletion.")

    delete_status_parser = subparsers.add_parser("delete-status", help="Delete all candidates whose latest status matches.")
    delete_status_parser.add_argument("--status", required=True, help="Latest status to delete, for example: active.")
    delete_status_parser.add_argument("--yes", action="store_true", help="Confirm deletion.")

    delete_snapshot_parser = subparsers.add_parser("delete-snapshot", help="Delete one snapshot.")
    delete_snapshot_parser.add_argument("--snapshot-id", type=int, required=True, help="Snapshot id to delete.")
    delete_snapshot_parser.add_argument("--yes", action="store_true", help="Confirm deletion.")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    db_path = resolve_db_path(args.db)
    if not os.path.exists(db_path):
        raise SystemExit(f"Database not found: {db_path}")

    with connect_db(db_path) as conn:
        if args.command == "stats":
            print_stats(conn)
            return
        if args.command == "list":
            print_candidate_list(conn, status=args.status, limit=args.limit)
            return
        if args.command == "show":
            print_candidate_detail(
                conn,
                candidate_id=getattr(args, "candidate_id", "") or "",
                snapshot_id=int(getattr(args, "snapshot_id", 0) or 0),
            )
            return
        if args.command == "history":
            print_candidate_history(conn, args.candidate_id)
            return
        if args.command == "delete-candidate":
            require_confirm(args)
            delete_candidate(conn, args.candidate_id)
            return
        if args.command == "delete-status":
            require_confirm(args)
            delete_candidates_by_status(conn, args.status)
            return
        if args.command == "delete-snapshot":
            require_confirm(args)
            delete_snapshot(conn, args.snapshot_id)
            return
    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
