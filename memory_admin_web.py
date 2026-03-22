import argparse
import html
import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlencode, urlparse

from app_config import CANDIDATE_MEMORY_FILE
from memory_admin import (
    candidate_from_snapshot_row,
    connect_db,
    delete_candidate,
    delete_snapshot,
    fetch_all_snapshots_for_candidate,
    fetch_latest_candidates,
    fetch_latest_snapshot_for_candidate,
    fetch_snapshot,
    resolve_db_path,
)


def html_page(title: str, body: str, flash: str = "") -> bytes:
    flash_html = ""
    if flash:
        flash_html = f'<div class="flash">{html.escape(flash)}</div>'
    rendered = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      --bg: #f6f1e8;
      --panel: #fffaf0;
      --ink: #1e1a16;
      --muted: #6d6258;
      --line: #d8cab7;
      --accent: #914f1e;
      --danger: #a22828;
      --danger-bg: #fff1f1;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", serif;
      background:
        radial-gradient(circle at top right, rgba(145,79,30,0.08), transparent 28rem),
        linear-gradient(180deg, #fbf7f0 0%, var(--bg) 100%);
      color: var(--ink);
    }}
    a {{ color: var(--accent); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .shell {{ max-width: 1320px; margin: 0 auto; padding: 24px; }}
    .topbar {{
      display: flex; gap: 16px; justify-content: space-between; align-items: baseline;
      border-bottom: 1px solid var(--line); padding-bottom: 16px; margin-bottom: 20px;
    }}
    .title {{ font-size: 30px; font-weight: 700; letter-spacing: 0.01em; }}
    .sub {{ color: var(--muted); font-size: 14px; }}
    .grid {{ display: grid; gap: 18px; }}
    .grid.two {{ grid-template-columns: 340px minmax(0, 1fr); }}
    .panel {{
      background: rgba(255,250,240,0.92);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 18px;
      box-shadow: 0 8px 30px rgba(84, 53, 24, 0.06);
    }}
    .panel h2, .panel h3 {{ margin: 0 0 12px 0; }}
    .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; }}
    .stat {{
      padding: 12px; border: 1px solid var(--line); border-radius: 12px; background: rgba(255,255,255,0.72);
    }}
    .stat .k {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em; }}
    .stat .v {{ margin-top: 6px; font-size: 26px; font-weight: 700; }}
    .toolbar {{ display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 16px; }}
    .toolbar input, .toolbar select {{
      padding: 10px 12px; border: 1px solid var(--line); border-radius: 10px; background: white;
      font: inherit; min-width: 0;
    }}
    .toolbar button, .danger-btn {{
      padding: 10px 14px; border: 0; border-radius: 10px; background: var(--accent);
      color: white; font: inherit; cursor: pointer;
    }}
    .danger-btn {{ background: var(--danger); }}
    .danger-zone {{
      margin-top: 16px; padding: 14px; border: 1px solid #ebc0c0; background: var(--danger-bg); border-radius: 12px;
    }}
    .card-list {{ display: grid; gap: 12px; }}
    .candidate {{
      padding: 14px; border: 1px solid var(--line); border-radius: 14px; background: rgba(255,255,255,0.78);
    }}
    .candidate-head {{
      display: flex; gap: 10px; justify-content: space-between; align-items: center; margin-bottom: 8px;
    }}
    .badge {{
      display: inline-block; padding: 4px 8px; border-radius: 999px; font-size: 12px; background: #ede0cf;
      color: #603c1f;
    }}
    .badge.pruned {{ background: #f5d1d1; color: #7e1a1a; }}
    .badge.active {{ background: #d6ead8; color: #1f5c29; }}
    .mono {{ font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace; font-size: 13px; }}
    .meta {{ color: var(--muted); font-size: 13px; }}
    .snippet {{ margin-top: 8px; white-space: pre-wrap; word-break: break-word; }}
    .json {{
      white-space: pre-wrap; overflow-x: auto; font-family: "SFMono-Regular", Consolas, monospace;
      font-size: 13px; line-height: 1.45; background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 14px;
    }}
    .history {{ display: grid; gap: 10px; }}
    .history-item {{ border-top: 1px solid var(--line); padding-top: 10px; }}
    .flash {{
      margin-bottom: 16px; padding: 12px 14px; border-radius: 12px; border: 1px solid #cbb493; background: #fff3df;
    }}
    form.inline {{ display: inline; }}
    @media (max-width: 920px) {{
      .grid.two {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <div class="topbar">
      <div>
        <div class="title">Candidate Memory Admin</div>
        <div class="sub">Local-only admin UI for browsing and deleting memory records.</div>
      </div>
      <div class="sub"><a href="/">Home</a></div>
    </div>
    {flash_html}
    {body}
  </div>
</body>
</html>"""
    return rendered.encode("utf-8")


def status_badge(status: str) -> str:
    normalized = (status or "").strip().lower()
    cls = "badge"
    if normalized:
        cls += f" {html.escape(normalized)}"
    return f'<span class="{cls}">{html.escape(status or "unknown")}</span>'


def summarize_record(record) -> str:
    pieces = []
    for prop, detail in (record.property_status or {}).items():
        status = str((detail or {}).get("status", "")).strip() or "untested"
        pieces.append(f"{prop}={status}")
    return "; ".join(pieces)


class MemoryAdminHandler(BaseHTTPRequestHandler):
    db_path = ""

    def log_message(self, fmt, *args):
        print(f"[memory_admin_web] {self.address_string()} - {fmt % args}", flush=True)

    def _parse_query(self):
        return parse_qs(urlparse(self.path).query, keep_blank_values=True)

    def _read_form(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        raw = self.rfile.read(length).decode("utf-8", errors="replace") if length > 0 else ""
        parsed = parse_qs(raw, keep_blank_values=True)
        return {key: values[-1] if values else "" for key, values in parsed.items()}

    def _send_html(self, title: str, body: str, flash: str = "", status: int = HTTPStatus.OK):
        payload = html_page(title, body, flash=flash)
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _redirect(self, location: str):
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.end_headers()

    def _stats(self, conn):
        tables = [
            "candidate_latest",
            "candidate_snapshots",
            "property_states",
            "proposition_states",
            "artifacts",
            "tool_request_states",
        ]
        return {table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] or 0) for table in tables}

    def _render_home(self, flash: str = ""):
        query = self._parse_query()
        status_filter = (query.get("status", [""])[-1] or "").strip()
        limit_text = (query.get("limit", ["50"])[-1] or "50").strip()
        try:
            limit = max(1, int(limit_text))
        except ValueError:
            limit = 50
        with connect_db(self.db_path) as conn:
            stats = self._stats(conn)
            rows = fetch_latest_candidates(conn, status=status_filter, limit=limit)
        cards = []
        for row in rows:
            record = candidate_from_snapshot_row(row)
            candidate_link = f"/candidate?id={quote(record.candidate_id, safe='')}"
            snapshot_link = f"/snapshot?id={row['snapshot_id']}"
            pruned = (
                f'<div class="snippet"><strong>Pruned:</strong> {html.escape(record.pruned_reason[:300])}</div>'
                if record.pruned_reason
                else ""
            )
            decision = (
                f'<div class="meta">Decision: {html.escape(record.terminal_decision.get("action", ""))}</div>'
                if record.terminal_decision.get("action")
                else ""
            )
            cards.append(
                f"""
                <div class="candidate">
                  <div class="candidate-head">
                    <div>
                      <a href="{candidate_link}"><strong>{html.escape(record.candidate_id)}</strong></a>
                      {status_badge(str(row['status']))}
                    </div>
                    <div class="meta mono">snapshot={row['snapshot_id']}</div>
                  </div>
                  <div class="meta">derived_from={html.escape(str(row['derived_from'] or '[none]'))}</div>
                  <div class="snippet mono">{html.escape(str(row['form']))}</div>
                  <div class="snippet"><strong>Properties:</strong> {html.escape(summarize_record(record))}</div>
                  {pruned}
                  {decision}
                  <div class="toolbar" style="margin-top:10px">
                    <a href="{candidate_link}">Open candidate</a>
                    <a href="{snapshot_link}">Open snapshot JSON</a>
                  </div>
                </div>
                """
            )
        if not cards:
            cards.append('<div class="candidate">No candidates found.</div>')
        stats_html = "".join(
            f'<div class="stat"><div class="k">{html.escape(key)}</div><div class="v">{value}</div></div>'
            for key, value in stats.items()
        )
        body = f"""
        <div class="grid">
          <div class="panel">
            <h2>Overview</h2>
            <div class="stats">{stats_html}</div>
          </div>
          <div class="panel">
            <h2>Browse Latest Candidates</h2>
            <form class="toolbar" method="get" action="/">
              <input type="text" name="status" value="{html.escape(status_filter)}" placeholder="status filter: active / pruned / passed">
              <input type="number" min="1" name="limit" value="{limit}">
              <button type="submit">Refresh</button>
            </form>
            <div class="card-list">
              {''.join(cards)}
            </div>
          </div>
        </div>
        """
        self._send_html("Candidate Memory Admin", body, flash=flash)

    def _render_candidate(self, candidate_id: str, flash: str = ""):
        with connect_db(self.db_path) as conn:
            row = fetch_latest_snapshot_for_candidate(conn, candidate_id)
            history = fetch_all_snapshots_for_candidate(conn, candidate_id)
        if not row:
            self._send_html("Not Found", "<div class='panel'>Candidate not found.</div>", status=HTTPStatus.NOT_FOUND)
            return
        record = candidate_from_snapshot_row(row)
        history_html = []
        for item in history:
            snap_id = int(item["snapshot_id"])
            snap_record = candidate_from_snapshot_row(item)
            delete_form = ""
            if len(history) > 1:
                delete_form = f"""
                <form class="inline" method="post" action="/delete-snapshot" onsubmit="return confirm('Delete snapshot {snap_id}?');">
                  <input type="hidden" name="snapshot_id" value="{snap_id}">
                  <input type="hidden" name="return_to" value="/candidate?id={html.escape(candidate_id)}">
                  <button class="danger-btn" type="submit">Delete snapshot</button>
                </form>
                """
            history_html.append(
                f"""
                <div class="history-item">
                  <div><strong>snapshot={snap_id}</strong> {status_badge(str(item['status']))}</div>
                  <div class="meta">saved_at={html.escape(str(item['saved_at']))}</div>
                  <div class="meta">derived_from={html.escape(str(item['derived_from'] or '[none]'))}</div>
                  <div class="toolbar">
                    <a href="/snapshot?id={snap_id}">Open JSON</a>
                    {delete_form}
                  </div>
                  <div class="snippet">{html.escape(snap_record.pruned_reason[:320]) if snap_record.pruned_reason else ''}</div>
                </div>
                """
            )
        body = f"""
        <div class="grid two">
          <div class="panel">
            <h2>{html.escape(candidate_id)}</h2>
            <div class="meta">Latest snapshot: {row['snapshot_id']}</div>
            <div class="meta">Status: {html.escape(str(row['status']))}</div>
            <div class="meta">Derived from: {html.escape(str(row['derived_from'] or '[none]'))}</div>
            <div class="snippet mono">{html.escape(str(row['form']))}</div>
            <div class="snippet"><strong>Properties:</strong> {html.escape(summarize_record(record))}</div>
            <div class="danger-zone">
              <div><strong>Danger zone</strong></div>
              <div class="meta">Delete every snapshot for this candidate.</div>
              <form method="post" action="/delete-candidate" onsubmit="return confirm('Delete candidate {html.escape(candidate_id)} and all snapshots?');">
                <input type="hidden" name="candidate_id" value="{html.escape(candidate_id)}">
                <button class="danger-btn" type="submit">Delete candidate</button>
              </form>
            </div>
          </div>
          <div class="grid">
            <div class="panel">
              <h3>Snapshot History</h3>
              <div class="history">
                {''.join(history_html)}
              </div>
            </div>
            <div class="panel">
              <h3>Latest JSON Payload</h3>
              <div class="json">{html.escape(json.dumps(record.to_dict(), ensure_ascii=False, indent=2))}</div>
            </div>
          </div>
        </div>
        """
        self._send_html(f"Candidate {candidate_id}", body, flash=flash)

    def _render_snapshot(self, snapshot_id: int, flash: str = ""):
        with connect_db(self.db_path) as conn:
            row = fetch_snapshot(conn, snapshot_id)
        if not row:
            self._send_html("Not Found", "<div class='panel'>Snapshot not found.</div>", status=HTTPStatus.NOT_FOUND)
            return
        record = candidate_from_snapshot_row(row)
        candidate_id = record.candidate_id
        body = f"""
        <div class="panel">
          <h2>Snapshot {snapshot_id}</h2>
          <div class="meta">candidate_id={html.escape(candidate_id)}</div>
          <div class="meta">saved_at={html.escape(str(row['saved_at']))}</div>
          <div class="toolbar">
            <a href="/candidate?id={html.escape(candidate_id)}">Back to candidate</a>
            <form class="inline" method="post" action="/delete-snapshot" onsubmit="return confirm('Delete snapshot {snapshot_id}?');">
              <input type="hidden" name="snapshot_id" value="{snapshot_id}">
              <input type="hidden" name="return_to" value="/candidate?id={html.escape(candidate_id)}">
              <button class="danger-btn" type="submit">Delete snapshot</button>
            </form>
          </div>
          <div class="json">{html.escape(json.dumps(record.to_dict(), ensure_ascii=False, indent=2))}</div>
        </div>
        """
        self._send_html(f"Snapshot {snapshot_id}", body, flash=flash)

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        flash = (query.get("flash", [""])[-1] or "").strip()
        if parsed.path == "/":
            self._render_home(flash=flash)
            return
        if parsed.path == "/candidate":
            candidate_id = (query.get("id", [""])[-1] or "").strip()
            self._render_candidate(candidate_id, flash=flash)
            return
        if parsed.path == "/snapshot":
            raw_id = (query.get("id", ["0"])[-1] or "0").strip()
            try:
                snapshot_id = int(raw_id)
            except ValueError:
                snapshot_id = 0
            self._render_snapshot(snapshot_id, flash=flash)
            return
        self._send_html("Not Found", "<div class='panel'>Route not found.</div>", status=HTTPStatus.NOT_FOUND)

    def do_POST(self):
        parsed = urlparse(self.path)
        form = self._read_form()
        if parsed.path == "/delete-candidate":
            candidate_id = (form.get("candidate_id") or "").strip()
            if not candidate_id:
                self._redirect("/?" + urlencode({"flash": "Missing candidate_id"}))
                return
            with connect_db(self.db_path) as conn:
                delete_candidate(conn, candidate_id)
            self._redirect("/?" + urlencode({"flash": f"Deleted candidate {candidate_id}"}))
            return
        if parsed.path == "/delete-snapshot":
            raw_id = (form.get("snapshot_id") or "0").strip()
            return_to = (form.get("return_to") or "/").strip() or "/"
            try:
                snapshot_id = int(raw_id)
            except ValueError:
                snapshot_id = 0
            if snapshot_id <= 0:
                self._redirect("/?" + urlencode({"flash": "Invalid snapshot_id"}))
                return
            with connect_db(self.db_path) as conn:
                delete_snapshot(conn, snapshot_id)
            sep = "&" if "?" in return_to else "?"
            self._redirect(return_to + sep + urlencode({"flash": f"Deleted snapshot {snapshot_id}"}))
            return
        self._send_html("Not Found", "<div class='panel'>Route not found.</div>", status=HTTPStatus.NOT_FOUND)


def build_parser():
    parser = argparse.ArgumentParser(description="Run a local web UI for candidate memory admin.")
    parser.add_argument("--db", default=CANDIDATE_MEMORY_FILE, help="Path to candidate memory sqlite file.")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind.")
    parser.add_argument("--port", type=int, default=8765, help="Port to bind.")
    return parser


def main():
    args = build_parser().parse_args()
    db_path = resolve_db_path(args.db)
    if not os.path.exists(db_path):
        raise SystemExit(f"Database not found: {db_path}")
    handler_cls = type("BoundMemoryAdminHandler", (MemoryAdminHandler,), {"db_path": db_path})
    server = ThreadingHTTPServer((args.host, args.port), handler_cls)
    print(f"Memory admin web UI listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
