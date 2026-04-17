import argparse
import html
import json
import os
import signal
import subprocess
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlparse

from proof_agent.app_config import CANDIDATE_MEMORY_FILE
from proof_agent.cli.memory_admin import (
    candidate_from_snapshot_row,
    connect_db,
    delete_candidate,
    delete_candidates_by_status,
    delete_snapshot,
    fetch_all_snapshots_for_candidate,
    fetch_latest_candidates,
    fetch_latest_snapshot_for_candidate,
    fetch_snapshot,
    resolve_db_path,
)
from proof_agent.paths import LOGS_DIR, MAIN_PID_FILE, PROJECT_ROOT, START_SCRIPT_FILE

ROOT = PROJECT_ROOT
LOG_DIR = LOGS_DIR
PID_FILE = MAIN_PID_FILE
START_SCRIPT = START_SCRIPT_FILE


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
    .nav {{ display: flex; gap: 14px; flex-wrap: wrap; margin-top: 10px; }}
    .nav a {{
      padding: 7px 11px; border: 1px solid var(--line); border-radius: 999px; background: rgba(255,255,255,0.78);
    }}
    .grid {{ display: grid; gap: 18px; }}
    .grid.two {{ grid-template-columns: 340px minmax(0, 1fr); }}
    .grid.log {{ grid-template-columns: 320px minmax(0, 1fr); }}
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
    .badge.passed {{ background: #dfeaf7; color: #1d4f88; }}
    .mono {{ font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace; font-size: 13px; }}
    .meta {{ color: var(--muted); font-size: 13px; }}
    .snippet {{ margin-top: 8px; white-space: pre-wrap; word-break: break-word; }}
    .json {{
      white-space: pre-wrap; overflow-x: auto; font-family: "SFMono-Regular", Consolas, monospace;
      font-size: 13px; line-height: 1.45; background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 14px;
    }}
    .history {{ display: grid; gap: 10px; }}
    .history-item {{ border-top: 1px solid var(--line); padding-top: 10px; }}
    .tree-column {{ display: grid; gap: 12px; }}
    .tree-node {{
      border: 1px solid var(--line); border-radius: 14px; padding: 14px; background: rgba(255,255,255,0.82);
      animation: riseIn 320ms ease both;
    }}
    .tree-children {{
      margin-top: 12px; margin-left: 20px; padding-left: 14px; border-left: 2px solid #e5d5be;
      display: grid; gap: 12px;
    }}
    .pill {{
      display: inline-block; padding: 4px 8px; border-radius: 999px; background: #f1e6d6; color: #6a4526; font-size: 12px;
    }}
    .logbox {{
      white-space: pre-wrap; overflow-x: auto; font-family: "SFMono-Regular", Consolas, monospace;
      font-size: 13px; line-height: 1.45; background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 14px;
      max-height: 70vh; overflow-y: auto;
    }}
    .agentbox {{
      max-height: 30rem; overflow-y: auto; border: 1px solid var(--line); border-radius: 12px; padding: 12px; background: rgba(255,255,255,0.75);
    }}
    .timeline {{ display: grid; gap: 14px; }}
    .timeline-item {{
      border: 1px solid var(--line); border-radius: 14px; padding: 14px; background: rgba(255,255,255,0.82);
      animation: riseIn 380ms ease both;
    }}
    .fold {{
      margin-top: 10px; border: 1px solid var(--line); border-radius: 12px; background: rgba(255,255,255,0.65);
      overflow: hidden;
    }}
    .fold summary {{
      cursor: pointer; padding: 10px 12px; list-style: none; font-weight: 600;
      background: rgba(145,79,30,0.07);
    }}
    .fold summary::-webkit-details-marker {{ display: none; }}
    .fold-body {{ padding: 12px; display: grid; gap: 10px; opacity: 0; transform: translateY(-4px); transition: opacity 180ms ease, transform 180ms ease; }}
    .fold[open] .fold-body {{ opacity: 1; transform: translateY(0); }}
    .stage-list {{ display: grid; gap: 10px; }}
    .stage-card {{
      border-left: 5px solid #c8b59d; border-radius: 10px; padding: 10px 12px; background: #fffdf9;
      box-shadow: 0 2px 12px rgba(84,53,24,0.04);
    }}
    .stage-head {{ display: flex; justify-content: space-between; gap: 12px; align-items: center; }}
    .stage-badge {{
      display: inline-block; padding: 4px 8px; border-radius: 999px; font-size: 12px; font-weight: 700;
      letter-spacing: 0.04em; text-transform: uppercase;
    }}
    .stage-design {{ border-left-color: #7057a3; }}
    .stage-design .stage-badge {{ background: #ebe5fa; color: #5b4687; }}
    .stage-badge.stage-design {{ background: #ebe5fa; color: #5b4687; }}
    .stage-plan {{ border-left-color: #2d6f9f; }}
    .stage-plan .stage-badge {{ background: #ddeef9; color: #23557b; }}
    .stage-badge.stage-plan {{ background: #ddeef9; color: #23557b; }}
    .stage-proof {{ border-left-color: #2e8161; }}
    .stage-proof .stage-badge {{ background: #dff4eb; color: #215f48; }}
    .stage-badge.stage-proof {{ background: #dff4eb; color: #215f48; }}
    .stage-review {{ border-left-color: #9a6a17; }}
    .stage-review .stage-badge {{ background: #f7edd3; color: #7d5712; }}
    .stage-badge.stage-review {{ background: #f7edd3; color: #7d5712; }}
    .stage-prune {{ border-left-color: #a22828; }}
    .stage-prune .stage-badge {{ background: #f8dddd; color: #8d2323; }}
    .stage-badge.stage-prune {{ background: #f8dddd; color: #8d2323; }}
    .stage-terminal {{ border-left-color: #6b6258; }}
    .stage-terminal .stage-badge {{ background: #ece7de; color: #584f45; }}
    .stage-badge.stage-terminal {{ background: #ece7de; color: #584f45; }}
    .stage-post-terminal-decision {{ border-left-color: #8a4b1d; }}
    .stage-post-terminal-decision .stage-badge {{ background: #f4e3d6; color: #7a4319; }}
    .stage-badge.stage-post-terminal-decision {{ background: #f4e3d6; color: #7a4319; }}
    .stage-other .stage-badge {{ background: #efe8de; color: #65584b; }}
    .stage-badge.stage-other {{ background: #efe8de; color: #65584b; }}
    .active-node {{
      box-shadow: 0 0 0 0 rgba(46,129,97,0.24);
      animation: riseIn 320ms ease both, pulseGlow 2.2s ease-in-out infinite;
    }}
    .flow-arrow {{
      color: var(--muted); font-size: 22px; line-height: 1; margin: 2px 0 6px 0;
    }}
    .stage-strip {{
      display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px;
    }}
    .timeline-note {{
      border-top: 1px dashed var(--line); margin-top: 10px; padding-top: 10px;
    }}
    .sticky {{ position: sticky; top: 20px; align-self: start; }}
    .flash {{
      margin-bottom: 16px; padding: 12px 14px; border-radius: 12px; border: 1px solid #cbb493; background: #fff3df;
    }}
    form.inline {{ display: inline; }}
    @keyframes riseIn {{
      from {{ opacity: 0; transform: translateY(10px); }}
      to {{ opacity: 1; transform: translateY(0); }}
    }}
    @keyframes pulseGlow {{
      0% {{ box-shadow: 0 0 0 0 rgba(46,129,97,0.20); }}
      70% {{ box-shadow: 0 0 0 14px rgba(46,129,97,0.0); }}
      100% {{ box-shadow: 0 0 0 0 rgba(46,129,97,0.0); }}
    }}
    @media (max-width: 920px) {{
      .grid.two, .grid.log {{ grid-template-columns: 1fr; }}
      .sticky {{ position: static; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <div class="topbar">
      <div>
        <div class="title">Candidate Memory Admin</div>
        <div class="sub">Local-only admin UI for browsing and deleting memory records.</div>
        <div class="nav">
          <a href="/">Memory</a>
          <a href="/search">Search Tree</a>
          <a href="/run">Run / Logs</a>
        </div>
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


def read_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        value = PID_FILE.read_text(encoding="utf-8").strip()
        return int(value) if value else None
    except (OSError, ValueError):
        return None


def is_pid_running(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def list_logs(limit: int = 40):
    if not LOG_DIR.exists():
        return []
    logs = [path for path in LOG_DIR.iterdir() if path.is_file() and path.suffix == ".log"]
    logs.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return logs[:limit]


def resolve_log_path(name: str):
    if not name:
        return None
    path = (LOG_DIR / name).resolve()
    try:
        path.relative_to(LOG_DIR.resolve())
    except ValueError:
        return None
    if not path.exists() or not path.is_file():
        return None
    return path


def read_log_tail(path: Path, max_chars: int = 100000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"[unable to read log: {exc}]"
    if len(text) <= max_chars:
        return text
    return "... [truncated] ...\n" + text[-max_chars:]


def collect_agent_events(text: str, limit: int = 80):
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    events = []
    prefixes = ("[", "▶️", "🗄️", "🚀", "✨", "⚠️")
    for line in lines:
        if line.startswith(prefixes):
            events.append(line)
        elif "PROOF_PLAN_JSON" in line or "POST_TERMINAL_DECISION" in line:
            events.append(line)
    return events[-limit:]


def count_errors(text: str) -> int:
    markers = ("Traceback", "RuntimeError", "Error:", "ReadTimeout", "RateLimitError", "missing non-empty")
    return sum(text.count(marker) for marker in markers)


def format_ts(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def stage_css_class(stage: str) -> str:
    normalized = str(stage or "").strip().lower().replace("_", "-")
    if normalized in {"design", "plan", "proof", "review", "prune", "terminal", "post-terminal-decision"}:
        return f"stage-{normalized}"
    return "stage-other"


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

    def _latest_records(self, conn):
        rows = fetch_latest_candidates(conn, limit=0)
        records = []
        for row in rows:
            record = candidate_from_snapshot_row(row)
            records.append({"row": row, "record": record})
        return records

    def _render_search_tree_node(self, node, children_map):
        row = node["row"]
        record = node["record"]
        candidate_link = f"/candidate?id={quote(record.candidate_id, safe='')}"
        latest_log = record.exploration_log[-1] if record.exploration_log else {}
        stage = str(latest_log.get("stage", "")).strip() or "[none]"
        stage_msg = str(latest_log.get("message", "")).strip()
        note = record.pruned_reason or (record.terminal_decision.get("rationale", "") if record.terminal_decision else "")
        props = summarize_record(record)
        stage_entries = "".join(self._render_stage_entry(entry, index) for index, entry in enumerate(record.exploration_log, start=1))
        active_cls = " active-node" if record.status == "active" else ""
        child_html = "".join(self._render_search_tree_node(child, children_map) for child in children_map.get(record.candidate_id, []))
        children_block = f'<div class="tree-children">{child_html}</div>' if child_html else ""
        return f"""
        <div class="tree-node{active_cls}">
          <div class="candidate-head">
            <div>
              <a href="{candidate_link}"><strong>{html.escape(record.candidate_id)}</strong></a>
              {status_badge(record.status)}
            </div>
            <div class="meta mono">snapshot={row['snapshot_id']}</div>
          </div>
          <div class="meta">derived_from={html.escape(record.derived_from or '[none]')}</div>
          <div class="meta">latest_stage=<span class="pill">{html.escape(stage)}</span></div>
          <div class="snippet mono">{html.escape(record.form)}</div>
          <div class="snippet"><strong>Properties:</strong> {html.escape(props)}</div>
          <div class="stage-strip">
            {''.join(f'<span class="stage-badge {stage_css_class(str(item.get("stage", "")))}">{html.escape(str(item.get("stage", "") or "[none]"))}</span>' for item in record.exploration_log)}
          </div>
          {f'<div class="snippet"><strong>Stage note:</strong> {html.escape(stage_msg)}</div>' if stage_msg else ''}
          {f'<div class="snippet"><strong>Why stopped:</strong> {html.escape(note[:600])}</div>' if note else ''}
          <details class="fold">
            <summary>Stage Timeline</summary>
            <div class="fold-body stage-list">
              {stage_entries or '<div class="meta">No exploration log recorded.</div>'}
            </div>
          </details>
          {children_block}
        </div>
        """

    def _render_stage_entry(self, entry, index: int):
        stage = str((entry or {}).get("stage", "")).strip() or "other"
        message = str((entry or {}).get("message", "")).strip()
        extras = []
        for key, value in (entry or {}).items():
            if key in {"stage", "message"}:
                continue
            rendered = json.dumps(value, ensure_ascii=False, indent=2) if isinstance(value, (dict, list)) else str(value)
            extras.append(f'<div class="snippet"><strong>{html.escape(str(key))}:</strong> {html.escape(rendered[:1200])}</div>')
        return f"""
        <div class="stage-card {stage_css_class(stage)}">
          <div class="stage-head">
            <span class="stage-badge">{html.escape(stage)}</span>
            <span class="meta mono">#{index}</span>
          </div>
          {f'<div class="snippet">{html.escape(message)}</div>' if message else ''}
          {''.join(extras)}
        </div>
        """

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
            <div class="danger-zone">
              <div><strong>Danger zone</strong></div>
              <div class="meta">Delete every latest candidate currently marked as active. Useful for clearing interrupted runs.</div>
              <form method="post" action="/delete-active" onsubmit="return confirm('Delete all active candidates and their snapshots?');">
                <button class="danger-btn" type="submit">Delete all active candidates</button>
              </form>
            </div>
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

    def _render_search(self, flash: str = ""):
        with connect_db(self.db_path) as conn:
            items = self._latest_records(conn)
        if not items:
            self._send_html("Search Tree", "<div class='panel'>No candidates found.</div>", flash=flash)
            return
        nodes = {item["record"].candidate_id: item for item in items}
        children_map = {}
        roots = []
        status_counts = {"active": 0, "pruned": 0, "passed": 0}
        recent_pruned = []
        recent_active = []
        for item in items:
            record = item["record"]
            status_counts[record.status] = status_counts.get(record.status, 0) + 1
            if record.status == "pruned":
                recent_pruned.append(item)
            if record.status == "active":
                recent_active.append(item)
            parent = record.derived_from or ""
            if parent and parent in nodes and parent != record.candidate_id:
                children_map.setdefault(parent, []).append(item)
            else:
                roots.append(item)
        roots.sort(key=lambda item: int(item["row"]["snapshot_id"]), reverse=True)
        for key in children_map:
            children_map[key].sort(key=lambda item: int(item["row"]["snapshot_id"]), reverse=True)
        tree_html = "".join(self._render_search_tree_node(root, children_map) for root in roots)
        timeline_items = []
        for item in sorted(items, key=lambda item: int(item["row"]["snapshot_id"])):
            record = item["record"]
            parent = nodes.get(record.derived_from or "")
            decision = (parent["record"].terminal_decision if parent else {}) or {}
            latest_stage = record.exploration_log[-1] if record.exploration_log else {}
            stage_strip = "".join(
                f'<span class="stage-badge {stage_css_class(str(log_item.get("stage", "")))}">{html.escape(str(log_item.get("stage", "") or "[none]"))}</span>'
                for log_item in record.exploration_log
            )
            timeline_items.append(
                f"""
                <div class="timeline-item">
                  <div class="candidate-head">
                    <div>
                      <a href="/candidate?id={quote(record.candidate_id, safe='')}"><strong>{html.escape(record.candidate_id)}</strong></a>
                      {status_badge(record.status)}
                    </div>
                    <div class="meta mono">snapshot={item['row']['snapshot_id']}</div>
                  </div>
                  <div class="meta">derived_from={html.escape(record.derived_from or '[none]')}</div>
                  <div class="flow-arrow">↓</div>
                  {f'<div class="snippet"><strong>Spawned by post_terminal_decision:</strong> {html.escape(decision.get("action", "[none]"))}</div>' if parent else '<div class="snippet"><strong>Root candidate</strong></div>'}
                  {f'<div class="snippet"><strong>Parent rationale:</strong> {html.escape(str(decision.get("rationale", ""))[:700])}</div>' if parent and decision.get("rationale") else ''}
                  {f'<div class="snippet"><strong>Parent next_direction:</strong> {html.escape(str(decision.get("next_direction", ""))[:700])}</div>' if parent and decision.get("next_direction") else ''}
                  <div class="timeline-note">
                    <div class="meta">latest_stage={html.escape(str(latest_stage.get("stage", "[none]")))}</div>
                    <div class="stage-strip">{stage_strip or '<span class="meta">No stages</span>'}</div>
                  </div>
                </div>
                """
            )
        pruned_html = []
        for item in recent_pruned[:8]:
            record = item["record"]
            pruned_html.append(
                f"""
                <div class="candidate">
                  <div class="candidate-head">
                    <a href="/candidate?id={quote(record.candidate_id, safe='')}"><strong>{html.escape(record.candidate_id)}</strong></a>
                    <div class="meta mono">snapshot={item['row']['snapshot_id']}</div>
                  </div>
                  <div class="snippet">{html.escape(record.pruned_reason[:500] or '[none]')}</div>
                </div>
                """
            )
        active_html = []
        for item in recent_active[:8]:
            record = item["record"]
            latest_stage = record.exploration_log[-1] if record.exploration_log else {}
            active_html.append(
                f"""
                <div class="candidate">
                  <div class="candidate-head">
                    <a href="/candidate?id={quote(record.candidate_id, safe='')}"><strong>{html.escape(record.candidate_id)}</strong></a>
                    <div>{status_badge(record.status)}</div>
                  </div>
                  <div class="meta">derived_from={html.escape(record.derived_from or '[none]')}</div>
                  <div class="snippet"><strong>Stage:</strong> {html.escape(str(latest_stage.get('stage', '[none]')))}</div>
                  <div class="snippet">{html.escape(str(latest_stage.get('message', ''))[:300])}</div>
                </div>
                """
            )
        stats_html = "".join(
            f'<div class="stat"><div class="k">{html.escape(key)}</div><div class="v">{html.escape(str(value))}</div></div>'
            for key, value in {
                "Roots": len(roots),
                "Active": status_counts.get("active", 0),
                "Pruned": status_counts.get("pruned", 0),
                "Passed": status_counts.get("passed", 0),
                "Latest snapshots": len(items),
            }.items()
        )
        body = f"""
        <div class="grid two">
          <div class="grid">
            <div class="panel sticky">
              <h2>Search Overview</h2>
              <div class="stats">{stats_html}</div>
              <div class="snippet">This view is built from the latest snapshot of each candidate in the memory database.</div>
            </div>
            <div class="panel">
              <h3>Active Frontier</h3>
              <form class="toolbar" method="post" action="/delete-active" onsubmit="return confirm('Delete all active candidates and their snapshots?');">
                <button class="danger-btn" type="submit">Delete all active candidates</button>
              </form>
              <div class="card-list">{''.join(active_html) or '<div class="candidate">No active candidates.</div>'}</div>
            </div>
            <div class="panel">
              <h3>Recent Pruning</h3>
              <div class="card-list">{''.join(pruned_html) or '<div class="candidate">No pruned candidates.</div>'}</div>
            </div>
            <div class="panel">
              <h3>Spawn Timeline</h3>
              <div class="timeline">{''.join(timeline_items) or '<div class="candidate">No timeline data.</div>'}</div>
            </div>
          </div>
          <div class="panel">
            <h2>Candidate Search Tree</h2>
            <div class="tree-column">{tree_html}</div>
          </div>
        </div>
        """
        self._send_html("Search Tree", body, flash=flash)

    def _render_run(self, flash: str = ""):
        query = self._parse_query()
        selected_name = (query.get("log", [""])[-1] or "").strip()
        auto_refresh = (query.get("refresh", ["1"])[-1] or "1").strip() != "0"
        logs = list_logs(limit=30)
        chosen = resolve_log_path(selected_name) if selected_name else (logs[0] if logs else None)
        pid = read_pid()
        running = is_pid_running(pid)
        log_text = read_log_tail(chosen, max_chars=100000) if chosen else ""
        events = collect_agent_events(log_text, limit=100)
        refresh_script = '<script>setTimeout(function(){ location.reload(); }, 3000);</script>' if auto_refresh else ""
        logs_html = []
        for path in logs:
            logs_html.append(
                f"""
                <div class="candidate">
                  <div class="candidate-head">
                    <a href="/run?{urlencode({'log': path.name, 'refresh': '1' if auto_refresh else '0'})}"><strong>{html.escape(path.name)}</strong></a>
                    <div class="meta">{format_ts(path.stat().st_mtime)}</div>
                  </div>
                  <div class="meta">size={path.stat().st_size / 1024:.1f} KB</div>
                </div>
                """
            )
        events_html = "".join(f'<div class="mono" style="margin-bottom:8px">{html.escape(line)}</div>' for line in events) or '<div class="meta">No recent agent events parsed.</div>'
        stats_html = "".join(
            f'<div class="stat"><div class="k">{html.escape(key)}</div><div class="v">{html.escape(str(value))}</div></div>'
            for key, value in {
                "Process": "running" if running else "stopped",
                "PID": pid if pid is not None else "[none]",
                "Logs": len(logs),
                "Errors in log": count_errors(log_text) if log_text else 0,
            }.items()
        )
        raw_link = f"/api/log?{urlencode({'name': chosen.name})}" if chosen else "#"
        body = f"""
        {refresh_script}
        <div class="grid log">
          <div class="grid">
            <div class="panel sticky">
              <h2>Run Status</h2>
              <div class="stats">{stats_html}</div>
              <div class="toolbar">
                <form method="post" action="/start-main">
                  <button type="submit">Start Research Run</button>
                </form>
                <form method="post" action="/stop-main">
                  <button class="danger-btn" type="submit">Stop Research Run</button>
                </form>
                <a href="/run?{urlencode({'log': chosen.name if chosen else '', 'refresh': '1'})}">Auto-refresh on</a>
                <a href="/run?{urlencode({'log': chosen.name if chosen else '', 'refresh': '0'})}">Auto-refresh off</a>
              </div>
              <div class="snippet">Latest log refreshes every 3s when auto-refresh is on.</div>
            </div>
            <div class="panel">
              <h3>Recent Logs</h3>
              <div class="card-list">{''.join(logs_html) or '<div class="candidate">No logs found.</div>'}</div>
            </div>
            <div class="panel">
              <h3>Parsed Agent Events</h3>
              <div class="agentbox">{events_html}</div>
            </div>
          </div>
          <div class="panel">
            <h2>{html.escape(chosen.name if chosen else 'No log selected')}</h2>
            <div class="toolbar">
              <a href="{raw_link}">Raw tail</a>
            </div>
            <div class="logbox">{html.escape(log_text or 'No log available.')}</div>
          </div>
        </div>
        """
        self._send_html("Run / Logs", body, flash=flash)

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
        if parsed.path == "/search":
            self._render_search(flash=flash)
            return
        if parsed.path == "/run":
            self._render_run(flash=flash)
            return
        if parsed.path == "/api/log":
            name = (query.get("name", [""])[-1] or "").strip()
            path = resolve_log_path(name)
            if not path:
                payload = b"unknown log"
                self.send_response(HTTPStatus.NOT_FOUND)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            payload = read_log_tail(path, max_chars=120000).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
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
        if parsed.path == "/start-main":
            pid = read_pid()
            if pid and is_pid_running(pid):
                self._redirect("/run?" + urlencode({"flash": f"Research run is already active with PID {pid}"}))
                return
            try:
                result = subprocess.run(
                    ["bash", str(START_SCRIPT)],
                    cwd=str(ROOT),
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=30,
                )
                output = (result.stdout or "").strip() or (result.stderr or "").strip() or "start_main.sh finished."
            except Exception as exc:
                output = f"Failed to start research run: {exc}"
            self._redirect("/run?" + urlencode({"flash": output}))
            return
        if parsed.path == "/stop-main":
            pid = read_pid()
            if not pid:
                self._redirect("/run?" + urlencode({"flash": "No main.pid found"}))
                return
            try:
                os.kill(pid, signal.SIGTERM)
                output = f"Sent SIGTERM to PID {pid}"
            except OSError as exc:
                output = f"Failed to stop PID {pid}: {exc}"
            self._redirect("/run?" + urlencode({"flash": output}))
            return
        if parsed.path == "/delete-candidate":
            candidate_id = (form.get("candidate_id") or "").strip()
            if not candidate_id:
                self._redirect("/?" + urlencode({"flash": "Missing candidate_id"}))
                return
            with connect_db(self.db_path) as conn:
                delete_candidate(conn, candidate_id)
            self._redirect("/?" + urlencode({"flash": f"Deleted candidate {candidate_id}"}))
            return
        if parsed.path == "/delete-active":
            with connect_db(self.db_path) as conn:
                deleted = delete_candidates_by_status(conn, "active")
            count = len(deleted or [])
            flash = "No active candidates to delete." if count <= 0 else f"Deleted {count} active candidate(s)."
            self._redirect("/?" + urlencode({"flash": flash}))
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
