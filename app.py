"""RECLAW Command Center v7 — Family Impressive.

Single-file Flask app. Receives JSON from the patrol cron via POST /api/update,
serves a premium dark dashboard at GET /.
Quick-action buttons let the owner respond to tickets without typing.
Actions are queued to actions_queue.json for patrol cron pickup.

Deploy to Railway. Bookmark the URL on your phone.
"""

import os
import json
import hmac
import threading
from datetime import datetime, timezone
from pathlib import Path

import requests as http_requests
from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)

AUTH_TOKEN = os.environ.get("STATUS_BOARD_TOKEN", "changeme")
# Separate write token for patrol cron endpoints (/api/update, /api/actions, etc.)
# Falls back to AUTH_TOKEN if not set, preserving backward compatibility.
WRITE_TOKEN = os.environ.get("STATUS_BOARD_WRITE_TOKEN", AUTH_TOKEN)
VOICE_RELAY_URL = os.environ.get("VOICE_RELAY_URL", "https://voice-relay-v2-production.up.railway.app")
ACTIONS_FILE = Path("actions_queue.json")
STATE_FILE = Path("state.json")

# In-memory store — survives redeploys via STATE_FILE persistence
_state = {
    "last_updated": None,
    "projects": [],
}

# Load persisted state on startup (survives Railway redeploys)
if STATE_FILE.exists():
    try:
        _state.update(json.loads(STATE_FILE.read_text()))
    except Exception:
        pass

_actions_lock = threading.Lock()
_actions_queue: list[dict] = []

# Load any persisted actions on startup
if ACTIONS_FILE.exists():
    try:
        _actions_queue.extend(json.loads(ACTIONS_FILE.read_text()))
    except Exception:
        pass


def _persist_state():
    """Write full state to disk so it survives Railway redeploys."""
    try:
        STATE_FILE.write_text(json.dumps(_state, default=str))
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
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="theme-color" content="#030308">
<link rel="manifest" href="/manifest.json">
<title>RECLAW // COMMAND CENTER</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;700&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg:#030308;--panel:rgba(8,12,24,0.75);--border:rgba(0,240,255,0.15);
  --cyan:#00f0ff;--magenta:#ff2d78;--green:#39ff14;--amber:#ffb800;
  --dim:#444c5e;--text:#c8d0e0;--bright:#eef1f8;
  --font:'JetBrains Mono',monospace;
  --radius:8px;
}
html{font-size:14px}
body{
  background:var(--bg);color:var(--text);font-family:var(--font);
  min-height:100vh;overflow-x:hidden;position:relative;
}
/* Scan-line overlay */
body::after{
  content:'';position:fixed;inset:0;pointer-events:none;z-index:9999;
  background:repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(0,240,255,0.03) 2px,rgba(0,240,255,0.03) 4px);
}
canvas#starfield{position:fixed;inset:0;z-index:0}
#app{position:relative;z-index:1;max-width:1400px;margin:0 auto;padding:0 20px 40px}

/* Top Bar */
.topbar{
  display:flex;align-items:center;justify-content:space-between;
  padding:18px 0 12px;flex-wrap:wrap;gap:8px;
}
.logo{font-size:1.6rem;font-weight:700;letter-spacing:2px}
.logo .rc{color:var(--cyan);text-shadow:0 0 20px var(--cyan),0 0 40px rgba(0,240,255,0.3)}
.logo .cc{color:var(--dim);font-weight:300;font-size:1rem;margin-left:6px}
.clock-block{text-align:center}
.clock{font-size:1.5rem;color:var(--bright);font-weight:700;letter-spacing:3px}
.clock-date{font-size:.7rem;color:var(--dim);margin-top:2px}
.meta-block{text-align:right;font-size:.75rem}
.meta-block .refresh-timer{color:var(--cyan)}
.meta-block .last-sync{color:var(--dim);margin-top:2px}
/* sweep line */
.sweep{height:1px;background:linear-gradient(90deg,transparent,var(--cyan),transparent);margin:0 0 20px;position:relative;overflow:hidden}
.sweep::after{
  content:'';position:absolute;top:0;left:-100%;width:40%;height:100%;
  background:linear-gradient(90deg,transparent,var(--cyan),transparent);
  animation:sweep 3s linear infinite;
}
@keyframes sweep{0%{left:-40%}100%{left:100%}}

/* Glass Panel */
.panel{
  background:var(--panel);border:1px solid var(--border);
  border-radius:var(--radius);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);
  padding:20px;margin-bottom:20px;
}
.panel-header{
  display:flex;align-items:center;gap:8px;margin-bottom:16px;
  font-size:.85rem;font-weight:700;letter-spacing:2px;color:var(--bright);text-transform:uppercase;
}
.pulse-dot{
  width:8px;height:8px;border-radius:50%;background:var(--green);
  box-shadow:0 0 6px var(--green);animation:pulse 2s infinite;
}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}

/* Stats Row */
.stats-row{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:20px}
.stat-card{
  background:var(--panel);border:1px solid var(--border);border-radius:var(--radius);
  backdrop-filter:blur(12px);padding:18px 16px;text-align:center;position:relative;overflow:hidden;
}
.stat-card::before{content:'';position:absolute;top:0;left:0;right:0;height:2px}
.stat-card.c-cyan::before{background:var(--cyan)}
.stat-card.c-green::before{background:var(--green)}
.stat-card.c-amber::before{background:var(--amber)}
.stat-card.c-magenta::before{background:var(--magenta)}
.stat-num{font-size:2rem;font-weight:700;color:var(--bright);line-height:1}
.stat-label{font-size:.65rem;color:var(--dim);letter-spacing:2px;margin-top:6px;text-transform:uppercase}

/* Agent Fleet */
.company-divider{
  font-size:.65rem;color:var(--dim);letter-spacing:3px;text-transform:uppercase;
  margin:14px 0 8px;padding-bottom:4px;border-bottom:1px solid rgba(255,255,255,0.05);
}
.company-divider:first-child{margin-top:0}
.agent-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px}
.agent-card{
  display:flex;align-items:center;gap:10px;padding:10px 12px;
  background:rgba(255,255,255,0.03);border-radius:6px;border:1px solid rgba(255,255,255,0.04);
}
.agent-avatar{
  width:34px;height:34px;border-radius:50%;display:flex;align-items:center;justify-content:center;
  font-size:.7rem;font-weight:700;color:var(--bg);flex-shrink:0;
}
.agent-avatar.online{background:var(--green);box-shadow:0 0 8px var(--green);animation:pulse 2s infinite}
.agent-avatar.idle{background:var(--cyan);box-shadow:0 0 8px var(--cyan)}
.agent-avatar.paused{background:var(--dim);box-shadow:none}
.agent-avatar.error{background:var(--magenta);box-shadow:0 0 8px var(--magenta);animation:pulse 1.5s infinite}
.agent-avatar.offline{background:var(--dim);box-shadow:none}
.agent-info .agent-name{font-size:.75rem;color:var(--bright);font-weight:500}
.agent-info .agent-role{font-size:.6rem;color:var(--dim)}

/* Operations Board */
.ops-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(420px,1fr));gap:16px}
.proj-card{
  background:rgba(255,255,255,0.02);border:1px solid var(--border);border-radius:var(--radius);
  padding:16px;
}
.proj-header{
  display:flex;align-items:center;gap:8px;margin-bottom:10px;cursor:pointer;user-select:none;
}
.proj-prefix{
  font-size:.6rem;font-weight:700;padding:2px 7px;border-radius:3px;
  background:var(--cyan);color:var(--bg);letter-spacing:1px;
}
.proj-name{font-size:.85rem;font-weight:600;color:var(--bright)}
.proj-toggle{margin-left:auto;font-size:.7rem;color:var(--dim);transition:transform .2s}
.proj-toggle.open{transform:rotate(90deg)}
.proj-stats{display:flex;gap:14px;font-size:.65rem;margin-bottom:8px;flex-wrap:wrap}
.proj-stats span{letter-spacing:1px}
.proj-stats .s-active{color:var(--cyan)}
.proj-stats .s-blocked{color:var(--magenta)}
.proj-stats .s-queued{color:var(--amber)}
.proj-stats .s-done{color:var(--green)}
.prog-bar{
  height:4px;background:rgba(255,255,255,0.06);border-radius:2px;overflow:hidden;margin-bottom:6px;
}
.prog-fill{height:100%;background:var(--cyan);box-shadow:0 0 8px var(--cyan);border-radius:2px;transition:width .6s ease}
.ticket-list{max-height:0;overflow:hidden;transition:max-height .4s ease}
.ticket-list.open{max-height:2000px}
.ticket{
  display:flex;align-items:center;gap:8px;padding:6px 0;
  border-bottom:1px solid rgba(255,255,255,0.03);font-size:.7rem;flex-wrap:wrap;
}
.ticket:last-child{border-bottom:none}
.ticket-key{color:var(--dim);font-weight:500;min-width:70px;flex-shrink:0}
.ticket-title{color:var(--text);flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.badge{
  font-size:.55rem;padding:1px 6px;border-radius:3px;font-weight:700;letter-spacing:1px;flex-shrink:0;
}
.badge-active{background:rgba(0,240,255,0.15);color:var(--cyan)}
.badge-blocked{background:rgba(255,45,120,0.15);color:var(--magenta);animation:pulse 1.5s infinite}
.badge-queued{background:rgba(255,184,0,0.15);color:var(--amber)}
.badge-done{background:rgba(57,255,20,0.15);color:var(--green)}
.ticket-assignee{font-size:.6rem;color:var(--dim)}
.ticket-actions{display:flex;gap:4px;margin-left:auto}
.ticket-actions button{
  font-family:var(--font);font-size:.55rem;padding:2px 8px;border-radius:3px;
  border:1px solid var(--border);background:transparent;color:var(--cyan);cursor:pointer;
  letter-spacing:1px;transition:all .15s;
}
.ticket-actions button:hover{background:var(--cyan);color:var(--bg)}
.ticket-actions button.btn-done{color:var(--green);border-color:rgba(57,255,20,0.3)}
.ticket-actions button.btn-done:hover{background:var(--green);color:var(--bg)}

/* Launch Tracker */
.launch-countdown{font-size:2.5rem;font-weight:700;color:var(--cyan);text-shadow:0 0 20px rgba(0,240,255,0.4)}
.launch-product{font-size:.8rem;color:var(--dim);margin-bottom:12px}
.checklist-item{display:flex;align-items:center;gap:8px;padding:4px 0;font-size:.75rem}
.check-done{color:var(--green)}
.check-pending{color:var(--dim)}

/* Revenue */
.rev-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px}
.rev-card{
  background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.04);
  border-radius:6px;padding:14px;
}
.rev-label{font-size:.6rem;color:var(--dim);letter-spacing:2px;text-transform:uppercase;margin-bottom:4px}
.rev-value{font-size:1.1rem;font-weight:700;color:var(--bright)}

/* Toast */
.toast{
  position:fixed;bottom:24px;right:24px;background:rgba(0,240,255,0.12);
  border:1px solid var(--cyan);color:var(--cyan);padding:10px 18px;
  border-radius:6px;font-size:.75rem;font-family:var(--font);
  transform:translateY(80px);opacity:0;transition:all .3s ease;z-index:10000;
  backdrop-filter:blur(8px);
}
.toast.show{transform:translateY(0);opacity:1}

/* Awaiting state */
.awaiting{color:var(--dim);font-size:.75rem;letter-spacing:2px;text-align:center;padding:30px 0}

/* CEO Executive Briefings */
.ceo-section{margin-bottom:20px}
.ceo-header{
  display:flex;align-items:center;gap:10px;margin-bottom:16px;
  font-size:.9rem;font-weight:700;letter-spacing:3px;color:var(--amber);text-transform:uppercase;
}
.ceo-header-line{flex:1;height:1px;background:linear-gradient(90deg,var(--amber),transparent)}
.ceo-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}
.ceo-card{
  background:var(--panel);border:1px solid var(--border);border-radius:var(--radius);
  backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);
  padding:18px;position:relative;overflow:hidden;
  box-shadow:0 0 20px rgba(0,0,0,0.3);
  opacity:0;animation:ceoFadeIn .5s ease forwards;
}
.ceo-card:nth-child(1){animation-delay:.05s}
.ceo-card:nth-child(2){animation-delay:.1s}
.ceo-card:nth-child(3){animation-delay:.15s}
.ceo-card:nth-child(4){animation-delay:.2s}
.ceo-card:nth-child(5){animation-delay:.25s}
.ceo-card:nth-child(6){animation-delay:.3s}
@keyframes ceoFadeIn{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
.ceo-card::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:var(--cyan)}
.ceo-card.border-ljsa::before{background:var(--cyan)}
.ceo-card.border-ljsm::before{background:var(--magenta)}
.ceo-card.border-ljsc::before{background:var(--green)}
.ceo-card.border-pws::before{background:var(--amber)}
.ceo-card.border-sn::before{background:#a78bfa}
.ceo-card.border-mw::before{background:#f472b6}
.ceo-card:hover{box-shadow:0 0 30px rgba(0,240,255,0.1);border-color:rgba(0,240,255,0.25)}
.ceo-card-head{display:flex;align-items:center;gap:8px;margin-bottom:10px;flex-wrap:wrap}
.ceo-prefix{
  font-size:.6rem;font-weight:700;padding:2px 7px;border-radius:3px;
  background:var(--cyan);color:var(--bg);letter-spacing:1px;
}
.ceo-leader{font-size:.75rem;color:var(--bright);font-weight:500}
.ceo-role{
  font-size:.55rem;padding:1px 6px;border-radius:3px;font-weight:700;letter-spacing:1px;
  background:rgba(255,184,0,0.15);color:var(--amber);margin-left:auto;
}
.ceo-status-dot{
  width:7px;height:7px;border-radius:50%;flex-shrink:0;
}
.ceo-status-dot.online{background:var(--green);box-shadow:0 0 6px var(--green);animation:pulse 2s infinite}
.ceo-status-dot.idle{background:var(--cyan);box-shadow:0 0 6px var(--cyan)}
.ceo-status-dot.error{background:var(--magenta);box-shadow:0 0 6px var(--magenta);animation:pulse 1.5s infinite}
.ceo-status-dot.offline{background:var(--dim)}
.ceo-body{
  font-size:.7rem;color:var(--text);line-height:1.5;margin-bottom:10px;
  max-height:4.5em;overflow:hidden;transition:max-height .3s ease;cursor:pointer;
  position:relative;
}
.ceo-body.truncated::after{
  content:'';position:absolute;bottom:0;left:0;right:0;height:1.5em;
  background:linear-gradient(transparent,rgba(8,12,24,0.95));pointer-events:none;
}
.ceo-body.expanded{max-height:none;cursor:default}
.ceo-body.expanded::after{display:none}
.ceo-expand{font-size:.6rem;color:var(--cyan);cursor:pointer;letter-spacing:1px;margin-bottom:8px}
.ceo-expand:hover{text-decoration:underline}
.ceo-footer{font-size:.6rem;color:var(--dim);letter-spacing:1px}
.ceo-awaiting{
  color:var(--dim);font-size:.7rem;letter-spacing:2px;text-align:center;
  padding:20px 0;animation:pulse 2s infinite;
}
.ceo-card.dimmed{opacity:0.5}
.ceo-card.dimmed .ceo-awaiting{animation:pulse 2s infinite}
@media(max-width:768px){
  .ceo-grid{grid-template-columns:1fr}
}

/* Comment Modal */
.modal-overlay{
  position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:10001;
  display:none;align-items:center;justify-content:center;
}
.modal-overlay.open{display:flex}
.modal-box{
  background:#0a0f1a;border:1px solid var(--cyan);border-radius:var(--radius);
  padding:24px;width:90%;max-width:420px;
}
.modal-box h3{font-size:.85rem;color:var(--cyan);margin-bottom:12px;letter-spacing:1px}
.modal-box textarea{
  width:100%;height:80px;background:rgba(255,255,255,0.05);border:1px solid var(--border);
  border-radius:4px;color:var(--text);font-family:var(--font);font-size:.75rem;padding:10px;
  resize:vertical;
}
.modal-box .modal-btns{display:flex;gap:8px;margin-top:12px;justify-content:flex-end}
.modal-box .modal-btns button{
  font-family:var(--font);font-size:.7rem;padding:6px 16px;border-radius:4px;
  border:1px solid var(--border);cursor:pointer;letter-spacing:1px;
}
.modal-box .modal-btns .btn-send{background:var(--cyan);color:var(--bg);border-color:var(--cyan)}
.modal-box .modal-btns .btn-cancel{background:transparent;color:var(--dim)}

/* Call Log */
.call-log-header{
  display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;
}
.call-log-header .panel-header{margin-bottom:0}
.call-log-count{font-size:.7rem;color:var(--dim);letter-spacing:1px}
.call-card{
  background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.06);
  border-radius:6px;padding:14px 16px;margin-bottom:10px;cursor:pointer;
  transition:border-color .2s,background .2s;position:relative;
}
.call-card:hover{border-color:var(--cyan);background:rgba(0,240,255,0.03)}
.call-card-top{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.call-direction{
  font-size:.6rem;letter-spacing:1px;padding:2px 6px;border-radius:3px;font-weight:700;
}
.call-direction.inbound{color:var(--green);border:1px solid rgba(57,255,20,0.3)}
.call-direction.outbound{color:var(--amber);border:1px solid rgba(255,184,0,0.3)}
.call-caller{font-size:.85rem;color:var(--bright);font-weight:500}
.call-phone{font-size:.7rem;color:var(--dim);font-family:var(--font)}
.call-meta{
  display:flex;gap:16px;margin-top:8px;font-size:.65rem;color:var(--dim);letter-spacing:1px;
}
.call-meta span{display:flex;align-items:center;gap:4px}
.call-summary{
  font-size:.75rem;color:var(--text);margin-top:8px;line-height:1.5;
  opacity:0.8;
}
.call-transcript{
  display:none;margin-top:14px;padding-top:14px;border-top:1px solid rgba(255,255,255,0.06);
}
.call-card.expanded .call-transcript{display:block}
.call-card.expanded{border-color:rgba(0,240,255,0.3)}
.tx-line{
  display:flex;gap:10px;margin-bottom:8px;font-size:.75rem;line-height:1.5;
}
.tx-speaker{
  flex-shrink:0;font-size:.6rem;font-weight:700;letter-spacing:1px;
  padding:2px 6px;border-radius:3px;height:fit-content;margin-top:2px;
}
.tx-speaker.caller{color:var(--cyan);border:1px solid rgba(0,240,255,0.25)}
.tx-speaker.roy{color:var(--magenta);border:1px solid rgba(255,45,120,0.25)}
.tx-text{color:var(--text);flex:1}
.call-expand-hint{
  font-size:.6rem;color:var(--cyan);letter-spacing:1px;margin-top:8px;text-align:right;
}
.call-empty{
  text-align:center;padding:40px 20px;color:var(--dim);font-size:.8rem;letter-spacing:2px;
}
.call-perf{
  display:flex;gap:12px;margin-top:10px;font-size:.6rem;color:var(--dim);letter-spacing:1px;
  flex-wrap:wrap;
}
.call-perf .perf-good{color:var(--green)}
.call-perf .perf-warn{color:var(--amber)}
.call-perf .perf-bad{color:var(--magenta)}

/* Mobile */
@media(max-width:768px){
  .topbar{flex-direction:column;align-items:flex-start}
  .clock-block,.meta-block{text-align:left}
  .stats-row{grid-template-columns:repeat(2,1fr)}
  .agent-grid{grid-template-columns:repeat(2,1fr)}
  .ops-grid{grid-template-columns:1fr}
  .rev-grid{grid-template-columns:repeat(2,1fr)}
  .logo{font-size:1.2rem}
  .clock{font-size:1.1rem}
  .call-meta{flex-wrap:wrap;gap:8px}
}
</style>
</head>
<body>
<canvas id="starfield"></canvas>
<div id="app">

  <!-- Top Bar -->
  <div class="topbar">
    <div class="logo"><span class="rc">RECLAW</span><span class="cc">// COMMAND CENTER</span></div>
    <div class="clock-block">
      <div class="clock" id="clock">--:--:--</div>
      <div class="clock-date" id="clockDate">---</div>
    </div>
    <div class="meta-block">
      <div class="refresh-timer">REFRESH: <span id="countdown">30</span>s</div>
      <div class="last-sync" id="lastSync">LAST SYNC: --</div>
    </div>
  </div>
  <div class="sweep"></div>

  <!-- Stats Row -->
  <div class="stats-row">
    <div class="stat-card c-cyan">
      <div class="stat-num" id="statAgents">--</div>
      <div class="stat-label">Agents Online</div>
    </div>
    <div class="stat-card c-green">
      <div class="stat-num" id="statActive">--</div>
      <div class="stat-label">Active Ops</div>
    </div>
    <div class="stat-card c-amber">
      <div class="stat-num" id="statProjects">--</div>
      <div class="stat-label">Projects</div>
    </div>
    <div class="stat-card c-magenta">
      <div class="stat-num" id="statQueue">--</div>
      <div class="stat-label">Queue Depth</div>
    </div>
  </div>

  <!-- CEO Executive Briefings -->
  <div class="ceo-section" id="ceoSection" style="display:none">
    <div class="ceo-header">
      EXECUTIVE BRIEFINGS
      <div class="ceo-header-line"></div>
    </div>
    <div class="ceo-grid" id="ceoGrid"></div>
  </div>

  <!-- Agent Fleet -->
  <div class="panel" id="agentPanel" style="display:none">
    <div class="panel-header"><span class="pulse-dot"></span> AGENT FLEET</div>
    <div id="agentFleet"></div>
  </div>

  <!-- Operations Board -->
  <div class="panel">
    <div class="panel-header"><span class="pulse-dot" style="background:var(--cyan);box-shadow:0 0 6px var(--cyan)"></span> OPERATIONS</div>
    <div class="ops-grid" id="opsGrid">
      <div class="awaiting">AWAITING DATA</div>
    </div>
  </div>

  <!-- Launch Tracker -->
  <div class="panel" id="launchPanel" style="display:none">
    <div class="panel-header"><span class="pulse-dot" style="background:var(--amber);box-shadow:0 0 6px var(--amber)"></span> LAUNCH COUNTDOWN</div>
    <div id="launchContent"></div>
  </div>

  <!-- Revenue Telemetry -->
  <div class="panel" id="revenuePanel" style="display:none">
    <div class="panel-header"><span class="pulse-dot" style="background:var(--green);box-shadow:0 0 6px var(--green)"></span> REVENUE STREAMS</div>
    <div class="rev-grid" id="revGrid"></div>
  </div>

  <!-- Call Log -->
  <div class="panel" id="callLogPanel">
    <div class="call-log-header">
      <div class="panel-header"><span class="pulse-dot" style="background:var(--magenta);box-shadow:0 0 6px var(--magenta)"></span> ROY — CALL LOG</div>
      <div class="call-log-count" id="callLogCount">--</div>
    </div>
    <div id="callLogGrid">
      <div class="call-empty">LOADING CALL DATA...</div>
    </div>
  </div>

</div>

<!-- Toast -->
<div class="toast" id="toast"></div>

<!-- Comment Modal -->
<div class="modal-overlay" id="commentModal">
  <div class="modal-box">
    <h3 id="commentModalTitle">ADD COMMENT</h3>
    <textarea id="commentText" placeholder="Type your comment..."></textarea>
    <div class="modal-btns">
      <button class="btn-cancel" onclick="closeCommentModal()">CANCEL</button>
      <button class="btn-send" onclick="submitComment()">SEND</button>
    </div>
  </div>
</div>

<script>
/* ── CONFIG ─────────────────────────────────────────────── */
window.__TOKEN__ = '{{ auth_token }}';
const REFRESH_INTERVAL = 30;
let countdown = REFRESH_INTERVAL;
let refreshTimer = null;
let firstLoad = true;
let pendingCommentTicket = null;
let pendingCommentProject = null;

/* ── STARFIELD ──────────────────────────────────────────── */
(function(){
  const c = document.getElementById('starfield');
  const ctx = c.getContext('2d');
  let stars = [];
  function resize(){
    c.width = window.innerWidth;
    c.height = window.innerHeight;
    stars = [];
    for(let i=0;i<120;i++){
      stars.push({
        x:Math.random()*c.width,
        y:Math.random()*c.height,
        r:Math.random()*1.2+0.3,
        dx:(Math.random()-0.5)*0.15,
        dy:(Math.random()-0.5)*0.1,
        a:Math.random()*0.5+0.2
      });
    }
  }
  function draw(){
    ctx.clearRect(0,0,c.width,c.height);
    for(const s of stars){
      ctx.beginPath();
      ctx.arc(s.x,s.y,s.r,0,Math.PI*2);
      ctx.fillStyle='rgba(200,220,255,'+s.a+')';
      ctx.fill();
      s.x+=s.dx; s.y+=s.dy;
      if(s.x<0)s.x=c.width; if(s.x>c.width)s.x=0;
      if(s.y<0)s.y=c.height; if(s.y>c.height)s.y=0;
    }
    requestAnimationFrame(draw);
  }
  window.addEventListener('resize',resize);
  resize(); draw();
})();

/* ── CLOCK ──────────────────────────────────────────────── */
function updateClock(){
  const now = new Date();
  document.getElementById('clock').textContent =
    now.toLocaleTimeString('en-US',{hour12:false,hour:'2-digit',minute:'2-digit',second:'2-digit'});
  document.getElementById('clockDate').textContent =
    now.toLocaleDateString('en-US',{weekday:'short',month:'short',day:'numeric',year:'numeric'}).toUpperCase();
}
setInterval(updateClock,1000);
updateClock();

/* ── COUNTDOWN ──────────────────────────────────────────── */
function startCountdown(){
  countdown = REFRESH_INTERVAL;
  if(refreshTimer) clearInterval(refreshTimer);
  refreshTimer = setInterval(()=>{
    countdown--;
    document.getElementById('countdown').textContent = countdown;
    if(countdown<=0){ fetchData(); }
  },1000);
}

/* ── RELATIVE TIME ──────────────────────────────────────── */
function timeAgo(iso){
  if(!iso) return '--';
  const diff = (Date.now() - new Date(iso).getTime())/1000;
  if(diff<60) return 'just now';
  if(diff<3600) return Math.floor(diff/60)+' min ago';
  if(diff<86400) return Math.floor(diff/3600)+' hr ago';
  return Math.floor(diff/86400)+' days ago';
}

/* ── TOAST ──────────────────────────────────────────────── */
function showToast(msg){
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'),2500);
}

/* ── COUNT-UP ANIMATION ─────────────────────────────────── */
function animateNum(el, target){
  const dur = 600;
  const start = parseInt(el.textContent)||0;
  if(start===target){el.textContent=target;return;}
  const t0 = performance.now();
  function tick(now){
    const p = Math.min((now-t0)/dur,1);
    el.textContent = Math.round(start + (target-start)*p);
    if(p<1) requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}

/* ── STATUS HELPERS ─────────────────────────────────────── */
const STATUS_MAP = {
  in_progress:{label:'ACTIVE',cls:'badge-active'},
  blocked:{label:'BLOCKED',cls:'badge-blocked'},
  todo:{label:'QUEUED',cls:'badge-queued'},
  done:{label:'DONE',cls:'badge-done'}
};
function statusBadge(s){
  const m = STATUS_MAP[s]||{label:s,cls:'badge-queued'};
  return '<span class="badge '+m.cls+'">'+m.label+'</span>';
}

/* ── RENDER STATS ───────────────────────────────────────── */
function renderStats(data){
  // Handle enriched agents structure: {roster:[], summary:{}, total:N}
  // Fallback: if data.agents is a flat array (old format), treat as roster directly
  const agentsObj = data.agents||{};
  const roster = Array.isArray(agentsObj) ? agentsObj : (agentsObj.roster||[]);
  const summary = Array.isArray(agentsObj) ? null : (agentsObj.summary||{});
  const totalAgents = Array.isArray(agentsObj) ? roster.length : (agentsObj.total||roster.length);
  const onlineCount = summary ? (summary.online||0) : roster.filter(a=>a.status==='online').length;

  const projects = data.projects||[];
  let active=0, queued=0;
  projects.forEach(p=>{
    const s = p.stats;
    if(s){
      active += s.active||0;
      queued += s.queued||0;
    } else {
      (p.tickets||[]).forEach(t=>{
        if(t.status==='in_progress') active++;
        if(t.status==='todo') queued++;
      });
    }
  });
  document.getElementById('statAgents').textContent = onlineCount+'/'+totalAgents;
  animateNum(document.getElementById('statActive'), active);
  animateNum(document.getElementById('statProjects'), projects.length);
  animateNum(document.getElementById('statQueue'), queued);
}

/* ── AGENT STATUS CONFIG ────────────────────────────────── */
const AGENT_STATUS = {
  online: {cls:'online', label:'ACTIVE'},
  idle:   {cls:'idle',   label:'READY'},
  paused: {cls:'paused', label:'PAUSED'},
  error:  {cls:'error',  label:'ERROR'},
  offline:{cls:'offline',label:'OFFLINE'}
};

/* ── RENDER CEO REPORTS ─────────────────────────────────── */
function renderCeoReports(reports){
  const section = document.getElementById('ceoSection');
  const grid = document.getElementById('ceoGrid');
  if(!reports||!reports.length){section.style.display='none';return;}
  section.style.display='block';

  // Color map for company prefixes
  const colorMap = {LJSA:'ljsa',LJSM:'ljsm',LJSC:'ljsc',PWS:'pws',SN:'sn',MW:'mw'};

  let html='';
  reports.forEach(r=>{
    const prefix = (r.prefix||'').toUpperCase();
    const borderCls = colorMap[prefix]?'border-'+colorMap[prefix]:'';
    const st = (r.status||'offline').toLowerCase();
    const dotCls = ({online:'online',idle:'idle',error:'error'})[st]||'offline';
    const noReport = !r.last_report||r.last_report.trim()==='No report yet...';
    const dimmed = noReport?'dimmed':'';

    html+='<div class="ceo-card '+borderCls+' '+dimmed+'">';
    html+='<div class="ceo-card-head">';
    html+='<span class="ceo-status-dot '+dotCls+'"></span>';
    html+='<span class="ceo-prefix">'+esc(prefix)+'</span>';
    html+='<span class="ceo-leader">'+esc(r.leader||'Unknown')+'</span>';
    html+='<span class="ceo-role">'+esc((r.role||'').toUpperCase())+'</span>';
    html+='</div>';

    if(noReport){
      html+='<div class="ceo-awaiting">AWAITING REPORT</div>';
    } else {
      // Basic markdown: **bold** → <strong>, \n → <br>
      let body = esc(r.last_report||'');
      body = body.replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>');
      body = body.replace(/\n/g,'<br>');
      const needsTrunc = (r.last_report||'').length>200;
      const truncCls = needsTrunc?'truncated':'';
      html+='<div class="ceo-body '+truncCls+'" onclick="toggleCeoBody(this)">'+body+'</div>';
      if(needsTrunc) html+='<div class="ceo-expand" onclick="toggleCeoBody(this.previousElementSibling)">SHOW MORE</div>';
    }

    html+='<div class="ceo-footer">REPORT '+timeAgo(r.report_time)+'</div>';
    html+='</div>';
  });
  grid.innerHTML = html;
}

function toggleCeoBody(el){
  if(!el||!el.classList.contains('ceo-body')) return;
  const wasExpanded = el.classList.contains('expanded');
  el.classList.toggle('expanded');
  el.classList.toggle('truncated',wasExpanded);
  const expandBtn = el.nextElementSibling;
  if(expandBtn&&expandBtn.classList.contains('ceo-expand')){
    expandBtn.textContent = wasExpanded?'SHOW MORE':'SHOW LESS';
  }
}

/* ── RENDER AGENTS ──────────────────────────────────────── */
function renderAgents(roster){
  const panel = document.getElementById('agentPanel');
  const el = document.getElementById('agentFleet');
  if(!roster||!roster.length){panel.style.display='none';return;}
  panel.style.display='block';
  // Group by company
  const groups = {};
  roster.forEach(a=>{
    const co = a.company||'UNASSIGNED';
    if(!groups[co]) groups[co]=[];
    groups[co].push(a);
  });
  let html='';
  for(const co of Object.keys(groups).sort()){
    html+='<div class="company-divider">'+esc(co)+' ('+groups[co].length+')</div><div class="agent-grid">';
    groups[co].forEach(a=>{
      const initials = (a.name||'??').split(/\s+/).map(w=>w[0]).join('').substring(0,2).toUpperCase();
      const st = (a.status||'offline').toLowerCase();
      const cfg = AGENT_STATUS[st]||AGENT_STATUS.offline;
      const stuckBadge = a.stuck ? '<span style="font-size:.55rem;color:var(--magenta);border:1px solid rgba(255,45,120,0.4);padding:1px 4px;border-radius:3px;margin-left:4px;letter-spacing:1px">STUCK</span>' : '';
      html+='<div class="agent-card"><div class="agent-avatar '+cfg.cls+'">'+initials+'</div>';
      html+='<div class="agent-info"><div class="agent-name">'+esc(a.name||'Unknown')+stuckBadge+'</div>';
      html+='<div class="agent-role">'+esc(a.role||'')+' &middot; '+cfg.label+'</div></div></div>';
    });
    html+='</div>';
  }
  el.innerHTML = html;
}

/* ── RENDER OPERATIONS ──────────────────────────────────── */
function renderOps(projects){
  const el = document.getElementById('opsGrid');
  if(!projects||!projects.length){el.innerHTML='<div class="awaiting">AWAITING DATA</div>';return;}
  let html='';
  projects.forEach((p,pi)=>{
    const tickets = p.tickets||[];
    const s = p.stats||{};
    // Use pre-computed stats if available, else count manually
    const act = s.active!=null ? s.active : tickets.filter(t=>t.status==='in_progress').length;
    const blk = s.blocked!=null ? s.blocked : tickets.filter(t=>t.status==='blocked').length;
    const que = s.queued!=null ? s.queued : tickets.filter(t=>t.status==='todo').length;
    const don = s.done!=null ? s.done : tickets.filter(t=>t.status==='done').length;
    const pct = s.completion_pct!=null ? s.completion_pct : ((act+blk+que+don)? Math.round(don/(act+blk+que+don)*100) : 0);
    const prefix = p.prefix||p.name||'PROJECT';
    html+='<div class="proj-card">';
    html+='<div class="proj-header" onclick="toggleTickets('+pi+')">';
    html+='<span class="proj-prefix">'+esc(prefix)+'</span>';
    html+='<span class="proj-name">'+esc(p.name||prefix)+'</span>';
    html+='<span style="margin-left:auto;font-size:.7rem;color:var(--cyan)">'+pct+'%</span>';
    html+='<span class="proj-toggle" id="toggle'+pi+'">&#9654;</span></div>';
    html+='<div class="proj-stats">';
    html+='<span class="s-active">'+act+' ACTIVE</span>';
    html+='<span class="s-blocked">'+blk+' BLOCKED</span>';
    html+='<span class="s-queued">'+que+' QUEUED</span>';
    html+='<span class="s-done">'+don+' DONE</span>';
    html+='</div>';
    html+='<div class="prog-bar"><div class="prog-fill" style="width:'+pct+'%"></div></div>';
    html+='<div class="ticket-list" id="tickets'+pi+'">';
    // Sort: blocked first, then active, queued, done
    const order={blocked:0,in_progress:1,todo:2,done:3};
    tickets.sort((a,b)=>(order[a.status]||9)-(order[b.status]||9));
    tickets.forEach(t=>{
      html+='<div class="ticket">';
      const paperclipBase = (window._state&&window._state.paperclip_ui_url)||'';
      const tkLink = (t.url&&paperclipBase) ? '<a href="'+paperclipBase+esc(t.url)+'" target="_blank" style="color:inherit;text-decoration:none;border-bottom:1px dotted rgba(0,240,255,0.4)">'+esc(t.key||'')+'</a>' : esc(t.key||'');
      html+='<span class="ticket-key">'+tkLink+'</span>';
      html+='<span class="ticket-title" title="'+esc(t.title||'')+'">'+esc(t.title||'Untitled')+'</span>';
      html+=statusBadge(t.status);
      if(t.assignee) html+='<span class="ticket-assignee">'+esc(t.assignee)+'</span>';
      html+='<span class="ticket-actions">';
      html+='<button onclick="doAction(event,\''+esc(t.key||'')+'\',\''+esc(prefix)+'\',\'approve\')">APPROVE</button>';
      html+='<button class="btn-done" onclick="doAction(event,\''+esc(t.key||'')+'\',\''+esc(prefix)+'\',\'done\')">DONE</button>';
      html+='<button onclick="openCommentModal(\''+esc(t.key||'')+'\',\''+esc(prefix)+'\')">COMMENT</button>';
      html+='</span></div>';
    });
    html+='</div></div>';
  });
  el.innerHTML = html;
}

function toggleTickets(i){
  const list = document.getElementById('tickets'+i);
  const tog = document.getElementById('toggle'+i);
  if(list){
    list.classList.toggle('open');
    tog.classList.toggle('open');
  }
}

/* ── RENDER LAUNCH ──────────────────────────────────────── */
function renderLaunch(launch){
  const panel = document.getElementById('launchPanel');
  const el = document.getElementById('launchContent');
  if(!launch||!launch.target_date){panel.style.display='none';return;}
  panel.style.display='block';
  const diff = Math.max(0,Math.ceil((new Date(launch.target_date)-Date.now())/86400000));
  const checklist = launch.checklist||[];
  const done = checklist.filter(c=>c.done).length;
  const pct = checklist.length?Math.round(done/checklist.length*100):0;
  let html='<div class="launch-product">'+esc(launch.product_name||'PRODUCT')+'</div>';
  html+='<div class="launch-countdown">'+diff+' DAYS</div>';
  html+='<div class="prog-bar" style="margin:12px 0"><div class="prog-fill" style="width:'+pct+'%"></div></div>';
  html+='<div style="font-size:.65rem;color:var(--dim);margin-bottom:10px">'+done+'/'+checklist.length+' COMPLETE</div>';
  checklist.forEach(c=>{
    const cls = c.done?'check-done':'check-pending';
    const icon = c.done?'[x]':'[ ]';
    html+='<div class="checklist-item '+cls+'"><span>'+icon+'</span> '+esc(c.item||'')+'</div>';
  });
  el.innerHTML = html;
}

/* ── RENDER REVENUE ─────────────────────────────────────── */
function fmtDollar(v){
  if(v===null||v===undefined) return '--';
  const n = parseFloat(v);
  if(isNaN(n)||n===0) return 'PRE-LAUNCH';
  return '$'+n.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
}
function renderRevenue(rev){
  const panel = document.getElementById('revenuePanel');
  const el = document.getElementById('revGrid');
  if(!rev){panel.style.display='none';return;}
  panel.style.display='block';
  const w2 = rev.w2_status||'--';
  const w2Style = w2==='ACTIVE'?'color:var(--green)':'';
  const cards = [
    {label:'W-2 STATUS',value:w2,style:w2Style},
    {label:'SCRIPTURE NOTES MRR',value:fmtDollar(rev.scripture_mrr)},
    {label:'SCRIPTURE SUBS',value:rev.scripture_subs!=null?(rev.scripture_subs||'0'):'--'},
    {label:'PWS AUM',value:fmtDollar(rev.pws_aum)},
    {label:'REAL ESTATE NOI',value:fmtDollar(rev.re_noi)},
    {label:'RE UNITS',value:rev.re_units!=null?(rev.re_units||'0'):'--'}
  ];
  let html='';
  cards.forEach(c=>{
    const st = c.style?' style="'+c.style+'"':'';
    html+='<div class="rev-card"><div class="rev-label">'+c.label+'</div><div class="rev-value"'+st+'>'+esc(String(c.value))+'</div></div>';
  });
  el.innerHTML = html;
}

/* ── QUICK ACTIONS ──────────────────────────────────────── */
function doAction(e, ticketId, projectKey, action){
  e.stopPropagation();
  fetch('/api/action',{
    method:'POST',
    headers:{'Content-Type':'application/json','X-Auth-Token':window.__TOKEN__},
    body:JSON.stringify({ticket_id:ticketId,project_key:projectKey,action:action})
  }).then(r=>r.json()).then(d=>{
    if(d.ok) showToast(action.toUpperCase()+' queued for '+ticketId);
    else showToast('Error: '+(d.error||'unknown'));
  }).catch(()=>showToast('Network error'));
}

function openCommentModal(ticketId, projectKey){
  pendingCommentTicket = ticketId;
  pendingCommentProject = projectKey;
  document.getElementById('commentModalTitle').textContent = 'COMMENT ON '+ticketId;
  document.getElementById('commentText').value = '';
  document.getElementById('commentModal').classList.add('open');
  setTimeout(()=>document.getElementById('commentText').focus(),100);
}

function closeCommentModal(){
  document.getElementById('commentModal').classList.remove('open');
  pendingCommentTicket = null;
  pendingCommentProject = null;
}

function submitComment(){
  const msg = document.getElementById('commentText').value.trim();
  if(!msg){showToast('Comment cannot be empty');return;}
  fetch('/api/action',{
    method:'POST',
    headers:{'Content-Type':'application/json','X-Auth-Token':window.__TOKEN__},
    body:JSON.stringify({ticket_id:pendingCommentTicket,project_key:pendingCommentProject,action:'comment',message:msg})
  }).then(r=>r.json()).then(d=>{
    if(d.ok) showToast('Comment queued for '+pendingCommentTicket);
    else showToast('Error: '+(d.error||'unknown'));
  }).catch(()=>showToast('Network error'));
  closeCommentModal();
}

// Close modal on overlay click
document.getElementById('commentModal').addEventListener('click',function(e){if(e.target===this)closeCommentModal()});

/* ── ESCAPE HTML ────────────────────────────────────────── */
function esc(s){
  const d=document.createElement('div');d.textContent=s;return d.innerHTML;
}

/* ── FETCH DATA ─────────────────────────────────────────── */
function fetchData(){
  fetch('/api/status')
    .then(r=>r.json())
    .then(data=>{
      window._state = data;
      renderStats(data);
      renderCeoReports(data.ceo_reports);
      // Handle enriched agents structure or flat array fallback
      const agentsData = data.agents||{};
      const roster = Array.isArray(agentsData) ? agentsData : (agentsData.roster||[]);
      renderAgents(roster);
      renderOps(data.projects);
      renderLaunch(data.launch);
      renderRevenue(data.revenue);
      document.getElementById('lastSync').textContent = 'LAST SYNC: '+timeAgo(data.last_updated);
      if(!firstLoad) showToast('DATA REFRESHED');
      firstLoad = false;
      startCountdown();
    })
    .catch(e=>{
      console.error('Fetch error:',e);
      startCountdown();
    });
}

/* ── CALL LOG ──────────────────────────────────────────── */
let callsCache = {};  // sid → full call data

function formatDuration(sec){
  sec = parseFloat(sec)||0;
  const m = Math.floor(sec/60);
  const s = Math.round(sec%60);
  return m>0 ? m+'m '+s+'s' : s+'s';
}

function formatCallTime(iso){
  if(!iso) return '--';
  const d = new Date(iso);
  return d.toLocaleDateString('en-US',{month:'short',day:'numeric'})+' '+d.toLocaleTimeString('en-US',{hour:'numeric',minute:'2-digit',hour12:true});
}

function perfClass(ms){
  if(!ms||ms<=0) return 'perf-good';
  if(ms<800) return 'perf-good';
  if(ms<1500) return 'perf-warn';
  return 'perf-bad';
}

function renderCallLog(calls){
  const el = document.getElementById('callLogGrid');
  const countEl = document.getElementById('callLogCount');
  if(!calls||!calls.length){
    el.innerHTML='<div class="call-empty">NO CALLS RECORDED YET</div>';
    countEl.textContent='0 CALLS';
    return;
  }
  countEl.textContent=calls.length+' CALL'+(calls.length!==1?'S':'');
  let html='';
  calls.forEach((c,i)=>{
    const dir = c.direction||'inbound';
    const name = c.callerName||'Unknown Caller';
    const phone = c.callerNumber||'';
    const dur = formatDuration(c.durationSec);
    const time = formatCallTime(c.timestamp);
    const summary = c.summary||'';
    const p50 = c.turnaroundP50Ms||0;

    html+='<div class="call-card" onclick="toggleCallTranscript(this,\''+esc(c.callSid)+'\')" data-sid="'+esc(c.callSid)+'">';
    html+='<div class="call-card-top">';
    html+='<span class="call-direction '+dir+'">'+dir.toUpperCase()+'</span>';
    html+='<span class="call-caller">'+esc(name)+'</span>';
    if(phone) html+='<span class="call-phone">'+esc(phone)+'</span>';
    html+='</div>';
    html+='<div class="call-meta">';
    html+='<span>'+time+'</span>';
    html+='<span>'+dur+'</span>';
    html+='<span>'+c.totalTurns+' turns</span>';
    if(p50) html+='<span class="'+perfClass(p50)+'">p50: '+p50+'ms</span>';
    if(c.bargeIns) html+='<span>'+c.bargeIns+' interrupts</span>';
    html+='</div>';
    if(summary) html+='<div class="call-summary">'+esc(summary)+'</div>';
    if(c.objective) html+='<div class="call-summary" style="color:var(--amber);font-size:.65rem;margin-top:4px">OBJECTIVE: '+esc(c.objective)+'</div>';
    html+='<div class="call-expand-hint">CLICK TO VIEW TRANSCRIPT</div>';
    html+='<div class="call-transcript" id="tx-'+esc(c.callSid)+'"><div class="call-empty" style="padding:20px">LOADING...</div></div>';
    html+='</div>';
  });
  el.innerHTML=html;
}

function toggleCallTranscript(card, sid){
  const wasExpanded = card.classList.contains('expanded');
  // Collapse all others
  document.querySelectorAll('.call-card.expanded').forEach(c=>{
    if(c!==card) c.classList.remove('expanded');
  });
  if(wasExpanded){
    card.classList.remove('expanded');
    return;
  }
  card.classList.add('expanded');
  const txEl = document.getElementById('tx-'+sid);
  if(!txEl) return;

  // Check cache
  if(callsCache[sid]){
    renderTranscript(txEl, callsCache[sid]);
    return;
  }

  // Fetch full call data via proxy
  txEl.innerHTML='<div class="call-empty" style="padding:20px">LOADING TRANSCRIPT...</div>';
  fetch('/api/calls/'+sid)
    .then(r=>r.json())
    .then(data=>{
      if(data.error){
        txEl.innerHTML='<div class="call-empty" style="padding:10px;color:var(--magenta)">'+esc(data.error)+'</div>';
        return;
      }
      callsCache[sid]=data;
      renderTranscript(txEl, data);
    })
    .catch(e=>{
      txEl.innerHTML='<div class="call-empty" style="padding:10px;color:var(--magenta)">FETCH ERROR</div>';
    });
}

function renderTranscript(el, data){
  const turns = data.turns||data.transcript||[];
  if(!turns.length){
    el.innerHTML='<div class="call-empty" style="padding:10px">NO TRANSCRIPT DATA</div>';
    return;
  }
  let html='';
  // Performance summary
  if(data.turnaroundP50Ms||data.turnaroundP95Ms){
    html+='<div class="call-perf">';
    html+='<span class="'+perfClass(data.turnaroundP50Ms)+'">P50: '+(data.turnaroundP50Ms||'--')+'ms</span>';
    html+='<span class="'+perfClass(data.turnaroundP95Ms)+'">P95: '+(data.turnaroundP95Ms||'--')+'ms</span>';
    html+='<span>BARGE-INS: '+(data.bargeIns||0)+'</span>';
    html+='<span>SILENCE EVENTS: '+((data.silenceEvents||[]).length||0)+'</span>';
    html+='</div>';
  }
  // Transcript lines
  turns.forEach(t=>{
    if(t.userText||t.user||(t.speaker==='Caller'&&t.text)){
      const text = t.userText||t.user||t.text||'';
      if(text) html+='<div class="tx-line"><span class="tx-speaker caller">CALLER</span><span class="tx-text">'+esc(text)+'</span></div>';
    }
    if(t.royText||t.roy||(t.speaker==='Roy'&&t.text)){
      const text = t.royText||t.roy||t.text||'';
      if(text) html+='<div class="tx-line"><span class="tx-speaker roy">ROY</span><span class="tx-text">'+esc(text)+'</span></div>';
    }
  });
  if(!html) html='<div class="call-empty" style="padding:10px">NO TRANSCRIPT DATA</div>';
  el.innerHTML=html;
}

function fetchCallLog(){
  fetch('/api/calls')
    .then(r=>r.json())
    .then(data=>{
      renderCallLog(data.calls||[]);
    })
    .catch(e=>{
      console.error('Call log fetch error:',e);
      document.getElementById('callLogGrid').innerHTML='<div class="call-empty">VOICE RELAY OFFLINE</div>';
    });
}

/* ── INIT ──────────────────────────────────────────────── */
fetchData();
fetchCallLog();
// Refresh call log every 60s
setInterval(fetchCallLog, 60000);
</script>
</body>
</html>"""


# ── Manifest ─────────────────────────────────────────────────────────────────

MANIFEST = json.dumps({
    "name": "RECLAW Command Center",
    "short_name": "RECLAW",
    "start_url": "/",
    "display": "standalone",
    "background_color": "#0a0f1a",
    "theme_color": "#0a0f1a",
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
    return render_template_string(HTML, auth_token=AUTH_TOKEN)


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
    if not hmac.compare_digest(token, WRITE_TOKEN):
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "no json body"}), 400

    _state["last_updated"] = data.get(
        "last_updated", datetime.now(timezone.utc).isoformat()
    )

    # Patrol sends separate arrays (in_progress, todo, blocked, recently_done).
    # The frontend expects a single flat "tickets" array per project.
    raw_projects = data.get("projects", [])
    for p in raw_projects:
        if "tickets" not in p:
            merged = []
            for t in p.get("in_progress", []):
                t.setdefault("status", "in_progress")
                merged.append(t)
            for t in p.get("blocked", []):
                t.setdefault("status", "blocked")
                merged.append(t)
            for t in p.get("todo", []):
                t.setdefault("status", "todo")
                merged.append(t)
            for t in p.get("recently_done", []):
                t.setdefault("status", "done")
                merged.append(t)
            # Normalize field names: patrol sends latest_comments, JS expects comments
            for t in merged:
                if "latest_comments" in t and "comments" not in t:
                    t["comments"] = t.pop("latest_comments")
            p["tickets"] = merged
    _state["projects"] = raw_projects

    # Accept optional enrichment keys inline
    if "revenue" in data:
        _state["revenue"] = data["revenue"]
    if "agents" in data:
        _state["agents"] = data["agents"]
    if "launch" in data:
        _state["launch"] = data["launch"]
    if "threats" in data:
        _state["threats"] = data["threats"]
    if "executive_summary" in data:
        _state["executive_summary"] = data["executive_summary"]
    if "ceo_reports" in data:
        _state["ceo_reports"] = data["ceo_reports"]
    if "paperclip_ui_url" in data:
        _state["paperclip_ui_url"] = data["paperclip_ui_url"]

    _persist_state()
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
    if not hmac.compare_digest(token, WRITE_TOKEN):
        return jsonify({"error": "unauthorized"}), 401

    with _actions_lock:
        pending = [a for a in _actions_queue if a.get("status") == "pending"]
        for a in _actions_queue:
            if a.get("status") == "pending":
                a["status"] = "read"
        _persist_actions()

    return jsonify({"actions": pending, "count": len(pending)})


@app.route("/api/revenue", methods=["POST"])
def api_revenue():
    """Update revenue data (optional enrichment)."""
    token = request.headers.get("X-Auth-Token", "")
    if not hmac.compare_digest(token, WRITE_TOKEN):
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "no json body"}), 400

    _state["revenue"] = {
        "w2_status": data.get("w2_status"),
        "pws_aum": data.get("pws_aum"),
        "scripture_mrr": data.get("scripture_mrr"),
        "scripture_subs": data.get("scripture_subs"),
        "re_noi": data.get("re_noi"),
        "re_units": data.get("re_units"),
    }
    _persist_state()
    return jsonify({"ok": True})


@app.route("/api/agents", methods=["POST"])
def api_agents():
    """Update agent fleet status (optional enrichment)."""
    token = request.headers.get("X-Auth-Token", "")
    if not hmac.compare_digest(token, WRITE_TOKEN):
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "no json body"}), 400

    _state["agents"] = data.get("agents", [])
    _persist_state()
    return jsonify({"ok": True})


@app.route("/api/launch", methods=["POST"])
def api_launch():
    """Update launch countdown data (optional enrichment)."""
    token = request.headers.get("X-Auth-Token", "")
    if not hmac.compare_digest(token, WRITE_TOKEN):
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "no json body"}), 400

    _state["launch"] = {
        "target_date": data.get("target_date"),
        "product_name": data.get("product_name"),
        "checklist": data.get("checklist", []),
    }
    _persist_state()
    return jsonify({"ok": True})


@app.route("/api/calls")
def api_calls():
    """Proxy call log list from voice relay."""
    try:
        r = http_requests.get(f"{VOICE_RELAY_URL}/calls?limit=50", timeout=10)
        return app.response_class(r.content, mimetype="application/json")
    except Exception as e:
        return jsonify({"calls": [], "error": str(e)})


@app.route("/api/calls/<sid>")
def api_call_detail(sid):
    """Proxy full call transcript from voice relay."""
    try:
        r = http_requests.get(f"{VOICE_RELAY_URL}/calls/{sid}", timeout=10)
        return app.response_class(r.content, mimetype="application/json", status=r.status_code)
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/health")
def health():
    return "ok"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
