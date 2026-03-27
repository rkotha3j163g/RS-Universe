#!/usr/bin/env python3
"""
RS Scanner Dashboard
Displays results from a locally-run rs_scan.py CSV upload.
No scanning happens on this server — just reads uploaded data.
"""

import io
import os
import json
from datetime import datetime

import pandas as pd
from flask import Flask, jsonify, request, redirect, url_for

app = Flask(__name__)

UPLOAD_TOKEN = os.environ.get("UPLOAD_TOKEN", "")   # set in Railway env vars

# ── In-memory store ───────────────────────────────────────────────────────────

_store = {
    "uploaded_at": None,
    "filename":    None,
    "rows":        [],   # list of dicts (all ranked rows)
}


def parse_csv(fileobj):
    df = pd.read_csv(fileobj)
    df.columns = [c.strip() for c in df.columns]

    # normalise column names from rs_scan.py output
    rename = {
        "Ticker":       "ticker",
        "Price":        "price",
        "RS_Score":     "rs_score",
        "RS_Rank":      "rs_rank",
        "RS_Slope_20d": "slope",
        "Drawdown%":    "drawdown",
        "vs_SPY_DD":    "vs_spy",
        "Chg_1M%":      "chg_1m",
        "Chg_3M%":      "chg_3m",
        "Above_21MA":   "above_21",
        "Above_50MA":   "above_50",
        "Above_200MA":  "above_200",
    }
    df.rename(columns=rename, inplace=True)

    # coerce booleans
    for col in ("above_21", "above_50", "above_200"):
        if col in df.columns:
            df[col] = df[col].map(lambda v: str(v).strip().lower() in ("true", "1", "yes"))

    # fill NaN with None for JSON serialisation
    df = df.where(pd.notnull(df), None)

    rows = df.to_dict(orient="records")
    return rows


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/upload", methods=["GET", "POST"])
def upload():
    if request.method == "GET":
        return UPLOAD_PAGE

    # ── POST ──────────────────────────────────────────────────────────────────
    # Accept either form-based upload (browser) or raw CSV body (curl)
    token = (
        request.form.get("token", "")
        or request.headers.get("X-Upload-Token", "")
    )
    if UPLOAD_TOKEN and token != UPLOAD_TOKEN:
        return "Unauthorized", 401

    file = request.files.get("file")
    if file is None:
        # try raw body (curl --data-binary)
        raw = request.get_data()
        if not raw:
            return "No file provided", 400
        fileobj = io.StringIO(raw.decode("utf-8"))
        filename = "upload.csv"
    else:
        fileobj = io.StringIO(file.stream.read().decode("utf-8"))
        filename = file.filename

    try:
        rows = parse_csv(fileobj)
    except Exception as e:
        return f"Could not parse CSV: {e}", 400

    _store["rows"]        = rows
    _store["uploaded_at"] = datetime.now().isoformat()
    _store["filename"]    = filename

    # return JSON for curl, redirect for browser
    if request.accept_mimetypes.best == "application/json" or not file:
        return jsonify({"ok": True, "rows": len(rows), "filename": filename})
    return redirect(url_for("index"))


@app.route("/api/data")
def api_data():
    return jsonify(_store)


# ── HTML ──────────────────────────────────────────────────────────────────────

UPLOAD_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Upload RS Scan</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #0d1117; color: #e6edf3;
       font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       display: flex; align-items: center; justify-content: center; min-height: 100vh; }
.card { background: #161b22; border: 1px solid #30363d; border-radius: 12px;
        padding: 32px 36px; max-width: 480px; width: 100%; }
h2 { font-size: 1.1rem; margin-bottom: 8px; }
p  { color: #8b949e; font-size: .85rem; margin-bottom: 20px; line-height: 1.5; }
label { display: block; font-size: .8rem; color: #8b949e; margin-bottom: 5px; }
input[type=file], input[type=password], input[type=text] {
  width: 100%; background: #0d1117; border: 1px solid #30363d;
  color: #e6edf3; border-radius: 6px; padding: 8px 10px;
  font-size: .85rem; margin-bottom: 14px;
}
button { background: #238636; border: none; color: #fff; padding: 8px 20px;
         border-radius: 6px; cursor: pointer; font-size: .88rem; font-weight: 600;
         width: 100%; transition: background .2s; }
button:hover { background: #2ea043; }
.code { background: #0d1117; border: 1px solid #30363d; border-radius: 6px;
        padding: 10px 12px; font-family: monospace; font-size: .78rem;
        color: #58a6ff; margin-top: 16px; word-break: break-all; }
</style>
</head>
<body>
<div class="card">
  <h2>Upload RS Scan Results</h2>
  <p>Run <code>rs_scan.py</code> locally, then upload the CSV here to update the dashboard.</p>
  <form method="POST" enctype="multipart/form-data">
    <label>CSV file (rs_scan_YYYY-MM-DD.csv)</label>
    <input type="file" name="file" accept=".csv" required>
    <label>Upload token (if set)</label>
    <input type="password" name="token" placeholder="leave blank if no token">
    <button type="submit">Upload &amp; Update Dashboard</button>
  </form>
  <div class="code">
    # Or upload via curl:<br>
    curl -X POST https://&lt;your-app&gt;/upload \\<br>
    &nbsp;&nbsp;-H "X-Upload-Token: &lt;token&gt;" \\<br>
    &nbsp;&nbsp;-F "file=@rs_scan_2026-03-27.csv"
  </div>
</div>
</body>
</html>"""

DASHBOARD = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RS Universe — Relative Strength Leaderboard</title>
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

header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 13px 28px; border-bottom: 1px solid var(--border);
  background: var(--surface); position: sticky; top: 0; z-index: 10;
  flex-wrap: wrap; gap: 10px;
}
header h1 { font-size: 1.1rem; font-weight: 700; }
header h1 span { color: var(--amber); }
.hdr-right { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
#meta { font-size: .75rem; color: var(--muted); }
.btn {
  background: #1f3a5f; border: none; color: #fff; padding: 5px 13px;
  border-radius: 6px; cursor: pointer; font-size: .8rem; font-weight: 600;
  text-decoration: none; display: inline-block; transition: background .2s;
}
.btn:hover { background: #2255a0; }
.btn-upload { background: #238636; }
.btn-upload:hover { background: #2ea043; }

main { max-width: 1400px; margin: 0 auto; padding: 20px 16px; }

/* Filter bar */
.filter-bar {
  display: flex; gap: 10px; align-items: center; flex-wrap: wrap;
  margin-bottom: 16px;
}
.filter-bar input {
  background: var(--surface); border: 1px solid var(--border);
  color: var(--text); border-radius: 6px; padding: 6px 10px;
  font-size: .82rem; width: 200px;
}
.filter-bar input:focus { outline: none; border-color: var(--blue); }
.filter-bar label { font-size: .78rem; color: var(--muted); display: flex; align-items: center; gap: 5px; }
.filter-bar input[type=checkbox] { width: auto; }
#row-count { font-size: .75rem; color: var(--muted); margin-left: auto; }

/* Table */
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: .82rem; }
thead th {
  background: var(--surface); color: var(--muted); font-size: .71rem;
  font-weight: 700; letter-spacing: .5px; text-transform: uppercase;
  padding: 9px 10px; border-bottom: 1px solid var(--border);
  text-align: right; white-space: nowrap; cursor: pointer; user-select: none;
}
thead th:first-child,
thead th:nth-child(2) { text-align: left; }
thead th:hover { color: var(--text); }
thead th.sort-asc::after  { content: " ↑"; color: var(--amber); }
thead th.sort-desc::after { content: " ↓"; color: var(--amber); }

tbody tr { border-bottom: 1px solid var(--border); transition: background .1s; }
tbody tr:hover { background: #1c2128; }
tbody td { padding: 9px 10px; text-align: right; vertical-align: middle; }
tbody td:first-child { text-align: left; color: var(--muted); font-size: .75rem; }
tbody td:nth-child(2) { text-align: left; }

.ticker { font-weight: 700; font-size: .88rem; }

.rs-pill {
  display: inline-block; padding: 2px 8px; border-radius: 10px;
  font-weight: 800; font-size: .78rem; min-width: 34px; text-align: center;
}
.rs-90 { background:#0d3320; color:#3fb950; border:1px solid #3fb950; }
.rs-75 { background:#0d2a1a; color:#56d975; border:1px solid #3fb950; }
.rs-60 { background:#1f3a5f; color:#58a6ff; border:1px solid #58a6ff; }
.rs-lo { background:#2a1f00; color:#f0a500; border:1px solid #f0a500; }

.up  { color: var(--green); }
.dn  { color: var(--red); }
.neu { color: var(--muted); }

.ma-dots { display: flex; gap: 3px; justify-content: flex-end; }
.dot {
  width: 24px; height: 18px; border-radius: 3px; font-size: .6rem;
  font-weight: 700; display: flex; align-items: center; justify-content: center;
}
.dot.yes { background: #0d3320; color: #3fb950; }
.dot.no  { background: #300;    color: #f85149; }

/* Empty state */
#empty {
  text-align: center; padding: 80px 20px; color: var(--muted);
}
#empty h2 { font-size: 1rem; margin-bottom: 10px; color: var(--text); }
#empty p  { font-size: .85rem; margin-bottom: 20px; }

/* Legend */
.legend {
  margin-top: 14px; padding: 10px 14px;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 8px; font-size: .74rem; color: var(--muted); line-height: 1.8;
}
.legend b { color: var(--text); }
</style>
</head>
<body>

<header>
  <h1>RS <span>Universe</span></h1>
  <div class="hdr-right">
    <span id="meta">No data uploaded yet</span>
    <a class="btn btn-upload" href="/upload">&#8593; Upload CSV</a>
  </div>
</header>

<main>
  <!-- Empty state -->
  <div id="empty">
    <h2>No scan data yet</h2>
    <p>Run <code>rs_scan.py</code> locally to generate a CSV, then upload it here.</p>
    <a class="btn btn-upload" href="/upload">Upload RS Scan CSV</a>
  </div>

  <!-- Results -->
  <div id="results" style="display:none">
    <div class="filter-bar">
      <input type="text" id="search" placeholder="Filter ticker…" oninput="applyFilters()">
      <label><input type="checkbox" id="f-above200" onchange="applyFilters()"> Above 200MA</label>
      <label><input type="checkbox" id="f-rising"   onchange="applyFilters()"> Rising RS slope</label>
      <label><input type="checkbox" id="f-rs80"     onchange="applyFilters()"> RS ≥ 80</label>
      <span id="row-count"></span>
    </div>
    <div class="table-wrap">
      <table id="rs-table">
        <thead>
          <tr>
            <th>#</th>
            <th data-col="ticker">Ticker</th>
            <th data-col="rs_rank">RS Rank</th>
            <th data-col="price">Price</th>
            <th data-col="chg_1m">1M Chg</th>
            <th data-col="chg_3m">3M Chg</th>
            <th data-col="drawdown">Drawdown</th>
            <th data-col="vs_spy">vs SPY DD</th>
            <th data-col="slope">RS Slope</th>
            <th>MAs 21/50/200</th>
          </tr>
        </thead>
        <tbody id="rs-tbody"></tbody>
      </table>
    </div>
    <div class="legend">
      <b>RS Rank</b>: IBD-style percentile vs universe (99 = strongest). &nbsp;
      <b>vs SPY DD</b>: stock drawdown minus SPY drawdown — positive = holding up better. &nbsp;
      <b>RS Slope</b>: 20-day trend of stock/SPY ratio — positive = RS line rising. &nbsp;
      <b>MAs</b>: green = above that moving average.
    </div>
  </div>
</main>

<script>
let _allRows  = [];
let _sortCol  = null;
let _sortAsc  = true;

const fmt2   = n => n == null ? '—' : Number(n).toFixed(2);
const fmtPct = n => n == null ? '—' : (n >= 0 ? '+' : '') + Number(n).toFixed(1) + '%';

function rsPillClass(rs) {
  if (rs >= 90) return 'rs-90';
  if (rs >= 75) return 'rs-75';
  if (rs >= 60) return 'rs-60';
  return 'rs-lo';
}

function renderRows(rows) {
  const tbody = document.getElementById('rs-tbody');
  tbody.innerHTML = '';
  rows.forEach((r, i) => {
    const tr = document.createElement('tr');
    const slopeClass = r.slope == null ? 'neu' : r.slope > 0 ? 'up' : 'dn';
    const slopeText  = r.slope == null ? '—' : (r.slope > 0 ? '+' : '') + Number(r.slope).toFixed(2);
    const dot = (label, val) =>
      `<div class="dot ${val ? 'yes' : 'no'}">${label}</div>`;

    tr.innerHTML = `
      <td>${i + 1}</td>
      <td><span class="ticker">${r.ticker}</span></td>
      <td><span class="rs-pill ${rsPillClass(r.rs_rank)}">${r.rs_rank}</span></td>
      <td>$${fmt2(r.price)}</td>
      <td class="${(r.chg_1m||0) >= 0 ? 'up':'dn'}">${fmtPct(r.chg_1m)}</td>
      <td class="${(r.chg_3m||0) >= 0 ? 'up':'dn'}">${fmtPct(r.chg_3m)}</td>
      <td class="${(r.drawdown||0) >= 0 ? 'up':'dn'}">${fmtPct(r.drawdown)}</td>
      <td class="${(r.vs_spy||0)  >= 0 ? 'up':'dn'}">${fmtPct(r.vs_spy)}</td>
      <td class="${slopeClass}">${slopeText}</td>
      <td><div class="ma-dots">${dot('21',r.above_21)}${dot('50',r.above_50)}${dot('200',r.above_200)}</div></td>`;
    tbody.appendChild(tr);
  });
  document.getElementById('row-count').textContent =
    `${rows.length} of ${_allRows.length} stocks`;
}

function applyFilters() {
  const q        = document.getElementById('search').value.trim().toUpperCase();
  const above200 = document.getElementById('f-above200').checked;
  const rising   = document.getElementById('f-rising').checked;
  const rs80     = document.getElementById('f-rs80').checked;

  let rows = _allRows.filter(r => {
    if (q && !r.ticker.toUpperCase().includes(q)) return false;
    if (above200 && !r.above_200) return false;
    if (rising   && !(r.slope > 0)) return false;
    if (rs80     && !(r.rs_rank >= 80)) return false;
    return true;
  });

  if (_sortCol) {
    rows.sort((a, b) => {
      let va = a[_sortCol], vb = b[_sortCol];
      if (typeof va === 'string') va = va.toLowerCase();
      if (typeof vb === 'string') vb = vb.toLowerCase();
      if (va == null) return 1;
      if (vb == null) return -1;
      return _sortAsc ? (va > vb ? 1 : -1) : (va < vb ? 1 : -1);
    });
  }
  renderRows(rows);
}

document.querySelectorAll('thead th[data-col]').forEach(th => {
  th.addEventListener('click', () => {
    const col = th.dataset.col;
    if (_sortCol === col) { _sortAsc = !_sortAsc; }
    else { _sortCol = col; _sortAsc = col === 'ticker'; }
    document.querySelectorAll('thead th').forEach(h => h.classList.remove('sort-asc','sort-desc'));
    th.classList.add(_sortAsc ? 'sort-asc' : 'sort-desc');
    applyFilters();
  });
});

(function load() {
  fetch('/api/data')
    .then(r => r.json())
    .then(d => {
      if (!d.rows || d.rows.length === 0) return;
      _allRows = d.rows;
      document.getElementById('empty').style.display = 'none';
      document.getElementById('results').style.display = 'block';
      const ts = d.uploaded_at
        ? new Date(d.uploaded_at).toLocaleString()
        : '';
      document.getElementById('meta').textContent =
        (d.filename ? d.filename + ' · ' : '') +
        (ts ? 'uploaded ' + ts : '') +
        ` · ${d.rows.length} stocks`;
      applyFilters();
    });
})();
</script>
</body>
</html>"""


@app.route("/")
def index():
    return DASHBOARD


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    print(f"\n  RS Universe Dashboard → http://localhost:{port}\n")
    app.run(debug=False, port=port)
