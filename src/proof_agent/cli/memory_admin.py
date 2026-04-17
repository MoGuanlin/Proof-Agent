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


def fetch_latest_candidates(conn: sqlite3.Connection, status: str = "", limit: int = 0):
    clauses = []
    params = []
    sql = [
        "SELECT cs.snapshot_id, cs.saved_at, cs.status, cs.derived_from, cs.form, cs.payload_json",
        "FROM candidate_latest cl",
        "JOIN candidate_snapshots cs ON cs.snapshot_id = cl.snapshot_id",
    ]
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
        "SELECT snapshot_id, saved_at, status, derived_from, form, payload_json "
        "FROM candidate_snapshots WHERE snapshot_id = ?",
        (snapshot_id,),
    ).fetchone()


def fetch_latest_snapshot_for_candidate(conn: sqlite3.Connection, candidate_id: str):
    return conn.execute(
        """
        SELECT cs.snapshot_id, cs.saved_at, cs.status, cs.derived_from, cs.form, cs.payload_json
        FROM candidate_latest cl
        JOIN candidate_snapshots cs ON cs.snapshot_id = cl.snapshot_id
        WHERE cl.candidate_id = ?
        """,
        (candidate_id,),
    ).fetchone()


def fetch_all_snapshots_for_candidate(conn: sqlite3.Connection, candidate_id: str):
    return conn.execute(
        """
        SELECT snapshot_id, saved_at, status, derived_from, form, payload_json
        FROM candidate_snapshots
        WHERE candidate_id = ?
        ORDER BY snapshot_id DESC
        """,
        (candidate_id,),
    ).fetchall()


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


def delete_candidate(conn: sqlite3.Connection, candidate_id: str):
    row = conn.execute(
        "SELECT COUNT(*) AS count FROM candidate_snapshots WHERE candidate_id = ?",
        (candidate_id,),
    ).fetchone()
    count = int(row["count"] or 0)
    if count <= 0:
        print(f"Candidate not found: {candidate_id}", file=sys.stderr)
        raise SystemExit(1)
    conn.execute("DELETE FROM tool_request_states WHERE candidate_id = ?", (candidate_id,))
    conn.execute("DELETE FROM artifacts WHERE candidate_id = ?", (candidate_id,))
    conn.execute("DELETE FROM proposition_states WHERE candidate_id = ?", (candidate_id,))
    conn.execute("DELETE FROM property_states WHERE candidate_id = ?", (candidate_id,))
    conn.execute("DELETE FROM candidate_latest WHERE candidate_id = ?", (candidate_id,))
    conn.execute("DELETE FROM candidate_snapshots WHERE candidate_id = ?", (candidate_id,))
    conn.commit()
    print(f"Deleted candidate {candidate_id} and {count} snapshot(s).")


def delete_candidates_by_status(conn: sqlite3.Connection, status: str):
    normalized = str(status or "").strip().lower()
    if not normalized:
        raise SystemExit("Missing status.")
    rows = fetch_latest_candidates(conn, status=normalized, limit=0)
    candidate_ids = []
    for row in rows:
        record = candidate_from_snapshot_row(row)
        if record.candidate_id:
            candidate_ids.append(record.candidate_id)
    if not candidate_ids:
        print(f"No candidates found with status={normalized}.")
        return []
    deleted = []
    for candidate_id in candidate_ids:
        delete_candidate(conn, candidate_id)
        deleted.append(candidate_id)
    return deleted


def delete_snapshot(conn: sqlite3.Connection, snapshot_id: int):
    row = conn.execute(
        "SELECT snapshot_id, candidate_id FROM candidate_snapshots WHERE snapshot_id = ?",
        (snapshot_id,),
    ).fetchone()
    if not row:
        print(f"Snapshot not found: {snapshot_id}", file=sys.stderr)
        raise SystemExit(1)
    candidate_id = str(row["candidate_id"])
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
        "SELECT snapshot_id FROM candidate_latest WHERE candidate_id = ?",
        (candidate_id,),
    ).fetchone()
    replacement = conn.execute(
        "SELECT snapshot_id, status, derived_from, form FROM candidate_snapshots "
        "WHERE candidate_id = ? AND snapshot_id != ? ORDER BY snapshot_id DESC LIMIT 1",
        (candidate_id, snapshot_id),
    ).fetchone()
    if latest and int(latest["snapshot_id"]) == int(snapshot_id):
        if replacement:
            conn.execute(
                """
                UPDATE candidate_latest
                SET snapshot_id = ?, status = ?, derived_from = ?, form = ?
                WHERE candidate_id = ?
                """,
                (
                    int(replacement["snapshot_id"]),
                    str(replacement["status"] or ""),
                    str(replacement["derived_from"] or ""),
                    str(replacement["form"] or ""),
                    candidate_id,
                ),
            )
        else:
            conn.execute("DELETE FROM candidate_latest WHERE candidate_id = ?", (candidate_id,))
    conn.execute("DELETE FROM candidate_snapshots WHERE snapshot_id = ?", (snapshot_id,))
    if latest and int(latest["snapshot_id"]) != int(snapshot_id) and replacement:
        conn.execute(
            """
            INSERT INTO candidate_latest (candidate_id, snapshot_id, status, derived_from, form)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(candidate_id) DO UPDATE SET
                snapshot_id=excluded.snapshot_id,
                status=excluded.status,
                derived_from=excluded.derived_from,
                form=excluded.form
            """,
            (
                candidate_id,
                int(replacement["snapshot_id"]),
                str(replacement["status"] or ""),
                str(replacement["derived_from"] or ""),
                str(replacement["form"] or ""),
            ),
        )
    conn.commit()
    print(f"Deleted snapshot {snapshot_id} for candidate {candidate_id}.")


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
