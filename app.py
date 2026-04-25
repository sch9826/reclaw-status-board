"""Status Board — mobile-first live dashboard for Jared.

Single-file Flask app. Receives JSON from the patrol cron via POST /api/update,
serves a clean mobile-friendly dashboard at GET /.

Deploy to Railway. Bookmark the URL on your phone.
"""

import os
import json
import hashlib
import hmac
from datetime import datetime, timezone

from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)

# Simple auth token — patrol cron sends this in X-Auth-Token header
AUTH_TOKEN = os.environ.get("STATUS_BOARD_TOKEN", "changeme")

# In-memory store (Railway containers persist while running)
_state = {
    "last_updated": None,
    "projects": [],
}

# ── HTML Template (mobile-first) ──────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>Status Board</title>
<style>
  :root {
    --bg: #0d1117;
    --card: #161b22;
    --border: #30363d;
    --text: #e6edf3;
    --text-dim: #8b949e;
    --accent: #58a6ff;
    --green: #3fb950;
    --yellow: #d29922;
    --red: #f85149;
    --orange: #db6d28;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    padding: 16px;
    padding-bottom: 80px;
    -webkit-font-smoothing: antialiased;
  }
  .header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 20px;
    padding-bottom: 12px;
    border-bottom: 1px solid var(--border);
  }
  .header h1 {
    font-size: 20px;
    font-weight: 700;
    letter-spacing: -0.3px;
  }
  .header .updated {
    font-size: 12px;
    color: var(--text-dim);
    text-align: right;
  }
  .pulse {
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--green);
    margin-right: 6px;
    animation: pulse 2s ease-in-out infinite;
  }
  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.3; }
  }
  .summary {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 10px;
    margin-bottom: 20px;
  }
  .stat {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 14px 12px;
    text-align: center;
  }
  .stat .num {
    font-size: 28px;
    font-weight: 700;
    line-height: 1;
  }
  .stat .label {
    font-size: 11px;
    color: var(--text-dim);
    margin-top: 4px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  .stat.active .num { color: var(--accent); }
  .stat.todo .num { color: var(--yellow); }
  .stat.blocked .num { color: var(--red); }

  .project {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    margin-bottom: 12px;
    overflow: hidden;
  }
  .project-header {
    padding: 14px 16px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
  }
  .project-header h2 {
    font-size: 15px;
    font-weight: 600;
  }
  .project-badges {
    display: flex;
    gap: 6px;
  }
  .badge {
    font-size: 11px;
    font-weight: 600;
    padding: 3px 8px;
    border-radius: 12px;
    line-height: 1;
  }
  .badge.active { background: rgba(88, 166, 255, 0.15); color: var(--accent); }
  .badge.todo { background: rgba(210, 153, 34, 0.15); color: var(--yellow); }
  .badge.blocked { background: rgba(248, 81, 73, 0.15); color: var(--red); }
  .badge.done { background: rgba(63, 185, 80, 0.15); color: var(--green); }

  .project-body {
    display: none;
    padding: 0 16px 14px;
  }
  .project.open .project-body { display: block; }
  .project-header .chevron {
    transition: transform 0.2s;
    color: var(--text-dim);
  }
  .project.open .project-header .chevron { transform: rotate(90deg); }

  .section-label {
    font-size: 11px;
    font-weight: 600;
    color: var(--text-dim);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin: 12px 0 6px;
  }
  .section-label:first-child { margin-top: 0; }

  .ticket {
    font-size: 13px;
    padding: 8px 0;
    border-bottom: 1px solid var(--border);
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 8px;
  }
  .ticket:last-child { border-bottom: none; }
  .ticket .title {
    flex: 1;
    line-height: 1.35;
  }
  .ticket .agent {
    font-size: 11px;
    color: var(--text-dim);
    white-space: nowrap;
    flex-shrink: 0;
  }
  .ticket .updated-tag {
    font-size: 10px;
    color: var(--text-dim);
    margin-top: 2px;
  }

  .empty {
    font-size: 13px;
    color: var(--text-dim);
    font-style: italic;
    padding: 8px 0;
  }

  .refresh-bar {
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    background: var(--card);
    border-top: 1px solid var(--border);
    padding: 12px 16px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 12px;
    color: var(--text-dim);
    z-index: 10;
  }
  .refresh-bar button {
    background: var(--accent);
    color: #fff;
    border: none;
    border-radius: 8px;
    padding: 8px 16px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
  }
  .refresh-bar button:active { opacity: 0.7; }

  .no-data {
    text-align: center;
    padding: 60px 20px;
    color: var(--text-dim);
  }
  .no-data h2 { font-size: 18px; margin-bottom: 8px; color: var(--text); }
  .no-data p { font-size: 14px; }
</style>
</head>
<body>

<div class="header">
  <h1><span class="pulse"></span> Status Board</h1>
  <div class="updated" id="updated-time">Loading...</div>
</div>

<div class="summary" id="summary"></div>
<div id="projects"></div>

<div class="refresh-bar">
  <span id="countdown">Auto-refresh in 60s</span>
  <button onclick="fetchData()">Refresh Now</button>
</div>

<script>
let countdownInterval;

async function fetchData() {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    render(data);
    resetCountdown();
  } catch (e) {
    console.error('Fetch failed:', e);
  }
}

function render(data) {
  // Updated time
  const el = document.getElementById('updated-time');
  if (data.last_updated) {
    const d = new Date(data.last_updated);
    const now = new Date();
    const diffMin = Math.round((now - d) / 60000);
    el.textContent = diffMin < 2 ? 'Just now' : diffMin + 'm ago';
  } else {
    el.textContent = 'No data yet';
  }

  const projects = data.projects || [];

  if (!projects.length) {
    document.getElementById('summary').innerHTML = '';
    document.getElementById('projects').innerHTML =
      '<div class="no-data"><h2>Waiting for data</h2><p>Patrol cron will push status updates every 12 minutes.</p></div>';
    return;
  }

  // Summary stats
  let totalActive = 0, totalTodo = 0, totalBlocked = 0;
  projects.forEach(p => {
    totalActive += (p.in_progress || []).length;
    totalTodo += (p.todo || []).length;
    totalBlocked += (p.blocked || []).length;
  });

  document.getElementById('summary').innerHTML = `
    <div class="stat active"><div class="num">${totalActive}</div><div class="label">Active</div></div>
    <div class="stat todo"><div class="num">${totalTodo}</div><div class="label">To Do</div></div>
    <div class="stat blocked"><div class="num">${totalBlocked}</div><div class="label">Blocked</div></div>
  `;

  // Projects
  let html = '';
  projects.forEach((p, i) => {
    const activeCount = (p.in_progress || []).length;
    const todoCount = (p.todo || []).length;
    const blockedCount = (p.blocked || []).length;
    const doneCount = (p.recently_done || []).length;

    let badges = '';
    if (activeCount) badges += `<span class="badge active">${activeCount} active</span>`;
    if (todoCount) badges += `<span class="badge todo">${todoCount} todo</span>`;
    if (blockedCount) badges += `<span class="badge blocked">${blockedCount} blocked</span>`;
    if (doneCount) badges += `<span class="badge done">${doneCount} done</span>`;

    html += `<div class="project${i === 0 ? ' open' : ''}" onclick="this.classList.toggle('open')">`;
    html += `<div class="project-header">`;
    html += `<h2>${esc(p.name)}</h2>`;
    html += `<div style="display:flex;align-items:center;gap:8px">`;
    html += `<div class="project-badges">${badges}</div>`;
    html += `<span class="chevron">&#9654;</span>`;
    html += `</div></div>`;
    html += `<div class="project-body">`;

    if (blockedCount) {
      html += `<div class="section-label" style="color:var(--red)">Blocked on You</div>`;
      html += renderTickets(p.blocked);
    }
    if (activeCount) {
      html += `<div class="section-label">In Progress</div>`;
      html += renderTickets(p.in_progress);
    }
    if (todoCount) {
      html += `<div class="section-label">To Do</div>`;
      html += renderTickets(p.todo);
    }
    if (doneCount) {
      html += `<div class="section-label">Recently Done</div>`;
      html += renderTickets(p.recently_done);
    }
    if (!activeCount && !todoCount && !blockedCount && !doneCount) {
      html += `<div class="empty">All clear.</div>`;
    }

    html += `</div></div>`;
  });

  document.getElementById('projects').innerHTML = html;
}

function renderTickets(tickets) {
  if (!tickets || !tickets.length) return '';
  let h = '';
  tickets.forEach(t => {
    h += `<div class="ticket">`;
    h += `<div><div class="title">${esc(t.title || '(untitled)')}</div>`;
    if (t.updated) h += `<div class="updated-tag">${esc(t.updated)}</div>`;
    h += `</div>`;
    if (t.agent) h += `<div class="agent">${esc(t.agent)}</div>`;
    h += `</div>`;
  });
  return h;
}

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function resetCountdown() {
  let sec = 60;
  clearInterval(countdownInterval);
  countdownInterval = setInterval(() => {
    sec--;
    document.getElementById('countdown').textContent = `Auto-refresh in ${sec}s`;
    if (sec <= 0) {
      fetchData();
    }
  }, 1000);
}

// Boot
fetchData();
</script>
</body>
</html>"""


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/api/status")
def api_status():
    return jsonify(_state)


@app.route("/api/update", methods=["POST"])
def api_update():
    """Receive status update from patrol cron."""
    token = request.headers.get("X-Auth-Token", "")
    if not hmac.compare_digest(token, AUTH_TOKEN):
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "no json body"}), 400

    _state["last_updated"] = data.get(
        "last_updated", datetime.now(timezone.utc).isoformat()
    )
    _state["projects"] = data.get("projects", [])

    return jsonify({"ok": True, "projects": len(_state["projects"])})


@app.route("/health")
def health():
    return "ok"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
