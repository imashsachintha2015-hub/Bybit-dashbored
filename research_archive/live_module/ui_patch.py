"""Adds the Kalman Trend mode to index.html: three-way mode menu, Kalman view (panel), KALMAN chart indicator.
Every edit is an exact, unique anchor replacement; the script refuses to write if any anchor is missing or repeated."""
import sys
P = "/home/user/Bybit-dashbored/index.html"
s = open(P, encoding="utf-8").read()
orig = s

def rep(old, new, count=1):
    global s
    n = s.count(old)
    if n != count: sys.exit(f"anchor found {n} times (expected {count}):\n{old[:200]}")
    s = s.replace(old, new)

# ---------------------------------------------------------------- CSS
CSS = """<style>
/* Kalman Trend mode (daemons/kalman_trend_engine.py) */
.dash-champ-toggle-btn.kalman{background:linear-gradient(135deg,rgba(167,139,250,.22),rgba(14,165,233,.14));color:#c4b5fd;border:1px solid rgba(167,139,250,.5)}
.dash-champ-toggle-btn.kalman:hover,.dash-champ-toggle-btn.kalman:active{background:rgba(167,139,250,.3)}
.mode-menu{position:fixed;z-index:9999;width:240px;background:#0b0f17;border:1px solid rgba(255,255,255,.12);border-radius:10px;box-shadow:0 12px 32px rgba(0,0,0,.6);padding:6px}
.mode-menu button{display:flex;align-items:flex-start;gap:10px;width:100%;background:transparent;border:0;border-radius:8px;color:#e5e7eb;padding:9px 10px;text-align:left;cursor:pointer;font:600 12px 'Inter',sans-serif}
.mode-menu button:hover{background:rgba(255,255,255,.06)}
.mode-menu button.on{background:rgba(167,139,250,.14)}
.mode-menu small{display:block;color:#8b9bb4;font-weight:500;font-size:10.5px;margin-top:2px}
.mode-menu i{width:16px;margin-top:2px;text-align:center}
.bsi-btn.active .bsi-dot.kal{background:#a78bfa;box-shadow:0 0 6px #a78bfa}
.kal-tag{font-size:10px;font-weight:700;padding:2px 6px;border-radius:4px;background:rgba(255,255,255,.05);color:var(--td);margin-left:6px;white-space:nowrap}
.kt-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;padding:10px 12px}
.kt-tile{background:var(--s2);border:1px solid var(--b1);border-radius:8px;padding:8px 10px;min-width:0}
.kt-tile .l{font-size:10px;color:var(--tm);text-transform:uppercase;letter-spacing:.04em}
.kt-tile .v{font-size:15px;font-weight:700;color:var(--tx);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kt-tile .s{font-size:10.5px;color:var(--tm);margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kt-sub{padding:10px 12px 4px;font-size:11px;font-weight:700;color:var(--tm);text-transform:uppercase;letter-spacing:.05em}
.kt-z{font-weight:700}.kt-up{color:#22c55e}.kt-dn{color:#ef4444}.kt-nt{color:var(--tm)}
.kt-pill{display:inline-block;font-size:10px;font-weight:700;padding:1px 6px;border-radius:4px}
.kt-pill.L{background:rgba(34,197,94,.15);color:#22c55e}.kt-pill.S{background:rgba(239,68,68,.15);color:#ef4444}
.kt-settings{margin:6px 12px 12px;padding:10px;border:1px solid var(--b1);border-radius:8px;background:var(--s2);display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:10px}
.kt-settings label{display:flex;flex-direction:column;gap:4px;font-size:11px;color:var(--tm)}
.kt-settings input[type=number]{background:#000;border:1px solid var(--b2);border-radius:6px;color:var(--tx);padding:6px 8px;font:12px 'JetBrains Mono',monospace}
.kt-settings label.row{flex-direction:row;align-items:center;gap:8px;color:var(--tx)}
.kt-note{padding:2px 12px 8px;font-size:11px;color:var(--tm);line-height:1.5}
.kt-events{padding:0 12px 12px;font:11px 'JetBrains Mono',monospace;color:var(--tm);max-height:170px;overflow:auto;line-height:1.6}
.kt-scroll{max-height:380px;overflow:auto}
</style>
</head>"""
rep("</head>", CSS)

# ---------------------------------------------------------------- navigation: sidebar, quick-jump bar, mobile subnav
rep("""<button class="sbb"    data-tip="Analytics &amp; History" onclick="switchView('analytics',3)"><i class="fa-solid fa-chart-pie"></i></button>""",
    """<button class="sbb"    data-tip="Analytics &amp; History" onclick="switchView('analytics',3)"><i class="fa-solid fa-chart-pie"></i></button>
    <button class="sbb"    data-tip="Kalman Trend" onclick="switchView('kalman',4)"><i class="fa-solid fa-wave-square" style="color:#a78bfa"></i></button>""")
rep("""<button class="qjump-btn" id="qj3" onclick="switchView('analytics',3)"><i class="fa-solid fa-chart-pie"></i> Analytics &amp; History</button>""",
    """<button class="qjump-btn" id="qj3" onclick="switchView('analytics',3)"><i class="fa-solid fa-chart-pie"></i> Analytics &amp; History</button>
      <button class="qjump-btn" id="qj4" onclick="switchView('kalman',4)"><i class="fa-solid fa-wave-square"></i> Kalman Trend</button>""")
rep("""<button class="bsn-item" id="bsnOverview" onclick="switchBybitSubnav('analytics')">History</button>""",
    """<button class="bsn-item" id="bsnOverview" onclick="switchBybitSubnav('analytics')">History</button>
      <button class="bsn-item" id="bsnKalman" onclick="switchBybitSubnav('kalman')">Kalman</button>""")

# ---------------------------------------------------------------- mode switch buttons open a menu
rep('onclick="toggleDashboardStrategyMode()"', 'onclick="toggleDashboardStrategyMode(event)"', count=2)

# ---------------------------------------------------------------- chart indicator chips + readout
rep("""<button class="cind on" id="iVOL" onclick="toggleChartIndicator('vol')">VOL</button>""",
    """<button class="cind on" id="iVOL" onclick="toggleChartIndicator('vol')">VOL</button>
              <button class="cind" id="iKAL" onclick="toggleChartIndicator('kal')" title="Kalman Trend (4H): trend line, signals, stop">KALMAN</button>""")
rep("""<button class="bsi-btn active" id="btnIndVOL" onclick="toggleChartIndicator('vol')"><span class="bsi-dot vol"></span> VOL</button>""",
    """<button class="bsi-btn active" id="btnIndVOL" onclick="toggleChartIndicator('vol')"><span class="bsi-dot vol"></span> VOL</button>
            <button class="bsi-btn" id="btnIndKAL" onclick="toggleChartIndicator('kal')"><span class="bsi-dot kal"></span> KALMAN</button>""")
rep("""<span class="amd-tag" id="tickAmd">AMD: SCANNING</span>""",
    """<span class="amd-tag" id="tickAmd">AMD: SCANNING</span>
              <span class="kal-tag" id="tickKal">KT: OFF</span>""")

# ---------------------------------------------------------------- the Kalman view
VIEW = """      <!-- View 5: Kalman Trend (funding-aware 4H trend system; daemons/kalman_trend_engine.py) -->
      <section class="app-view" id="view-kalman">
        <div class="card" id="a5" style="margin-top:12px">
          <div class="cardh">
            <div class="cardt-group">
              <span class="cardt"><i class="fa-solid fa-wave-square" style="color:#a78bfa"></i> Kalman Trend &middot; 4H</span>
              <span class="cbadge fm" id="ktBadge">LOADING</span>
            </div>
            <div class="cardh-actions">
              <button class="hbtn" id="ktModeBtn" onclick="setDashboardStrategyMode('kalman')" style="font-size:10px;padding:3px 8px"><i class="fa-solid fa-power-off"></i> <span>Use this mode</span></button>
              <button class="hbtn" onclick="ktToggleSettings()" style="font-size:10px;padding:3px 8px"><i class="fa-solid fa-sliders"></i> Settings</button>
              <button class="hbtn" onclick="ktRefresh(true)" style="font-size:10px;padding:3px 8px" title="Refresh"><i class="fa-solid fa-rotate"></i></button>
            </div>
          </div>
          <div class="kt-note">Long when the 4H Kalman trend turns up (z above +1) while BTC's daily trend is up, unless funding is overheated. Short when it turns down (z below &minus;1) while BTC's daily trend is down and funding is positive. Stop 3 ATR, out when the trend flips back or after 20 days, no take-profit. 0.5% risk per trade, at most 8 open, one per coin. Decisions happen at each 4H close (00, 04, 08, 12, 16, 20 UTC).</div>
          <div class="kt-settings" id="ktSettings" style="display:none">
            <label>Risk per trade (%) <input type="number" id="ktRisk" min="0.05" max="2" step="0.05"></label>
            <label>Max open positions <input type="number" id="ktMaxOpen" min="1" max="12" step="1"></label>
            <label>Max risk if the exchange minimum is bigger (%) <input type="number" id="ktMinRisk" min="0.1" max="5" step="0.1"></label>
            <label>Leverage (margin only) <input type="number" id="ktLev" min="1" max="20" step="1"></label>
            <label>Paper start equity ($) <input type="number" id="ktPaperStart" min="1" step="1"></label>
            <label class="row"><input type="checkbox" id="ktOi"> Longs only when open interest is above its 30-day mean</label>
            <label class="row"><input type="checkbox" id="ktCorr"> Correlation cap (skip if 3+ open trades move together)</label>
            <label class="row"><input type="checkbox" id="ktLive"> <b>LIVE orders</b> <span id="ktLiveNote" style="color:var(--tm);font-weight:500"></span></label>
            <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
              <button class="hbtn pri" onclick="ktSaveSettings()"><i class="fa-solid fa-floppy-disk"></i> Save</button>
              <button class="hbtn" onclick="ktResetPaper()"><i class="fa-solid fa-arrow-rotate-left"></i> Restart paper account</button>
            </div>
          </div>
          <div class="kt-grid" id="ktTiles"></div>
          <div class="kt-sub">Open positions</div>
          <div class="tw kt-scroll"><table><thead><tr><th>Coin</th><th>Side</th><th>Entry</th><th>Stop</th><th>Price</th><th>R now</th><th>Bars</th><th>z</th><th>Mode</th></tr></thead><tbody id="ktPos"><tr><td colspan="9" class="emp">No open positions</td></tr></tbody></table></div>
          <div class="kt-sub">Signal radar &middot; every coin at the last 4H close</div>
          <div class="tw kt-scroll"><table><thead><tr><th>Coin</th><th>z</th><th>Trend</th><th>Funding / 8h</th><th>Signal this bar</th><th>In trade</th><th>Last trade</th></tr></thead><tbody id="ktRadar"><tr><td colspan="7" class="emp">Waiting for the Kalman engine&hellip;</td></tr></tbody></table></div>
          <div class="kt-sub">Recent trades</div>
          <div class="tw kt-scroll"><table><thead><tr><th>Coin</th><th>Side</th><th>Entry</th><th>Exit</th><th>Reason</th><th>R</th><th>P&amp;L</th><th>Mode</th></tr></thead><tbody id="ktTrades"><tr><td colspan="8" class="emp">No closed trades yet</td></tr></tbody></table></div>
          <div class="kt-sub">Engine events</div>
          <div class="kt-events" id="ktEvents">&ndash;</div>
        </div>
      </section>

      <!-- View 4: Analytics & History -->"""
rep("""      <!-- View 4: Analytics & History -->""", VIEW)

# ---------------------------------------------------------------- view switching
rep("""var subnavMap = {'chart': 'bsnChart', 'positions': 'bsnPositions', 'signals': 'bsnRadar', 'analytics': 'bsnOverview'};""",
    """var subnavMap = {'chart': 'bsnChart', 'positions': 'bsnPositions', 'signals': 'bsnRadar', 'analytics': 'bsnOverview', 'kalman': 'bsnKalman'};
  if(viewName === 'kalman' && window.ktRefresh) window.ktRefresh(true);""")
rep("""      'signals': 'a3',
      'analytics': 'a4'
    };""", """      'signals': 'a3',
      'analytics': 'a4',
      'kalman': 'a5'
    };""")
rep("""var tabMap = {'chart': 0, 'positions': 1, 'signals': 2, 'analytics': 3};""",
    """var tabMap = {'chart': 0, 'positions': 1, 'signals': 2, 'analytics': 3, 'kalman': 4};""")

# ---------------------------------------------------------------- mode: menu, three modes, UI
rep("""window.toggleDashboardStrategyMode = function(){
  var nextMode = window.currentStrategyMode === 'championship' ? 'standard' : 'championship';
  window.setDashboardStrategyMode(nextMode);
};""", """window.toggleDashboardStrategyMode = function(ev){
  // Three modes now (Championship, CME-X5 Pure, Kalman Trend): open the chooser instead of flipping between two.
  window.openModeMenu(ev || window.event);
};""")
rep("""  mode = (mode === 'championship') ? 'championship' : 'standard';
  window.currentStrategyMode = mode;""", """  mode = normMode(mode);
  window.currentStrategyMode = mode;""")
rep("""  showToast(isChamp ? '🏆 Switched to Championship Mode' : '⚡ Switched to CME-X5 Pure Mode');""",
    """  showToast(isChamp ? '🏆 Switched to Championship Mode' : (mode === 'kalman' ? '〰 Switched to Kalman Trend Mode' : '⚡ Switched to CME-X5 Pure Mode'));
  if(mode === 'kalman'){
    if(window.indicatorState && !window.indicatorState.kal) window.toggleChartIndicator('kal');
    if(window.ktRefresh) setTimeout(function(){ window.ktRefresh(true); }, 800);
  }""")
rep("""      var confirmedMode = (res.strategyMode === 'championship' || res.championshipMode) ? 'championship' : 'standard';""",
    """      var confirmedMode = normMode(res.strategyMode || (res.championshipMode ? 'championship' : 'standard'));""")
rep("""window.updateChampionshipUI = function(){
  var isChamp = window.currentStrategyMode === 'championship';""", """window.updateChampionshipUI = function(){
  var isChamp = window.currentStrategyMode === 'championship';
  var isKal = window.currentStrategyMode === 'kalman';""")
rep("""    } else {
      btn.className = 'dash-champ-toggle-btn standard';""", """    } else if(isKal){
      btn.className = 'dash-champ-toggle-btn kalman active';
      btn.innerHTML = '<i class="fa-solid fa-wave-square"></i> <span>KALMAN TREND</span>';
      btn.title = 'Current: Kalman Trend (4H, funding-aware). Tap to choose a mode.';
    } else {
      btn.className = 'dash-champ-toggle-btn standard';""")
rep("""  } else {
    if(stripName) stripName.textContent = 'CME-X5 Pure Algorithm';""", """  } else if(isKal){
    if(stripName) stripName.textContent = 'Kalman Trend (4H)';
    if(stripBadge){
      stripBadge.textContent = 'FUNDING-AWARE';
      stripBadge.style.color = '#c4b5fd';
      stripBadge.style.background = 'rgba(167,139,250,0.15)';
      stripBadge.style.borderColor = 'rgba(167,139,250,0.4)';
    }
    if(stripStrat){
      stripStrat.innerHTML = '<b>z &plusmn;1 trend entries</b> &nbsp;|&nbsp; <b>BTC 1D filter</b> &nbsp;|&nbsp; <b>3-ATR stop, trend-flip exit</b>';
    }
    if(esEngine){
      esEngine.textContent = 'KALMAN';
      esEngine.style.color = '#c4b5fd';
    }
    if(wmStratMode){
      wmStratMode.textContent = 'KALMAN TREND';
      wmStratMode.style.color = '#c4b5fd';
      wmStratMode.style.borderColor = 'rgba(167,139,250,0.3)';
      wmStratMode.style.background = 'rgba(167,139,250,0.14)';
    }
  } else {
    if(stripName) stripName.textContent = 'CME-X5 Pure Algorithm';""")
rep("""  if (stateText) stateText.textContent = isChamp ? 'CHAMPIONSHIP ARMED' : 'CME-X5 PURE ACTIVE';""",
    """  if (stateText) stateText.textContent = isChamp ? 'CHAMPIONSHIP ARMED' : (isKal ? 'KALMAN TREND ACTIVE' : 'CME-X5 PURE ACTIVE');
  var ktModeBtn = document.getElementById('ktModeBtn');
  if (ktModeBtn) ktModeBtn.innerHTML = isKal ? '<i class="fa-solid fa-circle-check" style="color:#22c55e"></i> <span>Mode selected</span>' : '<i class="fa-solid fa-power-off"></i> <span>Use this mode</span>';""")
# background sync of the mode from the server (two places)
rep("""    var isChamp = (s.strategyMode === 'championship') || Boolean(s.championshipMode);
    var backendMode = isChamp ? 'championship' : 'standard';
    var currentMode = window.currentStrategyMode === 'championship' ? 'championship' : 'standard';""",
    """    var backendMode = normMode(s.strategyMode || (s.championshipMode ? 'championship' : 'standard'));
    var isChamp = backendMode === 'championship';
    var currentMode = normMode(window.currentStrategyMode);""")
rep("""  var backendMode = (state.strategyMode === 'championship' || state.championshipMode)
    ? 'championship' : 'standard';""", """  var backendMode = normMode(state.strategyMode || (state.championshipMode ? 'championship' : 'standard'));""")

# ---------------------------------------------------------------- indicator state and toggle
rep("""  vol: localStorage.getItem('ind_vol') !== '0'
};""", """  vol: localStorage.getItem('ind_vol') !== '0',
  kal: localStorage.getItem('ind_kal') === '1'
};""")
rep("""    } else if(name === 'vol'){
      var isVol = !!window.indicatorState.vol;""", """    } else if(name === 'kal'){
      if(window.ktChartOverlay) window.ktChartOverlay(true);
    } else if(name === 'vol'){
      var isVol = !!window.indicatorState.vol;""")
rep("""  ['ema', 'poc', 'fvg', 'vol'].forEach(function(k){""", """  ['ema', 'poc', 'fvg', 'vol', 'kal'].forEach(function(k){""")
rep("""      // 4. Draw Order Levels (TP Entire, P&L, SL Entire) if active
      updateOrderLines();""", """      // 4. Draw Order Levels (TP Entire, P&L, SL Entire) if active
      updateOrderLines();

      // 5. Kalman Trend overlay (trend line, signals, stop) when its indicator is on
      if(window.ktChartOverlay) window.ktChartOverlay(false);""")

# ---------------------------------------------------------------- the Kalman panel and overlay code (inside the page IIFE)
JS = open("/tmp/claude-0/-home-user-Bybit-dashbored/75f1764d-aab5-5961-97c3-7261f6eaa24e/scratchpad/ui_kalman.js", encoding="utf-8").read()
rep("""// Reconcile visible mode with shared Railway/Supabase state.""", JS + """
// Reconcile visible mode with shared Railway/Supabase state.""")

open(P, "w", encoding="utf-8").write(s)
print(f"index.html patched: {len(orig)} -> {len(s)} bytes")
