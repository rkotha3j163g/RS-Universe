#!/usr/bin/env python3
"""
Mag 7 Stock Dashboard — Relative Strength Edition
Powered by Alpaca Market Data API
"""
from flask import Flask, jsonify
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests, os, json
from datetime import datetime, timedelta, timezone

app = Flask(__name__)

# ── Alpaca credentials ───────────────────────────────────────────────────────
ALPACA_API_KEY    = os.environ["ALPACA_API_KEY"]
ALPACA_SECRET_KEY = os.environ["ALPACA_SECRET_KEY"]

DATA_BASE_URL = "https://data.alpaca.markets"
HEADERS = {
    "APCA-API-KEY-ID":     ALPACA_API_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
}

MAG7 = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA"]

NAMES = {
    "AAPL":  "Apple",   "MSFT":  "Microsoft", "GOOGL": "Alphabet",
    "AMZN":  "Amazon",  "NVDA":  "NVIDIA",    "META":  "Meta",
    "TSLA":  "Tesla",
}
COLORS = {
    "AAPL":  "#A2AAAD", "MSFT":  "#00A4EF", "GOOGL": "#4285F4",
    "AMZN":  "#FF9900", "NVDA":  "#76B900", "META":  "#0866FF",
    "TSLA":  "#CC0000",
}


# ── Data helpers ─────────────────────────────────────────────────────────────

def get_snapshots():
    resp = requests.get(
        f"{DATA_BASE_URL}/v2/stocks/snapshots",
        headers=HEADERS,
        params={"symbols": ",".join(MAG7), "feed": "iex"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def get_bars(symbol, days=30):
    """Fetch the last N daily bars for a symbol."""
    end   = datetime.now(timezone.utc)
    start = end - timedelta(days=int(days * 2.5))   # calendar-day buffer
    resp  = requests.get(
        f"{DATA_BASE_URL}/v2/stocks/{symbol}/bars",
        headers=HEADERS,
        params={
            "timeframe": "1Day",
            "start":     start.strftime("%Y-%m-%d"),
            "end":       end.strftime("%Y-%m-%d"),
            "limit":     days,
            "feed":      "iex",
            "sort":      "asc",
        },
        timeout=15,
    )
    resp.raise_for_status()
    bars = resp.json().get("bars", [])
    return bars[-days:] if len(bars) > days else bars


def compute_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains  = [max(d, 0) for d in deltas[-period:]]
    losses = [abs(min(d, 0)) for d in deltas[-period:]]
    avg_g  = sum(gains) / period
    avg_l  = sum(losses) / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return round(100 - 100 / (1 + rs), 1)


def signals_for(symbol):
    """Compute all technical signals for one symbol."""
    bars   = get_bars(symbol, days=215)
    closes = [b["c"] for b in bars]
    highs  = [b["h"] for b in bars]
    lows   = [b["l"] for b in bars]
    if not closes:
        return {}

    price    = closes[-1]
    ma200    = round(sum(closes[-200:]) / 200, 2) if len(closes) >= 200 else None
    ma50     = round(sum(closes[-50:])  / 50,  2) if len(closes) >= 50  else None
    rsi      = compute_rsi(closes[-30:])                  # last 30 closes for RSI-14
    ret_30d  = round((price / closes[-30] - 1) * 100, 2)  if len(closes) >= 30 else None
    ret_5d   = round((price / closes[-5]  - 1) * 100, 2)  if len(closes) >= 5  else None
    w52_high = round(max(highs[-252:]), 2) if len(highs) >= 20 else None
    w52_low  = round(min(lows[-252:]),  2) if len(lows)  >= 20 else None

    above_200    = bool(price > ma200)  if ma200 else None
    pct_vs_200   = round((price - ma200) / ma200 * 100, 2) if ma200 else None
    pct_frm_high = round((price - w52_high) / w52_high * 100, 2) if w52_high else None

    return {
        "ma200": ma200, "ma50": ma50,
        "rsi": rsi,
        "ret_30d": ret_30d, "ret_5d": ret_5d,
        "above_200": above_200, "pct_vs_200": pct_vs_200,
        "w52_high": w52_high, "w52_low": w52_low,
        "pct_from_high": pct_frm_high,
    }


# ── API routes ───────────────────────────────────────────────────────────────

@app.route("/api/overview")
def api_overview():
    data   = get_snapshots()
    result = []
    for sym in MAG7:
        snap  = data.get(sym, {})
        daily = snap.get("dailyBar", {})
        prev  = snap.get("prevDailyBar", {})
        quote = snap.get("latestQuote", {})

        close  = daily.get("c") or quote.get("ap") or 0
        prev_c = prev.get("c") or close
        chg    = close - prev_c
        pct    = (chg / prev_c * 100) if prev_c else 0

        result.append({
            "symbol": sym, "name": NAMES[sym], "color": COLORS[sym],
            "price":  round(close, 2),
            "change": round(chg, 2),
            "pct":    round(pct, 2),
            "open":   round(daily.get("o") or 0, 2),
            "high":   round(daily.get("h") or 0, 2),
            "low":    round(daily.get("l") or 0, 2),
            "volume": daily.get("v") or 0,
            "vwap":   round(daily.get("vw") or 0, 2),
        })
    return jsonify(result)


@app.route("/api/signals")
def api_signals():
    """Fetch bars for all 7 in parallel, compute RS + technicals."""
    raw = {}
    with ThreadPoolExecutor(max_workers=7) as pool:
        futures = {pool.submit(signals_for, sym): sym for sym in MAG7}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                raw[sym] = fut.result()
            except Exception as e:
                raw[sym] = {"error": str(e)}

    # RS rank by 30-day return (1 = strongest)
    ranked = sorted(
        [(sym, raw[sym].get("ret_30d") or -999) for sym in MAG7],
        key=lambda x: x[1], reverse=True,
    )
    for rank, (sym, _) in enumerate(ranked, 1):
        raw[sym]["rs_rank"] = rank

    # Simple actionable signal
    for sym in MAG7:
        d = raw[sym]
        rsi      = d.get("rsi")
        above200 = d.get("above_200")
        rank     = d.get("rs_rank", 4)

        if above200 and rsi and rsi < 70 and rank <= 2:
            d["signal"] = "STRONG"
        elif above200 and rsi and rsi < 70:
            d["signal"] = "BULLISH"
        elif rsi and rsi > 70:
            d["signal"] = "OVERBOUGHT"
        elif rsi and rsi < 30:
            d["signal"] = "OVERSOLD"
        elif not above200 and rank >= 6:
            d["signal"] = "WEAK"
        else:
            d["signal"] = "NEUTRAL"

    return jsonify(raw)


@app.route("/api/chart/<symbol>")
def api_chart(symbol):
    if symbol not in MAG7:
        return jsonify({"error": "invalid symbol"}), 400
    bars       = get_bars(symbol, days=230)
    disp       = bars[-30:] if len(bars) >= 30 else bars
    all_closes = [b["c"] for b in bars]
    n          = len(bars)
    disp_start = n - len(disp)

    ma200, ma50 = [], []
    for i in range(disp_start, n):
        ma200.append(round(sum(all_closes[max(0,i-199):i+1]) / min(i+1, 200), 2) if i >= 199 else None)
        ma50.append( round(sum(all_closes[max(0,i-49):i+1])  / min(i+1, 50),  2) if i >= 49  else None)

    closes = [round(b["c"], 2) for b in disp]
    base   = closes[0] if closes else 1
    return jsonify({
        "dates":  [b["t"][:10] for b in disp],
        "closes": closes,
        "normed": [round((c - base) / base * 100, 2) for c in closes],
        "ma200":  ma200,
        "ma50":   ma50,
    })


@app.route("/api/compare")
def api_compare():
    out = {}
    with ThreadPoolExecutor(max_workers=7) as pool:
        def _fetch(sym):
            bars   = get_bars(sym, days=30)
            closes = [round(b["c"], 2) for b in bars]
            base   = closes[0] if closes else 1
            return sym, {
                "dates":  [b["t"][:10] for b in bars],
                "normed": [round((c - base) / base * 100, 2) for c in closes],
                "color":  COLORS[sym],
            }
        for sym, val in pool.map(_fetch, MAG7):
            out[sym] = val
    return jsonify(out)


# ── HTML ─────────────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Mag 7 · Relative Strength Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg:      #0d1117;
  --surface: #161b22;
  --border:  #30363d;
  --text:    #e6edf3;
  --muted:   #8b949e;
  --green:   #3fb950;
  --red:     #f85149;
  --amber:   #f0a500;
  --blue:    #58a6ff;
}
body { background: var(--bg); color: var(--text);
       font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }

/* ── Header ── */
header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 16px 32px; border-bottom: 1px solid var(--border);
  background: var(--surface); position: sticky; top: 0; z-index: 10;
}
header h1 { font-size: 1.2rem; font-weight: 700; }
header h1 span { color: var(--amber); }
header h1 small { font-size: .72rem; color: var(--muted); margin-left: 8px; font-weight: 400; }
.hdr-right { display: flex; align-items: center; gap: 14px; }
#last-updated { font-size: .76rem; color: var(--muted); }
.btn { background: #238636; border: none; color: #fff; padding: 6px 14px;
       border-radius: 6px; cursor: pointer; font-size: .82rem; font-weight: 600;
       transition: background .2s; }
.btn:hover { background: #2ea043; }
#sort-btn { background: #1f3a5f; }
#sort-btn:hover { background: #2255a0; }

main { max-width: 1500px; margin: 0 auto; padding: 24px 20px; }

/* ── RS Bar ── */
#rs-bar {
  display: flex; gap: 8px; margin-bottom: 24px;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; padding: 12px 16px; align-items: center; flex-wrap: wrap;
}
#rs-bar .rs-label { font-size: .72rem; color: var(--muted); margin-right: 6px; white-space: nowrap; }
.rs-badge {
  display: flex; align-items: center; gap: 5px;
  background: #0d1117; border: 1px solid var(--border);
  border-radius: 20px; padding: 4px 10px; font-size: .78rem; font-weight: 600;
  cursor: pointer; transition: border-color .15s;
}
.rs-badge:hover { border-color: var(--amber); }
.rs-badge .rank { color: var(--amber); font-size: .68rem; }
.rs-badge .ret  { font-size: .72rem; }

/* ── Cards ── */
.cards {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 12px; margin-bottom: 28px;
}
@media (max-width: 1100px) { .cards { grid-template-columns: repeat(4, 1fr); } }
@media (max-width: 700px)  { .cards { grid-template-columns: repeat(2, 1fr); } }

.card {
  background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
  padding: 14px 13px; cursor: pointer; transition: border-color .2s, transform .15s;
  position: relative; overflow: hidden;
}
.card:hover, .card.active { border-color: var(--accent); transform: translateY(-2px); }
.card::before { content:''; position:absolute; top:0; left:0; right:0; height:3px; background: var(--accent); }

.card-top { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 6px; }
.ticker  { font-size: .72rem; font-weight: 700; color: var(--muted); letter-spacing: 1px; }
.sig-badge {
  font-size: .58rem; font-weight: 700; padding: 2px 6px; border-radius: 4px;
  text-transform: uppercase; letter-spacing: .5px;
}
.sig-STRONG     { background: #0d3320; color: #3fb950; border: 1px solid #3fb950; }
.sig-BULLISH    { background: #0d2a1a; color: #56d975; border: 1px solid #3fb950; }
.sig-OVERBOUGHT { background: #3a2000; color: #f0a500; border: 1px solid #f0a500; }
.sig-OVERSOLD   { background: #300010; color: #f85149; border: 1px solid #f85149; }
.sig-WEAK       { background: #300; color: #f85149; border: 1px solid #f85149; }
.sig-NEUTRAL    { background: #1c2128; color: #8b949e; border: 1px solid #30363d; }

.company { font-size: .75rem; color: var(--muted); margin-bottom: 8px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.price   { font-size: 1.3rem; font-weight: 700; }
.chg     { font-size: .78rem; font-weight: 600; margin-top: 3px; }
.chg.up  { color: var(--green); }
.chg.dn  { color: var(--red); }

.divider { border: none; border-top: 1px solid var(--border); margin: 10px 0; }

.stats { display: grid; grid-template-columns: 1fr 1fr; gap: 3px 8px; }
.sl    { font-size: .62rem; color: var(--muted); }
.sv    { font-size: .72rem; font-weight: 600; }
.sv.up { color: var(--green); }
.sv.dn { color: var(--red); }
.sv.ob { color: var(--amber); }  /* overbought RSI */
.sv.os { color: var(--blue); }   /* oversold RSI */

.ma-row { display: flex; justify-content: space-between; align-items: center; margin-top: 8px; gap: 4px; }
.ma-pill {
  font-size: .6rem; font-weight: 700; padding: 2px 6px; border-radius: 4px; flex: 1; text-align: center;
}
.ma-pill.above { background: #0d3320; color: #3fb950; }
.ma-pill.below { background: #300;    color: #f85149; }
.ma-pill.na    { background: #1c2128; color: #8b949e; }

.rs-rank-row { text-align: center; margin-top: 6px; font-size: .65rem; color: var(--muted); }
.rs-rank-row span { color: var(--amber); font-weight: 700; font-size: .8rem; }

/* ── Charts ── */
.charts-row { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
@media (max-width: 800px) { .charts-row { grid-template-columns: 1fr; } }
.chart-box {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; padding: 18px;
}
.chart-box h3 { font-size: .85rem; color: var(--muted); margin-bottom: 12px; font-weight: 600; }
.chart-box h3 span { color: var(--text); }
canvas { width: 100% !important; }

.spinner   { text-align:center; padding:50px; color:var(--muted); }
.error-msg { color:var(--red); background:#300; border:1px solid var(--red);
             border-radius:8px; padding:12px 16px; margin:16px 0; font-size:.88rem; }
</style>
</head>
<body>

<header>
  <h1>Mag <span>7</span> Dashboard <small>Relative Strength Edition</small></h1>
  <div class="hdr-right">
    <span id="last-updated">Loading…</span>
    <button class="btn" id="sort-btn" onclick="toggleSort()">⇅ Sort by RS</button>
    <button class="btn" onclick="loadAll()">↻ Refresh</button>
  </div>
</header>

<main>
  <div id="error-box"></div>

  <!-- RS leaderboard bar -->
  <div id="rs-bar"><span class="rs-label">30-Day RS Rank →</span><span style="color:var(--muted);font-size:.8rem">Loading signals…</span></div>

  <!-- Stock cards -->
  <div id="cards-container" class="cards">
    <div class="spinner" style="grid-column:1/-1">Fetching live data…</div>
  </div>

  <!-- Charts -->
  <div class="charts-row">
    <div class="chart-box">
      <h3>Price + MA Lines — <span id="chart-title">click a card</span></h3>
      <canvas id="priceChart" height="220"></canvas>
    </div>
    <div class="chart-box">
      <h3>30-Day Return Comparison <span style="font-size:.72rem;color:var(--muted)">(% from equal start)</span></h3>
      <canvas id="compareChart" height="220"></canvas>
    </div>
  </div>
</main>

<script>
const COLORS  = __COLORS__;
let priceChart = null, compareChart = null;
let activeSymbol = null;
let overviewData = [];
let signalsData  = {};
let sortByRS     = false;

const fmt    = n => n == null ? '—' : n.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
const fmtVol = v => v >= 1e9 ? (v/1e9).toFixed(2)+'B' : v >= 1e6 ? (v/1e6).toFixed(2)+'M' : v.toLocaleString();
const fmtPct = v => v == null ? '—' : (v >= 0 ? '+' : '') + v.toFixed(2) + '%';

function showError(msg) {
  document.getElementById('error-box').innerHTML = `<div class="error-msg">⚠ ${msg}</div>`;
}

/* ── RS leaderboard bar ── */
function renderRSBar(signals) {
  const el = document.getElementById('rs-bar');
  el.innerHTML = '<span class="rs-label">30-Day RS Rank →</span>';
  const ranked = Object.entries(signals)
    .filter(([,d]) => d.rs_rank != null)
    .sort((a,b) => a[1].rs_rank - b[1].rs_rank);
  ranked.forEach(([sym, d]) => {
    const up  = (d.ret_30d || 0) >= 0;
    const btn = document.createElement('div');
    btn.className = 'rs-badge';
    btn.style.borderColor = COLORS[sym] + '80';
    btn.innerHTML = `
      <span class="rank">#${d.rs_rank}</span>
      <span style="color:${COLORS[sym]};font-weight:700">${sym}</span>
      <span class="ret" style="color:${up?'#3fb950':'#f85149'}">${fmtPct(d.ret_30d)}</span>`;
    btn.onclick = () => loadPriceChart(sym);
    el.appendChild(btn);
  });
}

/* ── Cards ── */
function renderCards(stocks, signals) {
  const el     = document.getElementById('cards-container');
  const sorted = sortByRS
    ? [...stocks].sort((a, b) => (signals[a.symbol]?.rs_rank || 99) - (signals[b.symbol]?.rs_rank || 99))
    : stocks;

  el.innerHTML = '';
  sorted.forEach(s => {
    const sig = signals[s.symbol] || {};
    const up  = s.pct >= 0;
    const div = document.createElement('div');
    div.className = 'card' + (activeSymbol === s.symbol ? ' active' : '');
    div.style.setProperty('--accent', s.color);

    // 200D MA pill
    const ma200pill = sig.ma200 == null ? `<span class="ma-pill na">200D N/A</span>`
      : sig.above_200
        ? `<span class="ma-pill above">▲ 200D +${Math.abs(sig.pct_vs_200||0).toFixed(1)}%</span>`
        : `<span class="ma-pill below">▼ 200D ${(sig.pct_vs_200||0).toFixed(1)}%</span>`;

    // 50D MA pill
    const ma50val  = sig.ma50  ? `$${fmt(sig.ma50)}`  : '—';
    const ma50cls  = sig.ma50  ? (s.price > sig.ma50 ? 'above' : 'below') : 'na';
    const ma50pill = `<span class="ma-pill ${ma50cls}">50D $${sig.ma50 ? fmt(sig.ma50) : '—'}</span>`;

    // RSI colour
    const rsiCls = sig.rsi == null ? '' : sig.rsi > 70 ? 'ob' : sig.rsi < 30 ? 'os' : 'up';

    div.innerHTML = `
      <div class="card-top">
        <span class="ticker">${s.symbol}</span>
        <span class="sig-badge sig-${sig.signal || 'NEUTRAL'}">${sig.signal || '…'}</span>
      </div>
      <div class="company">${s.name}</div>
      <div class="price">$${fmt(s.price)}</div>
      <div class="chg ${up?'up':'dn'}">${up?'▲':'▼'} ${fmt(Math.abs(s.change))} (${fmt(Math.abs(s.pct))}%)</div>
      <hr class="divider">
      <div class="stats">
        <div class="sl">30D Ret</div><div class="sv ${(sig.ret_30d||0)>=0?'up':'dn'}">${fmtPct(sig.ret_30d)}</div>
        <div class="sl">5D Ret</div> <div class="sv ${(sig.ret_5d||0)>=0?'up':'dn'}">${fmtPct(sig.ret_5d)}</div>
        <div class="sl">RSI 14</div><div class="sv ${rsiCls}">${sig.rsi ?? '—'}</div>
        <div class="sl">Vol</div>    <div class="sv">${fmtVol(s.volume)}</div>
        <div class="sl">VWAP</div>   <div class="sv">$${fmt(s.vwap)}</div>
        <div class="sl">52W Hi</div> <div class="sv">${sig.pct_from_high != null ? fmtPct(sig.pct_from_high) : '—'}</div>
      </div>
      <div class="ma-row">${ma200pill}${ma50pill}</div>
      <div class="rs-rank-row">RS Rank <span>${sig.rs_rank ? '#'+sig.rs_rank+' of 7' : '…'}</span></div>`;

    div.addEventListener('click', () => loadPriceChart(s.symbol));
    el.appendChild(div);
  });
}

/* ── Price chart ── */
async function loadPriceChart(symbol) {
  activeSymbol = symbol;
  document.querySelectorAll('.card').forEach(c => {
    c.classList.toggle('active', c.querySelector('.ticker')?.textContent === symbol);
  });
  const s   = overviewData.find(x => x.symbol === symbol);
  const col = s ? s.color : COLORS[symbol];
  document.getElementById('chart-title').textContent = (NAMES[symbol] || symbol) + ' (' + symbol + ')';

  const res  = await fetch('/api/chart/' + symbol);
  const data = await res.json();
  if (priceChart) priceChart.destroy();
  const ctx  = document.getElementById('priceChart').getContext('2d');
  const grad = ctx.createLinearGradient(0, 0, 0, 260);
  grad.addColorStop(0, col + '55'); grad.addColorStop(1, col + '00');

  priceChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: data.dates,
      datasets: [
        { label: 'Price',  data: data.closes, borderColor: col, backgroundColor: grad,
          borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, fill: true, tension: 0.3 },
        { label: '200D MA', data: data.ma200, borderColor: '#f0a500', backgroundColor: 'transparent',
          borderWidth: 1.5, borderDash: [5,4], pointRadius: 0, fill: false, tension: 0.3, spanGaps: false },
        { label: '50D MA',  data: data.ma50,  borderColor: '#58a6ff', backgroundColor: 'transparent',
          borderWidth: 1.5, borderDash: [3,3], pointRadius: 0, fill: false, tension: 0.3, spanGaps: false },
      ]
    },
    options: {
      responsive: true,
      plugins: {
        legend: { display: true, labels: { color: '#8b949e', boxWidth: 18, padding: 10, font: {size:11},
          filter: i => i.datasetIndex > 0 } },
        tooltip: { callbacks: { label: c => c.parsed.y == null ? null : ` ${c.dataset.label}: $${fmt(c.parsed.y)}` } }
      },
      scales: {
        x: { grid:{color:'#30363d'}, ticks:{color:'#8b949e', maxTicksLimit:6} },
        y: { grid:{color:'#30363d'}, ticks:{color:'#8b949e', callback: v => '$'+v} }
      }
    }
  });
}

/* ── Compare chart ── */
async function loadCompareChart() {
  const res  = await fetch('/api/compare');
  const data = await res.json();
  const syms = Object.keys(data);
  if (compareChart) compareChart.destroy();
  compareChart = new Chart(document.getElementById('compareChart').getContext('2d'), {
    type: 'line',
    data: {
      labels: data[syms[0]].dates,
      datasets: syms.map(sym => ({
        label: sym, data: data[sym].normed,
        borderColor: data[sym].color, backgroundColor: 'transparent',
        borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, tension: 0.3,
      }))
    },
    options: {
      responsive: true,
      plugins: {
        legend: { labels: { color:'#e6edf3', boxWidth:12, padding:12, font:{size:11} } },
        tooltip: { callbacks: { label: c => ` ${c.dataset.label}: ${fmtPct(c.parsed.y)}` } }
      },
      scales: {
        x: { grid:{color:'#30363d'}, ticks:{color:'#8b949e', maxTicksLimit:6} },
        y: { grid:{color:'#30363d'}, ticks:{color:'#8b949e', callback: v => (v>=0?'+':'')+v+'%'} }
      }
    }
  });
}

function toggleSort() {
  sortByRS = !sortByRS;
  document.getElementById('sort-btn').textContent = sortByRS ? '⇅ Default Order' : '⇅ Sort by RS';
  if (overviewData.length) renderCards(overviewData, signalsData);
}

async function loadAll() {
  document.getElementById('error-box').innerHTML = '';
  try {
    // 1. Fast snapshot load
    const snap = await fetch('/api/overview');
    if (!snap.ok) throw new Error('HTTP ' + snap.status);
    overviewData = await snap.json();
    renderCards(overviewData, signalsData);
    document.getElementById('last-updated').textContent = 'Updated ' + new Date().toLocaleTimeString();

    // Auto-select first card on initial load
    if (!activeSymbol && overviewData.length) loadPriceChart(overviewData[0].symbol);

    // 2. Charts (parallel with signals)
    loadCompareChart();

    // 3. Signals (runs in background, updates cards when done)
    fetch('/api/signals').then(r => r.json()).then(sigs => {
      signalsData = sigs;
      renderCards(overviewData, signalsData);
      renderRSBar(signalsData);
    }).catch(e => console.warn('Signals failed:', e));

  } catch(e) {
    showError('Could not fetch data — check Alpaca API keys. (' + e.message + ')');
  }
}

loadAll();
setInterval(loadAll, 60000);
</script>
</body>
</html>
"""

@app.route("/")
def index():
    return HTML.replace("__COLORS__", json.dumps(COLORS))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    print(f"\n  Mag 7 RS Dashboard → http://localhost:{port}\n")
    app.run(debug=False, port=port)
