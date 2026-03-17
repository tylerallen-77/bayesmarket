"""Web dashboard — browser-based alternative to Rich terminal.

Serves a single HTML page with the same 4-panel layout as the terminal dashboard.
Uses Server-Sent Events (SSE) for real-time updates without page reload.
Runs on aiohttp (already a dependency).

Enabled automatically on Railway (no TTY), or via WEB_DASHBOARD=true env var.

Tabs:
  - Dashboard: live 4-panel scores + position + risk (SSE, 3s refresh)
  - Config: read-only runtime config display (fetched on tab switch)
  - Trades: recent trade history + summary stats from SQLite
"""

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import structlog
from aiohttp import web

from bayesmarket import config

if TYPE_CHECKING:
    from bayesmarket.data.state import MarketState
    from bayesmarket.data.storage import Storage

logger = structlog.get_logger()

WEB_PORT = int(os.getenv("PORT", "8080"))


# ── Data extraction ──────────────────────────────────────────────────────────

def _extract_dashboard_data(state: "MarketState") -> dict:
    """Extract all dashboard data into a JSON-serializable dict."""
    data: dict = {
        "timestamp": time.strftime("%H:%M:%S"),
        "mid_price": state.mid_price,
        "capital": state.capital,
        "kline_source": state.kline_source,
        "funding_rate": state.funding_rate,
        "funding_tier": state.funding_tier,
        "cascade_direction": state.cascade_allowed_direction,
        "cascade_context": state.cascade_context_confirmed,
        "timeframes": {},
        "position": None,
        "risk": None,
        "mode": "SHADOW",
    }

    # Mode
    rt = state.runtime
    if rt:
        if not rt.live_mode:
            data["mode"] = "SHADOW"
        elif config.IS_TESTNET:
            data["mode"] = "TESTNET"
        else:
            data["mode"] = "LIVE"
        data["paused"] = rt.trading_paused
    else:
        data["paused"] = False

    # Risk
    risk = state.risk
    data["risk"] = {
        "daily_pnl": risk.daily_pnl,
        "trades_today": risk.trades_today,
        "consecutive_wins": risk.consecutive_wins,
        "consecutive_losses": risk.consecutive_losses,
        "cooldown": risk.cooldown_active,
        "full_stop": risk.full_stop_active,
        "daily_paused": risk.daily_paused,
    }

    risk_label = "NORMAL"
    if risk.full_stop_active:
        risk_label = "FULL_STOP"
    elif risk.daily_paused:
        risk_label = "DAILY_PAUSED"
    elif risk.cooldown_active:
        risk_label = "COOLDOWN"
    data["risk"]["label"] = risk_label

    # Position
    pos = state.position
    if pos:
        from bayesmarket.engine.position import calculate_unrealized_pnl
        unrealized = calculate_unrealized_pnl(pos, state.mid_price)
        pnl_pct = unrealized / state.capital * 100 if state.capital > 0 else 0
        duration = time.time() - pos.entry_time
        data["position"] = {
            "side": pos.side,
            "entry_price": pos.entry_price,
            "remaining_size": pos.remaining_size,
            "sl_price": pos.sl_price,
            "sl_basis": pos.sl_basis,
            "tp1_price": pos.tp1_price,
            "tp1_hit": pos.tp1_hit,
            "tp2_price": pos.tp2_price,
            "unrealized": unrealized,
            "pnl_pct": pnl_pct,
            "duration_s": duration,
            "trailing_active": pos.trailing_active,
        }

    # Timeframes
    for tf_name in ["4h", "1h", "15m", "5m"]:
        tf_state = state.tf_states.get(tf_name)
        tf_cfg = config.TIMEFRAMES.get(tf_name, {})
        snap = tf_state.signal if tf_state else None

        tf_data: dict = {
            "role": tf_cfg.get("role", ""),
            "using_fallback": tf_state.using_fallback if tf_state else False,
            "signal": None,
        }

        if snap:
            tf_data["signal"] = {
                "total_score": snap.total_score,
                "category_a": snap.category_a,
                "category_b": snap.category_b,
                "category_c": snap.category_c,
                "signal": snap.signal,
                "signal_blocked": snap.signal_blocked_reason,
                "cascade_blocked": snap.cascade_blocked_reason,
                "regime": snap.regime,
                "atr_percentile": snap.atr_percentile,
                "obi_raw": snap.obi_raw,
                "obi_score": snap.obi_score,
                "depth_score": snap.depth_score,
                "cvd_zscore_raw": snap.cvd_zscore_raw,
                "cvd_score": snap.cvd_score,
                "vwap_value": snap.vwap_value,
                "vwap_score": snap.vwap_score,
                "poc_value": snap.poc_value,
                "poc_score": snap.poc_score,
                "rsi_value": snap.rsi_value,
                "rsi_score": snap.rsi_score,
                "macd_score": snap.macd_score,
                "ema_short": snap.ema_short,
                "ema_long": snap.ema_long,
                "ema_score": snap.ema_score,
                "ha_score": snap.ha_score,
                "cascade_allowed_direction": snap.cascade_allowed_direction,
                "cascade_context_confirmed": snap.cascade_context_confirmed,
                "cascade_timing_zone_active": snap.cascade_timing_zone_active,
                "cascade_timing_zone_direction": snap.cascade_timing_zone_direction,
            }

        # Walls
        bid_walls = [w for w in state.tracked_walls if w.side == "bid" and w.is_valid]
        ask_walls = [w for w in state.tracked_walls if w.side == "ask" and w.is_valid]
        tf_data["bid_wall"] = _wall_to_dict(max(bid_walls, key=lambda w: w.total_size)) if bid_walls else None
        tf_data["ask_wall"] = _wall_to_dict(max(ask_walls, key=lambda w: w.total_size)) if ask_walls else None

        data["timeframes"][tf_name] = tf_data

    return data


def _extract_config_data(state: "MarketState") -> dict:
    """Extract current runtime config as read-only dict."""
    rt = state.runtime
    if not rt:
        return {}

    # Determine mode label
    if not rt.live_mode:
        mode_label = "SHADOW"
    elif config.IS_TESTNET:
        mode_label = "TESTNET"
    else:
        mode_label = "LIVE"

    return {
        "deployment": {
            "mode": mode_label,
            "trading_paused": rt.trading_paused,
            "pause_reason": rt.pause_reason or "---",
            "environment": config.DEPLOYMENT_ENV,
            "network": "testnet" if config.IS_TESTNET else "mainnet",
            "coin": config.COIN,
            "capital": f"${state.capital:,.1f}",
            "web_dashboard": config.WEB_DASHBOARD,
        },
        "scoring": {
            "threshold_5m": rt.scoring_threshold_5m,
            "bias_threshold": rt.bias_threshold,
            "vwap_sensitivity": rt.vwap_sensitivity,
            "poc_sensitivity": rt.poc_sensitivity,
        },
        "risk": {
            "risk_per_trade": f"{rt.max_risk_per_trade:.0%}",
            "max_leverage": f"{rt.max_leverage:.1f}x",
            "daily_loss_limit": f"{rt.daily_loss_limit:.0%}",
        },
        "tp_strategy": {
            "tp1_size": f"{rt.tp1_size_pct:.0%}",
            "trailing_stop": rt.trailing_stop_enabled,
            "trail_distance_atr": rt.trailing_stop_distance_atr,
            "regime_adaptive": rt.tp_regime_adaptive,
        },
        "static": {
            "kline_source": config.KLINE_SOURCE,
            "wall_bin_size": f"${config.WALL_BIN_SIZE:.0f}",
            "atr_period": config.ATR_PERIOD,
            "emergency_sl": f"{config.EMERGENCY_SL_PCT}%",
            "max_sl_tp_ratio": config.MAX_SL_TP_RATIO,
            "simulated_capital": f"${config.SIMULATED_CAPITAL:,.0f}",
        },
    }


def _wall_to_dict(wall) -> dict:
    """Convert WallInfo to dict."""
    return {
        "bin_center": wall.bin_center,
        "total_size": wall.total_size,
        "age_s": int(wall.age_seconds),
        "ratio_pct": int(wall.size_ratio * 100),
    }


# ── HTML template ────────────────────────────────────────────────────────────

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BayesMarket Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<style>
  :root {
    --bg: #0d1117; --card: #161b22; --border: #30363d;
    --text: #c9d1d9; --dim: #6e7681; --green: #3fb950;
    --red: #f85149; --yellow: #d29922; --cyan: #58a6ff;
    --bright-green: #56d364; --bright-red: #ff7b72;
  }
  * { margin:0; padding:0; box-sizing:border-box; }
  body { background:var(--bg); color:var(--text); font-family:'Cascadia Code','Fira Code','Consolas',monospace; font-size:13px; padding:8px; }
  .grid { display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-bottom:8px; }
  .card { background:var(--card); border:1px solid var(--border); border-radius:6px; padding:10px 12px; }
  .card h3 { font-size:13px; margin-bottom:8px; padding-bottom:4px; border-bottom:1px solid var(--border); }
  .row { display:flex; justify-content:space-between; padding:2px 0; }
  .row .label { color:var(--dim); min-width:90px; }
  .status-bar { display:grid; grid-template-columns:2fr 2fr 1fr; gap:8px; }
  .score-bar { display:inline-block; font-size:12px; letter-spacing:-1px; }
  .pos { color:var(--green); } .neg { color:var(--red); } .neu { color:var(--dim); }
  .tag { display:inline-block; padding:1px 6px; border-radius:3px; font-size:11px; font-weight:bold; }
  .tag-shadow { background:#d2992233; color:var(--yellow); }
  .tag-live { background:#f8514933; color:var(--red); }
  .tag-testnet { background:#d2992233; color:#e3b341; }
  .tag-normal { background:#3fb95033; color:var(--green); }
  .tag-cooldown { background:#d2992233; color:var(--yellow); }
  .tag-fullstop { background:#f8514933; color:var(--red); }
  .tag-long { background:#3fb95033; color:var(--green); }
  .tag-short { background:#f8514933; color:var(--red); }
  .tag-neutral { background:#30363d; color:var(--dim); }
  .section-label { color:var(--cyan); font-weight:bold; font-size:11px; margin-top:6px; margin-bottom:2px; text-transform:uppercase; }
  .header { display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; padding:6px 12px; background:var(--card); border:1px solid var(--border); border-radius:6px; }
  .header h1 { font-size:15px; letter-spacing:2px; }
  .header .meta { font-size:11px; color:var(--dim); }
  .no-pos { color:var(--dim); font-style:italic; }

  /* Tabs */
  .tabs { display:flex; gap:2px; margin-bottom:8px; }
  .tab { padding:6px 16px; background:var(--card); border:1px solid var(--border); border-radius:6px 6px 0 0; cursor:pointer; color:var(--dim); font-weight:bold; font-size:12px; }
  .tab.active { background:var(--border); color:var(--cyan); border-bottom-color:var(--border); }
  .tab-content { display:none; }
  .tab-content.active { display:block; }

  /* Config table */
  .cfg-grid { display:grid; grid-template-columns:1fr 1fr; gap:8px; }
  .cfg-section { margin-bottom:4px; }
  .cfg-row { display:flex; justify-content:space-between; padding:3px 8px; border-bottom:1px solid #21262d; }
  .cfg-row:last-child { border-bottom:none; }
  .cfg-key { color:var(--dim); }
  .cfg-val { color:var(--text); font-weight:bold; }
  .cfg-val-bool-true { color:var(--green); font-weight:bold; }
  .cfg-val-bool-false { color:var(--red); font-weight:bold; }

  /* Trade table */
  .trades-table { width:100%; border-collapse:collapse; font-size:12px; }
  .trades-table th { text-align:left; padding:6px 8px; border-bottom:2px solid var(--border); color:var(--cyan); font-size:11px; text-transform:uppercase; }
  .trades-table td { padding:4px 8px; border-bottom:1px solid #21262d; }
  .trades-table tr:hover { background:#1c2333; }
  .summary-grid { display:grid; grid-template-columns:repeat(4,1fr); gap:8px; margin-bottom:12px; }
  .summary-card { text-align:center; }
  .summary-card .val { font-size:18px; font-weight:bold; }
  .summary-card .lbl { font-size:10px; color:var(--dim); text-transform:uppercase; }
  .btn { padding:4px 12px; background:var(--border); color:var(--text); border:1px solid #444; border-radius:4px; cursor:pointer; font-size:11px; font-family:inherit; }
  .btn:hover { background:#444; }

  @media (max-width:700px) { .grid { grid-template-columns:1fr; } .status-bar { grid-template-columns:1fr; } .cfg-grid { grid-template-columns:1fr; } .summary-grid { grid-template-columns:repeat(2,1fr); } }
</style>
</head>
<body>
<div class="header">
  <h1>BAYESMARKET</h1>
  <div class="meta">
    <span id="mode-tag" class="tag tag-shadow">SHADOW</span>
    <span id="price">$0.0</span>
    <span id="clock" style="margin-left:8px">--:--:--</span>
  </div>
</div>

<div class="tabs">
  <div class="tab active" onclick="switchTab('dashboard')">Dashboard</div>
  <div class="tab" onclick="switchTab('config')">Config</div>
  <div class="tab" onclick="switchTab('trades')">Trades</div>
</div>

<!-- Tab 1: Dashboard (live SSE) -->
<div id="tab-dashboard" class="tab-content active">
  <div class="grid" id="panels"></div>
  <div class="status-bar" id="status"></div>
</div>

<!-- Tab 2: Config (read-only) -->
<div id="tab-config" class="tab-content">
  <div class="cfg-grid" id="config-panels"></div>
  <p style="margin-top:8px;color:var(--dim);font-size:11px;">Read-only. Change via Telegram /set command.</p>
</div>

<!-- Tab 3: Trades -->
<div id="tab-trades" class="tab-content">
  <div id="trade-summary"></div>
  <div class="card" style="margin-bottom:8px;">
    <h3>Equity Curve</h3>
    <div style="position:relative;height:220px;"><canvas id="equity-chart"></canvas></div>
  </div>
  <div class="card">
    <h3>Recent Trades <button class="btn" onclick="loadTrades()" style="float:right">Refresh</button></h3>
    <div id="trade-table" style="overflow-x:auto;"></div>
  </div>
</div>

<script>
const $ = s => document.querySelector(s);
const fmt = (n,d=1) => n != null ? n.toFixed(d) : '---';
const fmtUsd = n => n != null ? '$'+n.toLocaleString('en',{minimumFractionDigits:1,maximumFractionDigits:1}) : '---';
const clr = v => v > 0 ? 'pos' : v < 0 ? 'neg' : 'neu';

// ── Tab switching ──────────────────────────────────────────────
function switchTab(name) {
  document.querySelectorAll('.tab').forEach((t,i) => t.classList.toggle('active', ['dashboard','config','trades'][i]===name));
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  $(`#tab-${name}`).classList.add('active');
  if (name==='config') loadConfig();
  if (name==='trades') loadTrades();
}

// ── Dashboard (SSE) ────────────────────────────────────────────
function scoreBar(score, w=14) {
  const r = Math.min(Math.abs(score)/13.5, 1);
  const f = Math.round(r*w);
  return (score>=0?'+':'') + score.toFixed(1) + ' ' + '\u2588'.repeat(f) + '\u2591'.repeat(w-f);
}

function wallStr(wall) {
  if (!wall) return '<span class="neu">none</span>';
  return `$${wall.bin_center.toLocaleString('en',{maximumFractionDigits:0})} (${wall.total_size.toFixed(2)}) ${wall.ratio_pct}% ${wall.age_s}s`;
}

function signalTag(sig, blocked) {
  if (blocked) return `<span class="tag tag-neutral">${sig} [${blocked}]</span>`;
  if (sig==='LONG') return '<span class="tag tag-long">\u25B2 LONG</span>';
  if (sig==='SHORT') return '<span class="tag tag-short">\u25BC SHORT</span>';
  return '<span class="tag tag-neutral">\u2500 NEUTRAL</span>';
}

const roleLabel = {bias:'BIAS',context:'CTX',timing:'ZONE',trigger:'TRIG'};

function cascadeInfo(tf, role, s) {
  if (role==='bias') return `Direction: <b class="${s.cascade_allowed_direction==='LONG'?'pos':s.cascade_allowed_direction==='SHORT'?'neg':'neu'}">${s.cascade_allowed_direction}</b>`;
  if (role==='context') return `Bias:${s.cascade_allowed_direction} ${s.cascade_context_confirmed?'<span class="pos">CONFIRMED</span>':'<span class="neg">NOT CONFIRMED</span>'}`;
  if (role==='timing') { const z=s.cascade_timing_zone_direction||'NONE'; return `Zone: <span class="${z==='LONG'?'pos':z==='SHORT'?'neg':'neu'}">${z}</span>`; }
  if (role==='trigger') { return s.cascade_timing_zone_active?'<span class="pos">READY</span>':(s.cascade_blocked||'<span class="neu">WAITING</span>'); }
  return '';
}

function buildPanel(tf, d) {
  const s = d.signal;
  const rl = roleLabel[d.role]||d.role;
  const fb = d.using_fallback ? ' <span class="neg">[FALLBACK]</span>' : '';
  if (!s) return `<div class="card"><h3>BTC ${tf} <span class="neu">[${rl}]</span>${fb}</h3><span class="neu">warming up...</span></div>`;

  const sc = s.total_score;
  const blocked = s.signal_blocked || s.cascade_blocked || null;

  return `<div class="card" style="border-color:${sc>=6?'var(--green)':sc<=-6?'var(--red)':'var(--border)'}">
    <h3>BTC ${tf} <span class="neu">[${rl}]</span>${fb}</h3>
    <div class="row"><span class="label">Score</span><span class="${clr(sc)}"><b>${scoreBar(sc)}</b></span></div>
    <div class="row"><span class="label">Signal</span>${signalTag(s.signal, blocked)}</div>
    <div class="row"><span class="label">Regime</span><span style="color:${s.regime==='ranging'?'var(--yellow)':'var(--cyan)'}">${s.regime.toUpperCase()}</span> ATR%:${fmt(s.atr_percentile,0)}</div>
    <div class="row"><span class="label">${rl}</span>${cascadeInfo(tf, d.role, s)}</div>
    <div class="row"><span class="label">A/B/C</span><b>${fmt(s.category_a)}</b> / <b>${fmt(s.category_b)}</b> / <b>${fmt(s.category_c)}</b></div>

    <div class="section-label">Order Book</div>
    <div class="row"><span class="label">OBI</span><span class="${clr(s.obi_raw)}">${(s.obi_raw*100).toFixed(1)}%</span> (${fmt(s.obi_score,2)})</div>
    <div class="row"><span class="label">Depth</span><span class="${clr(s.depth_score)}">${fmt(s.depth_score,2)}</span></div>
    <div class="row"><span class="label">Bid Wall</span>${wallStr(d.bid_wall)}</div>
    <div class="row"><span class="label">Ask Wall</span>${wallStr(d.ask_wall)}</div>

    <div class="section-label">Flow</div>
    <div class="row"><span class="label">CVD Z</span><span class="${clr(s.cvd_zscore_raw)}">${fmt(s.cvd_zscore_raw)}\u03C3</span> (${fmt(s.cvd_score,2)})</div>
    <div class="row"><span class="label">VWAP</span>${s.vwap_value ? fmtUsd(s.vwap_value)+' ('+fmt(s.vwap_score,2)+')' : '---'}</div>
    <div class="row"><span class="label">POC</span>${s.poc_value ? fmtUsd(s.poc_value)+' ('+fmt(s.poc_score,2)+')' : '---'}</div>

    <div class="section-label">Technical</div>
    <div class="row"><span class="label">RSI(14)</span>${s.rsi_value!=null?'<span class="'+(s.rsi_value<=35?'pos':s.rsi_value>=65?'neg':'neu')+'">'+fmt(s.rsi_value)+'</span> ('+fmt(s.rsi_score,2)+')':'---'}</div>
    <div class="row"><span class="label">MACD</span>${fmt(s.macd_score,2)}</div>
    <div class="row"><span class="label">EMA</span>${s.ema_short&&s.ema_long?'<span class="'+clr(s.ema_score)+'">5'+(s.ema_short>s.ema_long?'>':'<')+'20</span> ('+fmt(s.ema_score,2)+')':'---'}</div>
  </div>`;
}

function buildStatus(d) {
  let posHtml;
  const p = d.position;
  if (p) {
    const dur = Math.floor(p.duration_s/60)+'m '+Math.floor(p.duration_s%60)+'s';
    const sideTag = p.side==='long'?'<span class="tag tag-long">\u25B2 LONG</span>':'<span class="tag tag-short">\u25BC SHORT</span>';
    posHtml = `${sideTag} <b>${p.remaining_size.toFixed(4)} BTC</b><br>
      Entry <b>${fmtUsd(p.entry_price)}</b> \u2192 Mid <b>${fmtUsd(d.mid_price)}</b><br>
      SL <b>${fmtUsd(p.sl_price)}</b> [${p.sl_basis}${p.trailing_active?' \u27F0trail':''}]
      TP1 <b>${fmtUsd(p.tp1_price)}</b> ${p.tp1_hit?'\u2713':'\u25CB'}
      TP2 <b>${fmtUsd(p.tp2_price)}</b> \u25CB<br>
      PnL <b class="${clr(p.unrealized)}">${p.unrealized>=0?'+':''}${p.unrealized.toFixed(2)} (${p.pnl_pct>=0?'+':''}${p.pnl_pct.toFixed(2)}%)</b>
      <span class="neu">${dur}</span>`;
  } else {
    posHtml = '<span class="no-pos">\u2500\u2500 No open position \u2500\u2500</span>';
  }

  const r = d.risk;
  const riskTag = r.label==='NORMAL'?'tag-normal':r.label==='COOLDOWN'?'tag-cooldown':'tag-fullstop';
  const fundClr = d.funding_tier==='safe'?'pos':d.funding_tier==='caution'?'':'neg';

  const riskHtml = `Risk: <span class="tag ${riskTag}">${r.label}</span><br>
    Daily PnL: <b class="${clr(r.daily_pnl)}">${r.daily_pnl>=0?'+':''}${r.daily_pnl.toFixed(2)}</b>
    Capital: <b>${fmtUsd(d.capital)}</b><br>
    Funding: <span class="${fundClr}">${(d.funding_rate*100).toFixed(4)}%/h</span> (${d.funding_tier})<br>
    Trades: ${r.trades_today} W:<span class="pos">${r.consecutive_wins}</span> L:<span class="neg">${r.consecutive_losses}</span>`;

  const modeTag = d.mode==='LIVE'?'tag-live':d.mode==='TESTNET'?'tag-testnet':'tag-shadow';
  const modeHtml = `<span class="tag ${modeTag}">${d.mode}</span>${d.paused?' <span class="tag tag-cooldown">PAUSED</span>':''}<br>
    Klines: <span class="${d.kline_source==='synthetic'?'pos':''}">${d.kline_source}</span><br>
    Cascade: <b>${d.cascade_direction}</b> CTX:${d.cascade_context?'\u2713':'\u2717'}`;

  return `<div class="card">${posHtml}</div><div class="card">${riskHtml}</div><div class="card">${modeHtml}</div>`;
}

function renderDashboard(d) {
  $('#price').textContent = fmtUsd(d.mid_price);
  $('#clock').textContent = d.timestamp;
  const mt = d.mode==='LIVE'?'tag-live':d.mode==='TESTNET'?'tag-testnet':'tag-shadow';
  $('#mode-tag').className = 'tag '+mt;
  $('#mode-tag').textContent = d.mode;

  let panels = '';
  for (const tf of ['5m','15m','1h','4h']) {
    panels += buildPanel(tf, d.timeframes[tf]);
  }
  $('#panels').innerHTML = panels;
  $('#status').innerHTML = buildStatus(d);
}

// SSE connection with auto-reconnect
function connectSSE() {
  const es = new EventSource('/stream');
  es.onmessage = e => { try { renderDashboard(JSON.parse(e.data)); } catch(err) { console.error(err); } };
  es.onerror = () => { es.close(); setTimeout(connectSSE, 3000); };
}
connectSSE();

// ── Config tab ─────────────────────────────────────────────────
function loadConfig() {
  fetch('/api/config').then(r=>r.json()).then(renderConfig).catch(e=>console.error(e));
}

function renderConfig(cfg) {
  const el = $('#config-panels');
  let html = '';
  const sectionNames = {deployment:'Deployment',scoring:'Scoring',risk:'Risk',tp_strategy:'TP Strategy',static:'Static Config'};
  for (const [section, label] of Object.entries(sectionNames)) {
    const entries = cfg[section];
    if (!entries) continue;
    html += `<div class="card cfg-section"><h3>${label}</h3>`;
    for (const [k, v] of Object.entries(entries)) {
      let valClass = 'cfg-val';
      let display = v;
      if (v === true) { valClass = 'cfg-val-bool-true'; display = 'ON'; }
      else if (v === false) { valClass = 'cfg-val-bool-false'; display = 'OFF'; }
      // Special styling for mode field
      if (k === 'mode') {
        if (v === 'LIVE') valClass = 'neg';
        else if (v === 'TESTNET') valClass = 'cfg-val';
        else valClass = 'cfg-val-bool-true';
        display = v;
      }
      html += `<div class="cfg-row"><span class="cfg-key">${k.replace(/_/g,' ')}</span><span class="${valClass}">${display}</span></div>`;
    }
    html += '</div>';
  }
  el.innerHTML = html;
}

// ── Trades tab ─────────────────────────────────────────────────
let equityChart = null;

function loadTrades() {
  Promise.all([
    fetch('/api/trades').then(r=>r.json()),
    fetch('/api/trades/summary').then(r=>r.json()),
    fetch('/api/trades/equity').then(r=>r.json()),
  ]).then(([trades, summary, equity]) => {
    renderTradeSummary(summary);
    renderEquityCurve(equity);
    renderTradeTable(trades);
  }).catch(e => console.error(e));
}

function renderTradeSummary(s) {
  const wrClr = s.win_rate >= 50 ? 'pos' : s.win_rate > 0 ? 'neg' : 'neu';
  const pnlClr = clr(s.total_pnl);
  $('#trade-summary').innerHTML = `
    <div class="summary-grid">
      <div class="card summary-card"><div class="val">${s.total}</div><div class="lbl">Total Trades</div></div>
      <div class="card summary-card"><div class="val ${wrClr}">${s.win_rate.toFixed(1)}%</div><div class="lbl">Win Rate (${s.wins}W / ${s.losses}L)</div></div>
      <div class="card summary-card"><div class="val ${pnlClr}">${s.total_pnl>=0?'+':''}$${s.total_pnl.toFixed(2)}</div><div class="lbl">Total PnL</div></div>
      <div class="card summary-card"><div class="val">${s.avg_pnl>=0?'+':''}$${s.avg_pnl.toFixed(2)}</div><div class="lbl">Avg PnL</div></div>
    </div>`;
}

function renderEquityCurve(points) {
  const ctx = document.getElementById('equity-chart');
  if (!ctx) return;
  if (points.length <= 1) {
    ctx.parentElement.innerHTML = '<p class="no-pos" style="padding:20px;text-align:center;">No trades yet — equity curve will appear after first trade.</p>';
    return;
  }

  const labels = points.map((p, i) => i === 0 ? 'Start' : tsToStr(p.time));
  const data = points.map(p => p.equity);
  const colors = points.map(p => (p.pnl||0) >= 0 ? '#3fb950' : '#f85149');
  const startEquity = data[0];

  if (equityChart) equityChart.destroy();
  equityChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: labels,
      datasets: [{
        label: 'Equity ($)',
        data: data,
        borderColor: '#58a6ff',
        backgroundColor: 'rgba(88,166,255,0.1)',
        borderWidth: 2,
        fill: true,
        tension: 0.3,
        pointRadius: data.length > 50 ? 0 : 3,
        pointHoverRadius: 5,
        pointBackgroundColor: colors,
        pointBorderColor: colors,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#161b22',
          borderColor: '#30363d',
          borderWidth: 1,
          titleColor: '#c9d1d9',
          bodyColor: '#c9d1d9',
          callbacks: {
            afterBody: function(ctx) {
              const i = ctx[0].dataIndex;
              const p = points[i];
              if (i === 0) return '';
              const pnlStr = (p.pnl >= 0 ? '+' : '') + p.pnl.toFixed(2);
              return `PnL: $${pnlStr}  |  ${(p.side||'').toUpperCase()}  |  ${p.reason||''}`;
            }
          }
        }
      },
      scales: {
        x: {
          display: true,
          ticks: { color: '#6e7681', maxTicksLimit: 8, maxRotation: 0, font: { size: 10 } },
          grid: { color: '#21262d' },
        },
        y: {
          display: true,
          ticks: { color: '#6e7681', callback: v => '$' + v.toFixed(0), font: { size: 10 } },
          grid: { color: '#21262d' },
        }
      }
    }
  });
}

function tsToStr(ts) {
  if (!ts) return '---';
  const d = new Date(ts * 1000);
  return d.toLocaleDateString('en',{month:'short',day:'numeric'}) + ' ' + d.toLocaleTimeString('en',{hour:'2-digit',minute:'2-digit',hour12:false});
}

function renderTradeTable(trades) {
  if (!trades.length) {
    $('#trade-table').innerHTML = '<p class="no-pos" style="padding:12px;">No trades yet. Signals are computing...</p>';
    return;
  }
  let html = `<table class="trades-table">
    <tr><th>Time</th><th>Side</th><th>Entry</th><th>Exit</th><th>Size</th><th>PnL</th><th>PnL%</th><th>Reason</th><th>Regime</th><th>Category</th></tr>`;
  for (const t of trades) {
    const sideClr = t.side==='long'?'pos':'neg';
    const pnlClr = clr(t.pnl);
    html += `<tr>
      <td>${tsToStr(t.exit_time)}</td>
      <td class="${sideClr}">${(t.side||'').toUpperCase()}</td>
      <td>${fmtUsd(t.entry_price)}</td>
      <td>${fmtUsd(t.exit_price)}</td>
      <td>${t.size ? t.size.toFixed(4) : '---'}</td>
      <td class="${pnlClr}"><b>${t.pnl>=0?'+':''}${t.pnl.toFixed(2)}</b></td>
      <td class="${pnlClr}">${t.pnl_pct>=0?'+':''}${t.pnl_pct.toFixed(2)}%</td>
      <td>${t.exit_reason||'---'}</td>
      <td>${t.regime||'---'}</td>
      <td>${t.loss_category||'---'}</td>
    </tr>`;
  }
  html += '</table>';
  $('#trade-table').innerHTML = html;
}
</script>
</body>
</html>"""


# ── Web server ───────────────────────────────────────────────────────────────

_state_ref: "MarketState | None" = None
_storage_ref: "Storage | None" = None


async def _handle_index(request: web.Request) -> web.Response:
    """Serve the dashboard HTML page."""
    return web.Response(text=HTML_PAGE, content_type="text/html")


async def _handle_stream(request: web.Request) -> web.StreamResponse:
    """SSE endpoint — pushes dashboard data every 3 seconds."""
    response = web.StreamResponse()
    response.headers["Content-Type"] = "text/event-stream"
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Connection"] = "keep-alive"
    response.headers["Access-Control-Allow-Origin"] = "*"
    await response.prepare(request)

    try:
        while True:
            if _state_ref is not None:
                data = _extract_dashboard_data(_state_ref)
                payload = f"data: {json.dumps(data)}\n\n"
                await response.write(payload.encode("utf-8"))
            await asyncio.sleep(3.0)
    except (asyncio.CancelledError, ConnectionResetError):
        pass
    return response


async def _handle_api_dashboard(request: web.Request) -> web.Response:
    """JSON API endpoint — single dashboard snapshot."""
    if _state_ref is None:
        return web.json_response({"error": "not ready"}, status=503)
    data = _extract_dashboard_data(_state_ref)
    return web.json_response(data)


async def _handle_api_config(request: web.Request) -> web.Response:
    """JSON API endpoint — current runtime config (read-only)."""
    if _state_ref is None:
        return web.json_response({"error": "not ready"}, status=503)
    data = _extract_config_data(_state_ref)
    return web.json_response(data)


async def _handle_api_trades(request: web.Request) -> web.Response:
    """JSON API endpoint — recent trades from SQLite."""
    if _storage_ref is None:
        return web.json_response([], status=200)
    trades = _storage_ref.query_recent_trades(limit=30)
    return web.json_response(trades)


async def _handle_api_trades_summary(request: web.Request) -> web.Response:
    """JSON API endpoint — aggregate trade stats."""
    if _storage_ref is None:
        return web.json_response({"total": 0, "wins": 0, "losses": 0,
                                   "total_pnl": 0.0, "avg_pnl": 0.0,
                                   "best_trade": 0.0, "worst_trade": 0.0,
                                   "win_rate": 0.0})
    summary = _storage_ref.query_trade_summary()
    return web.json_response(summary)


async def _handle_api_equity(request: web.Request) -> web.Response:
    """JSON API endpoint — equity curve data points."""
    capital = config.SIMULATED_CAPITAL
    if _state_ref is not None:
        capital = _state_ref.capital
    if _storage_ref is None:
        return web.json_response([{"time": 0, "equity": capital, "label": "Start"}])
    points = _storage_ref.query_equity_curve(starting_capital=config.SIMULATED_CAPITAL)
    return web.json_response(points)


async def web_dashboard_loop(state: "MarketState", storage: "Storage | None" = None) -> None:
    """Start aiohttp web server for browser-based dashboard."""
    global _state_ref, _storage_ref
    _state_ref = state
    _storage_ref = storage

    app = web.Application()
    app.router.add_get("/", _handle_index)
    app.router.add_get("/stream", _handle_stream)
    app.router.add_get("/api/dashboard", _handle_api_dashboard)
    app.router.add_get("/api/config", _handle_api_config)
    app.router.add_get("/api/trades", _handle_api_trades)
    app.router.add_get("/api/trades/summary", _handle_api_trades_summary)
    app.router.add_get("/api/trades/equity", _handle_api_equity)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", WEB_PORT)
    await site.start()

    logger.info("web_dashboard_started", port=WEB_PORT, url=f"http://0.0.0.0:{WEB_PORT}")

    # Keep running forever
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        await runner.cleanup()
