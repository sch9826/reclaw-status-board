"""Status Board v3 — high-end dashboard with quick actions and enriched ticket views.

Single-file Flask app. Receives JSON from the patrol cron via POST /api/update,
serves a polished glass-morphism dashboard at GET /.
Quick-action buttons let Jared respond to tickets without typing.
Actions are queued to /tmp/status-board-actions.json for patrol cron pickup.

Deploy to Railway. Bookmark the URL on your phone.
"""

import os
import json
import hmac
import threading
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)

AUTH_TOKEN = os.environ.get("STATUS_BOARD_TOKEN", "changeme")
ACTIONS_FILE = Path("/tmp/status-board-actions.json")

# In-memory store
_state = {
    "last_updated": None,
    "projects": [],
}

_actions_lock = threading.Lock()
_actions_queue: list[dict] = []

# Load any persisted actions on startup
if ACTIONS_FILE.exists():
    try:
        _actions_queue.extend(json.loads(ACTIONS_FILE.read_text()))
    except Exception:
        pass


def _persist_actions():
    """Write action queue to disk so patrol cron can read it."""
    try:
        ACTIONS_FILE.write_text(json.dumps(_actions_queue, indent=2))
    except Exception:
        pass


# ── HTML Template ────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="theme-color" content="#0d1117">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>&#9889;</text></svg>">
<link rel="manifest" href="/manifest.json">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<title>Status Board</title>
<style>
  :root {
    --bg: #0a0e14;
    --bg2: #0d1117;
    --card: rgba(22, 27, 34, 0.75);
    --card-solid: #161b22;
    --border: rgba(48, 54, 61, 0.6);
    --border-light: rgba(48, 54, 61, 0.35);
    --text: #e6edf3;
    --text-dim: #8b949e;
    --text-dimmer: #484f58;
    --accent: #58a6ff;
    --green: #3fb950;
    --yellow: #d29922;
    --red: #f85149;
    --orange: #db6d28;
    --purple: #bc8cff;
    --glass-blur: 16px;
    --radius: 14px;
    --radius-sm: 10px;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    padding: 16px;
    padding-top: env(safe-area-inset-top, 16px);
    padding-bottom: 90px;
    -webkit-font-smoothing: antialiased;
    min-height: 100vh;
    background-image:
      radial-gradient(ellipse 80% 50% at 50% -20%, rgba(88,166,255,0.08), transparent),
      radial-gradient(ellipse 60% 40% at 80% 100%, rgba(63,185,80,0.05), transparent);
  }

  /* ── Header ── */
  .header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 20px;
    padding-bottom: 14px;
    border-bottom: 1px solid var(--border-light);
  }
  .header h1 {
    font-size: 20px;
    font-weight: 700;
    letter-spacing: -0.5px;
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
    margin-right: 8px;
    animation: pulse 2s ease-in-out infinite;
    vertical-align: middle;
  }
  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.3; }
  }

  /* ── Summary Stats ── */
  .summary {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 10px;
    margin-bottom: 24px;
  }
  .stat {
    background: var(--card);
    backdrop-filter: blur(var(--glass-blur));
    -webkit-backdrop-filter: blur(var(--glass-blur));
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    padding: 14px 10px;
    text-align: center;
    transition: transform 0.2s, box-shadow 0.2s;
  }
  .stat:hover { transform: translateY(-2px); box-shadow: 0 4px 20px rgba(0,0,0,0.3); }
  .stat .num {
    font-size: 26px;
    font-weight: 700;
    line-height: 1;
  }
  .stat .label {
    font-size: 10px;
    color: var(--text-dim);
    margin-top: 4px;
    text-transform: uppercase;
    letter-spacing: 0.6px;
    font-weight: 500;
  }
  .stat.active .num { color: var(--accent); }
  .stat.todo .num { color: var(--yellow); }
  .stat.blocked .num { color: var(--red); }
  .stat.needs-response .num { color: var(--purple); }

  /* ── Projects Grid ── */
  .projects-grid {
    display: grid;
    grid-template-columns: 1fr;
    gap: 14px;
  }
  @media (min-width: 769px) {
    .projects-grid {
      grid-template-columns: 1fr 1fr;
      gap: 16px;
    }
    .summary { max-width: 600px; }
    body { padding: 24px 32px; padding-bottom: 90px; }
  }
  @media (min-width: 1200px) {
    body { padding: 28px 48px; padding-bottom: 90px; }
  }

  .project {
    background: var(--card);
    backdrop-filter: blur(var(--glass-blur));
    -webkit-backdrop-filter: blur(var(--glass-blur));
    border: 1px solid var(--border);
    border-radius: var(--radius);
    overflow: hidden;
    transition: box-shadow 0.3s;
  }
  .project:hover { box-shadow: 0 4px 24px rgba(0,0,0,0.2); }
  .project-header {
    padding: 14px 16px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
    user-select: none;
  }
  .project-header h2 {
    font-size: 15px;
    font-weight: 600;
    letter-spacing: -0.2px;
  }
  .project-badges { display: flex; gap: 6px; flex-wrap: wrap; }
  .badge {
    font-size: 10px;
    font-weight: 600;
    padding: 3px 8px;
    border-radius: 12px;
    line-height: 1;
    letter-spacing: 0.2px;
  }
  .badge.active { background: rgba(88,166,255,0.12); color: var(--accent); }
  .badge.todo { background: rgba(210,153,34,0.12); color: var(--yellow); }
  .badge.blocked { background: rgba(248,81,73,0.12); color: var(--red); }
  .badge.done { background: rgba(63,185,80,0.12); color: var(--green); }

  .project-body {
    max-height: 0;
    overflow: hidden;
    transition: max-height 0.4s cubic-bezier(0.4, 0, 0.2, 1), padding 0.3s ease;
    padding: 0 16px;
  }
  .project.open .project-body {
    max-height: 5000px;
    padding: 0 16px 16px;
  }
  .chevron {
    transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    color: var(--text-dim);
    font-size: 12px;
    flex-shrink: 0;
    margin-left: 8px;
  }
  .project.open .chevron { transform: rotate(90deg); }

  .section-label {
    font-size: 10px;
    font-weight: 600;
    color: var(--text-dim);
    text-transform: uppercase;
    letter-spacing: 0.6px;
    margin: 14px 0 6px;
  }
  .section-label:first-child { margin-top: 0; }
  .section-label.blocked-label { color: var(--red); }

  /* ── Ticket Cards ── */
  .ticket-card {
    background: rgba(13, 17, 23, 0.5);
    border: 1px solid var(--border-light);
    border-radius: var(--radius-sm);
    margin-bottom: 8px;
    overflow: hidden;
    transition: border-color 0.2s, box-shadow 0.2s;
    border-left: 3px solid var(--text-dimmer);
  }
  .ticket-card.priority-blocked { border-left-color: var(--red); }
  .ticket-card.priority-active { border-left-color: var(--accent); }
  .ticket-card.priority-todo { border-left-color: var(--yellow); }
  .ticket-card.priority-done { border-left-color: var(--green); }
  .ticket-card:hover { border-color: var(--border); }

  .ticket-header {
    padding: 10px 12px;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 8px;
  }
  .ticket-title {
    font-size: 13px;
    font-weight: 500;
    line-height: 1.4;
    flex: 1;
  }
  .ticket-meta-right {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 4px;
    flex-shrink: 0;
  }
  .ticket-agent {
    font-size: 10px;
    color: var(--text-dim);
    white-space: nowrap;
    background: rgba(88,166,255,0.08);
    padding: 2px 6px;
    border-radius: 6px;
  }
  .ticket-updated {
    font-size: 10px;
    color: var(--text-dimmer);
  }

  /* ── Ticket Detail (expand) ── */
  .ticket-detail {
    max-height: 0;
    overflow: hidden;
    transition: max-height 0.35s cubic-bezier(0.4, 0, 0.2, 1), padding 0.25s ease;
    padding: 0 12px;
    border-top: 0px solid transparent;
  }
  .ticket-card.open .ticket-detail {
    max-height: 2000px;
    padding: 0 12px 12px;
    border-top: 1px solid var(--border-light);
  }
  .detail-section {
    margin-top: 10px;
  }
  .detail-label {
    font-size: 10px;
    font-weight: 600;
    color: var(--text-dim);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 4px;
  }
  .detail-text {
    font-size: 12px;
    color: var(--text);
    line-height: 1.5;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .detail-text.dim { color: var(--text-dim); font-style: italic; }

  .comment-item {
    background: rgba(22, 27, 34, 0.5);
    border: 1px solid var(--border-light);
    border-radius: 8px;
    padding: 8px 10px;
    margin-bottom: 6px;
  }
  .comment-author {
    font-size: 10px;
    font-weight: 600;
    color: var(--accent);
    margin-bottom: 2px;
  }
  .comment-text {
    font-size: 12px;
    line-height: 1.45;
    color: var(--text);
  }
  .comment-date {
    font-size: 10px;
    color: var(--text-dimmer);
    margin-top: 3px;
  }

  .detail-dates {
    display: flex;
    gap: 16px;
    flex-wrap: wrap;
  }
  .detail-date-item {
    font-size: 11px;
    color: var(--text-dim);
  }
  .detail-date-item span { color: var(--text-dimmer); font-size: 10px; }

  /* ── Quick Actions ── */
  .quick-actions {
    display: flex;
    gap: 8px;
    margin-top: 12px;
    flex-wrap: wrap;
    align-items: center;
  }
  .action-btn {
    border: none;
    border-radius: 8px;
    padding: 7px 14px;
    font-size: 12px;
    font-weight: 600;
    font-family: 'Inter', sans-serif;
    cursor: pointer;
    transition: transform 0.15s, opacity 0.15s, box-shadow 0.2s;
    display: inline-flex;
    align-items: center;
    gap: 5px;
    -webkit-tap-highlight-color: transparent;
  }
  .action-btn:active { transform: scale(0.95); }
  .action-btn:hover { box-shadow: 0 2px 12px rgba(0,0,0,0.3); }
  .action-btn.proceed { background: rgba(63,185,80,0.15); color: var(--green); border: 1px solid rgba(63,185,80,0.25); }
  .action-btn.hold { background: rgba(210,153,34,0.15); color: var(--yellow); border: 1px solid rgba(210,153,34,0.25); }
  .action-btn.question { background: rgba(88,166,255,0.15); color: var(--accent); border: 1px solid rgba(88,166,255,0.25); }
  .action-btn.thumbsup { background: rgba(188,140,255,0.15); color: var(--purple); border: 1px solid rgba(188,140,255,0.25); }

  .action-btn.sent {
    opacity: 0.5;
    pointer-events: none;
  }

  .question-input-wrap {
    display: none;
    flex: 1 1 100%;
    margin-top: 4px;
  }
  .question-input-wrap.visible { display: flex; gap: 6px; }
  .question-input {
    flex: 1;
    background: rgba(13, 17, 23, 0.8);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 7px 10px;
    font-size: 12px;
    font-family: 'Inter', sans-serif;
    color: var(--text);
    outline: none;
    transition: border-color 0.2s;
  }
  .question-input:focus { border-color: var(--accent); }
  .question-send {
    background: var(--accent);
    color: #fff;
    border: none;
    border-radius: 8px;
    padding: 7px 12px;
    font-size: 12px;
    font-weight: 600;
    font-family: 'Inter', sans-serif;
    cursor: pointer;
    white-space: nowrap;
  }

  .empty {
    font-size: 13px;
    color: var(--text-dim);
    font-style: italic;
    padding: 8px 0;
  }

  /* ── Refresh Bar ── */
  .refresh-bar {
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    background: rgba(22, 27, 34, 0.92);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    border-top: 1px solid var(--border-light);
    padding: 12px 16px;
    padding-bottom: max(12px, env(safe-area-inset-bottom));
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
    padding: 8px 18px;
    font-size: 13px;
    font-weight: 600;
    font-family: 'Inter', sans-serif;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
    transition: transform 0.15s, opacity 0.15s;
  }
  .refresh-bar button:active { transform: scale(0.95); opacity: 0.8; }

  .spinner {
    display: none;
    width: 16px;
    height: 16px;
    border: 2px solid var(--text-dimmer);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 0.6s linear infinite;
    margin-right: 8px;
  }
  .spinner.active { display: inline-block; }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* ── Toast ── */
  .toast {
    position: fixed;
    bottom: 80px;
    left: 50%;
    transform: translateX(-50%) translateY(20px);
    background: var(--card-solid);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 10px 20px;
    font-size: 13px;
    font-weight: 500;
    color: var(--text);
    opacity: 0;
    transition: opacity 0.3s, transform 0.3s;
    z-index: 20;
    pointer-events: none;
  }
  .toast.show {
    opacity: 1;
    transform: translateX(-50%) translateY(0);
  }

  .no-data {
    text-align: center;
    padding: 60px 20px;
    color: var(--text-dim);
    grid-column: 1 / -1;
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
<div class="projects-grid" id="projects"></div>

<div class="toast" id="toast"></div>

<div class="refresh-bar">
  <div style="display:flex;align-items:center">
    <div class="spinner" id="spinner"></div>
    <span id="countdown">Auto-refresh in 60s</span>
  </div>
  <button onclick="fetchData()">Refresh</button>
</div>

<script>
const AUTH_INPUT = null; // Not needed for read-only; actions use prompt
let countdownInterval;
let _projectsData = [];

function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2200);
}

async function fetchData() {
  const sp = document.getElementById('spinner');
  sp.classList.add('active');
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    _projectsData = data.projects || [];
    render(data);
    resetCountdown();
  } catch (e) {
    console.error('Fetch failed:', e);
  } finally {
    sp.classList.remove('active');
  }
}

function render(data) {
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

  let totalActive = 0, totalTodo = 0, totalBlocked = 0, totalNeedsResponse = 0;
  projects.forEach(p => {
    const inProg = p.in_progress || [];
    const todo = p.todo || [];
    const blocked = p.blocked || [];
    totalActive += inProg.length;
    totalTodo += todo.length;
    totalBlocked += blocked.length;
    // Needs response: tickets where needs_response flag is set
    [].concat(inProg, todo, blocked).forEach(t => {
      if (t.needs_response) totalNeedsResponse++;
    });
  });

  document.getElementById('summary').innerHTML = `
    <div class="stat active"><div class="num">${totalActive}</div><div class="label">Active</div></div>
    <div class="stat todo"><div class="num">${totalTodo}</div><div class="label">To Do</div></div>
    <div class="stat blocked"><div class="num">${totalBlocked}</div><div class="label">Blocked</div></div>
    <div class="stat needs-response"><div class="num">${totalNeedsResponse}</div><div class="label">Needs Reply</div></div>
  `;

  let html = '';
  projects.forEach((p, i) => {
    const activeCount = (p.in_progress || []).length;
    const todoCount = (p.todo || []).length;
    const blockedCount = (p.blocked || []).length;
    const doneCount = (p.recently_done || []).length;
    const key = p.key || '';

    let badges = '';
    if (blockedCount) badges += '<span class="badge blocked">' + blockedCount + ' blocked</span>';
    if (activeCount) badges += '<span class="badge active">' + activeCount + ' active</span>';
    if (todoCount) badges += '<span class="badge todo">' + todoCount + ' todo</span>';
    if (doneCount) badges += '<span class="badge done">' + doneCount + ' done</span>';

    html += '<div class="project' + (i === 0 ? ' open' : '') + '">';
    html += '<div class="project-header" onclick="toggleProject(this)">';
    html += '<h2>' + esc(p.name) + '</h2>';
    html += '<div style="display:flex;align-items:center;gap:8px">';
    html += '<div class="project-badges">' + badges + '</div>';
    html += '<span class="chevron">&#9654;</span>';
    html += '</div></div>';
    html += '<div class="project-body">';

    if (blockedCount) {
      html += '<div class="section-label blocked-label">Blocked on You</div>';
      html += renderTickets(p.blocked, key, 'blocked');
    }
    if (activeCount) {
      html += '<div class="section-label">In Progress</div>';
      html += renderTickets(p.in_progress, key, 'active');
    }
    if (todoCount) {
      html += '<div class="section-label">To Do</div>';
      html += renderTickets(p.todo, key, 'todo');
    }
    if (doneCount) {
      html += '<div class="section-label">Recently Done</div>';
      html += renderTickets(p.recently_done, key, 'done');
    }
    if (!activeCount && !todoCount && !blockedCount && !doneCount) {
      html += '<div class="empty">All clear.</div>';
    }

    html += '</div></div>';
  });

  document.getElementById('projects').innerHTML = html;
}

function renderTickets(tickets, projectKey, status) {
  if (!tickets || !tickets.length) return '';
  let h = '';
  tickets.forEach(t => {
    const tid = t.id || t.key || '';
    const pClass = 'priority-' + status;
    h += '<div class="ticket-card ' + pClass + '" data-tid="' + esc(tid) + '" data-pkey="' + esc(projectKey) + '">';
    h += '<div class="ticket-header" onclick="toggleTicket(this)">';
    h += '<div class="ticket-title">' + esc(t.title || '(untitled)') + '</div>';
    h += '<div class="ticket-meta-right">';
    if (t.agent) h += '<div class="ticket-agent">' + esc(t.agent) + '</div>';
    if (t.updated) h += '<div class="ticket-updated">' + esc(t.updated) + '</div>';
    h += '</div></div>';

    // Detail section (hidden by default)
    h += '<div class="ticket-detail">';

    // Description
    h += '<div class="detail-section">';
    h += '<div class="detail-label">Description</div>';
    if (t.description) {
      h += '<div class="detail-text">' + esc(t.description) + '</div>';
    } else {
      h += '<div class="detail-text dim">No description</div>';
    }
    h += '</div>';

    // Priority
    if (t.priority) {
      h += '<div class="detail-section">';
      h += '<div class="detail-label">Priority</div>';
      h += '<div class="detail-text">' + esc(t.priority) + '</div>';
      h += '</div>';
    }

    // Dates
    if (t.created_at || t.updated_at) {
      h += '<div class="detail-section">';
      h += '<div class="detail-label">Dates</div>';
      h += '<div class="detail-dates">';
      if (t.created_at) h += '<div class="detail-date-item"><span>Created</span> ' + esc(t.created_at) + '</div>';
      if (t.updated_at) h += '<div class="detail-date-item"><span>Updated</span> ' + esc(t.updated_at) + '</div>';
      h += '</div></div>';
    }

    // Comments
    const comments = t.latest_comments || [];
    if (comments.length) {
      h += '<div class="detail-section">';
      h += '<div class="detail-label">Recent Comments</div>';
      comments.forEach(c => {
        h += '<div class="comment-item">';
        h += '<div class="comment-author">' + esc(c.author || 'Unknown') + '</div>';
        h += '<div class="comment-text">' + esc(c.text || '') + '</div>';
        if (c.date) h += '<div class="comment-date">' + esc(c.date) + '</div>';
        h += '</div>';
      });
      h += '</div>';
    }

    // Quick actions
    h += '<div class="quick-actions">';
    h += '<button class="action-btn proceed" onclick="sendAction(this,\'' + escAttr(tid) + '\',\'' + escAttr(projectKey) + '\',\'proceed\')">&#10003; Proceed</button>';
    h += '<button class="action-btn hold" onclick="sendAction(this,\'' + escAttr(tid) + '\',\'' + escAttr(projectKey) + '\',\'hold\')">&#9646;&#9646; Hold</button>';
    h += '<button class="action-btn question" onclick="openQuestion(this,\'' + escAttr(tid) + '\',\'' + escAttr(projectKey) + '\')">? Question</button>';
    h += '<button class="action-btn thumbsup" onclick="sendAction(this,\'' + escAttr(tid) + '\',\'' + escAttr(projectKey) + '\',\'thumbsup\')">&#128077;</button>';
    h += '<div class="question-input-wrap">';
    h += '<input class="question-input" type="text" placeholder="Type your question..." onkeydown="if(event.key===\'Enter\')sendQuestion(this)">';
    h += '<button class="question-send" onclick="sendQuestion(this.previousElementSibling)">Send</button>';
    h += '</div>';
    h += '</div>';

    h += '</div>'; // ticket-detail
    h += '</div>'; // ticket-card
  });
  return h;
}

function toggleProject(header) {
  header.parentElement.classList.toggle('open');
}

function toggleTicket(header) {
  const card = header.parentElement;
  card.classList.toggle('open');
}

function esc(s) {
  if (!s) return '';
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function escAttr(s) {
  if (!s) return '';
  return s.replace(/\\/g,'\\\\').replace(/'/g,"\\'").replace(/"/g,'\\"');
}

async function sendAction(btn, ticketId, projectKey, action, message) {
  btn.classList.add('sent');
  btn.textContent = 'Sent';
  try {
    const body = { ticket_id: ticketId, project_key: projectKey, action: action };
    if (message) body.message = message;
    const res = await fetch('/api/action', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Auth-Token': getToken() },
      body: JSON.stringify(body)
    });
    if (res.ok) {
      showToast(action === 'thumbsup' ? 'Acknowledged' : action.charAt(0).toUpperCase() + action.slice(1) + ' sent');
    } else {
      const data = await res.json();
      showToast('Error: ' + (data.error || 'failed'));
      btn.classList.remove('sent');
      btn.textContent = action;
    }
  } catch(e) {
    showToast('Network error');
    btn.classList.remove('sent');
  }
}

function openQuestion(btn, ticketId, projectKey) {
  const wrap = btn.parentElement.querySelector('.question-input-wrap');
  wrap.classList.toggle('visible');
  if (wrap.classList.contains('visible')) {
    const inp = wrap.querySelector('.question-input');
    inp.focus();
    inp.dataset.tid = ticketId;
    inp.dataset.pkey = projectKey;
  }
}

function sendQuestion(input) {
  const msg = input.value.trim();
  if (!msg) return;
  const tid = input.dataset.tid;
  const pkey = input.dataset.pkey;
  const wrap = input.parentElement;
  const qBtn = wrap.parentElement.querySelector('.action-btn.question');
  input.value = '';
  wrap.classList.remove('visible');
  sendAction(qBtn, tid, pkey, 'question', msg);
}

function getToken() {
  let t = localStorage.getItem('sb_token');
  if (!t) {
    t = prompt('Enter auth token:');
    if (t) localStorage.setItem('sb_token', t);
  }
  return t || '';
}

function resetCountdown() {
  let sec = 60;
  clearInterval(countdownInterval);
  document.getElementById('countdown').textContent = 'Auto-refresh in 60s';
  countdownInterval = setInterval(() => {
    sec--;
    document.getElementById('countdown').textContent = 'Auto-refresh in ' + sec + 's';
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

MANIFEST = json.dumps({
    "name": "Status Board",
    "short_name": "StatusBoard",
    "start_url": "/",
    "display": "standalone",
    "background_color": "#0a0e14",
    "theme_color": "#0d1117",
    "icons": [
        {
            "src": "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>&#9889;</text></svg>",
            "sizes": "any",
            "type": "image/svg+xml"
        }
    ]
})


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/manifest.json")
def manifest():
    return app.response_class(MANIFEST, mimetype="application/manifest+json")


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


@app.route("/api/action", methods=["POST"])
def api_action():
    """Queue a quick action for patrol cron to process."""
    token = request.headers.get("X-Auth-Token", "")
    if not hmac.compare_digest(token, AUTH_TOKEN):
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "no json body"}), 400

    ticket_id = data.get("ticket_id")
    action = data.get("action")
    if not ticket_id or not action:
        return jsonify({"error": "ticket_id and action required"}), 400

    entry = {
        "ticket_id": ticket_id,
        "project_key": data.get("project_key", ""),
        "action": action,
        "message": data.get("message", ""),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
    }

    with _actions_lock:
        _actions_queue.append(entry)
        _persist_actions()

    return jsonify({"ok": True, "queued": len(_actions_queue)})


@app.route("/api/actions")
def api_actions():
    """Patrol cron reads pending actions. Returns and clears the queue."""
    token = request.headers.get("X-Auth-Token", "")
    if not hmac.compare_digest(token, AUTH_TOKEN):
        return jsonify({"error": "unauthorized"}), 401

    with _actions_lock:
        pending = [a for a in _actions_queue if a.get("status") == "pending"]
        # Mark all as read
        for a in _actions_queue:
            if a.get("status") == "pending":
                a["status"] = "read"
        _persist_actions()

    return jsonify({"actions": pending, "count": len(pending)})


@app.route("/health")
def health():
    return "ok"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
