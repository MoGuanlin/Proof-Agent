import argparse
import html
import json
import os
import re
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, quote, urlencode, urlparse

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
    list_namespaces,
    resolve_db_path,
)
from proof_agent.paths import LOGS_DIR, MAIN_PID_FILE, PROJECT_ROOT, START_SCRIPT_FILE

ROOT = PROJECT_ROOT
LOG_DIR = LOGS_DIR
PID_FILE = MAIN_PID_FILE
START_SCRIPT = START_SCRIPT_FILE


def html_page(title: str, body: str, flash: str = "", ns_bar: str = "") -> bytes:
    flash_html = ""
    if flash:
        flash_html = f'<div class="flash">{html.escape(flash)}</div>'
    ns_bar_html = f'<div class="ns-line"><span class="meta">namespace:</span>{ns_bar}</div>' if ns_bar else ""
    rendered = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css">
  <script defer src="https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js"></script>
  <script defer src="https://cdn.jsdelivr.net/npm/dompurify@3.1.6/dist/purify.min.js"></script>
  <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"></script>
  <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/contrib/auto-render.min.js"></script>
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
    html {{ font-size: 15px; max-width: 100%; overflow-x: hidden; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
        "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans", Arial, sans-serif;
      line-height: 1.55;
      background:
        radial-gradient(circle at top right, rgba(145,79,30,0.08), transparent 28rem),
        linear-gradient(180deg, #fbf7f0 0%, var(--bg) 100%);
      color: var(--ink);
      max-width: 100%;
      overflow-x: hidden;
    }}
    h1, h2, h3, .title {{
      font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Georgia, serif;
      letter-spacing: 0.005em;
    }}
    h2 {{ font-size: 20px; }}
    h3 {{ font-size: 16px; }}
    a {{ color: var(--accent); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .shell {{ max-width: 1180px; width: 100%; margin: 0 auto; padding: clamp(14px, 2.2vw, 22px); }}
    .shell, .panel, .candidate, .tree-node, .timeline-item, .stage-card, .history-item,
    .candidate-head, .candidate-title, .prop-head, .stage-head, .toolbar, .search-bar,
    .stats, .grid, .tree-column, .tree-children, .stage-list, .history, .agentbox,
    .json, .logbox, .snippet, .meta, .title, .nav, .ns-line {{
      min-width: 0;
      max-width: 100%;
    }}
    .topbar {{
      display: flex; gap: 16px; justify-content: space-between; align-items: baseline;
      border-bottom: 1px solid var(--line); padding-bottom: 14px; margin-bottom: 18px;
    }}
    .title {{ font-size: 28px; font-weight: 700; }}
    .sub {{ color: var(--muted); font-size: 13px; }}
    .nav {{ display: flex; gap: 10px; flex-wrap: wrap; margin-top: 8px; }}
    .nav a {{
      padding: 6px 11px; border: 1px solid var(--line); border-radius: 999px;
      background: rgba(255,255,255,0.78); font-size: 13px;
    }}
    /* text truncation */
    .clamp-1, .clamp-2, .clamp-3 {{
      display: -webkit-box; -webkit-box-orient: vertical; overflow: hidden;
      word-break: break-word;
    }}
    .clamp-1 {{ -webkit-line-clamp: 1; }}
    .clamp-2 {{ -webkit-line-clamp: 2; }}
    .clamp-3 {{ -webkit-line-clamp: 3; }}
    .truncate {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .chip-row {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }}
    .chip {{
      display: inline-flex; align-items: center; gap: 4px;
      padding: 2px 8px; border-radius: 999px; font-size: 12px;
      background: #f1e6d6; color: #6a4526; line-height: 1.6;
    }}
    .chip.prop-pass  {{ background: #d6ead8; color: #1f5c29; }}
    .chip.prop-fail  {{ background: #f5d1d1; color: #7e1a1a; }}
    .chip.prop-hypothesis {{ background: #f7edd3; color: #7d5712; }}
    .chip.prop-untested  {{ background: #ece7de; color: #584f45; }}
    .chip.prop-planned  {{ background: #ede0cf; color: #603c1f; }}
    .grid {{ display: grid; gap: 14px; }}
    .grid.two {{ grid-template-columns: minmax(0, 320px) minmax(0, 1fr); }}
    .grid.log {{ grid-template-columns: minmax(0, 300px) minmax(0, 1fr); }}
    .panel {{
      background: rgba(255,250,240,0.92);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 16px 18px;
      box-shadow: 0 6px 22px rgba(84, 53, 24, 0.05);
    }}
    .panel h2, .panel h3 {{ margin: 0 0 10px 0; }}
    .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 10px; }}
    .stat {{
      padding: 10px 12px; border: 1px solid var(--line); border-radius: 10px;
      background: rgba(255,255,255,0.72);
    }}
    .stat .k {{ color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; }}
    .stat .v {{ margin-top: 3px; font-size: 22px; font-weight: 700; line-height: 1.2; }}
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
    .card-list {{ display: grid; gap: 10px; }}
    .candidate {{
      padding: 12px 14px; border: 1px solid var(--line); border-radius: 12px;
      background: rgba(255,255,255,0.82);
    }}
    .candidate-head {{
      display: flex; gap: 10px; justify-content: space-between; align-items: center;
      flex-wrap: wrap; row-gap: 4px;
    }}
    .candidate-title {{
      display: flex; align-items: center; gap: 8px; min-width: 0;
      font-weight: 600;
    }}
    .candidate-title a {{
      min-width: 0; max-width: 100%; overflow: hidden; text-overflow: ellipsis;
      overflow-wrap: anywhere; word-break: break-word;
    }}
    .badge {{
      display: inline-block; padding: 3px 8px; border-radius: 999px; font-size: 11.5px;
      font-weight: 600; letter-spacing: 0.02em; background: #ede0cf; color: #603c1f;
    }}
    .badge.pruned {{ background: #f5d1d1; color: #7e1a1a; }}
    .badge.active {{ background: #d6ead8; color: #1f5c29; }}
    .badge.passed {{ background: #dfeaf7; color: #1d4f88; }}
    .mono {{ font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace; font-size: 12.5px; }}
    .meta {{ color: var(--muted); font-size: 12.5px; }}
    .meta.mono {{ font-size: 12px; }}
    .snippet {{ margin-top: 6px; white-space: pre-wrap; word-break: break-word; font-size: 13.5px; }}
    .snippet.mono {{ font-size: 12.5px; background: rgba(255,255,255,0.6);
      padding: 8px 10px; border-radius: 8px; border: 1px solid var(--line); }}
    .snippet, .json, .logbox, .agentbox, .meta, .math-text, .math-text-inline, .rich-content {{
      overflow-wrap: anywhere;
      word-break: break-word;
    }}
    details.inline-fold > summary {{
      cursor: pointer; list-style: none; color: var(--accent); font-size: 12.5px;
      padding: 2px 0; user-select: none;
    }}
    details.inline-fold > summary::-webkit-details-marker {{ display: none; }}
    details.inline-fold > summary::before {{ content: "▸ "; }}
    details.inline-fold[open] > summary::before {{ content: "▾ "; }}
    .json {{
      white-space: pre-wrap; overflow-x: hidden; font-family: "SFMono-Regular", Consolas, monospace;
      font-size: 13px; line-height: 1.45; background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 14px;
    }}
    .history {{ display: grid; gap: 10px; }}
    .history-item {{ border-top: 1px solid var(--line); padding-top: 10px; }}
    .tree-column {{ display: grid; gap: 10px; }}
    .tree-node {{
      border: 1px solid var(--line); border-radius: 12px; padding: 12px 14px;
      background: rgba(255,255,255,0.85); animation: riseIn 300ms ease both;
    }}
    .tree-children {{
      margin-top: 10px; margin-left: 8px; padding-left: 14px;
      border-left: 2px solid #e5d5be;
      display: grid; gap: 10px;
    }}
    .pill {{
      display: inline-block; padding: 4px 8px; border-radius: 999px; background: #f1e6d6; color: #6a4526; font-size: 12px;
    }}
    .logbox {{
      white-space: pre-wrap; overflow-x: hidden; font-family: "SFMono-Regular", Consolas, monospace;
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
    .stage-head {{ display: flex; justify-content: space-between; gap: 12px; align-items: center; flex-wrap: wrap; }}
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
    .flow-arrow {{
      color: var(--muted); font-size: 20px; line-height: 1; margin: 2px 0 6px 0;
    }}
    .stage-strip {{
      display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; align-items: center;
    }}
    .stage-strip .stage-badge {{ padding: 2px 7px; font-size: 10.5px; letter-spacing: 0.03em; }}
    .stage-strip .more-count {{
      color: var(--muted); font-size: 11.5px; padding: 2px 6px;
      border: 1px dashed var(--line); border-radius: 999px;
    }}
    .active-node {{
      box-shadow: 0 0 0 0 rgba(46,129,97,0.16);
      animation: riseIn 300ms ease both, pulseGlow 3.6s ease-in-out infinite;
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
      0% {{ box-shadow: 0 0 0 0 rgba(46,129,97,0.16); }}
      70% {{ box-shadow: 0 0 0 10px rgba(46,129,97,0); }}
      100% {{ box-shadow: 0 0 0 0 rgba(46,129,97,0); }}
    }}
    @media (max-width: 920px) {{
      .grid.two, .grid.log {{ grid-template-columns: 1fr; }}
      .sticky {{ position: static; }}
    }}
    .live-indicator {{
      position: fixed; bottom: 18px; right: 18px; z-index: 9999;
      padding: 10px 14px; border-radius: 999px;
      background: #2d6f9f; color: white; font: 13px/1.3 system-ui, sans-serif;
      box-shadow: 0 6px 18px rgba(0,0,0,0.2); display: none;
    }}
    .live-indicator.ok {{ background: #2e8161; }}
    .live-indicator.warn {{ background: #9a6a17; }}
    .live-indicator.err  {{ background: #a22828; }}
    .ns-switcher {{
      font: inherit; font-size: 13px; padding: 6px 10px;
      border: 1px solid var(--line); border-radius: 999px;
      background: rgba(255,255,255,0.82); color: var(--ink);
      max-width: 460px;
    }}
    .ns-line {{ margin-top: 8px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
    .search-bar {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }}
    .search-bar input[type=text] {{
      flex: 1 1 220px; min-width: 0; padding: 8px 10px;
      border: 1px solid var(--line); border-radius: 10px; background: white; font: inherit;
    }}
    .search-bar .status-chips {{ display: flex; gap: 6px; flex-wrap: wrap; }}
    .search-bar .status-chips label {{
      padding: 6px 10px; border: 1px solid var(--line); border-radius: 999px;
      background: rgba(255,255,255,0.78); font-size: 12.5px; cursor: pointer;
      display: inline-flex; gap: 4px; align-items: center;
    }}
    .search-bar .status-chips input {{ accent-color: var(--accent); }}
    .search-bar button {{
      padding: 7px 14px; border: 0; border-radius: 10px; background: var(--accent);
      color: white; font: inherit; cursor: pointer;
    }}
    .prop-detail {{
      border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px;
      background: rgba(255,255,255,0.72); margin-top: 8px;
    }}
    .prop-detail:target {{ box-shadow: 0 0 0 3px rgba(145,79,30,0.25); background: #fff8e6; }}
    .prop-head {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
    .math-text {{ white-space: pre-wrap; }}
    .math-text-inline {{ display: inline; }}
    .rich-content {{
      font-size: 13.5px;
      line-height: 1.65;
    }}
    .rich-content > :first-child {{ margin-top: 0; }}
    .rich-content > :last-child {{ margin-bottom: 0; }}
    .rich-content p, .rich-content ul, .rich-content ol, .rich-content blockquote,
    .rich-content pre, .rich-content table {{
      margin: 0 0 0.85em 0;
    }}
    .rich-content ul, .rich-content ol {{ padding-left: 1.35em; }}
    .rich-content blockquote {{
      margin-left: 0;
      padding-left: 12px;
      border-left: 3px solid #d7c7b0;
      color: #5f554a;
    }}
    .rich-content code {{
      font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
      font-size: 0.92em;
      background: rgba(145,79,30,0.09);
      padding: 0.1em 0.35em;
      border-radius: 6px;
    }}
    .rich-content pre {{
      background: rgba(255,255,255,0.92);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 10px 12px;
      overflow: auto;
      white-space: pre-wrap;
    }}
    .rich-content pre code {{
      background: transparent;
      padding: 0;
      border-radius: 0;
    }}
    .rich-content table {{
      display: block;
      width: 100%;
      overflow-x: auto;
      border-collapse: collapse;
    }}
    .rich-content th, .rich-content td {{
      border: 1px solid var(--line);
      padding: 6px 8px;
      text-align: left;
    }}
    .rich-content img {{ max-width: 100%; height: auto; }}
    .katex-display {{
      max-width: 100%;
      overflow-x: auto;
      overflow-y: hidden;
      padding: 0.2rem 0;
    }}
    .formula-render {{
      width: 100%;
      max-width: 100%;
      overflow-x: auto;
      overflow-y: hidden;
      padding: 0.1rem 0;
      font-size: 1rem;
    }}
    .formula-render.compact {{
      font-size: 0.96rem;
    }}
    .formula-render .katex-display {{
      margin: 0.1rem 0;
    }}
    .formula-render[data-render-failed="1"] {{
      white-space: pre-wrap;
      word-break: break-word;
      font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
      font-size: 12.5px;
      background: rgba(255,255,255,0.6);
      padding: 8px 10px;
      border-radius: 8px;
      border: 1px solid var(--line);
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
          <a href="/" data-nav="/">Memory</a>
          <a href="/search" data-nav="/search">Search Tree</a>
          <a href="/run" data-nav="/run">Run / Logs</a>
        </div>
        {ns_bar_html}
      </div>
      <div class="sub"><a href="/">Home</a></div>
    </div>
    {flash_html}
    {body}
  </div>
  <div id="live-indicator" class="live-indicator">live</div>
  <script>
  (function() {{
    // ------------ namespace-aware navigation ------------
    var currentParams = new URLSearchParams(window.location.search);
    var currentNs = currentParams.get('ns');
    function refreshCurrentNs() {{
      currentParams = new URLSearchParams(window.location.search);
      currentNs = currentParams.get('ns');
      var nsSelect = document.querySelector('.ns-switcher');
      if (currentNs === null && nsSelect) {{
        currentNs = nsSelect.value || null;
      }}
    }}
    refreshCurrentNs();
    function withNs(url) {{
      if (currentNs === null) return url;
      try {{
        var u = new URL(url, window.location.origin);
        if (!u.searchParams.has('ns')) u.searchParams.set('ns', currentNs);
        return u.pathname + (u.search || '') + (u.hash || '');
      }} catch (_) {{ return url; }}
    }}
    function rewriteNamespaceLinks(root) {{
      var scope = root || document;
      scope.querySelectorAll('a[data-nav]').forEach(function(a) {{
        a.setAttribute('href', withNs(a.getAttribute('data-nav')));
      }});
      scope.querySelectorAll('a[href^="/"]').forEach(function(a) {{
        if (a.hasAttribute('data-nav')) return;
        var href = a.getAttribute('href');
        if (!href || href.startsWith('//') || href.startsWith('/api/')) return;
        try {{
          var u = new URL(href, window.location.origin);
          if (!u.searchParams.has('ns') && currentNs !== null) {{
            u.searchParams.set('ns', currentNs);
            a.setAttribute('href', u.pathname + u.search + (u.hash || ''));
          }}
        }} catch (_) {{}}
      }});
    }}
    var mathDelimiters = [
      {{ left: '$$', right: '$$', display: true }},
      {{ left: '\\\\[', right: '\\\\]', display: true }},
      {{ left: '$', right: '$', display: false }},
      {{ left: '\\\\(', right: '\\\\)', display: false }}
    ];
    function renderRichContent(root) {{
      var scope = root || document;
      if (window.katex) {{
        scope.querySelectorAll('.formula-render').forEach(function(el) {{
          if (el.dataset.mathRendered === '1') return;
          var tex = el.getAttribute('data-tex') || el.textContent || '';
          try {{
            window.katex.render(tex, el, {{
              displayMode: true,
              throwOnError: false,
              strict: 'ignore'
            }});
            el.dataset.mathRendered = '1';
          }} catch (_) {{
            el.dataset.renderFailed = '1';
          }}
        }});
      }}
      if (window.marked && window.DOMPurify) {{
        scope.querySelectorAll('.md-block').forEach(function(el) {{
          if (el.dataset.mdRendered === '1') return;
          try {{
            var rendered = window.marked.parse(el.textContent || '', {{
              breaks: true,
              gfm: true,
              mangle: false,
              headerIds: false
            }});
            el.innerHTML = window.DOMPurify.sanitize(rendered);
            el.classList.add('rich-content');
            el.dataset.mdRendered = '1';
          }} catch (_) {{}}
        }});
      }}
      if (typeof window.renderMathInElement === 'function') {{
        scope.querySelectorAll('.math-text, .math-text-inline, .md-block, .rich-content').forEach(function(el) {{
          if (el.dataset.mathRendered === '1') return;
          try {{
            window.renderMathInElement(el, {{
              delimiters: mathDelimiters,
              throwOnError: false,
              strict: 'ignore'
            }});
            el.dataset.mathRendered = '1';
          }} catch (_) {{}}
        }});
      }}
    }}
    window.proofAgentSwitchNs = function(value) {{
      var u = new URL(window.location.href);
      if (value === '__all__' || value === '') {{
        u.searchParams.set('ns', '__all__');
      }} else {{
        u.searchParams.set('ns', value);
      }}
      window.location.href = u.pathname + u.search;
    }};

    // ------------ preserve details[open] + scrollY across reload ------------
    var STATE_KEY = 'proofAgentUiState:' + window.location.pathname + window.location.search;
    function captureUiState() {{
      try {{
        var opens = [];
        document.querySelectorAll('details').forEach(function(d) {{
          if (d.open && d.id) opens.push(d.id);
        }});
        sessionStorage.setItem(STATE_KEY, JSON.stringify({{
          opens: opens, y: window.scrollY || 0, t: Date.now()
        }}));
      }} catch(_) {{}}
    }}
    function restoreUiState() {{
      try {{
        var raw = sessionStorage.getItem(STATE_KEY);
        if (!raw) return;
        var state = JSON.parse(raw);
        if (!state) return;
        (state.opens || []).forEach(function(id) {{
          var el = document.getElementById(id);
          if (el) el.setAttribute('open', '');
        }});
        if (typeof state.y === 'number') window.scrollTo(0, state.y);
      }} catch(_) {{}}
    }}
    window.addEventListener('beforeunload', captureUiState);
    window.proofAgentPostSwap = function(root) {{
      refreshCurrentNs();
      rewriteNamespaceLinks(root || document);
      renderRichContent(root || document);
    }};
    document.addEventListener('DOMContentLoaded', function() {{
      restoreUiState();
      if (typeof window.proofAgentPostSwap === 'function') window.proofAgentPostSwap(document);
    }});

    // ------------ live updates via SSE ------------
    if (!window.EventSource) return;
    var path = window.location.pathname;
    var sseParams = new URLSearchParams();
    if (currentNs !== null && currentNs !== '__all__') sseParams.set('ns', currentNs);
    if (path === '/candidate') {{
      var cid = currentParams.get('id') || '';
      if (cid) sseParams.set('candidate', cid);
    }}
    var qs = sseParams.toString();
    if (qs) qs = '?' + qs;
    var ind = document.getElementById('live-indicator');
    function show(msg, cls) {{
      if (!ind) return;
      ind.textContent = msg;
      ind.className = 'live-indicator ' + (cls || '');
      ind.style.display = 'block';
    }}
    function hide() {{ if (ind) ind.style.display = 'none'; }}

    async function morphReload(msg) {{
      show(msg + ' — updating…', 'ok');
      captureUiState();
      try {{
        var res = await fetch(window.location.href, {{ credentials: 'same-origin' }});
        if (!res.ok) throw new Error('HTTP ' + res.status);
        var text = await res.text();
        var doc = new DOMParser().parseFromString(text, 'text/html');
        var newShell = doc.querySelector('.shell');
        var curShell = document.querySelector('.shell');
        if (newShell && curShell) {{
          curShell.innerHTML = newShell.innerHTML;
          // Re-run scripts that need DOM-ready logic (namespace rewriter, fold restore)
          if (typeof window.proofAgentPostSwap === 'function') window.proofAgentPostSwap();
          restoreUiState();
          show('updated', 'ok');
          setTimeout(hide, 900);
        }} else {{
          window.location.reload();
        }}
      }} catch (e) {{
        show('update failed: ' + e.message + ' — reloading', 'err');
        setTimeout(function() {{ window.location.reload(); }}, 800);
      }}
    }}

    var lastUpdate = 0;
    var pending = false;
    function scheduleMorph(msg) {{
      if (pending) return;
      pending = true;
      var wait = Math.max(0, 700 - (Date.now() - lastUpdate));
      setTimeout(function() {{
        pending = false;
        lastUpdate = Date.now();
        morphReload(msg);
      }}, wait);
    }}

    var es = new EventSource('/api/events' + qs);
    es.addEventListener('open', function() {{ show('connected', 'ok'); setTimeout(hide, 1200); }});
    es.addEventListener('snapshot', function(ev) {{
      try {{
        var data = JSON.parse(ev.data);
        scheduleMorph('snapshot #' + data.snapshot_id + ' (' + data.candidate_id + ')');
      }} catch(_) {{ scheduleMorph('new snapshot'); }}
    }});
    es.addEventListener('log', function(ev) {{
      try {{
        var data = JSON.parse(ev.data);
        if (path === '/run') scheduleMorph('log updated');
        else show('log tick: ' + (data.path || ''), '');
      }} catch(_) {{}}
    }});
    es.addEventListener('error', function() {{ show('reconnecting…', 'warn'); }});

  }})();
  </script>
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


def property_chips(record, candidate_link_prefix: str = "") -> str:
    chips = []
    for prop, detail in (record.property_status or {}).items():
        status = str((detail or {}).get("status", "")).strip() or "untested"
        safe_status = "".join(c if c.isalnum() or c in "-_" else "-" for c in status.lower()) or "untested"
        chip_inner = (
            f'{html.escape(prop)}<span class="meta" style="font-size:10.5px">·</span>'
            f'{html.escape(status)}'
        )
        chip_cls = f"chip prop-{html.escape(safe_status)}"
        if candidate_link_prefix:
            href = f"{candidate_link_prefix}#prop-{quote(prop, safe='')}"
            chips.append(
                f'<a class="{chip_cls}" href="{href}" title="Open property detail">{chip_inner}</a>'
            )
        else:
            chips.append(f'<span class="{chip_cls}">{chip_inner}</span>')
    return f'<div class="chip-row">{"".join(chips)}</div>' if chips else '<span class="meta">no properties</span>'


def truncate_text(text: str, limit: int = 220) -> str:
    s = str(text or "")
    if len(s) <= limit:
        return s
    return s[:limit].rstrip() + "…"


FORM_SPLIT_MARKERS = (
    ", where ",
    "; where ",
    ". where ",
    ". Where ",
    ". The target function ",
    " The target function ",
    ". Target parameters:",
    " Target parameters:",
    ". The correction term ",
    " The correction term ",
)


def split_formula_text(text: str) -> tuple[str, str]:
    source = str(text or "").strip()
    if not source:
        return "", ""
    split_at = None
    for marker in FORM_SPLIT_MARKERS:
        pos = source.find(marker)
        if pos > 0 and (split_at is None or pos < split_at):
            split_at = pos
    if split_at is None:
        return source, ""
    head = source[:split_at].strip().rstrip(",;")
    tail = source[split_at:].strip().lstrip(",; ")
    return head, tail


def normalize_formula_tex(text: str) -> str:
    source = str(text or "").strip()
    if not source:
        return ""
    tex = source
    literal_replacements = {
        "Phi_O": r"\Phi_O",
        "Upsilon_O": r"\Upsilon_O",
        "phi_O": r"\phi_O",
        ">=": r" \ge ",
        "<=": r" \le ",
        "!=": r" \ne ",
        "->": r" \to ",
    }
    for old, new in literal_replacements.items():
        tex = tex.replace(old, new)
    symbol_words = {
        "Phi": r"\Phi",
        "Upsilon": r"\Upsilon",
        "varphi": r"\varphi",
        "phi": r"\phi",
        "lambda": r"\lambda",
        "rho": r"\rho",
        "mu": r"\mu",
        "alpha": r"\alpha",
        "beta": r"\beta",
        "gamma": r"\gamma",
        "delta": r"\delta",
        "pi": r"\pi",
        "sum": r"\sum",
        "sqrt": r"\sqrt",
        "cos": r"\cos",
        "sin": r"\sin",
        "tan": r"\tan",
    }
    for word, command in symbol_words.items():
        tex = re.sub(rf"(?<!\\)\b{re.escape(word)}(?=\b|_)", lambda _m, c=command: c, tex)
    tex = re.sub(r"\\sqrt\(([^()]+)\)", r"\\sqrt{\1}", tex)
    tex = re.sub(r"\|\|(.+?)\|\|", lambda m: r"\lVert " + m.group(1).strip() + r" \rVert", tex)
    tex = re.sub(r"\s*\*\s*", lambda _m: r" \cdot ", tex)
    tex = re.sub(r"\s+", " ", tex).strip()
    return tex


def normalize_namespace_value(raw: str | None) -> str | None:
    if raw is None:
        return None
    value = str(raw).strip()
    if value in {"", NS_SENTINEL_ALL, "all"}:
        return None
    return value


def explicit_namespace_value(raw: str | None) -> str | None:
    if raw is None:
        return None
    value = str(raw).strip()
    if value in {"", NS_SENTINEL_ALL, "all"}:
        return NS_SENTINEL_ALL
    return value


def with_namespace_param(path: str, namespace_value: str | None) -> str:
    if namespace_value is None:
        return path
    parsed = urlparse(path)
    pairs = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key != "ns"]
    pairs.append(("ns", namespace_value))
    return parsed._replace(query=urlencode(pairs)).geturl()


def math_text_block(text: str, classes: str = "", tag: str = "div") -> str:
    cls = "math-text"
    if classes:
        cls += f" {classes}"
    return f'<{tag} class="{cls}">{html.escape(str(text or ""))}</{tag}>'


def inline_math_text(text: str, classes: str = "") -> str:
    cls = "math-text-inline"
    if classes:
        cls += f" {classes}"
    return f'<span class="{cls}">{html.escape(str(text or ""))}</span>'


def markdown_block(text: str, classes: str = "", tag: str = "div") -> str:
    cls = "md-block"
    if classes:
        cls += f" {classes}"
    return f'<{tag} class="{cls}">{html.escape(str(text or ""))}</{tag}>'


def equation_markup(text: str, preview_tail_limit: int = 0, compact: bool = False) -> str:
    source = str(text or "").strip()
    if not source:
        return ""
    formula_raw, tail = split_formula_text(source)
    formula_tex = normalize_formula_tex(formula_raw)
    formula_classes = "formula-render"
    if compact:
        formula_classes += " compact"
    pieces = [
        f'<div class="{formula_classes}" data-tex="{html.escape(formula_tex)}">{html.escape(formula_raw)}</div>'
    ]
    if tail:
        tail_text = truncate_text(tail, preview_tail_limit) if preview_tail_limit > 0 else tail
        tail_classes = "meta"
        if preview_tail_limit > 0:
            tail_classes += " clamp-2"
        pieces.append(f'<div class="{tail_classes}">{inline_math_text(tail_text)}</div>')
    return "".join(pieces)


def namespace_stats(conn, namespace: str | None = None):
    if namespace is None:
        tables = [
            "candidate_latest",
            "candidate_snapshots",
            "property_states",
            "proposition_states",
            "artifacts",
            "tool_request_states",
        ]
        return {table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] or 0) for table in tables}
    return {
        "candidate_latest": int(
            conn.execute(
                "SELECT COUNT(*) FROM candidate_latest WHERE memory_namespace = ?",
                (namespace,),
            ).fetchone()[0]
            or 0
        ),
        "candidate_snapshots": int(
            conn.execute(
                "SELECT COUNT(*) FROM candidate_snapshots WHERE memory_namespace = ?",
                (namespace,),
            ).fetchone()[0]
            or 0
        ),
        "property_states": int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM property_states ps
                JOIN candidate_snapshots cs ON cs.snapshot_id = ps.snapshot_id
                WHERE cs.memory_namespace = ?
                """,
                (namespace,),
            ).fetchone()[0]
            or 0
        ),
        "proposition_states": int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM proposition_states ps
                JOIN candidate_snapshots cs ON cs.snapshot_id = ps.snapshot_id
                WHERE cs.memory_namespace = ?
                """,
                (namespace,),
            ).fetchone()[0]
            or 0
        ),
        "artifacts": int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM artifacts a
                JOIN candidate_snapshots cs ON cs.snapshot_id = a.snapshot_id
                WHERE cs.memory_namespace = ?
                """,
                (namespace,),
            ).fetchone()[0]
            or 0
        ),
        "tool_request_states": int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM tool_request_states trs
                JOIN candidate_snapshots cs ON cs.snapshot_id = trs.snapshot_id
                WHERE cs.memory_namespace = ?
                """,
                (namespace,),
            ).fetchone()[0]
            or 0
        ),
    }


def stage_strip_limited(log_entries, max_visible: int = 6) -> str:
    entries = list(log_entries or [])
    if not entries:
        return ""
    if len(entries) <= max_visible:
        visible = entries
        extra = 0
    else:
        visible = entries[-max_visible:]
        extra = len(entries) - max_visible
    badges = [
        f'<span class="stage-badge {stage_css_class(str(item.get("stage", "")))}">'
        f'{html.escape(str(item.get("stage", "") or "[none]"))}</span>'
        for item in visible
    ]
    if extra:
        badges.insert(0, f'<span class="more-count">+{extra} earlier</span>')
    return '<div class="stage-strip">' + "".join(badges) + '</div>'


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


NS_SENTINEL_ALL = "__all__"


def _env_default_namespace() -> str | None:
    """Returns None (auto-pick latest), '' means no filter, or explicit value."""
    raw = os.getenv("PROOF_AGENT_WEB_NAMESPACE")
    if raw is None:
        return None
    value = raw.strip()
    if not value or value.lower() in {"all", "__all__"}:
        return ""
    return value


def resolve_namespace_from_query(query: dict, db_path: str) -> tuple[str | None, list[tuple[str, int, int, str]]]:
    """Return (namespace_filter, all_namespaces).

    `namespace_filter` is:
      - None   → show all namespaces (no filter)
      - "xxx"  → pin to namespace xxx
    """
    with connect_db(db_path) as conn:
        all_ns = list_namespaces(conn)

    raw = (query.get("ns", [None])[-1])
    if raw is not None:
        value = raw.strip()
        if value in {"", NS_SENTINEL_ALL, "all"}:
            return None, all_ns
        return value, all_ns

    env_default = _env_default_namespace()
    if env_default is not None:
        if env_default == "":
            return None, all_ns
        return env_default, all_ns

    if all_ns:
        return all_ns[0][0], all_ns
    return None, all_ns


def namespace_dropdown_html(current: str | None, all_ns: list[tuple[str, int, int, str]]) -> str:
    if not all_ns:
        return ""
    opts = []
    total = sum(n[1] for n in all_ns)
    all_selected = " selected" if current is None else ""
    opts.append(
        f'<option value="{NS_SENTINEL_ALL}"{all_selected}>All namespaces · {total}</option>'
    )
    for ns, count, _, latest_ts in all_ns:
        sel = " selected" if current == ns else ""
        label = f"{ns} · {count}  ({latest_ts[:16]})"
        opts.append(f'<option value="{html.escape(ns)}"{sel}>{html.escape(label)}</option>')
    return (
        '<select class="ns-switcher" onchange="window.proofAgentSwitchNs(this.value)">'
        + "".join(opts)
        + "</select>"
    )


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
        ns_bar = self._ns_bar_html()
        payload = html_page(title, body, flash=flash, ns_bar=ns_bar)
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _current_namespace(self) -> tuple[str | None, list[tuple[str, int, int, str]]]:
        if hasattr(self, "_ns_cache"):
            return self._ns_cache
        query = self._parse_query()
        ns, all_ns = resolve_namespace_from_query(query, self.db_path)
        self._ns_cache = (ns, all_ns)
        return ns, all_ns

    def _ns_bar_html(self) -> str:
        try:
            ns, all_ns = self._current_namespace()
        except Exception:
            return ""
        if not all_ns:
            return ""
        return namespace_dropdown_html(ns, all_ns)

    def _current_ns_form_value(self) -> str | None:
        query = self._parse_query()
        raw_values = query.get("ns", [])
        if raw_values:
            raw = (raw_values[-1] or "").strip()
            return NS_SENTINEL_ALL if raw in {"", NS_SENTINEL_ALL, "all"} else raw
        ns, _ = self._current_namespace()
        return ns or None

    def _ns_hidden_input(self, field_name: str = "ns") -> str:
        value = self._current_ns_form_value()
        if value is None:
            return ""
        return f'<input type="hidden" name="{html.escape(field_name)}" value="{html.escape(value)}">'

    def _with_current_ns(self, path: str) -> str:
        return with_namespace_param(path, self._current_ns_form_value())

    def _redirect(self, location: str):
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.end_headers()

    # ------------------------------ SSE ------------------------------

    def _sse_send(self, event: str, data: dict) -> bool:
        try:
            payload = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")
            self.wfile.write(payload)
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError, OSError):
            return False

    def _sse_heartbeat(self) -> bool:
        try:
            self.wfile.write(b": heartbeat\n\n")
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError, OSError):
            return False

    def _handle_sse(self, query):
        candidate_filter = (query.get("candidate", [""])[-1] or "").strip() or None
        log_filter = (query.get("log", [""])[-1] or "").strip() or None
        ns_raw = (query.get("ns", [None])[-1])
        namespace_filter: str | None
        if ns_raw is None:
            namespace_filter = None
        else:
            stripped = ns_raw.strip()
            namespace_filter = None if stripped in {"", NS_SENTINEL_ALL, "all"} else stripped

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        try:
            self.request.settimeout(None)
        except Exception:
            pass

        def latest_snapshot_id() -> int:
            try:
                with connect_db(self.db_path) as conn:
                    if namespace_filter is not None:
                        row = conn.execute(
                            "SELECT COALESCE(MAX(snapshot_id), 0) FROM candidate_snapshots WHERE memory_namespace = ?",
                            (namespace_filter,),
                        ).fetchone()
                    else:
                        row = conn.execute("SELECT COALESCE(MAX(snapshot_id), 0) FROM candidate_snapshots").fetchone()
                    return int(row[0]) if row else 0
            except Exception:
                return 0

        def fetch_new_snapshots(since_id: int):
            try:
                with connect_db(self.db_path) as conn:
                    clauses = ["snapshot_id > ?"]
                    params: list = [since_id]
                    if candidate_filter:
                        clauses.append("candidate_id = ?")
                        params.append(candidate_filter)
                    if namespace_filter is not None:
                        clauses.append("memory_namespace = ?")
                        params.append(namespace_filter)
                    sql = (
                        "SELECT snapshot_id, candidate_id, status, saved_at, form, memory_namespace "
                        "FROM candidate_snapshots WHERE "
                        + " AND ".join(clauses)
                        + " ORDER BY snapshot_id ASC LIMIT 20"
                    )
                    return list(conn.execute(sql, params).fetchall())
            except Exception:
                return []

        last_id = latest_snapshot_id()
        log_path = resolve_log_path(log_filter) if log_filter else None
        last_log_mtime = log_path.stat().st_mtime if log_path and log_path.exists() else 0.0

        if not self._sse_send("hello", {
            "max_snapshot_id": last_id,
            "candidate_filter": candidate_filter,
            "namespace_filter": namespace_filter,
            "server_time": datetime.now(timezone.utc).isoformat(),
        }):
            return

        poll_interval = max(0.5, float(os.getenv("PROOF_AGENT_SSE_POLL_SECONDS", "1.0")))
        heartbeat_every = max(5.0, float(os.getenv("PROOF_AGENT_SSE_HEARTBEAT_SECONDS", "20.0")))
        max_duration = float(os.getenv("PROOF_AGENT_SSE_MAX_SECONDS", "0"))

        started = time.time()
        last_heartbeat = started

        while True:
            if max_duration > 0 and (time.time() - started) > max_duration:
                self._sse_send("bye", {"reason": "max_duration"})
                return

            time.sleep(poll_interval)

            current_id = latest_snapshot_id()
            if current_id > last_id:
                for row in fetch_new_snapshots(last_id):
                    snapshot = {
                        "snapshot_id": int(row["snapshot_id"]),
                        "candidate_id": str(row["candidate_id"]),
                        "status": str(row["status"]),
                        "saved_at": str(row["saved_at"]),
                        "namespace": str(row["memory_namespace"] or ""),
                    }
                    if not self._sse_send("snapshot", snapshot):
                        return
                last_id = current_id

            if log_path and log_path.exists():
                try:
                    mtime = log_path.stat().st_mtime
                    if mtime > last_log_mtime + 0.2:
                        last_log_mtime = mtime
                        if not self._sse_send("log", {"path": log_path.name, "mtime": mtime}):
                            return
                except OSError:
                    pass

            now = time.time()
            if now - last_heartbeat > heartbeat_every:
                if not self._sse_heartbeat():
                    return
                last_heartbeat = now

    def _fetch_property_detail_bundle(self, conn, snapshot_id: int) -> dict:
        """Return {prop_name: {status, note, propositions:[...], artifacts: {...}}} for a snapshot."""
        out: dict = {}
        for row in conn.execute(
            "SELECT property_name, status, note, artifact_key FROM property_states WHERE snapshot_id = ?",
            (snapshot_id,),
        ):
            out.setdefault(row["property_name"], {
                "status": row["status"],
                "note": row["note"] or "",
                "artifact_key": row["artifact_key"] or "",
                "propositions": [],
                "artifacts": {},
            })
        for row in conn.execute(
            "SELECT property_name, proposition_id, position, title, claim, "
            "dependencies_json, verification_focus, status, note, requires_tool, artifact_key "
            "FROM proposition_states WHERE snapshot_id = ? ORDER BY property_name, position",
            (snapshot_id,),
        ):
            key = row["property_name"]
            out.setdefault(key, {
                "status": "", "note": "", "artifact_key": "",
                "propositions": [], "artifacts": {},
            })
            out[key]["propositions"].append({
                "id": row["proposition_id"],
                "title": row["title"] or "",
                "claim": row["claim"] or "",
                "deps": row["dependencies_json"] or "",
                "focus": row["verification_focus"] or "",
                "status": row["status"] or "",
                "note": row["note"] or "",
                "artifact_key": row["artifact_key"] or "",
                "requires_tool": bool(row["requires_tool"]),
            })
        for row in conn.execute(
            "SELECT artifact_key, content FROM artifacts WHERE snapshot_id = ?",
            (snapshot_id,),
        ):
            key = row["artifact_key"]
            if not key.startswith("property_") and not key.startswith("proposition_plan_"):
                continue
            if key.startswith("property_"):
                parts = key[len("property_"):].split("_", 1)
                prop = parts[0] if parts else ""
                out.setdefault(prop, {
                    "status": "", "note": "", "artifact_key": "",
                    "propositions": [], "artifacts": {},
                })
                out[prop]["artifacts"][key] = row["content"]
            elif key.startswith("proposition_plan_"):
                prop = key[len("proposition_plan_"):]
                out.setdefault(prop, {
                    "status": "", "note": "", "artifact_key": "",
                    "propositions": [], "artifacts": {},
                })
                out[prop]["artifacts"][key] = row["content"]
        return out

    def _render_property_panel(self, candidate_id: str, prop: str, bundle: dict) -> str:
        status = str(bundle.get("status", "")).strip() or "untested"
        safe_status = "".join(c if c.isalnum() or c in "-_" else "-" for c in status.lower()) or "untested"
        note = str(bundle.get("note", "")).strip()
        propositions = bundle.get("propositions", []) or []
        artifacts = bundle.get("artifacts", {}) or {}
        agg_key = f"property_{prop}"
        anchor_id = f"prop-{prop}"

        prop_items_html = []
        for item in propositions:
            proof_key = f"property_{prop}_{item['id']}"
            proof_content = artifacts.get(proof_key, "")
            proof_fold = (
                f'<details class="inline-fold" id="prop-{html.escape(prop)}-{html.escape(item["id"])}-proof">'
                f'<summary>Proof draft</summary>'
                f'{markdown_block(proof_content, classes="snippet")}'
                f'</details>' if proof_content else ""
            )
            prop_items_html.append(
                f"""
                <div class="prop-detail" id="{html.escape(anchor_id)}-{html.escape(item['id'])}">
                  <div class="prop-head">
                    <strong>{html.escape(item['id'])}</strong>
                    <span class="chip prop-{html.escape(item['status'] or 'planned')}">{html.escape(item['status'] or 'planned')}</span>
                    {('<span class="meta">requires tool</span>' if item['requires_tool'] else '')}
                  </div>
                  {f'<div class="snippet"><strong>Title.</strong> {inline_math_text(item["title"])}</div>' if item['title'] else ''}
                  {f'<div class="snippet clamp-3"><strong>Claim.</strong> {inline_math_text(item["claim"])}</div>' if item['claim'] else ''}
                  {f'<div class="meta clamp-2">focus: {inline_math_text(item["focus"])}</div>' if item['focus'] else ''}
                  {f'<div class="meta clamp-2">note: {inline_math_text(item["note"])}</div>' if item['note'] else ''}
                  {proof_fold}
                </div>
                """
            )

        plan_fold = ""
        plan_content = artifacts.get(f"proposition_plan_{prop}", "")
        if plan_content:
            plan_fold = (
                f'<details class="inline-fold" id="prop-{html.escape(prop)}-plan">'
                f'<summary>Proposition plan (raw JSON)</summary>'
                f'<div class="snippet mono">{html.escape(plan_content)}</div>'
                f'</details>'
            )

        agg_fold = ""
        if agg_key in artifacts:
            agg_fold = (
                f'<details class="inline-fold" id="prop-{html.escape(prop)}-aggregated">'
                f'<summary>Aggregated write-up</summary>'
                f'{markdown_block(artifacts[agg_key], classes="snippet")}'
                f'</details>'
            )

        return f"""
        <div class="prop-detail" id="{html.escape(anchor_id)}">
          <div class="prop-head">
            <strong>Property {html.escape(prop)}</strong>
            <span class="chip prop-{html.escape(safe_status)}">{html.escape(status)}</span>
            <span class="meta mono">{len(propositions)} propositions</span>
          </div>
          {f'<div class="meta clamp-3">{inline_math_text(note)}</div>' if note else ''}
          {"".join(prop_items_html)}
          {plan_fold}
          {agg_fold}
        </div>
        """

    def _stats(self, conn, namespace: str | None = None):
        return namespace_stats(conn, namespace=namespace)

    def _latest_records(self, conn, namespace: str | None = None):
        rows = fetch_latest_candidates(conn, limit=0, namespace=namespace)
        records = []
        for row in rows:
            record = candidate_from_snapshot_row(row)
            records.append({"row": row, "record": record})
        return records

    def _render_search_tree_node(self, node, children_map):
        row = node["row"]
        record = node["record"]
        candidate_link = self._with_current_ns(f"/candidate?id={quote(record.candidate_id, safe='')}")
        latest_log = record.exploration_log[-1] if record.exploration_log else {}
        stage = str(latest_log.get("stage", "")).strip() or "[none]"
        stage_msg = str(latest_log.get("message", "")).strip()
        note = record.pruned_reason or (record.terminal_decision.get("rationale", "") if record.terminal_decision else "")
        active_cls = " active-node" if record.status == "active" else ""
        child_html = "".join(self._render_search_tree_node(child, children_map) for child in children_map.get(record.candidate_id, []))
        children_block = f'<div class="tree-children">{child_html}</div>' if child_html else ""
        stage_badge_inline = (
            f'<span class="stage-badge {stage_css_class(stage)}">{html.escape(stage)}</span>'
            if stage and stage != "[none]" else ""
        )
        safe_id = "".join(c if c.isalnum() or c in "-_" else "-" for c in record.candidate_id)
        full_form_fold = (
            f'<details class="inline-fold" id="tn-{safe_id}-form"><summary>Full form</summary>'
            f'{equation_markup(record.form)}</details>'
            if record.form else ""
        )
        stage_entries = "".join(
            self._render_stage_entry(entry, index)
            for index, entry in enumerate(record.exploration_log, start=1)
        )
        timeline_fold = (
            f'<details class="inline-fold" id="tn-{safe_id}-timeline"><summary>Stage timeline ({len(record.exploration_log)})</summary>'
            f'<div class="fold-body stage-list">{stage_entries}</div></details>'
            if record.exploration_log else ""
        )
        note_fold = (
            f'<details class="inline-fold" id="tn-{safe_id}-note"><summary>'
            f'{"Pruned reason" if record.pruned_reason else "Terminal rationale"}</summary>'
            f'<div class="snippet">{html.escape(str(note)[:2000])}</div></details>'
            if note else ""
        )
        stage_msg_line = (
            f'<div class="meta clamp-2">{inline_math_text(stage_msg)}</div>'
            if stage_msg else ""
        )
        derived_line = (
            f'<div class="meta truncate">↳ from {inline_math_text(record.derived_from)}</div>'
            if record.derived_from else ""
        )
        return f"""
        <div class="tree-node{active_cls}">
          <div class="candidate-head">
            <div class="candidate-title">
              <a href="{candidate_link}">{html.escape(record.candidate_id)}</a>
              {status_badge(record.status)}
              {stage_badge_inline}
            </div>
            <div class="meta mono">#{row['snapshot_id']}</div>
          </div>
          {derived_line}
          {equation_markup(record.form, preview_tail_limit=240, compact=True)}
          {property_chips(record, candidate_link_prefix=candidate_link)}
          {stage_strip_limited(record.exploration_log, max_visible=6)}
          {stage_msg_line}
          {full_form_fold}
          {note_fold}
          {timeline_fold}
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
          {math_text_block(message, classes="snippet") if message else ''}
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
        ns, _ = self._current_namespace()
        ns_hidden = self._ns_hidden_input()
        current_ns_label = html.escape(ns or "All namespaces")
        with connect_db(self.db_path) as conn:
            stats = self._stats(conn, namespace=ns)
            rows = fetch_latest_candidates(conn, status=status_filter, limit=limit, namespace=ns)
        cards = []
        for row in rows:
            record = candidate_from_snapshot_row(row)
            candidate_link = self._with_current_ns(f"/candidate?id={quote(record.candidate_id, safe='')}")
            snapshot_link = self._with_current_ns(f"/snapshot?id={row['snapshot_id']}")
            form_text = str(row["form"] or "")
            safe_card_id = "".join(c if c.isalnum() or c in "-_" else "-" for c in record.candidate_id)
            form_fold = (
                f'<details class="inline-fold" id="hc-{safe_card_id}-form"><summary>Full form</summary>'
                f'{equation_markup(form_text)}</details>'
                if form_text else ""
            )
            pruned_fold = (
                f'<details class="inline-fold" id="hc-{safe_card_id}-pruned"><summary>Pruned reason</summary>'
                f'{math_text_block(str(record.pruned_reason)[:2000], classes="snippet")}</details>'
                if record.pruned_reason else ""
            )
            decision_line = (
                f'<div class="meta truncate">decision: {inline_math_text(record.terminal_decision.get("action", ""))}</div>'
                if record.terminal_decision.get("action") else ""
            )
            derived_line = (
                f'<div class="meta truncate">↳ from {inline_math_text(str(row["derived_from"]))}</div>'
                if row["derived_from"] else ""
            )
            cards.append(
                f"""
                <div class="candidate">
                  <div class="candidate-head">
                    <div class="candidate-title">
                      <a href="{candidate_link}">{html.escape(record.candidate_id)}</a>
                      {status_badge(str(row['status']))}
                    </div>
                    <div class="meta mono">#{row['snapshot_id']}</div>
                  </div>
                  {derived_line}
                  {equation_markup(form_text, preview_tail_limit=240, compact=True)}
                  {property_chips(record, candidate_link_prefix=candidate_link)}
                  {decision_line}
                  {form_fold}
                  {pruned_fold}
                  <div class="toolbar" style="margin-top:8px">
                    <a href="{candidate_link}">Open candidate →</a>
                    <span class="meta">·</span>
                    <a href="{snapshot_link}">Snapshot JSON</a>
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
            <div class="meta" style="margin-bottom:10px">Scope: {current_ns_label}</div>
            <div class="stats">{stats_html}</div>
            <div class="danger-zone">
              <div><strong>Danger zone</strong></div>
              <div class="meta">Delete every latest candidate currently marked as active. Useful for clearing interrupted runs.</div>
              <form method="post" action="/delete-active" onsubmit="return confirm('Delete all active candidates and their snapshots?');">
                {ns_hidden}
                <input type="hidden" name="ns_scope" value="{html.escape(self._current_ns_form_value() or NS_SENTINEL_ALL)}">
                <button class="danger-btn" type="submit">Delete all active candidates</button>
              </form>
            </div>
          </div>
          <div class="panel">
            <h2>Browse Latest Candidates</h2>
            <form class="toolbar" method="get" action="/">
              {ns_hidden}
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
        ns, _ = self._current_namespace()
        query = self._parse_query()
        q_text = (query.get("q", [""])[-1] or "").strip()
        raw_statuses = query.get("st", []) or []
        status_filters = {s.strip().lower() for s in raw_statuses if s.strip()}
        with connect_db(self.db_path) as conn:
            items = self._latest_records(conn, namespace=ns)
        if q_text:
            needle = q_text.lower()
            items = [
                item for item in items
                if needle in item["record"].candidate_id.lower()
                or needle in (item["record"].form or "").lower()
                or needle in (item["record"].derived_from or "").lower()
            ]
        if status_filters:
            items = [item for item in items if str(item["record"].status).lower() in status_filters]

        def _status_checkbox(value: str, label: str) -> str:
            checked = " checked" if value in status_filters or (not status_filters) else ""
            return (
                f'<label><input type="checkbox" name="st" value="{value}"{checked}> {label}</label>'
            )

        ns_hidden = self._ns_hidden_input()
        search_bar_html = f"""
          <form class="search-bar" method="get" action="/search">
            {ns_hidden}
            <input type="text" name="q" value="{html.escape(q_text)}" placeholder="Search candidate_id / form / derived_from…">
            <div class="status-chips">
              {_status_checkbox("active", "active")}
              {_status_checkbox("pruned", "pruned")}
              {_status_checkbox("passed", "passed")}
            </div>
            <button type="submit">Filter</button>
            <a class="meta" href="{self._with_current_ns('/search')}" style="align-self:center">reset</a>
          </form>
        """

        if not items:
            empty_body = f"""
            <div class="panel">
              <h2>Search tree</h2>
              {search_bar_html}
              <div class="meta">No candidates match the current filters.</div>
            </div>
            """
            self._send_html("Search Tree", empty_body, flash=flash)
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
        pruned_html = []
        for item in recent_pruned[:6]:
            record = item["record"]
            pruned_html.append(
                f"""
                <div class="candidate">
                  <div class="candidate-head">
                    <div class="candidate-title">
                      <a href="{self._with_current_ns(f"/candidate?id={quote(record.candidate_id, safe='')}")}">{html.escape(record.candidate_id)}</a>
                    </div>
                    <div class="meta mono">#{item['row']['snapshot_id']}</div>
                  </div>
                  <div class="meta clamp-2">{inline_math_text(record.pruned_reason or '[none]')}</div>
                </div>
                """
            )
        active_html = []
        for item in recent_active[:6]:
            record = item["record"]
            latest_stage = record.exploration_log[-1] if record.exploration_log else {}
            active_html.append(
                f"""
                <div class="candidate">
                  <div class="candidate-head">
                    <div class="candidate-title">
                      <a href="{self._with_current_ns(f"/candidate?id={quote(record.candidate_id, safe='')}")}">{html.escape(record.candidate_id)}</a>
                      {status_badge(record.status)}
                    </div>
                    <div class="meta mono">#{item['row']['snapshot_id']}</div>
                  </div>
                  <div class="meta truncate">stage: {inline_math_text(str(latest_stage.get('stage', '[none]')))}</div>
                  <div class="meta clamp-2">{inline_math_text(str(latest_stage.get('message', '')))}</div>
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
                "Total": len(items),
            }.items()
        )
        body = f"""
        <div class="grid two">
          <div class="grid">
            <div class="panel sticky">
              <h2>Overview</h2>
              <div class="stats">{stats_html}</div>
              <div class="meta" style="margin-top:8px">Built from the latest snapshot of each candidate. Click ▸ folds for full form / timeline / reason.</div>
            </div>
            <div class="panel">
              <h3>Active frontier</h3>
              <div class="card-list">{''.join(active_html) or '<div class="candidate">No active candidates.</div>'}</div>
            </div>
            <div class="panel">
              <h3>Recent pruning</h3>
              <div class="card-list">{''.join(pruned_html) or '<div class="candidate">No pruned candidates.</div>'}</div>
            </div>
          </div>
          <div class="panel">
            <h2>Search tree</h2>
            {search_bar_html}
            <div class="meta" style="margin-bottom:10px">{len(roots)} root(s), {len(items)} total after filter.</div>
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
        refresh_script = ""  # SSE (/api/events) drives live reloads; no blind 3s reload needed.
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
                  <button type="submit">Start run</button>
                </form>
                <form method="post" action="/stop-main">
                  <button class="danger-btn" type="submit">Stop run</button>
                </form>
              </div>
              <div class="meta" style="margin-top:6px">Log reloads automatically when the file changes (SSE).</div>
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
        ns, _ = self._current_namespace()
        with connect_db(self.db_path) as conn:
            row = fetch_latest_snapshot_for_candidate(conn, candidate_id, namespace=ns)
            if row is None and ns is not None:
                row = fetch_latest_snapshot_for_candidate(conn, candidate_id, namespace=None)
            if row is None:
                self._send_html(
                    "Not Found",
                    "<div class='panel'>Candidate not found.</div>",
                    status=HTTPStatus.NOT_FOUND,
                )
                return
            row_ns = str(row["memory_namespace"] or "") if "memory_namespace" in row.keys() else (ns or "")
            history = fetch_all_snapshots_for_candidate(conn, candidate_id, namespace=row_ns or None)
            property_artifacts = self._fetch_property_detail_bundle(conn, int(row["snapshot_id"]))
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
                  <input type="hidden" name="return_to" value="{html.escape(self._with_current_ns(f"/candidate?id={quote(candidate_id, safe='')}"))}">
                  <button class="danger-btn" type="submit">Delete</button>
                </form>
                """
            reason_line = (
                f'<div class="meta clamp-2" style="margin-top:6px">{inline_math_text(snap_record.pruned_reason)}</div>'
                if snap_record.pruned_reason else ""
            )
            history_html.append(
                f"""
                <div class="history-item">
                  <div class="candidate-head">
                    <div class="candidate-title">
                      <span class="mono">#{snap_id}</span>
                      {status_badge(str(item['status']))}
                    </div>
                    <div class="meta mono">{html.escape(str(item['saved_at']))}</div>
                  </div>
                  {reason_line}
                  <div class="toolbar" style="margin-top:6px">
                    <a href="{self._with_current_ns(f"/snapshot?id={snap_id}")}">JSON</a>
                    {delete_form}
                  </div>
                </div>
                """
            )
        this_candidate_link = self._with_current_ns(f"/candidate?id={quote(candidate_id, safe='')}")
        property_panels = [
            self._render_property_panel(candidate_id, prop, bundle)
            for prop, bundle in property_artifacts.items()
        ]
        property_section = (
            f'<div class="panel"><h3>Properties</h3>'
            f'{"".join(property_panels)}'
            f'</div>' if property_panels else ""
        )
        ns_badge = (
            f'<span class="chip mono" title="memory namespace">ns: {html.escape(row_ns)}</span>'
            if row_ns else ""
        )
        body = f"""
        <div class="grid two">
          <div class="panel">
            <div class="candidate-head">
              <h2 style="margin:0">{html.escape(candidate_id)}</h2>
              {status_badge(str(row['status']))}
              {ns_badge}
            </div>
            <div class="meta mono" style="margin-top:4px">latest snapshot #{row['snapshot_id']}</div>
            {f'<div class="meta">↳ derived from {inline_math_text(str(row["derived_from"]))}</div>' if row["derived_from"] else ''}
            {equation_markup(str(row['form']))}
            <div style="margin-top:10px">{property_chips(record, candidate_link_prefix=this_candidate_link)}</div>
            <details class="inline-fold" id="candidate-payload-json" style="margin-top:10px">
              <summary>Latest JSON payload</summary>
              <div class="json">{html.escape(json.dumps(record.to_dict(), ensure_ascii=False, indent=2))}</div>
            </details>
            <div class="danger-zone">
              <div><strong>Danger zone</strong></div>
              <div class="meta">Delete every snapshot for this candidate.</div>
              <form method="post" action="/delete-candidate" onsubmit="return confirm('Delete candidate {html.escape(candidate_id)} and all snapshots?');">
                {self._ns_hidden_input()}
                <input type="hidden" name="candidate_id" value="{html.escape(candidate_id)}">
                <input type="hidden" name="ns_scope" value="{html.escape(row_ns or NS_SENTINEL_ALL)}">
                <button class="danger-btn" type="submit">Delete candidate</button>
              </form>
            </div>
          </div>
          <div class="grid">
            {property_section}
            <div class="panel">
              <h3>Snapshot history ({len(history)})</h3>
              <div class="history">
                {''.join(history_html)}
              </div>
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
            <a href="{self._with_current_ns(f"/candidate?id={quote(candidate_id, safe='')}")}">Back to candidate</a>
            <form class="inline" method="post" action="/delete-snapshot" onsubmit="return confirm('Delete snapshot {snapshot_id}?');">
              <input type="hidden" name="snapshot_id" value="{snapshot_id}">
              <input type="hidden" name="return_to" value="{html.escape(self._with_current_ns(f"/candidate?id={quote(candidate_id, safe='')}"))}">
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
        if parsed.path == "/api/events":
            self._handle_sse(query)
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
            namespace_scope = normalize_namespace_value(form.get("ns_scope"))
            redirect_ns = explicit_namespace_value(form.get("ns")) or explicit_namespace_value(form.get("ns_scope"))
            if not candidate_id:
                self._redirect(with_namespace_param("/?" + urlencode({"flash": "Missing candidate_id"}), redirect_ns))
                return
            with connect_db(self.db_path) as conn:
                delete_candidate(conn, candidate_id, namespace=namespace_scope)
            flash = f"Deleted candidate {candidate_id}"
            if namespace_scope:
                flash += f" in namespace {namespace_scope}"
            self._redirect(with_namespace_param("/?" + urlencode({"flash": flash}), redirect_ns))
            return
        if parsed.path == "/delete-active":
            namespace_scope = normalize_namespace_value(form.get("ns_scope"))
            redirect_ns = explicit_namespace_value(form.get("ns")) or explicit_namespace_value(form.get("ns_scope"))
            with connect_db(self.db_path) as conn:
                deleted = delete_candidates_by_status(conn, "active", namespace=namespace_scope)
            count = len(deleted or [])
            flash = "No active candidates to delete." if count <= 0 else f"Deleted {count} active candidate(s)."
            self._redirect(with_namespace_param("/?" + urlencode({"flash": flash}), redirect_ns))
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
    parser.add_argument(
        "--namespace",
        default=None,
        help=(
            "Default memory namespace to show. Same as PROOF_AGENT_WEB_NAMESPACE env. "
            "Use empty string or 'all' to show every namespace."
        ),
    )
    return parser


def main():
    args = build_parser().parse_args()
    db_path = resolve_db_path(args.db)
    if args.namespace is not None:
        os.environ["PROOF_AGENT_WEB_NAMESPACE"] = args.namespace
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
