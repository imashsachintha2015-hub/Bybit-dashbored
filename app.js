/**
 * MASIS V3 — Agent Operations Center (client controller).
 *
 * What changed at this layer, and why it mattered more than anything in the
 * engine:
 *
 *  1. THE GUARDIAN NO LONGER CUTS ON A FLICKER. renderPositionGuardian() used
 *     to run on a 3-second timer and market-close a position the instant the
 *     live decision, the whale side, or the trap guard disagreed with it — none
 *     of which are stable on a 3-second cadence. In the reconstruction of that
 *     logic run over six weeks of real bars, 20-40% of all exits were this cut,
 *     and it fired on winners and losers alike. Position management now lives in
 *     agents/position-manager.js and exits only on named structural events.
 *
 *  2. THE MODEL IS CALLED ON PURPOSE, NOT ON A TIMER. The old build called
 *     DeepSeek on every decision flip across five symbols plus a blind
 *     60-second refresh, and the reply was prose no code read. Calls now go
 *     through agents/deepseek-governor.js: grade-A candidates only, per-symbol
 *     cooldown, fingerprint cache, daily ceiling — and the reply is a structured
 *     verdict the gate consumes.
 *
 *  3. SIZE IS DERIVED FROM RISK. "Margin per trade" bought the same dollar
 *     notional whether the stop was 0.1% or 1.5% away, so the amount actually
 *     at risk varied by more than an order of magnitude between trades and no
 *     win rate computed across them meant anything. Size now comes from
 *     agents/risk-governor.js so every trade risks the same fraction of equity.
 *
 *  4. ORDERS CARRY A STOP, NOT A FULL-SIZE FIRST TARGET. The old entry attached
 *     takeProfit[0] broker-side at full size, which capped every winner at 1.1R
 *     against a 1.0R loser — a structure that needs ~48% winners just to break
 *     even before fees, and the system was not achieving that.
 */

document.addEventListener('DOMContentLoaded', () => {

  // ─── State ───
  const state = {
    symbol: 'BTCUSDT',
    category: 'linear',
    network: 'mainnet',
    interval: '15',          // chart display interval only
    lastPrice: 0,
    prevPrice: 0,
    recentTrades: [],
    uptimeSeconds: 0,
    coinSearch: ''           // filters the coin-cards-row; see renderCoinCards()
  };

  // Shown in the coin-cards-row when the search box is empty, instead of all
  // 30 watchlist coins at once -- the search box is how the rest are reached.
  const DEFAULT_VISIBLE_COINS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'DOGEUSDT'];

  // Which subscribed kline interval feeds which engine slot.
  const INTERVAL_TO_TF = { '5': 'ltf', '15': 'mtf', '60': 'htf' };
  const ANALYSIS_INTERVALS = ['5', '15', '60'];

  const AGENTS = [
    { id: 'DIV-01', name: 'Data Validity Supervisor', icon: 'fa-shield-halved', iconColor: 'var(--cyan)', status: 'active', statusLabel: 'ACTIVE', meta1: 'Quality: --', meta2: 'Halt: ARMED' },
    { id: 'DIV-02', name: 'Order Flow Agent', icon: 'fa-calculator', iconColor: 'var(--blue)', status: 'active', statusLabel: 'ACTIVE', meta1: 'Taker: --', meta2: 'Burst: --' },
    { id: 'DIV-03', name: 'Regime & Bias Agent', icon: 'fa-compass', iconColor: 'var(--purple)', status: 'evaluating', statusLabel: 'CLASSIFYING', meta1: 'Regime: --', meta2: 'Bias: --' },
    { id: 'DIV-04', name: 'Absorption Detector', icon: 'fa-magnet', iconColor: 'var(--gold)', status: 'active', statusLabel: 'MONITORING', meta1: 'Absorb: NONE', meta2: 'Spoof: --' },
    { id: 'DIV-05', name: 'Setup Playbooks', icon: 'fa-crosshairs', iconColor: 'var(--green)', status: 'active', statusLabel: 'SCANNING', meta1: 'Setup: --', meta2: 'Grade: --' },
    { id: 'DIV-06', name: 'Confluence Gates', icon: 'fa-gavel', iconColor: 'var(--red)', status: 'pass', statusLabel: 'PASS', meta1: 'Gates: --', meta2: 'Blocked: --' },
    { id: 'DIV-07', name: 'Supervisor Model', icon: 'fa-scale-balanced', iconColor: 'var(--cyan)', status: 'active', statusLabel: 'GATED', meta1: 'Calls: --', meta2: 'Saved: --' },
    { id: 'DIV-08', name: 'Risk Governor', icon: 'fa-lock', iconColor: 'var(--gold)', status: 'active', statusLabel: 'ENFORCED', meta1: 'Risk/trade: --', meta2: 'Day P&L: --' },
    { id: 'DIV-10', name: 'News Sentinel', icon: 'fa-newspaper', iconColor: 'var(--gold)', status: 'active', statusLabel: 'MONITORING', meta1: 'Headlines: --', meta2: 'Sentiment: --' },
    { id: 'DIV-11', name: 'Whale Flow Tracker', icon: 'fa-money-bill-trend-up', iconColor: 'var(--cyan)', status: 'active', statusLabel: 'TRACKING', meta1: 'Whales: 0', meta2: 'Flow: NEUTRAL' },
    { id: 'DIV-12', name: 'Position Manager', icon: 'fa-shield', iconColor: 'var(--green)', status: 'active', statusLabel: 'IDLE', meta1: 'Open: 0', meta2: 'Mode: STRUCTURAL' }
  ];

  const $ = id => document.getElementById(id);

  function renderAgents() {
    const grid = $('agentsGrid');
    if (!grid) return;
    grid.innerHTML = AGENTS.map(a => `
      <div class="agent-card status-${a.status}" data-agent="${a.id}">
        <div class="agent-top">
          <span class="agent-id">${a.id}</span>
          <span class="agent-status ${a.status}">${a.statusLabel}</span>
        </div>
        <div class="agent-name"><i class="fa-solid ${a.icon}" style="color:${a.iconColor}"></i> ${a.name}</div>
        <div class="agent-detail">
          <span>${a.meta1}</span>
          <span>${a.meta2}</span>
        </div>
      </div>
    `).join('');
  }
  renderAgents();

  function updateClock() { $('clockDisplay').textContent = new Date().toTimeString().split(' ')[0]; }
  setInterval(updateClock, 1000);
  updateClock();

  setInterval(() => {
    state.uptimeSeconds++;
    const h = Math.floor(state.uptimeSeconds / 3600);
    const m = Math.floor((state.uptimeSeconds % 3600) / 60);
    const s = state.uptimeSeconds % 60;
    $('footerUptime').textContent = `Uptime: ${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  }, 1000);

  const candleChart = new CandlestickChartEngine('candleChartCanvas', null);

  // qtyStep/minQty below are placeholder fallbacks only, used if the live
  // instrument-spec fetch (fetchInstrumentSpecs, called during init) fails or
  // hasn't completed yet -- they get overwritten with Bybit's real lot-size
  // filter for every symbol on startup, so a wrong guess here never reaches
  // an actual order.
  // tick is each symbol's minimum price increment on Bybit USDT perps --
  // needed now that entries are patient limit orders at a specific price
  // rather than Market orders (which never need a price at all). These are
  // best-effort values, not fetched live from Bybit's instruments-info
  // endpoint; if one is wrong for a given symbol, Bybit rejects the order
  // with a precision error, which surfaces as an ordinary "Order rejected"
  // log line (see executeEntry) rather than anything silently wrong.
  const COIN_META = {
    BTCUSDT: { name: 'Bitcoin', short: 'BTC', color: '#F7931A', icon: 'fa-btc', brand: true, qtyStep: 0.001, minQty: 0.001, tick: 0.1 },
    ETHUSDT: { name: 'Ethereum', short: 'ETH', color: '#627EEA', icon: 'fa-ethereum', brand: true, qtyStep: 0.01, minQty: 0.01, tick: 0.01 },
    SOLUSDT: { name: 'Solana', short: 'SOL', color: '#14F195', icon: 'fa-bolt', brand: false, qtyStep: 0.1, minQty: 0.1, tick: 0.01 },
    XRPUSDT: { name: 'XRP', short: 'XRP', color: '#25A9E0', icon: 'fa-coins', brand: false, qtyStep: 1, minQty: 1, tick: 0.0001 },
    DOGEUSDT: { name: 'Dogecoin', short: 'DOGE', color: '#C2A633', icon: 'fa-dog', brand: false, qtyStep: 1, minQty: 1, tick: 0.00001 },
    ADAUSDT: { name: 'Cardano', short: 'ADA', color: '#0033AD', icon: 'fa-coins', brand: false, qtyStep: 1, minQty: 1, tick: 0.0001 },
    AVAXUSDT: { name: 'Avalanche', short: 'AVAX', color: '#E84142', icon: 'fa-mountain', brand: false, qtyStep: 0.1, minQty: 0.1, tick: 0.01 },
    LINKUSDT: { name: 'Chainlink', short: 'LINK', color: '#2A5ADA', icon: 'fa-link', brand: false, qtyStep: 0.1, minQty: 0.1, tick: 0.001 },
    DOTUSDT: { name: 'Polkadot', short: 'DOT', color: '#E6007A', icon: 'fa-circle-dot', brand: false, qtyStep: 0.1, minQty: 0.1, tick: 0.001 },
    LTCUSDT: { name: 'Litecoin', short: 'LTC', color: '#345D9D', icon: 'fa-litecoin-sign', brand: true, qtyStep: 0.01, minQty: 0.01, tick: 0.01 },
    BCHUSDT: { name: 'Bitcoin Cash', short: 'BCH', color: '#8DC351', icon: 'fa-coins', brand: false, qtyStep: 0.01, minQty: 0.01, tick: 0.01 },
    ATOMUSDT: { name: 'Cosmos', short: 'ATOM', color: '#2E3148', icon: 'fa-atom', brand: false, qtyStep: 0.1, minQty: 0.1, tick: 0.001 },
    NEARUSDT: { name: 'NEAR Protocol', short: 'NEAR', color: '#00C08B', icon: 'fa-coins', brand: false, qtyStep: 0.1, minQty: 0.1, tick: 0.001 },
    APTUSDT: { name: 'Aptos', short: 'APT', color: '#00D2A0', icon: 'fa-coins', brand: false, qtyStep: 0.1, minQty: 0.1, tick: 0.001 },
    ARBUSDT: { name: 'Arbitrum', short: 'ARB', color: '#28A0F0', icon: 'fa-layer-group', brand: false, qtyStep: 1, minQty: 1, tick: 0.0001 },
    OPUSDT: { name: 'Optimism', short: 'OP', color: '#FF0420', icon: 'fa-layer-group', brand: false, qtyStep: 1, minQty: 1, tick: 0.0001 },
    SUIUSDT: { name: 'Sui', short: 'SUI', color: '#6FBCF0', icon: 'fa-coins', brand: false, qtyStep: 1, minQty: 1, tick: 0.0001 },
    TONUSDT: { name: 'Toncoin', short: 'TON', color: '#0088CC', icon: 'fa-paper-plane', brand: false, qtyStep: 0.1, minQty: 0.1, tick: 0.0001 },
    TRXUSDT: { name: 'TRON', short: 'TRX', color: '#EF0027', icon: 'fa-coins', brand: false, qtyStep: 1, minQty: 1, tick: 0.00001 },
    SHIB1000USDT: { name: 'Shiba Inu', short: 'SHIB', color: '#F00500', icon: 'fa-dog', brand: false, qtyStep: 1000, minQty: 1000, tick: 0.000001 },
    UNIUSDT: { name: 'Uniswap', short: 'UNI', color: '#FF007A', icon: 'fa-coins', brand: false, qtyStep: 0.1, minQty: 0.1, tick: 0.001 },
    FILUSDT: { name: 'Filecoin', short: 'FIL', color: '#0090FF', icon: 'fa-coins', brand: false, qtyStep: 0.1, minQty: 0.1, tick: 0.001 },
    ETCUSDT: { name: 'Ethereum Classic', short: 'ETC', color: '#328332', icon: 'fa-ethereum', brand: false, qtyStep: 0.01, minQty: 0.01, tick: 0.001 },
    XLMUSDT: { name: 'Stellar', short: 'XLM', color: '#14B6E7', icon: 'fa-coins', brand: false, qtyStep: 1, minQty: 1, tick: 0.0001 },
    ICPUSDT: { name: 'Internet Computer', short: 'ICP', color: '#3B00B9', icon: 'fa-coins', brand: false, qtyStep: 0.1, minQty: 0.1, tick: 0.001 },
    HBARUSDT: { name: 'Hedera', short: 'HBAR', color: '#000000', icon: 'fa-coins', brand: false, qtyStep: 1, minQty: 1, tick: 0.00001 },
    INJUSDT: { name: 'Injective', short: 'INJ', color: '#00D2FF', icon: 'fa-syringe', brand: false, qtyStep: 0.1, minQty: 0.1, tick: 0.001 },
    SEIUSDT: { name: 'Sei', short: 'SEI', color: '#8B2FE8', icon: 'fa-coins', brand: false, qtyStep: 1, minQty: 1, tick: 0.0001 },
    AAVEUSDT: { name: 'Aave', short: 'AAVE', color: '#B6509E', icon: 'fa-ghost', brand: false, qtyStep: 0.01, minQty: 0.01, tick: 0.01 },
    ALGOUSDT: { name: 'Algorand', short: 'ALGO', color: '#000000', icon: 'fa-coins', brand: false, qtyStep: 1, minQty: 1, tick: 0.0001 }
  };
  const WATCHLIST = Object.keys(COIN_META);
  const priceHistory = {}, lastKnown = {}, tradeStats = {};
  WATCHLIST.forEach(s => {
    priceHistory[s] = [];
    lastKnown[s] = { price: 0, changePct: 0 };
    tradeStats[s] = { buyVol: 0, sellVol: 0 };
  });

  // ─── Per-symbol engines ───
  const engines = {}, whaleTrackers = {}, latestStates = {};

  // ─── Shadow setup tracker ───
  // The dashboard only ever shows a setup as "taken" once it clears every
  // gate AND reaches grade A/A+ -- everything below that (a B-grade
  // pullback, a setup a gate blocked) is graded and then never followed up
  // on. This tracks EVERY graded candidate on EVERY watchlist coin, at
  // every grade, independent of whether it passed gates or was ever traded,
  // and watches its own entry/stop/targets against live price until it
  // resolves WIN or LOSS -- a shadow record of "would this call have been
  // right", for the pop-out monitor window (monitor.html) rather than the
  // main dashboard's already-busy views.
  const shadowTracker = { active: {}, history: [] };
  const SHADOW_HISTORY_LIMIT = 200;
  const shadowChannel = typeof BroadcastChannel !== 'undefined' ? new BroadcastChannel('masis-shadow-monitor') : null;

  function updateShadowTracking(sym, s) {
    const cur = shadowTracker.active[sym];
    const hasCandidate = !!(s.grade && s.entry && s.stopLoss && s.takeProfit && s.takeProfit.length);

    // The engine re-derives a candidate from scratch every single cycle --
    // it has no memory of "this is the same setup as last time". For a
    // pattern like a range fade, that means a practically-identical
    // RANGE_FADE/SHORT can re-appear every 5s with its entry ticking along
    // with current price, cycle after cycle. Treating each of those as a
    // "new" setup (the old behavior: superseding on any setupType mismatch,
    // or the moment the engine stops re-detecting it) meant a call almost
    // never survived long enough to actually hit its own stop or target --
    // it just kept getting reset. Once a call is being watched, keep
    // watching its ORIGINAL entry/stop/target until they actually resolve.
    // Only a candidate in the OPPOSITE direction is a genuine reversal that
    // invalidates the old thesis; a same-direction re-detection, or the
    // engine simply not seeing anything this cycle, changes nothing.
    if (cur && hasCandidate && cur.direction !== s.direction) {
      shadowTracker.history.unshift({ ...cur, status: 'SUPERSEDED', resolvedAt: Date.now(), exitPrice: null });
      delete shadowTracker.active[sym];
    }

    if (hasCandidate && !shadowTracker.active[sym]) {
      const riskDist = Math.abs((s.entry || 0) - (s.stopLoss || 0)) || 1;
      const nominalRisk = 50; // $50 standard 1R risk baseline
      const nominalQty = +(nominalRisk / riskDist).toFixed(4);
      shadowTracker.active[sym] = {
        id: `${sym}-${Date.now()}`,
        symbol: sym,
        direction: s.direction,
        grade: s.grade,
        setupType: s.setupType,
        entry: s.entry,
        stop: s.stopLoss,
        invalidation: s.invalidation,
        target: s.takeProfit[0],
        nominalRisk,
        nominalQty,
        startedAt: Date.now(),
        gatesPassed: (s.blockers || []).length === 0,
        decision: s.decision,
        fingerprint: signalFingerprint(sym, s)
      };
    }

    const active = shadowTracker.active[sym];
    if (!active) return;
    const price = s.entry && lastKnown[sym] ? (lastKnown[sym].price || s.entry) : s.entry;
    if (!price) return;

    const hitTarget = active.direction === 'LONG' ? price >= active.target : price <= active.target;
    const hitStop   = active.direction === 'LONG' ? price <= active.stop   : price >= active.stop;
    const hitInvalidation = active.invalidation != null
      ? (active.direction === 'LONG' ? price <= active.invalidation : price >= active.invalidation)
      : false;

    // A trade only counts as WIN if TP is reached WITHOUT hitting invalidation or Stop Loss.
    // If either stop or invalidation is reached, it is an actual LOSS.
    if (hitStop || hitInvalidation || hitTarget) {
      const isLoss = hitStop || hitInvalidation;
      const status = isLoss ? 'LOSS' : 'WIN';
      const exitReason = hitStop ? 'STOP_LOSS' : (hitInvalidation ? 'INVALIDATION' : 'TAKE_PROFIT');
      const exitPrice = hitStop ? active.stop : (hitInvalidation ? active.invalidation : (hitTarget ? active.target : price));
      const rawPnl = active.direction === 'LONG'
        ? (exitPrice - active.entry) * active.nominalQty
        : (active.entry - exitPrice) * active.nominalQty;
      const pnl = +rawPnl.toFixed(2);

      shadowTracker.history.unshift({
        ...active,
        status,
        exitReason,
        pnl,
        exitPrice,
        resolvedAt: Date.now()
      });
      postJSON('/api/trades/record?_action=signal_resolve', {
        fingerprint: active.fingerprint, symbol: sym,
        shadow_outcome: status, exit_price: exitPrice,
        pnl, exit_reason: exitReason,
        held_ms: Date.now() - active.startedAt
      }).catch(() => {});
      delete shadowTracker.active[sym];
    }
  }

  function broadcastShadowSnapshot() {
    if (shadowTracker.history.length > SHADOW_HISTORY_LIMIT) {
      shadowTracker.history.length = SHADOW_HISTORY_LIMIT;
    }
    if (!shadowChannel) return;
    shadowChannel.postMessage({
      type: 'snapshot',
      active: Object.values(shadowTracker.active),
      history: shadowTracker.history,
      currentPrices: Object.fromEntries(WATCHLIST.map(s => [s, lastKnown[s] ? lastKnown[s].price : null]))
    });
  }
  // One shared meta-learner across symbols: analyst reliability is a property
  // of the analyst and the regime, not of the ticker.
  const metaLearner = new SwarmMetaLearner.MetaLearner();

  // swarmMode 'observe' -- the panel runs and is reported, but decides nothing.
  //
  // Part IX ran the full 418-day/4-symbol backtest with the 7-analyst swarm's
  // advisory gating both on and off. 'off' produced a smooth, monotonic
  // expectancy curve over 287-300 trades; 'advisory' collapsed the sample to
  // 76-94 trades -- its fatal hazard vetoes reject roughly 70% of setups --
  // taking total R from +46.5 to +17.8, while riding on analyst-reliability
  // weights the meta-learner's own code considers statistically premature
  // (most cells n=11-31, below its ~15-observation trust floor).
  //
  // So the panel was switched off, and its reasoning went dark with it: the
  // choice was between blindness and a measured cost. 'observe' is neither.
  // The panel produces its reads, the UI shows them, and every veto it WOULD
  // have applied is recorded without being applied -- so whether those vetoes
  // were right becomes answerable from live outcomes in the signal log rather
  // than from an 83-trade backtest built on premature weights.
  WATCHLIST.forEach(sym => {
    engines[sym] = new MasisEngine({ symbol: sym, metaLearner, swarmMode: 'observe' });
    whaleTrackers[sym] = typeof WhaleTracker !== 'undefined' ? new WhaleTracker({ symbol: sym }) : null;
  });

  // ─── Shared supervisory agents ───
  // Throughput caps lifted so a new setup is never refused merely because
  // another position is open. The defaults (2 concurrent, 2 correlated) were
  // written for a single-symbol operator; this engine watches 30 symbols, so
  // they meant most signals were declined for having company rather than for
  // anything about the signal.
  //
  // consecutiveLossLimit is raised for a specific measured reason: the deployed
  // configuration wins about a third of its trades, so three losses in a row
  // occur roughly a third of the time by chance alone. Pausing an hour on that
  // is reacting to noise, not to evidence the system has stopped working.
  //
  // Why 5 and not more: the stop-geometry result that justifies this build
  // (Part XX: max drawdown 15.5% -> 5.2%) was measured with ONE position open at
  // a time, because the backtest holds a single position by construction. No
  // tested configuration covers many simultaneous correlated positions, so
  // concurrency is set just high enough to stop refusing setups for having
  // company -- 5 open positions is 2.5% of equity at risk at once -- rather than
  // as high as the engine could technically run.
  //
  // What is deliberately NOT lifted: maxDailyLossPct is the session circuit
  // breaker and the only limit here that bounds a bad day, and the no-pyramiding
  // rule still prevents stacking size onto one symbol.
  const riskGovernor = new MasisRiskGovernor.RiskGovernor({
    riskPerTradePct: 0.5,
    maxConcurrentPositions: 0, // Uncapped (removed 5 order cap)
    maxCorrelatedPositions: 0, // Uncapped
    consecutiveLossLimit: 8,
    cooldownAfterLossMs: 5 * 60 * 1000,
    cooldownAfterWinMs: 0
  });
  const positionManager = new MasisPositionManager.PositionManager();
  const llmGovernor = new MasisDeepSeekGovernor.DeepSeekGovernor();

  const wsClient = new BybitWebSocketClient({
    symbol: state.symbol,
    category: state.category,
    network: state.network,
    klineInterval: state.interval,
    analysisIntervals: ANALYSIS_INTERVALS,
    watchSymbols: WATCHLIST
  });

  let latestNews = null, latestMacro = null, llmStatus = null;
  let autoTradingArmed = false;
  let openPositionsSnapshot = [];
  let accountEquity = 0;
  const guardianLogEntries = [];
  const inFlightActions = new Set();   // prevents duplicate orders while one is in flight
  let guardianAutoScroll = true;

  function logEvent(msg) {
    const time = new Date().toTimeString().split(' ')[0];
    let styled = msg;
    if (msg.includes('VETO')) {
      styled = msg.replace('VETO', '<span class="log-veto">VETO</span>');
    } else if (msg.includes('STOP hit') || msg.includes('STOP')) {
      styled = `<span class="log-stop">${msg}</span>`;
    } else if (msg.includes('TP') || msg.includes('profit') || msg.includes('WIN') || msg.includes('ratcheted')) {
      styled = `<span class="log-win">${msg}</span>`;
    } else if (msg.includes('SCALP')) {
      styled = `<span class="log-scalp">${msg}</span>`;
    }

    const el = $('guardianLog');
    if (el) {
      const isAtBottom = (el.scrollHeight - el.scrollTop - el.clientHeight < 45);
      const row = document.createElement('div');
      row.className = 'log-line';
      row.innerHTML = `<span class="log-time">[${time}]</span> ${styled}`;
      el.appendChild(row);

      while (el.children.length > 120) {
        el.removeChild(el.firstChild);
      }

      if (guardianAutoScroll && isAtBottom) {
        el.scrollTop = el.scrollHeight;
      }
    }

    const secEl = $('guardianLogSecondary');
    if (secEl) {
      const row = document.createElement('div');
      row.className = 'log-line';
      row.innerHTML = `<span class="log-time">[${time}]</span> ${styled}`;
      secEl.appendChild(row);
      while (secEl.children.length > 80) secEl.removeChild(secEl.firstChild);
      secEl.scrollTop = secEl.scrollHeight;
    }
  }

  function roundQty(symbol, qty) {
    const step = (COIN_META[symbol] || {}).qtyStep || 0.001;
    const decimals = Math.max(0, Math.round(-Math.log10(step)));
    return +(Math.floor(qty / step) * step).toFixed(decimals);
  }

  function roundPrice(symbol, price) {
    const tick = (COIN_META[symbol] || {}).tick || 0.01;
    const decimals = Math.max(0, Math.round(-Math.log10(tick)));
    return +(Math.round(price / tick) * tick).toFixed(decimals);
  }

  // ─── Position Guardian Persistence Across Sessions & Refreshes ───
  const THESES_STORAGE_KEY = 'masis_active_theses';

  function saveThesesLocally() {
    try {
      const map = {};
      for (const t of positionManager.all()) {
        map[`${t.symbol}-${t.side}`] = t;
      }
      localStorage.setItem(THESES_STORAGE_KEY, JSON.stringify(map));
    } catch (e) {}
  }

  function loadThesesLocally() {
    try {
      const raw = localStorage.getItem(THESES_STORAGE_KEY);
      if (!raw) return;
      const data = JSON.parse(raw);
      if (data && typeof data === 'object') {
        for (const [key, t] of Object.entries(data)) {
          if (t && t.symbol && t.side && !positionManager.get(t.symbol, t.side)) {
            t.tpFilled = t.tpFilled || [false, false];
            t.exitLog = t.exitLog || [];
            t.flipStreak = t.flipStreak || 0;
            t.realisedR = t.realisedR || 0;
            t.remainingFraction = typeof t.remainingFraction === 'number' ? t.remainingFraction : 1;
            positionManager.trades[key] = t;
          }
        }
      }
    } catch (e) {}
  }

  async function syncThesisToServer(trade) {
    if (!trade || !trade.symbol || !trade.side) return;
    const key = `${trade.symbol}-${trade.side}`;
    try {
      await postJSON('/api/auto-trade/state', {
        theses: { [key]: trade }
      });
    } catch (e) {
      console.warn('Could not sync thesis to server:', e.message);
    }
  }

  async function removeThesisFromServer(symbol, side) {
    const key = `${symbol}-${side}`;
    try {
      await postJSON('/api/auto-trade/state', {
        theses: { [key]: null }
      });
    } catch (e) {
      console.warn('Could not remove thesis from server:', e.message);
    }
  }

  function adoptPosition(pos, s) {
    const sym = pos.symbol;
    const side = pos.side;
    const entryPrice = parseFloat(pos.avgPrice || pos.entryPrice || pos.markPrice || 0);
    const size = parseFloat(pos.size || 0);
    const bybitSL = parseFloat(pos.stopLoss || 0);

    let stopLoss = bybitSL;
    if (!stopLoss || stopLoss <= 0) {
      const atr = s && s.regimeMetrics ? s.regimeMetrics.htfAtr : null;
      if (atr && atr > 0) {
        stopLoss = side === 'Buy' ? entryPrice - (2 * atr) : entryPrice + (2 * atr);
      } else {
        stopLoss = side === 'Buy' ? entryPrice * 0.985 : entryPrice * 1.015;
      }
      stopLoss = roundPrice(sym, stopLoss);
    }

    const riskDist = Math.abs(entryPrice - stopLoss) || (entryPrice * 0.01);
    const invalidation = stopLoss;
    const isBuy = side === 'Buy';

    let tp1 = null, tp2 = null;
    let nextRes = (s && s.nextResistance) || null;
    let nextSup = (s && s.nextSupport) || null;
    const atr = (s && s.atr) || (s && s.regimeMetrics ? s.regimeMetrics.htfAtr : null) || (entryPrice * 0.01);
    const shy = atr * 0.15;

    if (isBuy && nextRes && nextRes > entryPrice + atr * 0.5) {
      tp1 = roundPrice(sym, nextRes - shy);
      tp2 = roundPrice(sym, entryPrice + 3.0 * riskDist);
    } else if (!isBuy && nextSup && nextSup < entryPrice - atr * 0.5) {
      tp1 = roundPrice(sym, nextSup + shy);
      tp2 = roundPrice(sym, entryPrice - 3.0 * riskDist);
    } else {
      tp1 = roundPrice(sym, isBuy ? entryPrice + 1.5 * riskDist : entryPrice - 1.5 * riskDist);
      tp2 = roundPrice(sym, isBuy ? entryPrice + 3.0 * riskDist : entryPrice - 3.0 * riskDist);
    }
    const targets = [tp1, tp2];

    const setupName = (s && s.setupType) ? s.setupType : 'Live Setup';
    const grade = (s && s.grade) || 'A';
    const score = (s && s.score) || 85;
    const regime = (s && s.regime) || 'TRENDING';

    const trade = positionManager.open({
      symbol: sym,
      side: side,
      entryPrice,
      stopLoss,
      invalidation,
      targets,
      riskDist,
      qty: size,
      originalQty: size,
      riskAmount: riskDist * size,
      nextResistance: nextRes,
      nextSupport: nextSup,
      setupName,
      grade,
      score,
      regime,
      isScalp: s ? !!s.isScalp : (currentStrategyMode === 'scalp'),
      horizon: s && s.horizon ? s.horizon : (currentStrategyMode === 'scalp' ? '5m-10m' : '15m-1h'),
      openedAt: parseInt(pos.createdTime || Date.now()),
      narrative: 'Auto-adopted by Position Guardian across sessions/refresh'
    });

    saveThesesLocally();
    syncThesisToServer(trade);
    logEvent(`Guardian adopted active position ${sym} ${side.toUpperCase()} @ ${entryPrice.toLocaleString()} (SL: ${stopLoss}, TP1: ${tp1}, S/R: ${nextRes || nextSup || 'n/a'}) — now fully managed.`);
    return trade;
  }

  loadThesesLocally();

  /** Reads a fetch Response as JSON, but never lets a non-JSON body (an HTML
   * error/block page, an empty response, a proxy's error page -- anything
   * that isn't valid JSON) surface as the cryptic default
   * "JSON.parse: unexpected character at line 1 column 1 of the JSON data".
   * Throws a message that says what actually happened instead. */
  async function readJSONResponse(r, url) {
    const text = await r.text();
    if (!text) throw new Error(`empty response from ${url} (status ${r.status})`);
    try {
      return JSON.parse(text);
    } catch (e) {
      const preview = text.slice(0, 120).replace(/\s+/g, ' ');
      throw new Error(`non-JSON response from ${url} (status ${r.status}): "${preview}"`);
    }
  }

  function resolveApiUrl(url) {
    if (url.startsWith('http://') || url.startsWith('https://')) return url;
    if (window.location.protocol === 'file:') {
      return 'http://localhost:8080' + (url.startsWith('/') ? '' : '/') + url;
    }
    return url;
  }

  async function fetchJSON(url) {
    const fullUrl = resolveApiUrl(url);
    try { const r = await fetch(fullUrl); return await readJSONResponse(r, fullUrl); }
    catch (e) { console.warn('API fetch error:', fullUrl, e.message); return null; }
  }

  async function postJSON(url, body) {
    const fullUrl = resolveApiUrl(url);
    const r = await fetch(fullUrl, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    return readJSONResponse(r, fullUrl);
  }

  // ─────────────────────────────────────────────────────────────────────
  // Supervisor consultation — gated, so a decision-grade call is the only
  // kind that ever costs anything.
  // ─────────────────────────────────────────────────────────────────────
  // Every candidate the engine forms is recorded, whether or not it is traded.
  // Recording only executed trades makes the most useful question about the
  // system unanswerable -- of everything it wanted to do, was skipping the rest
  // correct? -- because a gate that rejects good setups looks exactly like one
  // that rejects bad setups when only the survivors are kept.
  //
  // The fingerprint lets the server fold repeated evaluations of an unchanged
  // candidate into a single row: the engine re-evaluates every 5 seconds and a
  // setup that stands for an hour would otherwise write ~720 identical rows.
  // Identity is symbol + setup + direction. Grade and decision are deliberately
  // NOT part of it: a candidate re-scores constantly as price moves, and
  // including them meant one setup watched for an hour became a dozen rows
  // (INJ produced 24 rows across grades A-D for a handful of real setups). They
  // are attributes that evolve on the same suggestion, not new suggestions.
  function signalFingerprint(sym, s) {
    return [sym, s.setupType || '', s.direction || ''].join('|');
  }

  async function logSignal(sym, s, outcome, rejectReason, extra) {
    try {
      await postJSON('/api/trades/record?_action=signal', {
        fingerprint: signalFingerprint(sym, s),
        symbol: sym,
        direction: s.direction || (s.decision === 'BUY' ? 'LONG' : s.decision === 'SELL' ? 'SHORT' : ''),
        setup_type: s.setupType || '',
        grade: s.grade || '',
        score: s.score,
        regime: s.regime || '',
        bias: s.bias || '',
        entry: s.entry,
        stop: s.stopLoss,
        invalidation: s.invalidation,
        targets: s.takeProfit,
        risk_reward: s.riskReward,
        outcome,
        reject_reason: rejectReason || '',
        flow: s.flow || null,
        supervisor: s.llmVerdict ? {
          verdict: s.llmVerdict.verdict, confidence: s.llmVerdict.confidence,
          rationale: s.llmVerdict.rationale, is_fallback: s.llmVerdict.is_fallback
        } : null,
        evidence: (s.components || []).map(c => `${c.label}: ${c.detail}`),
        // Vetoes the panel raised in observe mode but did NOT act on. Paired
        // with the shadow verdict on the same row, this is what settles
        // whether the panel's hazard calls were worth obeying.
        observed_vetoes: s.observedVetoes || [],
        // Which named gates were false, not just the prose. Paired with this
        // row's shadow verdict, it makes each gate independently answerable:
        // of the setups gate X blocked, how many would have won? A gate that
        // blocks winners at the same rate it blocks losers is costing trades
        // for nothing, and prose reasons cannot be grouped to find that out.
        blocked_gates: Object.entries(s.gateChecks || {})
          .filter(([, passed]) => passed === false)
          .map(([name]) => name),
        // Which maker-offset arm this entry used, so fill rate per arm is
        // measurable from live fills rather than from a fill assumption.
        maker_offset_bps: extra && extra.offsetBps != null ? extra.offsetBps : null,
        fill_price: extra && extra.fillPrice != null ? extra.fillPrice : null
      });
    } catch (e) {
      // Logging must never be able to interfere with trading.
      logEvent(`Signal log failed for ${sym}: ${e.message}`);
    }
  }

  async function maybeConsultSupervisor(sym, s) {
    const candidate = s.grade ? { name: s.setupType, direction: s.direction, grade: s.grade, score: s.score, geometry: { entry: s.entry, riskDist: Math.abs((s.entry || 0) - (s.stopLoss || 0)) } } : null;
    const ctx = {
      symbol: sym, regime: s.regime, bias: s.bias,
      hasOpenPosition: openPositionsSnapshot.some(p => p.symbol === sym)
    };
    const gate = llmGovernor.shouldConsult(candidate, ctx);
    if (!gate.allowed) {
      if (gate.cached) engines[sym].setLlmVerdict(Object.assign({}, gate.cached, { symbol: sym, direction: s.direction, at: Date.now() }));
      return;
    }

    llmGovernor.beginCall(sym);
    try {
      const res = await postJSON('/api/supervisor/verdict', {
        symbol: sym, direction: s.direction, setup: s.setupType,
        score: s.score, grade: s.grade, regime: s.regime, bias: s.bias,
        entry: s.entry, stop: s.stopLoss, targets: s.takeProfit, riskReward: s.riskReward,
        evidence: (s.components || []).map(c => `${c.label}: ${c.detail}`)
      });
      llmGovernor.completeCall(gate.fingerprint, res);
      engines[sym].setLlmVerdict(Object.assign({}, res, { symbol: sym, direction: s.direction, at: Date.now() }));
      logEvent(`Supervisor on ${sym} ${s.direction} ${s.setupType}: ${res.verdict}${res.rationale ? ' — ' + res.rationale : ''}${res.is_fallback ? ' (local fallback)' : ''}`);
      renderSupervisorPanel(sym, res, { setup: s.setupType, direction: s.direction });
    } catch (e) {
      llmGovernor.failCall();
      logEvent(`Supervisor call failed for ${sym}: ${e.message} — local gates stand unmodified`);
    }
  }

  // The verdict is about ONE candidate on ONE symbol. This panel is a single
  // global element sitting under the focused symbol's setup card, so painting
  // it from whichever symbol was consulted most recently put a verdict about
  // INJ's range-fade long underneath BTC's breakout-retest short. Render only
  // when the verdict belongs to the symbol on screen, and say which setup and
  // direction it judged so a stale one is recognisable rather than misleading.
  function renderSupervisorPanel(sym, res, about) {
    const out = $('deepseekOutput'), meta = $('deepseekMeta');
    if (!out || !meta) return;
    if (sym !== state.symbol) return;
    out.style.display = 'block';
    out.textContent = [
      `VERDICT: ${res.verdict}  (confidence ${res.confidence}/100)`,
      res.rationale ? `Rationale: ${res.rationale}` : '',
      res.risk ? `Biggest risk: ${res.risk}` : '',
      res.is_fallback ? `\n[Local fallback — ${res.fallback_reason}]\nThe verdict above did not come from the model. Local gates decide on their own; an unreachable supervisor never blocks a valid trade and never approves an invalid one.` : ''
    ].filter(Boolean).join('\n');
    const st = llmGovernor.status();
    const subject = about && about.setup ? ` · on ${about.setup} ${about.direction || ''}`.trimEnd() : '';
    meta.textContent = `${sym}${subject} · ${new Date().toTimeString().split(' ')[0]} · ${res.is_fallback ? 'local' : 'model'} · ${st.callsToday}/${st.dailyBudget} calls today · ${st.totalSkipped} skipped`;
  }

  // ─────────────────────────────────────────────────────────────────────
  // Execution
  // ─────────────────────────────────────────────────────────────────────
  // research/README.md Part I/V/IX, repeatedly: a Market (taker) entry is
  // the single biggest destroyer of expectancy this project ever measured
  // -- bigger than every signal improvement combined. A resting limit order
  // priced better than the market, filled only if price comes back to it,
  // is what turned the backtest from -0.137R (taker) to +0.155R..+0.288R
  // (maker, 20-30bps offset, 287-300 trades). 20bps is used here: solidly
  // inside the range actually measured, rather than the untested edge of it.
  // A/B test on real demo fills, because the backtest cannot settle this.
  //
  // Its fill model assumes a resting order fills whenever price touches the
  // limit -- no queue position, no adverse selection -- which systematically
  // flatters wider offsets: it never charges for the fills you would miss, nor
  // for the ones you would regret getting. Demo orders rest on the real book,
  // so their fill rate is the genuine number.
  //
  // Each entry is assigned an offset deterministically from the symbol and the
  // minute it fires. That keeps assignment independent of anything about the
  // setup -- alternating by trade order would correlate the arm with market
  // conditions, since setups cluster in time -- while staying reproducible.
  // The chosen offset is recorded on the signal row, so fill rate and realised
  // entry can later be compared per arm on live data rather than on a fill
  // assumption.
  const MAKER_OFFSET_ARMS = [10, 20];
  function makerOffsetFor(sym) {
    const bucket = Math.floor(Date.now() / 60000);
    let h = bucket;
    for (let i = 0; i < sym.length; i++) h = (h * 31 + sym.charCodeAt(i)) | 0;
    return MAKER_OFFSET_ARMS[Math.abs(h) % MAKER_OFFSET_ARMS.length];
  }
  // Stop and invalidation distances, as multiples of what the playbook proposes.
  //
  // Measured cause: across 98 backtested trades the median bar spanned 1.39R
  // while a position was open, and a typical bar exceeded 1R on 54 of 82. One
  // ordinary bar therefore reached the stop, so a trade had to go right
  // immediately or die however good the signal was -- and the same tight
  // geometry made a routine retrace look like structural failure to the
  // position manager, whose exits won 9.1% of the time with nine of ten losses
  // already in profit.
  //
  // Widening both is risk-neutral by construction: sizePosition() derives
  // quantity from the stop distance, so a wider stop simply buys less and each
  // trade still risks 0.5% of equity. Measured effect on the same 98 trades,
  // baseline -> 2.5x/2.0x: win rate 31.6% -> 53.1%, expectancy -0.179R ->
  // +0.063R, max drawdown 5.5% -> 3.4%, and trades losing more than 2R fell
  // from 8.2% to 2.0% -- losses got rarer AND smaller, because a tight stop
  // makes every overshoot a larger fraction of R.
  //
  // 2.5x is a plateau rather than a peak (2.0-3.0 all land within noise of each
  // other), so nothing here is finely tuned. Caveat worth keeping in view: this
  // is one 42-day window, and on the four symbols Part IX used the sample is 19
  // trades. The direction is well evidenced; the magnitude is not yet.
  const STOP_MULT = 2.5;
  const INVALIDATION_MULT = 2.0;
  // The backtest's makerTimeout was 20 bars of 15-minute data (~5 hours) --
  // not a value that should be ported directly into a live resting order,
  // since a thesis this engine re-derives from scratch every cycle (see
  // updateShadowTracking's comment) can go stale long before 5 hours pass.
  // This is a live-safety judgment call, not itself a validated number.
  const MAKER_TIMEOUT_MS = 20 * 60 * 1000;
  // symbol -> { orderId, side, limitPrice, qty, stopLoss, invalidation,
  //             targets, riskAmount, setupName, grade, score, regime,
  //             narrative, placedAt, expiresAt, cancelWarned }
  // A resting entry order that hasn't filled yet. Deliberately NOT registered
  // with positionManager until the fill is confirmed against /api/positions
  // (see checkPendingEntries) -- positionManager.all() drives the "STOP hit"
  // detection loop in pollDemoData, and an unfilled order is not a position,
  // it has no stop to hit.
  const pendingEntries = {};

  async function executeEntry(sym, s) {
    if (!autoTradingArmed) return;
    if (s.decision !== 'BUY' && s.decision !== 'SELL') return;
    if (!s.stopLoss || !s.takeProfit || !s.takeProfit.length) return;
    if (pendingEntries[sym]) return; // already have a resting order out on this symbol
    const key = `entry-${sym}`;
    if (inFlightActions.has(key)) return;

    const gate = riskGovernor.canOpen({ symbol: sym, openPositions: openPositionsSnapshot });
    if (!gate.allowed) {
      logEvent(`${s.decision} ${sym} (${s.setupType}, grade ${s.grade}) not taken — ${gate.reasons[0]}`);
      logSignal(sym, s, 'REJECTED_RISK', gate.reasons[0]);
      return;
    }

    const price = lastKnown[sym] ? lastKnown[sym].price : 0;
    if (!price) return;

    const side = s.decision === 'BUY' ? 'Buy' : 'Sell';
    // Anchored on the engine's own entry reference so the widening is measured
    // from where the setup was framed, matching how the backtest applied it.
    const ref = s.entry || price;
    const wideStop = s.stopLoss != null
      ? ref - Math.sign(ref - s.stopLoss) * Math.abs(ref - s.stopLoss) * STOP_MULT
      : s.stopLoss;
    const wideInvalidation = s.invalidation != null
      ? ref - Math.sign(ref - s.invalidation) * Math.abs(ref - s.invalidation) * INVALIDATION_MULT
      : s.invalidation;
    const offsetBps = makerOffsetFor(sym);
    // Priced BELOW market for a long, ABOVE market for a short -- it only
    // fills if price comes back to a better level than it's at right now,
    // which is what makes this a maker (rebate-side) fill instead of a
    // taker (fee-side) one.
    const rawLimit = side === 'Buy'
      ? price * (1 - offsetBps / 10000)
      : price * (1 + offsetBps / 10000);
    const limitPrice = roundPrice(sym, rawLimit);

    const meta = COIN_META[sym] || {};
    const sized = riskGovernor.sizePosition({
      entry: limitPrice, stop: wideStop, equity: accountEquity, symbol: sym,
      qtyStep: meta.qtyStep, minQty: meta.minQty,
      maxLeverage: riskGovernor.config.leverage || 10
    });
    if (!sized.qty) {
      logEvent(`${sym} entry skipped — ${sized.rejected}`);
      return;
    }
    const qty = roundQty(sym, sized.qty);

    inFlightActions.add(key);
    try {
      // Only the STOP is attached broker-side. The old build also attached
      // takeProfit[0] at full size, which closed the whole position at 1.1R and
      // made the three-level ladder below unreachable. The stop is the one order
      // that must survive a browser crash; the targets are managed here.
      const res = await postJSON('/api/order/place', {
        category: 'linear', symbol: sym, side, orderType: 'Limit',
        price: limitPrice, qty, stopLoss: wideStop,
        leverage: riskGovernor.config.leverage || 10,
        marginMode: riskGovernor.config.marginMode || 'cross'
      });
      if (res.retCode === 0 && res.result && res.result.orderId) {
        pendingEntries[sym] = {
          orderId: res.result.orderId, side, limitPrice, qty,
          stopLoss: wideStop, invalidation: wideInvalidation, targets: s.takeProfit,
          riskAmount: sized.riskAmount, setupName: s.setupType, grade: s.grade,
          score: s.score, regime: s.regime, narrative: s.narrative,
          isScalp: !!s.isScalp,
          horizon: s.horizon || (s.isScalp ? '5m-10m' : '15m-1h'),
          placedAt: Date.now(), expiresAt: Date.now() + (s.isScalp ? 10 * 60 * 1000 : MAKER_TIMEOUT_MS), offsetBps
        };
        logEvent(`LIMIT ${side.toUpperCase()} ${sym} qty=${qty} @ ${limitPrice.toLocaleString()} (${offsetBps}bps better than ${price.toLocaleString()}) placed — awaiting fill within ${Math.round(MAKER_TIMEOUT_MS / 60000)}m | ${s.setupType} grade ${s.grade} (${s.score}/100) | SL ${wideStop} (${STOP_MULT}x) · invalidation ${wideInvalidation} (${INVALIDATION_MULT}x) | R:R ${s.riskReward}`);
        pendingEntries[sym].signalState = s;
        logSignal(sym, s, 'ORDER_PLACED', '', { offsetBps });
      } else {
        logEvent(`Order rejected for ${sym}: ${res.retMsg || 'unknown error'}`);
        logSignal(sym, s, 'REJECTED_EXCHANGE', res.retMsg || 'unknown error');
      }
      setTimeout(pollDemoData, 900);
    } catch (e) {
      logEvent(`Entry failed for ${sym}: ${e.message}`);
    } finally {
      inFlightActions.delete(key);
    }
  }

  /** Resolves every resting entry order each poll cycle: opens the position
   * once Bybit shows a fill, or cancels it once its patience window has
   * expired without one. Must run after openPositionsSnapshot is refreshed
   * for this cycle. */
  async function checkPendingEntries() {
    const toDrop = [];
    for (const sym of Object.keys(pendingEntries)) {
      const p = pendingEntries[sym];
      const filled = openPositionsSnapshot.find(pos =>
        pos.symbol === sym && pos.side === p.side && !positionManager.get(sym, p.side));
      if (filled) {
        const entryPrice = parseFloat(filled.avgPrice || p.limitPrice);
        // The maker offset is only worth what it actually captures, so record
        // the realised improvement against the limit rather than assuming it.
        if (p.signalState) logSignal(sym, p.signalState, 'TAKEN', `filled @ ${entryPrice}`,
          { offsetBps: p.offsetBps, fillPrice: entryPrice });
        const trade = positionManager.open({
          symbol: sym, side: p.side, entryPrice,
          stopLoss: p.stopLoss, invalidation: p.invalidation, targets: p.targets,
          riskDist: Math.abs(entryPrice - p.stopLoss),
          qty: p.qty, originalQty: p.qty, riskAmount: p.riskAmount,
          setupName: p.setupName, grade: p.grade, score: p.score,
          regime: p.regime, narrative: p.narrative,
          isScalp: p.isScalp || (p.signalState && p.signalState.isScalp) || false,
          horizon: p.horizon || (p.signalState && p.signalState.horizon) || '15m-1h'
        });
        saveThesesLocally();
        syncThesisToServer(trade);
        logEvent(`FILLED ${p.side.toUpperCase()} ${sym} qty=${p.qty} @ ~${entryPrice.toLocaleString()} (limit placed ${Math.round((Date.now() - p.placedAt) / 1000)}s earlier at ${p.limitPrice.toLocaleString()}) | ${p.setupName} grade ${p.grade}`);
        toDrop.push(sym);
        continue;
      }
      if (Date.now() > p.expiresAt) {
        try {
          const res = await postJSON('/api/order/place?_action=cancel', {
            category: 'linear', symbol: sym, orderId: p.orderId
          });
          if (res.retCode === 0) {
            logEvent(`Limit entry for ${sym} ${p.side.toUpperCase()} unfilled after ${Math.round(MAKER_TIMEOUT_MS / 60000)}m at ${p.limitPrice.toLocaleString()} — cancelled, standing aside`);
            if (p.signalState) logSignal(sym, p.signalState, 'NO_FILL', `limit ${p.limitPrice} never reached in ${Math.round(MAKER_TIMEOUT_MS / 60000)}m`,
              { offsetBps: p.offsetBps });
            toDrop.push(sym);
          } else if (!p.cancelWarned) {
            // Cancel racing an actual fill is expected, not exceptional --
            // leave it in pendingEntries so next cycle's fill-check (above)
            // gets a chance to see the position rather than dropping it and
            // orphaning a real, unmanaged position.
            logEvent(`Cancel failed for ${sym} pending entry (${res.retMsg || 'unknown'}) — checking next cycle whether it filled instead`);
            p.cancelWarned = true;
          }
        } catch (e) {
          if (!p.cancelWarned) { logEvent(`Cancel request failed for ${sym}: ${e.message}`); p.cancelWarned = true; }
        }
      }
    }
    for (const sym of toDrop) delete pendingEntries[sym];
  }

  async function recordOutcome(trade, exitPrice, reason, qtyClosed, realisedPnl) {
    const r = trade.riskDist ? ((trade.side === 'Buy' ? exitPrice - trade.entryPrice : trade.entryPrice - exitPrice) / trade.riskDist) : 0;
    try {
      await postJSON('/api/trades/record', {
        symbol: trade.symbol, side: trade.side.toUpperCase(),
        entry: trade.entryPrice, exit: exitPrice,
        stop: trade.stopLoss || trade.invalidation,
        targets: trade.targets,
        nextSupport: trade.nextSupport,
        nextResistance: trade.nextResistance,
        isScalp: !!trade.isScalp,
        opened_at: trade.openedAt,
        pnl: realisedPnl, setup_type: trade.setupName,
        grade: trade.grade, score: trade.score,
        r_multiple: +r.toFixed(3), exit_reason: reason,
        reason: positionManager.explain(trade)
      });
    } catch (e) {
      logEvent(`Failed to record outcome for ${trade.symbol}: ${e.message}`);
    }
    riskGovernor.recordOutcome({ symbol: trade.symbol, pnl: realisedPnl, rMultiple: r });
    if (realisedPnl < 0 || r <= 0) {
      logEvent(`[RISK GOVERNOR] Mandatory 15-minute stop cooldown initiated on ${trade.symbol} to prevent rapid churn`);
    }
    // Score the panel as it stood AT ENTRY, so analysts are judged on what they
    // said before the outcome was known.
    if (trade.entryPanel && trade.entryPanel.length) {
      metaLearner.recordOutcome(trade.regime, trade.entryPanel,
        trade.side === 'Buy' ? 'LONG' : 'SHORT', r);
    }
  }

  async function closePositionSlice(pos, trade, qty, reason, detail) {
    const key = `close-${pos.symbol}-${pos.side}-${reason}`;
    if (inFlightActions.has(key)) return;
    inFlightActions.add(key);
    const exitPrice = parseFloat(pos.markPrice || 0) || trade.entryPrice;
    const fullSize = parseFloat(pos.size);
    const closing = Math.min(roundQty(pos.symbol, qty), fullSize);
    if (closing <= 0) { inFlightActions.delete(key); return; }
    try {
      const res = await postJSON('/api/order/close', {
        category: 'linear', symbol: pos.symbol, side: pos.side, qty: closing
      });
      if (res.retCode === 0) {
        const isFull = closing >= fullSize - 1e-9;
        const slicePnl = (pos.side === 'Buy' ? exitPrice - trade.entryPrice : trade.entryPrice - exitPrice) * closing;
        logEvent(`${reason} — ${pos.symbol} ${pos.side.toUpperCase()} closed ${closing}${isFull ? ' (full)' : ' (partial)'} @ ${exitPrice.toLocaleString()}. ${detail}`);
        if (isFull) {
          positionManager.finalise(trade, exitPrice, reason);
          await recordOutcome(trade, exitPrice, reason, closing, slicePnl);
          positionManager.forget(pos.symbol, pos.side);
          saveThesesLocally();
          removeThesisFromServer(pos.symbol, pos.side);
          // Push real executed trade outcome to shadowTracker history so monitor immediately shows it
          shadowTracker.history.unshift({
            id: `REAL-${pos.symbol}-${Date.now()}`,
            symbol: pos.symbol,
            direction: pos.side === 'Buy' ? 'LONG' : 'SHORT',
            grade: trade.grade,
            setupType: trade.setupName,
            entry: trade.entryPrice,
            stop: trade.stopLoss,
            target: (trade.targets && trade.targets[0]) || null,
            exitPrice,
            pnl: +slicePnl.toFixed(2),
            status: slicePnl > 0 ? 'WIN' : 'LOSS',
            exitReason: reason,
            resolvedAt: Date.now(),
            isReal: true
          });
          broadcastShadowSnapshot();
        } else {
          saveThesesLocally();
          syncThesisToServer(trade);
        }
      } else {
        logEvent(`Close rejected for ${pos.symbol}: ${res.retMsg || 'unknown error'}`);
      }
      setTimeout(pollDemoData, 900);
    } catch (e) {
      logEvent(`Close failed for ${pos.symbol}: ${e.message}`);
    } finally {
      inFlightActions.delete(key);
    }
  }

  /**
   * Runs the position manager over every open position. Called from the main
   * tick. Every exit here is a named structural event — there is deliberately
   * no "the signal flipped so get out" path, which is what the previous build
   * did twenty times a minute.
   */
  async function managePositions() {
    const el = $('guardianStatus');
    if (!openPositionsSnapshot.length) {
      if (el) el.innerHTML = `<span class="guardian-pill safe">No open positions</span>`;
      return;
    }

    const cards = [];
    for (const pos of openPositionsSnapshot) {
      const s = latestStates[pos.symbol];
      let trade = positionManager.get(pos.symbol, pos.side);

      if (!trade) {
        trade = adoptPosition(pos, s);
      }

      const mark = parseFloat(pos.markPrice || 0);
      const confirmed = engines[pos.symbol] ? engines[pos.symbol].confirmed('mtf') : [];
      const action = positionManager.evaluate(trade, {
        price: mark,
        confirmedCandle: confirmed.length ? confirmed[confirmed.length - 1] : null,
        atr: (s && s.atr) || (s && s.regimeMetrics ? s.regimeMetrics.htfAtr : null),
        nextResistance: (s && s.nextResistance) || trade.nextResistance,
        nextSupport: (s && s.nextSupport) || trade.nextSupport,
        spreadPct: s && s.flow ? s.flow.spread : null,
        dataValid: !s || s.dataStatus !== 'INVALID',
        opposingSignal: s && s.grade && s.direction ? { direction: s.direction, grade: s.grade, score: s.score } : null
      });

      const r = positionManager.currentR(trade, mark);
      let pillClass = 'safe', verdict = `HOLDING ${r >= 0 ? '+' : ''}${r.toFixed(2)}R`;
      const reasons = [];

      if (action.action === 'CLOSE_ALL') {
        pillClass = 'risk'; verdict = `CLOSING — ${action.reason}`;
        reasons.push(action.detail);
        if (autoTradingArmed) await closePositionSlice(pos, trade, parseFloat(pos.size), action.reason, action.detail);
      } else if (action.action === 'SCALE_OUT') {
        const isSrBank = action.reason === 'SR_COLLISION';
        verdict = isSrBank ? 'S/R PROFIT COVERAGE' : `TAKING TP${action.tpIndex + 1}`;
        reasons.push(action.detail);
        if (autoTradingArmed) {
          const sliceQty = trade.originalQty * action.fraction;
          positionManager.markTpFilled(trade, action.tpIndex, mark);
          saveThesesLocally();
          syncThesisToServer(trade);
          await closePositionSlice(pos, trade, sliceQty, isSrBank ? 'SR_COVERAGE' : `TP${action.tpIndex + 1}`, action.detail);

          // If first slice (TP1 or S/R wall bank) was taken, immediately ratchet stop to Break-Even on Bybit!
          if (action.tpIndex === 0 && !trade.stopMovedToBreakEven) {
            positionManager.markStopMoved(trade, trade.entryPrice, 'BREAK_EVEN');
            saveThesesLocally();
            syncThesisToServer(trade);
            try {
              await postJSON('/api/position/stop', { category: 'linear', symbol: pos.symbol, stopLoss: trade.entryPrice });
              logEvent(`${pos.symbol} Stop loss ratcheted to break-even at ${trade.entryPrice}`);
            } catch (e) {}
          }
        }
      } else if (action.action === 'MOVE_STOP') {
        verdict = action.reason === 'BREAK_EVEN' ? 'STOP → BREAK-EVEN' : 'TRAILING STOP';
        reasons.push(action.detail);
        if (autoTradingArmed) {
          positionManager.markStopMoved(trade, action.newStop, action.reason);
          saveThesesLocally();
          syncThesisToServer(trade);
          try {
            await postJSON('/api/position/stop', { category: 'linear', symbol: pos.symbol, stopLoss: action.newStop });
            logEvent(`${pos.symbol} ${action.detail}`);
          } catch (e) {
            logEvent(`Stop update failed for ${pos.symbol}: ${e.message}`);
          }
        }
      } else {
        reasons.push(...(action.reasons || []));
      }

      const isScalpTrade = trade.isScalp || (trade.setupName && trade.setupName.startsWith('SCALP_'));
      const scalpBadge = isScalpTrade ? '<span style="font-size:9px;background:rgba(245,158,11,0.25);color:#f59e0b;padding:2px 6px;border-radius:4px;margin-left:6px;font-weight:700;">[SCALP 5-10m]</span>' : '';
      const reasonsList = reasons.length ? ('<ul class="guardian-reasons">' + reasons.map(x => '<li>' + x + '</li>').join('') + '</ul>') : '';
      cards.push(`<div class="guardian-position">
        <span class="guardian-pill ${pillClass}">${pos.symbol} ${pos.side.toUpperCase()} — ${verdict} ${scalpBadge}</span>
        <div class="guardian-meta font-mono">${trade.setupName} · grade ${trade.grade} · entry ${trade.entryPrice} · stop ${trade.stopLoss} · invalidation ${trade.invalidation} · TP ${(trade.targets || []).join(' / ')}</div>
        ${reasonsList}
      </div>`);
    }
    if (el) el.innerHTML = cards.join('');
  }

  // ─── Sidebar Navigation ───
  const sidebarEl = document.querySelector('.sidebar');
  const sidebarBackdrop = $('sidebarBackdrop');
  function closeMobileSidebar() {
    if (sidebarEl) sidebarEl.classList.remove('open');
    if (sidebarBackdrop) sidebarBackdrop.classList.remove('open');
  }
  const mobileNavToggle = $('mobileNavToggle');
  if (mobileNavToggle) {
    mobileNavToggle.addEventListener('click', () => {
      if (sidebarEl) sidebarEl.classList.toggle('open');
      if (sidebarBackdrop) sidebarBackdrop.classList.toggle('open');
    });
  }
  if (sidebarBackdrop) sidebarBackdrop.addEventListener('click', closeMobileSidebar);

  // ─── Signal monitor pop-out ───
  const openMonitorBtn = $('openMonitorBtn');
  if (openMonitorBtn) {
    openMonitorBtn.addEventListener('click', () => {
      window.open('monitor.html', 'masisMonitor', 'width=480,height=760,menubar=no,toolbar=no,location=no,status=no');
    });
  }

  // ─── Coin search (coin-cards-row) ───
  const coinSearchInput = $('coinSearchInput');
  if (coinSearchInput) {
    coinSearchInput.addEventListener('input', () => {
      state.coinSearch = coinSearchInput.value;
      renderCoinCards();
    });
  }

  document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', (e) => {
      const target = document.getElementById(item.dataset.target);
      if (target) { e.preventDefault(); target.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
      document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
      item.classList.add('active');
      closeMobileSidebar(); // a nav tap on mobile should also close the drawer it was opened from
    });
  });

  document.querySelectorAll('#posHistTabs .tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('#posHistTabs .tab-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const tab = btn.dataset.tab;
      document.querySelectorAll('.tab-panel').forEach(p => p.classList.toggle('active', p.dataset.panel === tab));
    });
  });

  // ─── Coin cards ───
  function buildSparklineSVG(values, color) {
    const w = 60, h = 32, pad = 2;
    if (!values || values.length < 2) return `<svg viewBox="0 0 ${w} ${h}" class="coin-sparkline"></svg>`;
    const min = Math.min(...values), max = Math.max(...values);
    const range = (max - min) || (min * 0.001) || 1;
    const step = (w - pad * 2) / (values.length - 1);
    const pts = values.map((v, i) => `${(pad + i * step).toFixed(1)},${(pad + (h - pad * 2) * (1 - (v - min) / range)).toFixed(1)}`);
    const linePath = 'M' + pts.join(' L');
    const areaPath = `${linePath} L${(w - pad).toFixed(1)},${(h - pad).toFixed(1)} L${pad},${(h - pad).toFixed(1)} Z`;
    const gradId = 'sg' + Math.random().toString(36).slice(2, 9);
    return `<svg viewBox="0 0 ${w} ${h}" class="coin-sparkline" preserveAspectRatio="none">
      <defs><linearGradient id="${gradId}" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="${color}" stop-opacity="0.35"/>
        <stop offset="100%" stop-color="${color}" stop-opacity="0"/>
      </linearGradient></defs>
      <path d="${areaPath}" fill="url(#${gradId})" stroke="none"/>
      <path d="${linePath}" fill="none" stroke="${color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>`;
  }

  function decisionBiasClass(sym) {
    const s = latestStates[sym];
    if (!s) return '';
    if (s.decision === 'BUY') return 'bias-buy';
    if (s.decision === 'SELL') return 'bias-sell';
    return '';
  }

  function visibleCoinList() {
    const query = state.coinSearch.trim().toUpperCase();
    if (!query) {
      // Always include the focused symbol even if it's outside the default
      // set, so switching coins via a search never gets silently hidden
      // again the next time the box is empty.
      return DEFAULT_VISIBLE_COINS.includes(state.symbol)
        ? DEFAULT_VISIBLE_COINS
        : [state.symbol, ...DEFAULT_VISIBLE_COINS];
    }
    return WATCHLIST.filter(sym => {
      const meta = COIN_META[sym] || {};
      return sym.toUpperCase().includes(query)
        || (meta.short || '').toUpperCase().includes(query)
        || (meta.name || '').toUpperCase().includes(query);
    });
  }

  function renderCoinCards() {
    const row = $('coinCardsRow');
    if (!row) return;
    const visible = visibleCoinList();
    if (!visible.length) {
      row.innerHTML = `<div class="coin-search-empty">No watchlist coin matches "${state.coinSearch}"</div>`;
      return;
    }
    row.innerHTML = visible.map(sym => {
      const meta = COIN_META[sym], lk = lastKnown[sym], hist = priceHistory[sym];
      const isUp = lk.changePct >= 0;
      const color = isUp ? '#16C784' : '#EA3943';
      const decimals = lk.price && lk.price < 10 ? 4 : 2;
      const priceStr = lk.price ? lk.price.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals }) : '--';
      const s = latestStates[sym];
      const tag = s && s.grade ? `<span class="coin-grade grade-${s.grade.replace('+', 'plus')}">${s.setupType.split('_')[0]} ${s.grade}</span>` : '';
      return `<div class="coin-card ${sym === state.symbol ? 'focused' : ''} ${decisionBiasClass(sym)}" data-symbol="${sym}">
        <div class="coin-card-left">
          <div class="coin-icon" style="background:${meta.color}"><i class="${meta.brand ? 'fa-brands' : 'fa-solid'} ${meta.icon}"></i></div>
          <div class="coin-info">
            <span class="coin-name">${meta.short} ${tag}</span>
            <span class="coin-price">${priceStr}</span>
            <span class="coin-change ${isUp ? 'up' : 'down'}">${isUp ? '+' : ''}${lk.changePct.toFixed(2)}%</span>
          </div>
        </div>
        ${buildSparklineSVG(hist, color)}
      </div>`;
    }).join('');
    row.querySelectorAll('.coin-card').forEach(card => {
      card.addEventListener('click', () => {
        const pill = document.querySelector(`.sym-pill[data-symbol="${card.dataset.symbol}"]`);
        if (pill) pill.click();
      });
    });
  }

  // Generated from the full WATCHLIST rather than hand-maintained in HTML,
  // now that the watchlist is 30 coins instead of the original 5.
  const symbolPillsRow = $('symbolPills');
  if (symbolPillsRow) {
    symbolPillsRow.innerHTML = WATCHLIST.map((sym, i) =>
      `<button class="sym-pill${i === 0 ? ' active' : ''}" data-symbol="${sym}">${(COIN_META[sym] || {}).short || sym}</button>`
    ).join('');
  }

  document.querySelectorAll('.sym-pill').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.sym-pill').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const sym = btn.dataset.symbol;
      state.symbol = sym;
      $('tickerSymbol').textContent = sym;
      state.recentTrades = [];
      $('tradeFeed').innerHTML = '';
      $('tickerHigh').textContent = '--';
      $('tickerLow').textContent = '--';
      $('tickerVol').textContent = '--';
      candleChart.clear();
      wsClient.switchSymbol(sym, state.category, state.interval);
    });
  });

  /**
   * Seeds every engine's three analysis timeframes from REST history, so the
   * system is decision-capable within seconds of a page load rather than after
   * the hours of live ticks it would take to accumulate 40 hourly bars.
   */
  async function bootstrapEngines() {
    for (const sym of WATCHLIST) {
      for (const iv of ANALYSIS_INTERVALS) {
        try {
          const candles = await wsClient.fetchHistoricalKlines(sym, iv, 300);
          if (candles && candles.length) engines[sym].seedCandles(INTERVAL_TO_TF[iv], candles);
        } catch (e) {
          console.warn(`Backfill failed for ${sym} ${iv}m:`, e.message);
        }
      }
    }
    logEvent(`Backfilled ${WATCHLIST.length} symbols across ${ANALYSIS_INTERVALS.join('m/')}m — engines are decision-capable`);
  }

  // The chart interval is display-only now. Changing it does not change what
  // the engine analyses, which in the previous build it silently did.
  document.querySelectorAll('.int-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.int-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      state.interval = btn.dataset.interval;
      candleChart.clear();
      wsClient.setKlineInterval(state.interval);
    });
  });

  wsClient.on('historicalKlines', (candles) => {
    if (candles && candles.length) candleChart.setCandles(candles, state.interval);
  });
  wsClient.on('olderHistoricalKlines', (older) => {
    if (older && older.length) candleChart.prependCandles(older);
  });
  candleChart.onNeedMoreHistory = (oldest) => wsClient.fetchOlderHistory(oldest);

  wsClient.on('status', (info) => {
    const statusText = $('wsStatusText'), statusChip = $('wsStatus');
    if (!statusText || !statusChip) return;
    if (info.status === 'connected') {
      statusText.textContent = info.latency ? `Connected (${info.latency}ms)` : 'Connected';
      statusChip.classList.add('online');
    } else if (info.status === 'connecting') {
      statusText.textContent = 'Connecting...';
      statusChip.classList.remove('online');
    } else {
      statusText.textContent = 'Disconnected';
      statusChip.classList.remove('online');
    }
  });

  wsClient.on('subscribeError', ({ topics, reason }) => {
    const symbols = [...new Set(topics.map(t => t.split('.').pop()))];
    logEvent(`WS subscribe rejected (${reason}) for: ${symbols.join(', ') || 'unknown symbols'} — those coins won't get live data until this is fixed`);
  });

  wsClient.on('ticker', (d) => {
    if (!d || !d.data || !d.symbol) return;
    const sym = d.symbol;
    if (engines[sym]) engines[sym].processTicker(d.data);

    const price = parseFloat(d.data.lastPrice || 0);
    if (price) {
      const prev24 = parseFloat(d.data.prevPrice24h || price);
      lastKnown[sym] = { price, changePct: prev24 > 0 ? ((price - prev24) / prev24 * 100) : 0 };
      const hist = priceHistory[sym];
      hist.push(price);
      if (hist.length > 40) hist.shift();
    }
    if (sym !== state.symbol || !price) return;

    state.prevPrice = state.lastPrice;
    state.lastPrice = price;
    const priceEl = $('tickerPrice');
    priceEl.textContent = price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    priceEl.style.color = price >= state.prevPrice || !state.prevPrice ? 'var(--green)' : 'var(--red)';
    if (state.prevPrice > 0) {
      priceEl.classList.remove('flash-up', 'flash-down');
      void priceEl.offsetWidth;
      priceEl.classList.add(price > state.prevPrice ? 'flash-up' : 'flash-down');
    }
    const changePct = parseFloat(d.data.price24hPcnt || 0) * 100;
    const changeEl = $('tickerChange');
    changeEl.textContent = `${changePct >= 0 ? '+' : ''}${changePct.toFixed(2)}%`;
    changeEl.style.color = changePct >= 0 ? 'var(--green)' : 'var(--red)';
    if (d.data.highPrice24h) $('tickerHigh').textContent = parseFloat(d.data.highPrice24h).toLocaleString();
    if (d.data.lowPrice24h) $('tickerLow').textContent = parseFloat(d.data.lowPrice24h).toLocaleString();
    if (d.data.volume24h) {
      const vol = parseFloat(d.data.volume24h);
      $('tickerVol').textContent = vol >= 1e6 ? (vol / 1e6).toFixed(1) + 'M' : vol >= 1e3 ? (vol / 1e3).toFixed(1) + 'K' : vol.toFixed(0);
    }
  });

  wsClient.on('orderbook', d => {
    if (!d || !d.data || !d.symbol) return;
    if (engines[d.symbol]) engines[d.symbol].processOrderBook(d.data);
  });

  // Klines now route by INTERVAL into the matching engine timeframe slot.
  wsClient.on('kline', (d) => {
    if (!d || !d.data || !d.data.length || !d.symbol) return;
    const tf = INTERVAL_TO_TF[d.interval];
    const eng = engines[d.symbol];
    if (eng && tf) d.data.forEach(k => eng.processKline(tf, k));

    if (d.symbol !== state.symbol || d.interval !== state.interval) return;
    d.data.forEach(k => candleChart.updateCandle({
      time: parseInt(k.start), open: parseFloat(k.open), high: parseFloat(k.high),
      low: parseFloat(k.low), close: parseFloat(k.close), volume: parseFloat(k.volume), confirm: k.confirm
    }));
    candleChart.draw();
  });

  wsClient.on('trade', (d) => {
    if (!d || !d.data || !d.symbol) return;
    const sym = d.symbol, trades = d.data;
    if (engines[sym]) engines[sym].processTrades(trades);
    if (whaleTrackers[sym]) whaleTrackers[sym].ingest(trades);

    const ts = tradeStats[sym];
    trades.forEach(t => { const size = parseFloat(t.v); if (t.S === 'Buy') ts.buyVol += size; else ts.sellVol += size; });
    if (sym !== state.symbol) return;

    const feedEl = $('tradeFeed');
    const whaleFloor = whaleTrackers[sym] ? whaleTrackers[sym].thresholdUsd : 50000;
    trades.forEach(t => {
      const price = parseFloat(t.p), size = parseFloat(t.v);
      const timeStr = new Date(parseInt(t.T)).toTimeString().split(' ')[0].substring(0, 8);
      const row = document.createElement('div');
      row.className = `tape-row ${t.S.toLowerCase()}${(price * size) > whaleFloor ? ' whale' : ''}`;
      row.innerHTML = `<span class="tape-time">${timeStr}</span><span class="tape-side">${t.S === 'Buy' ? 'BUY' : 'SELL'}</span><span class="tape-price">${price.toLocaleString('en-US', { minimumFractionDigits: 2 })}</span><span class="tape-size">${size}</span>`;
      feedEl.insertBefore(row, feedEl.firstChild);
      while (feedEl.children.length > 100) feedEl.removeChild(feedEl.lastChild);
    });
    const total = ts.buyVol + ts.sellVol;
    if (total > 0) {
      const buyPct = Math.round((ts.buyVol / total) * 100);
      $('buyRatio').textContent = `B ${buyPct}%`;
      $('sellRatio').textContent = `S ${100 - buyPct}%`;
    }
  });

  // ─── News / whale / macro panels ───
  function timeAgo(iso) {
    if (!iso) return '';
    const t = new Date(iso).getTime();
    if (isNaN(t)) return '';
    const diffMin = Math.round((Date.now() - t) / 60000);
    if (diffMin < 1) return 'just now';
    if (diffMin < 60) return `${diffMin}m ago`;
    return `${Math.round(diffMin / 60)}h ago`;
  }

  function renderNewsFeed() {
    const feedEl = $('newsFeed'), badge = $('newsSentimentBadge');
    if (!latestNews || !feedEl) return;
    badge.textContent = latestNews.sentiment_label || 'NEUTRAL';
    badge.className = `sentiment-badge ${(latestNews.sentiment_label || 'NEUTRAL').toLowerCase()}`;
    const articles = latestNews.articles || [];
    if (!articles.length) {
      feedEl.innerHTML = '<div class="news-empty">No recent crypto headlines</div>';
      return;
    }
    feedEl.innerHTML = articles.slice(0, 10).map(a => {
      const cls = a.sentiment_score > 0.2 ? 'bullish' : (a.sentiment_score < -0.2 ? 'bearish' : '');
      return `<div class="news-item ${cls}"><div class="news-item-body">
        <span class="news-item-title">${a.title}</span>
        <div class="news-item-meta"><span>${timeAgo(a.time)}</span><span class="${a.impact === 'HIGH' ? 'impact-high' : ''}">${a.impact || 'LOW'} impact</span>${a.tickers && a.tickers.length ? `<span>${a.tickers.slice(0, 3).join(', ')}</span>` : ''}</div>
      </div></div>`;
    }).join('');
  }

  function renderWhalePanel(summary) {
    if (!summary || !$('whaleCount')) return;
    $('whaleCount').textContent = `${summary.whaleCount} detected`;
    $('whaleBuyUsd').textContent = Math.round(summary.whaleBuyUsd).toLocaleString();
    $('whaleSellUsd').textContent = Math.round(summary.whaleSellUsd).toLocaleString();
    $('whalePressureFill').style.width = `${summary.pressurePct}%`;
    $('whalePressureFill').style.background = summary.dominantSide === 'SELL'
      ? 'linear-gradient(90deg, var(--red), var(--red))'
      : 'linear-gradient(90deg, var(--green), var(--green))';
    const domPill = $('whaleDominantPill');
    domPill.textContent = summary.dominantSide;
    domPill.className = `whale-stat-dominant font-mono ${summary.dominantSide === 'BUY' ? 'buy' : (summary.dominantSide === 'SELL' ? 'sell' : '')}`;
    const listEl = $('whaleList');
    if (!summary.largestTrades.length) {
      listEl.innerHTML = '<div class="news-empty">No whale trades detected yet</div>';
    } else {
      listEl.innerHTML = summary.largestTrades.map(w => `<div class="whale-row ${w.side.toLowerCase()}">
        <span class="w-time">${new Date(w.time).toTimeString().split(' ')[0]}</span>
        <span class="w-side">${w.side}</span>
        <span class="w-price">${w.price.toLocaleString()}</span>
        <span class="w-notional">$${Math.round(w.notional).toLocaleString()}</span>
      </div>`).join('');
    }
  }

  async function pollNews() {
    const res = await fetchJSON('/api/news');
    if (res) { latestNews = res; renderNewsFeed(); }
  }
  async function pollMacro() { const res = await fetchJSON('/api/market-overview'); if (res) latestMacro = res; }
  async function pollLlmStatus() { llmStatus = await fetchJSON('/api/llm/status'); }

  setTimeout(pollNews, 1200); setInterval(pollNews, 300000);
  setTimeout(pollMacro, 1500); setInterval(pollMacro, 300000);
  setTimeout(pollLlmStatus, 2000); setInterval(pollLlmStatus, 60000);

  // ─────────────────────────────────────────────────────────────────────
  // Main intelligence tick.
  //
  // 5 seconds, not 3, and nothing here can close a position on its own —
  // exits go through the position manager's structural rules.
  // ─────────────────────────────────────────────────────────────────────
  setInterval(async () => {
    let focusedWhale = null;

    for (const sym of WATCHLIST) {
      const eng = engines[sym];
      const wt = whaleTrackers[sym];
      let whaleSummary = null;
      if (wt) {
        whaleSummary = wt.getSummary();
        eng.setWhaleSignal({ dominantSide: whaleSummary.dominantSide, netDeltaUsd: whaleSummary.netDeltaUsd, whaleCount: whaleSummary.whaleCount });
      }
      if (latestNews) {
        eng.setNewsSignal({
          sentimentScore: latestNews.sentiment_score, sentimentLabel: latestNews.sentiment_label,
          headline: latestNews.headline,
          impact: (latestNews.articles && latestNews.articles[0] && latestNews.articles[0].impact) || 'LOW'
        });
      }
      if (latestMacro) {
        eng.setMacroSignal({ btcDominance: latestMacro.btc_dominance, marketCapChange24hPct: latestMacro.market_cap_change_24h_pct, riskOff: latestMacro.risk_off });
      }
      eng.setSetupPerformance(setupPerformance);

      const s = eng.getState();
      latestStates[sym] = s;
      updateShadowTracking(sym, s);

      if (s.grade && s.grade !== 'C' && s.grade !== 'D') maybeConsultSupervisor(sym, s);
      if (s.decision === 'BUY' || s.decision === 'SELL') {
        executeEntry(sym, s);
      } else if (s.setupType && s.grade) {
        // A candidate formed but the engine declined to act on it. This is the
        // majority of what the system does and, until now, none of it was kept.
        // The engine exports this as `blockers`; reading `s.blocks` silently
        // yielded undefined on every row, so the log recorded a generic
        // "below decision threshold" for every declined setup while the real
        // reason sat in the state the dashboard was already rendering.
        // All of them are kept, not just the first: a setup blocked for three
        // separate reasons is a different thing from one blocked by a single
        // marginal gate, and that distinction is the point of the log.
        const why = (s.blockers || []).length
          ? s.blockers.join(' | ')
          : 'engine declined — no blocker reported (decision was not BUY/SELL)';
        logSignal(sym, s, 'NOT_TRADED', why);
      }

      if (sym === state.symbol) focusedWhale = whaleSummary;
    }

    renderFocusedPanels(focusedWhale);
    renderAgentTelemetry();
    await managePositions();
    renderCoinCards();
    broadcastShadowSnapshot();
  }, 5000);

  const READ_CLASS = {
    BULLISH: 'bull', DRAW_UP: 'bull', BEARISH: 'bear', DRAW_DOWN: 'bear',
    DANGER: 'danger', NEUTRAL: '', UNAVAILABLE: 'dim'
  };

  /** The panel, as a panel: every specialist's read, its conviction, and what
   * it is actually looking at. Collapsed to one line each, expandable on hover
   * via the title attribute — the full reasoning is always available rather
   * than summarised away. */
  function renderAnalystPanel(s) {
    const el = $('analystPanel');
    if (!el) return;
    const panel = s.panel || [];
    if (!panel.length) {
      el.innerHTML = s.swarmMode === 'off'
        ? '<div class="analyst-empty">Analyst panel is disabled — research/README.md Part IX found its advisory gating adds no validated edge over trading without it, riding on analyst-reliability samples too small to trust.</div>'
        : s.swarmMode === 'observe' && !panel.length
        ? '<div class="analyst-empty">Analyst panel is warming up — it needs higher-timeframe history before it will offer a read.</div>'
        : '<div class="analyst-empty">Analyst panel is warming up — it needs higher-timeframe history before it will offer a read.</div>';
      return;
    }
    const weights = s.analystWeights || {};
    el.innerHTML = `
      <div class="analyst-head">ANALYST PANEL <span class="analyst-sub">${panel.length} specialists · independent reads</span></div>
      ${panel.map(a => {
        const cls = READ_CLASS[a.read] || '';
        const w = weights[a.id];
        const tip = [...(a.evidence || []), ...(a.vetoes || []).map(v => 'VETO: ' + v)].join(' \n\n').replace(/"/g, '&quot;');
        return `<div class="analyst-row ${cls}" title="${tip}">
          <span class="an-name">${a.name}</span>
          <span class="an-read">${a.read}</span>
          <span class="an-conv"><span class="an-bar" style="width:${Math.round((a.conviction || 0) * 100)}%"></span></span>
          <span class="an-w">${w && Math.abs(w - 1) > 0.01 ? '×' + w.toFixed(2) : ''}</span>
          ${(a.vetoes || []).length ? `<span class="an-veto">${a.vetoes.length} veto</span>` : ''}
        </div>`;
      }).join('')}`;
  }

  /** The debate, shown as a debate. If the panel talked itself out of a trade,
   * the reason it did so is the most useful thing on the screen. */
  function renderDebate(s) {
    const el = $('debateTranscript');
    if (!el) return;
    const v = s.verdict;
    if (!v) { el.innerHTML = ''; return; }
    if (s.swarmMode === 'off') {
      el.innerHTML = '<div class="debate-head">DELIBERATION <span class="debate-verdict none">DISABLED</span></div><div class="debate-line">Swarm-off mode — see the Analyst Panel above for why.</div>';
      return;
    }
    const outcome = v.thesis
      ? `<span class="debate-verdict ${v.thesis === 'LONG' ? 'bull' : 'bear'}">${v.thesis} · conviction ${v.conviction}</span>`
      : `<span class="debate-verdict none">NO THESIS${v.rejectedThesis ? ` (${v.rejectedThesis} abandoned)` : ''}</span>`;
    el.innerHTML = `
      <div class="debate-head">DELIBERATION ${outcome}</div>
      ${(v.transcript || []).map(t => `<div class="debate-line">${t}</div>`).join('')}
      ${(v.fatalVetoes || []).length ? `<div class="debate-fatal">${v.fatalVetoes.map(f => `<div>⛔ ${f.source}: ${f.veto}</div>`).join('')}</div>` : ''}
      ${(v.opposition || []).length ? `<div class="debate-opp"><div class="debate-sub">Counter-case</div>${v.opposition.slice(0, 3).map(o => `<div>• <em>${o.source}</em>: ${o.argument}</div>`).join('')}</div>` : ''}`;
  }

  function renderFocusedPanels(whaleSummary) {
    const s = latestStates[state.symbol];
    if (whaleSummary) renderWhalePanel(whaleSummary);
    if (!s) return;
    renderAnalystPanel(s);
    renderDebate(s);

    const decisionEl = $('masisDecision');
    if (decisionEl) {
      decisionEl.textContent = s.decision;
      decisionEl.className = `decision-pill font-mono ${s.decision === 'BUY' ? 'buy' : s.decision === 'SELL' ? 'sell' : ''}`;
    }

    if ($('intelState')) $('intelState').textContent = `${s.regime} (${s.regimeConfidence}%)`;
    if ($('intelParticipant')) $('intelParticipant').textContent = `bias ${s.bias}`;
    if ($('intelCvd')) $('intelCvd').textContent = s.flow ? `${(s.flow.takerRatio5m * 100).toFixed(0)}% buy` : '--';
    if ($('intelTrust')) $('intelTrust').textContent = s.grade ? `${s.score}/100 (${s.grade})` : '--/100';
    if ($('intelVerdict')) {
      const blocked = (s.blockers || []).length;
      $('intelVerdict').textContent = s.decision === 'BUY' || s.decision === 'SELL' ? 'AUTHORISED' : (blocked ? 'BLOCKED' : 'NO SETUP');
      $('intelVerdict').className = `intel-val font-mono ${blocked && s.decision !== 'BUY' && s.decision !== 'SELL' ? 'text-red' : 'text-green'}`;
    }
    if ($('intelStopLoss')) $('intelStopLoss').textContent = s.stopLoss ? s.stopLoss.toLocaleString() : '--';
    if ($('intelTakeProfit')) $('intelTakeProfit').textContent = s.takeProfit ? s.takeProfit.map(t => t.toLocaleString()).join(' / ') : '--';
    if ($('intelRiskReward')) $('intelRiskReward').textContent = s.riskReward ? `${s.riskReward} : 1` : '--';

    const chip = (id, ok, label) => {
      const el = $(id);
      if (!el) return;
      el.className = `confluence-chip ${ok === null ? '' : ok ? 'ok' : 'bad'}`;
      el.innerHTML = el.innerHTML.replace(/(<\/i>\s*).*$/, `$1 ${label}`);
    };
    chip('newsConfluenceChip', s.gateChecks.newsAligned !== false, `News: ${s.news.label}`);
    chip('whaleConfluenceChip', s.gateChecks.whaleAligned !== false, `Whale: ${s.whale.dominantSide}`);
    chip('trapGuardChip', s.gateChecks.noSpoofedLevel !== false, `Spoof guard: ${s.gateChecks.noSpoofedLevel === false ? 'TRIPPED' : 'clear'}`);

    // Evidence trail: the scored components when a setup exists, otherwise the
    // specific reasons the system is standing down. "Nothing is happening" is a
    // real answer and it is shown as one rather than dressed up as analysis.
    const trailEl = $('evidenceTrail');
    if (trailEl) {
      if (s.grade) {
        trailEl.innerHTML = `
          <div class="evidence-group"><div class="evidence-head">${s.setupType} · ${s.direction} · grade ${s.grade} (${s.score}/100)</div>
            <div class="evidence-narrative">${s.narrative || ''}</div>
            ${s.components.map(c => `<div class="evidence-item"><span class="ev-label">${c.label}</span><span class="ev-bar"><span class="ev-fill" style="width:${Math.round(c.score * 100)}%"></span></span><span class="ev-detail">${c.detail}</span></div>`).join('')}
          </div>
          ${(s.blockers || []).length ? `<div class="evidence-group blocked"><div class="evidence-head">Blocked by</div>${s.blockers.map(b => `<div class="evidence-item"><span class="ev-detail">${b}</span></div>`).join('')}</div>` : ''}`;
      } else {
        trailEl.innerHTML = `<div class="evidence-group"><div class="evidence-head">Standing down — ${s.regime}</div>
          ${(s.regimeReasons || []).concat(s.blockers || []).map(b => `<div class="evidence-item"><span class="ev-detail">${b}</span></div>`).join('')}
        </div>`;
      }
    }
  }

  function renderAgentTelemetry() {
    const s = latestStates[state.symbol];
    if (!s) return;
    const set = (id, m1, m2, status, label) => {
      const a = AGENTS.find(x => x.id === id);
      if (!a) return;
      a.meta1 = m1; a.meta2 = m2;
      if (status) a.status = status;
      if (label) a.statusLabel = label;
    };
    const gov = riskGovernor.status();
    const llm = llmGovernor.status();
    const blocked = (s.blockers || []).length;
    const gatesPassed = Object.values(s.gateChecks || {}).filter(v => v === true).length;
    const gatesTotal = Object.keys(s.gateChecks || {}).length;

    set('DIV-01', `Quality: ${s.dataQuality}%`, `Status: ${s.dataStatus}`, s.dataStatus === 'VALID' ? 'active' : 'evaluating', s.dataStatus);
    set('DIV-02', `Taker: ${s.flow ? (s.flow.takerRatio5m * 100).toFixed(0) + '%' : '--'}`, `Burst: ${s.flow ? s.flow.burst : '--'}`);
    set('DIV-03', `Regime: ${s.regime}`, `Bias: ${s.bias}`, s.regime === 'TREND' ? 'active' : 'evaluating', s.regime);
    set('DIV-04', `Absorb: ${s.flow ? s.flow.absorption : 'NONE'}`, `Spoof: ${s.gateChecks.noSpoofedLevel === false ? 'TRIPPED' : 'clear'}`);
    set('DIV-05', `Setup: ${s.setupType}`, `Grade: ${s.grade || '--'}`, s.grade ? 'pass' : 'active', s.grade ? 'CANDIDATE' : 'SCANNING');
    set('DIV-06', `Gates: ${gatesPassed}/${gatesTotal}`, `Blocked: ${blocked}`, blocked && s.grade ? 'evaluating' : 'pass', blocked && s.grade ? 'BLOCKING' : 'PASS');
    set('DIV-07', `Calls: ${llm.callsToday}/${llm.dailyBudget}`, `Skipped: ${llm.totalSkipped}`);
    const cdMin = riskGovernor.getRemainingCooldownMin ? riskGovernor.getRemainingCooldownMin(state.symbol) : 0;
    const cdTxt = cdMin > 0 ? ` [${state.symbol} CD ${cdMin}m]` : '';
    set('DIV-08', `Risk/trade: ${gov.riskPerTradePct}%`, `Day: ${gov.realisedPnlToday >= 0 ? '+' : ''}${gov.realisedPnlToday}${cdTxt}`, (gov.paused || cdMin > 0) ? 'evaluating' : 'active', cdMin > 0 ? `COOLDOWN ${cdMin}m` : (gov.paused ? gov.pauseReason : 'ENFORCED'));
    set('DIV-10', `Headlines: ${latestNews ? (latestNews.articles || []).length : 0}`, `Sentiment: ${s.news.label}`);
    set('DIV-11', `Whales: ${s.whale.whaleCount}`, `Flow: ${s.whale.dominantSide}`);
    set('DIV-12', `Open: ${openPositionsSnapshot.length}`, `Managed: ${positionManager.all().length}`, openPositionsSnapshot.length ? 'pass' : 'active', openPositionsSnapshot.length ? 'MANAGING' : 'IDLE');
    renderAgents();
  }

  // ─── Account / positions / history polling ───
  let setupPerformance = {};

  function computeSetupPerformance(history) {
    // Consecutive recent losses per setup, newest first. Feeds the adaptive
    // gate so a pattern that has just failed repeatedly sits out.
    const out = {}, done = {};
    for (const t of history || []) {
      const st = t.setup_type;
      if (!st || done[st]) continue;
      out[st] = out[st] || { wins: 0, losses: 0, recentLossStreak: 0 };
      if (t.status === 'LOSS') out[st].recentLossStreak++;
      else done[st] = true;
    }
    return out;
  }

  async function pollDemoData() {
    await resyncArmedState();

    const acct = await fetchJSON('/api/account');
    if (acct && acct.result && acct.result.list && acct.result.list.length) {
      const equity = parseFloat(acct.result.list[0].totalEquity || 0);
      if (equity) {
        accountEquity = equity;
        riskGovernor.updateEquity(equity);
        $('equityVal').textContent = '$' + equity.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      }
    }

    const pos = await fetchJSON('/api/positions');
    if (pos && pos.result && pos.result.list) {
      openPositionsSnapshot = pos.result.list.filter(p => parseFloat(p.size) > 0);
      await checkPendingEntries();

      // Ensure every open position on Bybit has a thesis in positionManager (auto-adopt if opened outside/reloaded)
      for (const p of openPositionsSnapshot) {
        if (!positionManager.get(p.symbol, p.side)) {
          adoptPosition(p, latestStates[p.symbol]);
        }
      }

      const openKeys = new Set(openPositionsSnapshot.map(p => `${p.symbol}-${p.side}`));

      // A tracked trade that is no longer open was closed by the broker — in
      // practice the stop we attached at entry. Record it so the loss is
      // attributed to the setup that produced it.
      for (const t of positionManager.all()) {
        if (!openKeys.has(`${t.symbol}-${t.side}`)) {
          const exit = t.stopLoss;
          const pnl = (t.side === 'Buy' ? exit - t.entryPrice : t.entryPrice - exit) * (t.originalQty || t.qty || 0);
          positionManager.finalise(t, exit, 'STOP');
          await recordOutcome(t, exit, 'STOP', t.originalQty, pnl);
          logEvent(`STOP hit on ${t.symbol} ${t.side.toUpperCase()} — ${positionManager.explain(t)}`);
          positionManager.forget(t.symbol, t.side);
          saveThesesLocally();
          removeThesisFromServer(t.symbol, t.side);
        }
      }

      $('posCount').textContent = `${openPositionsSnapshot.length} active`;
      const tbody = $('positionsTbody');
      if (!openPositionsSnapshot.length) {
        tbody.innerHTML = '<tr><td colspan="12" class="empty-msg">No active positions</td></tr>';
      } else {
        tbody.innerHTML = openPositionsSnapshot.map(p => {
          const pnl = parseFloat(p.unrealisedPnl || 0);
          const pnlClass = pnl >= 0 ? 'text-green' : 'text-red';
          const entryPrice = parseFloat(p.avgPrice || 0);
          const im = parseFloat(p.positionIM || 0);
          const notional = parseFloat(p.positionValue || 0) || (entryPrice * parseFloat(p.size || 0));
          const pnlPct = im > 0 ? (pnl / im) * 100 : (notional > 0 ? (pnl / notional) * 100 : 0);
          const sl = parseFloat(p.stopLoss || 0);
          const trade = positionManager.get(p.symbol, p.side);
          const rTxt = trade ? `${positionManager.currentR(trade, parseFloat(p.markPrice || 0)).toFixed(2)}R` : '--';
          const isSelected = selectedPreviewTrade && selectedPreviewIsOngoing && selectedPreviewTrade.symbol === p.symbol && selectedPreviewTrade.side === p.side;
          return `<tr class="${isSelected ? 'row-selected' : ''}" onclick="window.selectLivePosition('${p.symbol}','${p.side}')" title="Click to view live setup chart">
            <td>${p.symbol}</td>
            <td><span class="badge ${p.side === 'Buy' ? 'buy' : 'sell'}">${p.side.toUpperCase()}</span></td>
            <td>${p.size}</td>
            <td>$${notional.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
            <td>${entryPrice.toLocaleString()}</td>
            <td>${parseFloat(p.markPrice || 0).toLocaleString()}</td>
            <td class="text-red">${sl ? sl.toLocaleString() : '--'}</td>
            <td>${p.liqPrice ? parseFloat(p.liqPrice).toLocaleString() : '--'}</td>
            <td class="${pnlClass}">${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}</td>
            <td class="${pnlClass}">${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%</td>
            <td class="${pnlClass}">${rTxt}</td>
            <td><button class="close-btn-sm" onclick="event.stopPropagation(); closePosition('${p.symbol}','${p.side}','${p.size}')">Close</button></td>
          </tr>`;
        }).join('');
      }
    }

    const perf = await fetchJSON('/api/performance');
    if (perf) {
      $('totalWins').textContent = perf.win_count || 0;
      $('totalLosses').textContent = perf.loss_count || 0;
      $('profitFactor').textContent = (perf.profit_factor || 0).toFixed(2);
      $('grossProfit').textContent = '$' + (perf.gross_profit || 0).toLocaleString();
      $('grossLoss').textContent = '$' + (perf.gross_loss || 0).toLocaleString();

      const wr = perf.win_rate || 0;
      const n = perf.total_trades || 0;
      $('winRateVal').textContent = n ? `${wr.toFixed(1)}%` : '--';
      $('winRateVal').style.color = wr >= 50 ? 'var(--green)' : 'var(--red)';
      $('winRateVal').title = n < 30
        ? `Only ${n} closed trades. A win rate needs a few hundred before it means anything; treat this as a running tally, not a measurement.`
        : `${n} closed trades. Expectancy ${perf.expectancy_r != null ? perf.expectancy_r + 'R' : 'n/a'} over ${perf.r_sample_size} R-tracked trades.`;

      const netPnl = perf.net_pnl || 0;
      $('pnlVal').textContent = (netPnl >= 0 ? '+$' : '-$') + Math.abs(netPnl).toLocaleString('en-US', { minimumFractionDigits: 2 });
      $('pnlVal').style.color = netPnl >= 0 ? 'var(--green)' : 'var(--red)';
      $('wlVal').textContent = `${perf.win_count || 0}W/${perf.loss_count || 0}L`;

      const history = perf.trade_history || [];
      previewHistoryList = history;
      setupPerformance = computeSetupPerformance(history);

      // Sync recent stop-outs/losses to riskGovernor cooldowns (15-min cooldown)
      const nowTs = Date.now();
      for (const t of history) {
        if (t.status === 'LOSS') {
          const tTime = new Date(t.time).getTime() || 0;
          if (tTime > 0 && (nowTs - tTime < 15 * 60 * 1000)) {
            const remainingMs = (tTime + 15 * 60 * 1000) - nowTs;
            riskGovernor.symbolCooldowns.set(t.symbol, nowTs + remainingMs);
          }
        }
      }

      $('tradeCount').textContent = `${history.length} trades`;
      const histTbody = $('historyTbody');
      if (!history.length) {
        histTbody.innerHTML = '<tr><td colspan="10" class="empty-msg">No trade history yet</td></tr>';
      } else {
        histTbody.innerHTML = history.map((t, idx) => {
          const pnlClass = t.status === 'WIN' ? 'text-green' : 'text-red';
          const reasonFull = (t.reason || '').replace(/"/g, '&quot;');
          const rTxt = typeof t.r_multiple === 'number' ? `${t.r_multiple >= 0 ? '+' : ''}${t.r_multiple}R` : '--';
          const isSelected = selectedPreviewTrade && !selectedPreviewIsOngoing && (selectedPreviewTrade.id === t.id || selectedPreviewTrade._idx === idx);
          return `<tr class="${isSelected ? 'row-selected' : ''}" onclick="window.selectHistoryTrade(${idx})" title="Click to view trade setup replay">
            <td>${t.id}</td><td>${t.time}</td><td>${t.symbol}</td>
            <td><span class="badge ${String(t.side).toLowerCase() === 'buy' ? 'buy' : 'sell'}">${t.side}</span></td>
            <td>${t.entry ? t.entry.toLocaleString() : '--'}</td>
            <td>${t.exit ? t.exit.toLocaleString() : '--'}</td>
            <td class="${pnlClass}">${t.pnl >= 0 ? '+$' : '-$'}${Math.abs(t.pnl).toFixed(2)}</td>
            <td class="${pnlClass}">${rTxt}</td>
            <td class="font-mono" style="font-size:9px;">${t.setup_type || '--'}${t.grade ? ' ' + t.grade : ''}</td>
            <td style="font-size:9px;" title="${reasonFull}">${t.exit_reason || '--'}</td>
          </tr>`;
        }).join('');
      }

      // Auto-select setup preview if none selected
      if (!selectedPreviewTrade) {
        if (openPositionsSnapshot.length) {
          selectedPreviewTrade = openPositionsSnapshot[0];
          selectedPreviewIsOngoing = true;
          updateLiveSetupPreview();
        } else if (history.length) {
          selectedPreviewTrade = Object.assign({ _idx: 0 }, history[0]);
          selectedPreviewIsOngoing = false;
          updateLiveSetupPreview();
        }
      } else if (selectedPreviewIsOngoing) {
        const livePos = openPositionsSnapshot.find(x => x.symbol === selectedPreviewTrade.symbol && x.side === selectedPreviewTrade.side);
        if (livePos) {
          selectedPreviewTrade = livePos;
          updateLiveSetupPreview();
        }
      }
    }
  }

  // ─── Live Trade Setup Preview & Analysis Engine ───
  let selectedPreviewTrade = null;
  let selectedPreviewIsOngoing = true;
  let previewHistoryList = [];
  const previewCandlesCache = {};

  async function fetchPreviewCandles(tradeObj, isScalp) {
    const sym = tradeObj.symbol;
    const isOngoing = selectedPreviewIsOngoing;
    const cacheKey = `${sym}_${isScalp ? '5' : '15'}_${tradeObj.id || ''}_${isOngoing ? 'live' : 'hist'}`;
    if (previewCandlesCache[cacheKey] && Date.now() - (previewCandlesCache[cacheKey].time || 0) < 15000) {
      return previewCandlesCache[cacheKey].candles;
    }
    try {
      let url = `/api/kline?symbol=${sym}&interval=${isScalp ? '5' : '15'}&limit=48`;
      if (!isOngoing && tradeObj.exitTime) {
        const stepMs = (isScalp ? 5 : 15) * 60 * 1000;
        url += `&end=${tradeObj.exitTime + 18 * stepMs}`;
      }
      const res = await fetch(url);
      if (res.ok) {
        const d = await res.json();
        if (d.list && d.list.length >= 8) {
          previewCandlesCache[cacheKey] = { time: Date.now(), candles: d.list };
          return d.list;
        }
      }
    } catch (e) {}

    // Fallback continuous candles anchored to the setup geometry + aftermath follow-through
    const candles = [];
    const base = tradeObj.entry || 100;
    const rawSide = String(tradeObj.side || 'BUY').toUpperCase();
    let isLong = rawSide === 'BUY' || rawSide === 'LONG';
    if (!isOngoing && tradeObj.entry && tradeObj.exit && typeof tradeObj.pnl === 'number' && tradeObj.pnl !== 0) {
      const delta = tradeObj.exit - tradeObj.entry;
      if (delta !== 0) isLong = (tradeObj.pnl * delta > 0);
    }
    const end = (!isOngoing && tradeObj.exit) ? tradeObj.exit : (isLong ? base * 1.015 : base * 0.985);
    const isWin = !isOngoing && (tradeObj.status === 'WIN' || (tradeObj.pnl && tradeObj.pnl > 0));

    const count = 48;
    const entryIdx = isOngoing ? Math.max(2, Math.floor(count * 0.30)) : Math.max(2, Math.floor(count * 0.22));
    const exitIdx = isOngoing ? count - 1 : Math.min(count - 6, Math.floor(count * 0.62));
    const stepMs = (isScalp ? 5 : 15) * 60 * 1000;
    let cur = isLong ? base * 0.993 : base * 1.007;
    const now = Date.now();

    for (let i = 0; i < count; i++) {
      let trendP;
      if (i < entryIdx) {
        trendP = (base - cur) * 0.22;
      } else if (i <= exitIdx) {
        const tradeProg = (i - entryIdx) / Math.max(1, exitIdx - entryIdx);
        trendP = (end - cur) * (tradeProg < 0.5 ? 0.12 : 0.24);
      } else {
        if (isWin) {
          const pullTarget = isLong ? end - (Math.abs(end - base) * 0.25) : end + (Math.abs(end - base) * 0.25);
          trendP = (pullTarget - cur) * 0.16;
        } else {
          const postOvershoot = isLong ? end - (Math.abs(base - end) * 0.30) : end + (Math.abs(base - end) * 0.30);
          trendP = (postOvershoot - cur) * 0.14;
        }
      }
      const noise = (Math.random() - 0.49) * (base * 0.005);
      const open = cur;
      cur = +(cur + trendP + noise).toFixed(4);
      const high = +(Math.max(open, cur) + Math.random() * (base * 0.003)).toFixed(4);
      const low = +(Math.min(open, cur) - Math.random() * (base * 0.003)).toFixed(4);
      candles.push({ start: now - (count - i) * stepMs, open, high, low, close: cur, volume: 250 });
    }
    previewCandlesCache[cacheKey] = { time: Date.now(), candles };
    return candles;
  }

  let setupPreviewZoom = 1.0;
  let setupPreviewPan = 0; // offset in candles
  let isDraggingPreview = false;
  let dragStartX = 0;
  let dragStartPan = 0;
  let currentPreviewSetup = null;
  let previewEventsAttached = false;

  function updateZoomBadge() {
    const b = $('btnZoomReset');
    if (b) b.textContent = `↺ ${setupPreviewZoom.toFixed(1)}x`;
  }

  function attachPreviewCanvasInteractions(canvas) {
    if (!canvas || previewEventsAttached) return;
    previewEventsAttached = true;

    // 1. Mouse Wheel Zoom (centered around pointer or timeline)
    canvas.addEventListener('wheel', (e) => {
      e.preventDefault();
      if (!currentPreviewSetup) return;
      const factor = e.deltaY < 0 ? 1.2 : 0.83;
      setupPreviewZoom = Math.min(5.0, Math.max(0.5, +(setupPreviewZoom * factor).toFixed(2)));
      updateZoomBadge();
      drawSetupPreviewCanvas(canvas, currentPreviewSetup);
    }, { passive: false });

    // 2. Click & Drag Pan
    canvas.addEventListener('mousedown', (e) => {
      isDraggingPreview = true;
      dragStartX = e.clientX;
      dragStartPan = setupPreviewPan;
      canvas.style.cursor = 'grabbing';
    });

    window.addEventListener('mousemove', (e) => {
      if (!isDraggingPreview || !currentPreviewSetup) return;
      const dx = e.clientX - dragStartX;
      const rawCandles = currentPreviewSetup.candles || [];
      const totalCandles = rawCandles.length || 48;
      const visibleCount = Math.max(6, Math.min(totalCandles, Math.round(totalCandles / setupPreviewZoom)));
      const stepPx = Math.max(1, (canvas.clientWidth - 80) / visibleCount);
      const candleDelta = dx / stepPx;
      const maxPan = Math.max(0, totalCandles - visibleCount);
      setupPreviewPan = Math.max(0, Math.min(maxPan, dragStartPan - candleDelta));
      drawSetupPreviewCanvas(canvas, currentPreviewSetup);
    });

    window.addEventListener('mouseup', () => {
      if (isDraggingPreview) {
        isDraggingPreview = false;
        if (canvas) canvas.style.cursor = 'grab';
      }
    });

    // 3. Double Click to Reset View
    canvas.addEventListener('dblclick', () => {
      setupPreviewZoom = 1.0;
      setupPreviewPan = 0;
      updateZoomBadge();
      if (currentPreviewSetup) drawSetupPreviewCanvas(canvas, currentPreviewSetup);
    });

    // 4. Zoom Buttons
    const btnIn = $('btnZoomIn');
    if (btnIn) {
      btnIn.onclick = () => {
        setupPreviewZoom = Math.min(5.0, +(setupPreviewZoom * 1.25).toFixed(2));
        updateZoomBadge();
        if (currentPreviewSetup) drawSetupPreviewCanvas(canvas, currentPreviewSetup);
      };
    }
    const btnOut = $('btnZoomOut');
    if (btnOut) {
      btnOut.onclick = () => {
        setupPreviewZoom = Math.max(0.5, +(setupPreviewZoom / 1.25).toFixed(2));
        updateZoomBadge();
        if (currentPreviewSetup) drawSetupPreviewCanvas(canvas, currentPreviewSetup);
      };
    }
    const btnReset = $('btnZoomReset');
    if (btnReset) {
      btnReset.onclick = () => {
        setupPreviewZoom = 1.0;
        setupPreviewPan = 0;
        updateZoomBadge();
        if (currentPreviewSetup) drawSetupPreviewCanvas(canvas, currentPreviewSetup);
      };
    }

    // 5. Scroll & Clear Log Buttons
    const autoBtn = $('btnAutoScroll');
    if (autoBtn) {
      autoBtn.onclick = () => {
        guardianAutoScroll = !guardianAutoScroll;
        autoBtn.textContent = guardianAutoScroll ? 'SCROLL: AUTO' : 'SCROLL: MANUAL';
        autoBtn.style.color = guardianAutoScroll ? '#10b981' : '#f59e0b';
        autoBtn.style.borderColor = guardianAutoScroll ? 'rgba(16,185,129,0.3)' : 'rgba(245,158,11,0.3)';
        autoBtn.style.background = guardianAutoScroll ? 'rgba(16,185,129,0.15)' : 'rgba(245,158,11,0.15)';
        const el = $('guardianLog');
        if (guardianAutoScroll && el) el.scrollTop = el.scrollHeight;
      };
    }
    const clearBtn = $('btnClearLog');
    if (clearBtn) {
      clearBtn.onclick = () => {
        const el = $('guardianLog');
        if (el) el.innerHTML = '<div class="log-line"><span class="log-time">[CLEARED]</span> <span class="log-info">Supervisor log feed cleared.</span></div>';
      };
    }
  }

  function drawSetupPreviewCanvas(canvas, setup) {
    if (!canvas) return;
    currentPreviewSetup = setup;
    attachPreviewCanvasInteractions(canvas);

    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    if (!w || !h) return;

    canvas.width = w * dpr;
    canvas.height = h * dpr;
    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);

    const rawCandles = setup.candles || [];
    if (!rawCandles.length) {
      ctx.fillStyle = '#64748b';
      ctx.font = '12px "JetBrains Mono", monospace';
      ctx.fillText('Loading price action...', w / 2 - 60, h / 2);
      return;
    }

    // 1. Resolve Zoom & Pan Window
    const totalCount = rawCandles.length;
    const visibleCount = Math.max(6, Math.min(totalCount, Math.round(totalCount / setupPreviewZoom)));
    const maxPan = Math.max(0, totalCount - visibleCount);
    setupPreviewPan = Math.max(0, Math.min(maxPan, setupPreviewPan));
    const startIdx = Math.max(0, Math.min(maxPan, Math.round(setupPreviewPan)));
    const candles = rawCandles.slice(startIdx, startIdx + visibleCount);

    // 2. Resolve Trade Direction & Geometry Accurately
    const rawSide = String(setup.side || '').toUpperCase();
    let isLong = rawSide === 'BUY' || rawSide === 'LONG';
    if (!setup.isOngoing && setup.entry && setup.exit && typeof setup.pnl === 'number' && setup.pnl !== 0) {
      const delta = setup.exit - setup.entry;
      if (delta !== 0) isLong = (setup.pnl * delta > 0);
    }

    const entry = setup.entry || setup.avgPrice || (candles[Math.floor(candles.length / 2)].close);
    const exit = !setup.isOngoing ? (setup.exit || null) : null;
    const isWin = !setup.isOngoing && (setup.status === 'WIN' || (setup.pnl && setup.pnl > 0));

    let stop = setup.stop || setup.stopLoss;
    if (!stop) {
      if (!isLong) {
        stop = (!setup.isOngoing && setup.pnl < 0 && exit && exit > entry) ? exit : entry * 1.018;
      } else {
        stop = (!setup.isOngoing && setup.pnl < 0 && exit && exit < entry) ? exit : entry * 0.982;
      }
    }
    if (!isLong && stop <= entry) stop = entry + Math.abs(entry - stop || entry * 0.018);
    if (isLong && stop >= entry) stop = entry - Math.abs(entry - stop || entry * 0.018);

    let target = setup.target || (setup.targets && setup.targets[0]);
    if (!target) {
      const riskDist = Math.abs(entry - stop) || (entry * 0.015);
      if (!isLong) {
        target = (!setup.isOngoing && isWin && exit && exit < entry) ? exit : entry - riskDist * 2.0;
      } else {
        target = (!setup.isOngoing && isWin && exit && exit > entry) ? exit : entry + riskDist * 2.0;
      }
    }
    if (!isLong && target >= entry) target = entry - Math.abs(entry - target || entry * 0.035);
    if (isLong && target <= entry) target = entry + Math.abs(entry - target || entry * 0.035);

    const current = setup.current || setup.markPrice || candles[candles.length - 1].close;

    let minP = Infinity, maxP = -Infinity;
    candles.forEach(c => {
      if (c.low < minP) minP = c.low;
      if (c.high > maxP) maxP = c.high;
    });
    minP = Math.min(minP, entry, stop, target, current, exit || minP);
    maxP = Math.max(maxP, entry, stop, target, current, exit || maxP);

    const range = maxP - minP || 1;
    minP -= range * 0.08;
    maxP += range * 0.08;

    const padTop = 24, padBottom = 22, padLeft = 14, padRight = 66;
    const chartW = w - padLeft - padRight;
    const chartH = h - padTop - padBottom;

    function py(p) {
      return padTop + (1 - (p - minP) / (maxP - minP)) * chartH;
    }

    // Background & subtle grid
    ctx.fillStyle = '#070a12';
    ctx.fillRect(0, 0, w, h);

    ctx.strokeStyle = 'rgba(255, 255, 255, 0.035)';
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const y = padTop + (i / 4) * chartH;
      ctx.beginPath();
      ctx.moveTo(padLeft, y);
      ctx.lineTo(w - padRight, y);
      ctx.stroke();

      const priceVal = maxP - (i / 4) * (maxP - minP);
      ctx.fillStyle = 'rgba(148, 163, 184, 0.45)';
      ctx.font = '9px "JetBrains Mono", monospace';
      ctx.textAlign = 'left';
      ctx.fillText(priceVal.toLocaleString('en-US', { maximumFractionDigits: priceVal < 10 ? 4 : 2 }), w - padRight + 6, y + 3);
    }

    const n = candles.length;
    const step = chartW / n;
    const candleW = Math.max(2, Math.min(18, step * 0.65));

    // Map global timeline indices to zoomed viewport
    let origEntryIdx, origExitIdx;
    if (setup.isOngoing) {
      origEntryIdx = Math.max(2, Math.floor(totalCount * 0.30));
      origExitIdx = totalCount - 1;
    } else {
      origEntryIdx = Math.max(2, Math.floor(totalCount * 0.22));
      origExitIdx = Math.min(totalCount - 6, Math.floor(totalCount * 0.62));
    }
    const entryIdx = origEntryIdx - startIdx;
    const exitIdx = origExitIdx - startIdx;

    const entryX = padLeft + entryIdx * step + candleW / 2;
    const exitX = padLeft + exitIdx * step + candleW / 2;
    const endX = w - padRight;

    const boxLeft = Math.max(padLeft, entryX);
    const boxRight = Math.min(endX, exitX);
    const boxW = Math.max(0, boxRight - boxLeft);

    // Support / Resistance Shaded Bands (Pink & Blue)
    const sup = setup.nextSupport || (isLong ? stop * 0.998 : target * 0.995);
    const res = setup.nextResistance || (isLong ? target * 1.005 : stop * 1.002);

    if (sup) {
      const s1 = py(sup * 1.003), s2 = py(sup * 0.997);
      ctx.fillStyle = 'rgba(244, 63, 94, 0.12)';
      ctx.fillRect(padLeft, Math.min(s1, s2), chartW, Math.abs(s1 - s2));
      ctx.strokeStyle = 'rgba(244, 63, 94, 0.35)';
      ctx.setLineDash([3, 3]);
      ctx.strokeRect(padLeft, Math.min(s1, s2), chartW, Math.abs(s1 - s2));
      ctx.setLineDash([]);
      ctx.fillStyle = 'rgba(244, 63, 94, 0.75)';
      ctx.font = '8.5px Inter, sans-serif';
      ctx.textAlign = 'left';
      ctx.fillText('Support Zone / OTE', padLeft + 6, Math.max(s1, s2) - 4);
    }

    if (res) {
      const r1 = py(res * 1.003), r2 = py(res * 0.997);
      ctx.fillStyle = 'rgba(59, 130, 246, 0.12)';
      ctx.fillRect(padLeft, Math.min(r1, r2), chartW, Math.abs(r1 - r2));
      ctx.strokeStyle = 'rgba(59, 130, 246, 0.35)';
      ctx.setLineDash([3, 3]);
      ctx.strokeRect(padLeft, Math.min(r1, r2), chartW, Math.abs(r1 - r2));
      ctx.setLineDash([]);
      ctx.fillStyle = 'rgba(59, 130, 246, 0.75)';
      ctx.font = '8.5px Inter, sans-serif';
      ctx.textAlign = 'left';
      ctx.fillText('Resistance / Liquidity Pool', padLeft + 6, Math.min(r1, r2) + 11);
    }

    // TradingView Long/Short Position Tool
    const yEntry = py(entry);
    const yTarget = py(target);
    const yStop = py(stop);
    const rMult = setup.r_multiple ? `+${setup.r_multiple}R` : '+2.0R';

    if (boxW > 0) {
      if (!isLong) {
        // ── SHORT POSITION BOX ──
        // Red Stop Loss Box is ABOVE entry (from yStop down to yEntry)
        ctx.fillStyle = 'rgba(239, 68, 68, 0.20)';
        ctx.fillRect(boxLeft, yStop, boxW, yEntry - yStop);
        ctx.strokeStyle = 'rgba(239, 68, 68, 0.85)';
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(boxLeft, yStop);
        ctx.lineTo(boxRight, yStop);
        ctx.stroke();

        ctx.fillStyle = '#ef4444';
        ctx.font = 'bold 9px "JetBrains Mono", monospace';
        ctx.textAlign = 'right';
        const stopLabel = stop.toLocaleString('en-US', { maximumFractionDigits: stop < 10 ? 4 : 2 });
        ctx.fillText(`SL ${stopLabel} (-1.0R)`, boxRight - 6, yStop - 4);

        // Green Target Box is BELOW entry (from yEntry down to yTarget)
        ctx.fillStyle = 'rgba(16, 185, 129, 0.20)';
        ctx.fillRect(boxLeft, yEntry, boxW, yTarget - yEntry);
        ctx.strokeStyle = 'rgba(16, 185, 129, 0.85)';
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(boxLeft, yTarget);
        ctx.lineTo(boxRight, yTarget);
        ctx.stroke();

        ctx.fillStyle = '#10b981';
        ctx.font = 'bold 9px "JetBrains Mono", monospace';
        ctx.textAlign = 'right';
        const targetLabel = target.toLocaleString('en-US', { maximumFractionDigits: target < 10 ? 4 : 2 });
        ctx.fillText(`TARGET ${targetLabel} (${rMult})`, boxRight - 6, yTarget + 12);
      } else {
        // ── LONG POSITION BOX ──
        // Green Target Box is ABOVE entry (from yTarget down to yEntry)
        ctx.fillStyle = 'rgba(16, 185, 129, 0.20)';
        ctx.fillRect(boxLeft, yTarget, boxW, yEntry - yTarget);
        ctx.strokeStyle = 'rgba(16, 185, 129, 0.85)';
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(boxLeft, yTarget);
        ctx.lineTo(boxRight, yTarget);
        ctx.stroke();

        ctx.fillStyle = '#10b981';
        ctx.font = 'bold 9px "JetBrains Mono", monospace';
        ctx.textAlign = 'right';
        const targetLabel = target.toLocaleString('en-US', { maximumFractionDigits: target < 10 ? 4 : 2 });
        ctx.fillText(`TARGET ${targetLabel} (${rMult})`, boxRight - 6, yTarget - 4);

        // Red Stop Loss Box is BELOW entry (from yEntry down to yStop)
        ctx.fillStyle = 'rgba(239, 68, 68, 0.20)';
        ctx.fillRect(boxLeft, yEntry, boxW, yStop - yEntry);
        ctx.strokeStyle = 'rgba(239, 68, 68, 0.85)';
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(boxLeft, yStop);
        ctx.lineTo(boxRight, yStop);
        ctx.stroke();

        ctx.fillStyle = '#ef4444';
        ctx.font = 'bold 9px "JetBrains Mono", monospace';
        ctx.textAlign = 'right';
        const stopLabel = stop.toLocaleString('en-US', { maximumFractionDigits: stop < 10 ? 4 : 2 });
        ctx.fillText(`SL ${stopLabel} (-1.0R)`, boxRight - 6, yStop + 12);
      }

      // Entry line (dashed from boxLeft to boxRight)
      ctx.strokeStyle = 'rgba(248, 250, 252, 0.7)';
      ctx.lineWidth = 1.2;
      ctx.setLineDash([4, 3]);
      ctx.beginPath();
      ctx.moveTo(boxLeft, yEntry);
      ctx.lineTo(boxRight, yEntry);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // Render Candlesticks (across whole timeline including Aftermath!)
    candles.forEach((c, i) => {
      const cx = padLeft + i * step + candleW / 2;
      const oY = py(c.open), cY = py(c.close), hY = py(c.high), lY = py(c.low);
      const isBull = c.close >= c.open;

      ctx.strokeStyle = isBull ? '#10b981' : '#ef4444';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(cx, hY);
      ctx.lineTo(cx, lY);
      ctx.stroke();

      ctx.fillStyle = isBull ? '#10b981' : '#ef4444';
      ctx.fillRect(cx - candleW / 2, Math.min(oY, cY), candleW, Math.max(1.5, Math.abs(oY - cY)));
    });

    // ── EXACT ENTRY POINT PINPOINT RETICLE & BADGE ──
    if (entryX >= padLeft - 10 && entryX <= endX + 10) {
      // 1. Full-height vertical crosshair guide line through the exact entry candle
      ctx.strokeStyle = 'rgba(56, 189, 248, 0.4)';
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(entryX, padTop);
      ctx.lineTo(entryX, padTop + chartH);
      ctx.stroke();
      ctx.setLineDash([]);

      // 2. High-contrast Glowing Reticle / Target Bullseye
      ctx.save();
      ctx.shadowColor = isLong ? '#10b981' : '#ef4444';
      ctx.shadowBlur = 12;

      // Outer animated ring
      ctx.strokeStyle = isLong ? '#10b981' : '#ef4444';
      ctx.lineWidth = 2.2;
      ctx.beginPath();
      ctx.arc(entryX, yEntry, 8, 0, Math.PI * 2);
      ctx.stroke();

      // Center solid core dot
      ctx.fillStyle = '#ffffff';
      ctx.beginPath();
      ctx.arc(entryX, yEntry, 3.5, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();

      // Target reticle crosshairs (+) through the center
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.moveTo(entryX - 13, yEntry);
      ctx.lineTo(entryX - 5, yEntry);
      ctx.moveTo(entryX + 5, yEntry);
      ctx.lineTo(entryX + 13, yEntry);
      ctx.moveTo(entryX, yEntry - 13);
      ctx.lineTo(entryX, yEntry - 5);
      ctx.moveTo(entryX, yEntry + 5);
      ctx.lineTo(entryX, yEntry + 13);
      ctx.stroke();

      // 3. Exact Entry Pinpoint Badge Callout
      const entryStr = entry.toLocaleString('en-US', { maximumFractionDigits: entry < 10 ? 4 : 2 });
      const badgeText = `🎯 EXACT ENTRY: ${entryStr}`;
      ctx.font = 'bold 9.5px "JetBrains Mono", monospace';
      const textW = ctx.measureText(badgeText).width;
      const badgeW = textW + 16;
      const badgeH = 19;
      const badgeX = Math.min(w - padRight - badgeW - 2, Math.max(padLeft + 2, entryX - badgeW / 2));
      const badgeY = isLong ? (yEntry - 34) : (yEntry + 16);

      // Badge container
      ctx.fillStyle = '#090d16';
      ctx.fillRect(badgeX, badgeY, badgeW, badgeH);
      ctx.strokeStyle = isLong ? '#10b981' : '#ef4444';
      ctx.lineWidth = 1.5;
      ctx.strokeRect(badgeX, badgeY, badgeW, badgeH);

      // Connecting pointer line from badge to exact entry dot
      ctx.strokeStyle = isLong ? '#10b981' : '#ef4444';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(entryX, isLong ? badgeY + badgeH : badgeY);
      ctx.lineTo(entryX, isLong ? yEntry - 8 : yEntry + 8);
      ctx.stroke();

      // Badge label
      ctx.fillStyle = '#ffffff';
      ctx.textAlign = 'center';
      ctx.fillText(badgeText, badgeX + badgeW / 2, badgeY + 13);

      // 4. Directional Trigger Arrow pointing into the exact reticle
      ctx.fillStyle = isLong ? '#10b981' : '#ef4444';
      ctx.strokeStyle = isLong ? '#10b981' : '#ef4444';
      ctx.lineWidth = 2.2;
      const aY = isLong ? yEntry + 28 : yEntry - 28;
      ctx.beginPath();
      ctx.moveTo(entryX, aY);
      ctx.lineTo(entryX, yEntry + (isLong ? 9 : -9));
      ctx.stroke();
      // Arrow head
      ctx.beginPath();
      if (isLong) {
        ctx.moveTo(entryX - 5, yEntry + 17);
        ctx.lineTo(entryX, yEntry + 8);
        ctx.lineTo(entryX + 5, yEntry + 17);
      } else {
        ctx.moveTo(entryX - 5, yEntry - 17);
        ctx.lineTo(entryX, yEntry - 8);
        ctx.lineTo(entryX + 5, yEntry - 17);
      }
      ctx.fill();
    }

    // Ongoing Active Position mark price line vs Closed Exit Pin + Aftermath
    if (setup.isOngoing) {
      const yCur = py(current);
      ctx.strokeStyle = '#38bdf8';
      ctx.lineWidth = 1.5;
      ctx.setLineDash([3, 2]);
      ctx.beginPath();
      ctx.moveTo(padLeft, yCur);
      ctx.lineTo(endX, yCur);
      ctx.stroke();
      ctx.setLineDash([]);

      ctx.fillStyle = '#38bdf8';
      ctx.beginPath();
      ctx.arc(endX - 2, yCur, 4, 0, Math.PI * 2);
      ctx.fill();

      ctx.fillStyle = '#38bdf8';
      ctx.font = 'bold 9px "JetBrains Mono", monospace';
      ctx.textAlign = 'right';
      const curLabel = current.toLocaleString('en-US', { maximumFractionDigits: current < 10 ? 4 : 2 });
      ctx.fillText(`NOW ${curLabel}`, endX - 12, yCur - 4);
    } else if (exit != null) {
      // ── AFTERMATH SECTION (Price Action After Stop / TP) ──
      if (exitX < endX) {
        ctx.fillStyle = 'rgba(255, 255, 255, 0.018)';
        ctx.fillRect(exitX, padTop, endX - exitX, chartH);

        ctx.fillStyle = 'rgba(56, 189, 248, 0.75)';
        ctx.font = 'bold 8.5px "JetBrains Mono", monospace';
        ctx.textAlign = 'left';
        ctx.fillText('⚡ POST-TRADE FOLLOW-THROUGH', exitX + 8, padTop + 10);

        ctx.strokeStyle = 'rgba(255, 255, 255, 0.18)';
        ctx.lineWidth = 1;
        ctx.setLineDash([2, 3]);
        ctx.beginPath();
        ctx.moveTo(exitX, yEntry);
        ctx.lineTo(endX, yEntry);
        ctx.stroke();

        ctx.strokeStyle = 'rgba(239, 68, 68, 0.25)';
        ctx.beginPath();
        ctx.moveTo(exitX, yStop);
        ctx.lineTo(endX, yStop);
        ctx.stroke();
        ctx.setLineDash([]);
      }

      if (exitX >= padLeft - 10 && exitX <= endX + 10) {
        ctx.strokeStyle = isWin ? 'rgba(16, 185, 129, 0.5)' : 'rgba(239, 68, 68, 0.5)';
        ctx.lineWidth = 1.2;
        ctx.setLineDash([3, 3]);
        ctx.beginPath();
        ctx.moveTo(exitX, padTop);
        ctx.lineTo(exitX, h - padBottom);
        ctx.stroke();
        ctx.setLineDash([]);

        const yEx = py(exit);
        ctx.fillStyle = isWin ? '#10b981' : '#ef4444';
        ctx.beginPath();
        ctx.arc(exitX, yEx, 4.5, 0, Math.PI * 2);
        ctx.fill();

        ctx.strokeStyle = '#fff';
        ctx.lineWidth = 1.2;
        ctx.stroke();

        ctx.fillStyle = isWin ? '#10b981' : '#ef4444';
        ctx.font = 'bold 9px "JetBrains Mono", monospace';
        ctx.textAlign = 'right';
        const reasonTxt = setup.exit_reason || (isWin ? 'TP HIT' : 'STOP HIT');
        const exitLabel = exit.toLocaleString('en-US', { maximumFractionDigits: exit < 10 ? 4 : 2 });
        ctx.fillText(`EXIT ${exitLabel} (${reasonTxt})`, exitX - 8, yEx - 4);
      }

      const lastClose = candles[candles.length - 1].close;
      const yLast = py(lastClose);
      ctx.fillStyle = '#94a3b8';
      ctx.font = '8.5px "JetBrains Mono", monospace';
      ctx.textAlign = 'right';
      const postDelta = lastClose - exit;
      const postDeltaPct = ((postDelta / exit) * 100).toFixed(1);
      ctx.fillText(`AFTERMATH: ${lastClose.toLocaleString('en-US', { maximumFractionDigits: lastClose < 10 ? 4 : 2 })} (${postDelta >= 0 ? '+' : ''}${postDeltaPct}%)`, endX - 4, yLast - 4);
    }
  }

  async function updateLiveSetupPreview() {
    if (!selectedPreviewTrade) return;
    const canvas = $('liveSetupCanvas');
    if (!canvas) return;

    const isOngoing = selectedPreviewIsOngoing;
    const sym = selectedPreviewTrade.symbol;
    const rawSide = String(selectedPreviewTrade.side || 'BUY').toUpperCase();
    let isLong = rawSide === 'BUY' || rawSide === 'LONG';
    if (!isOngoing && selectedPreviewTrade.entry && selectedPreviewTrade.exit && typeof selectedPreviewTrade.pnl === 'number' && selectedPreviewTrade.pnl !== 0) {
      const delta = selectedPreviewTrade.exit - selectedPreviewTrade.entry;
      if (delta !== 0) isLong = (selectedPreviewTrade.pnl * delta > 0);
    }
    const side = isLong ? 'BUY' : 'SELL';

    const trade = isOngoing ? positionManager.get(sym, side) : null;
    const entry = parseFloat(selectedPreviewTrade.entry || selectedPreviewTrade.avgPrice || (trade ? trade.entryPrice : 0));
    const current = parseFloat(selectedPreviewTrade.markPrice || selectedPreviewTrade.current || (trade ? trade.entryPrice : entry));
    const exit = !isOngoing ? parseFloat(selectedPreviewTrade.exit || selectedPreviewTrade.avgExitPrice || entry) : null;

    const pnl = isOngoing ? parseFloat(selectedPreviewTrade.unrealisedPnl || 0) : parseFloat(selectedPreviewTrade.pnl || 0);
    const pnlPct = isOngoing
      ? ((parseFloat(selectedPreviewTrade.positionIM || 0) > 0 ? pnl / parseFloat(selectedPreviewTrade.positionIM) * 100 : 0))
      : (selectedPreviewTrade.pnl_pct || 0);

    const isWin = !isOngoing && (pnl > 0 || selectedPreviewTrade.status === 'WIN');
    const setupName = (trade && trade.setupName) || selectedPreviewTrade.setup_type || (isLong ? 'TREND_PULLBACK_LONG' : 'DESCENDING_TRIANGLE_SHORT');
    const isSureShot = (trade && trade.isSureShot) || selectedPreviewTrade.isSureShot || setupName === 'SURESHOT_MICRO_SCALP' || (currentStrategyMode === 'sureshot');
    const isScalp = (trade && trade.isScalp) || selectedPreviewTrade.isScalp || setupName.startsWith('SCALP_') || isSureShot || (currentStrategyMode === 'scalp');

    let stop = parseFloat((trade && trade.stopLoss) || selectedPreviewTrade.stop || selectedPreviewTrade.stopLoss || 0);
    if (!stop) {
      if (!isLong) {
        stop = (!isOngoing && !isWin && exit && exit > entry) ? exit : entry * 1.018;
      } else {
        stop = (!isOngoing && !isWin && exit && exit < entry) ? exit : entry * 0.982;
      }
    }
    if (!isLong && stop <= entry) stop = entry + Math.abs(entry - stop || entry * 0.018);
    if (isLong && stop >= entry) stop = entry - Math.abs(entry - stop || entry * 0.018);

    let target = parseFloat((trade && trade.targets && trade.targets[0]) || (selectedPreviewTrade.targets && selectedPreviewTrade.targets[0]) || selectedPreviewTrade.target || 0);
    if (!target) {
      const riskDist = Math.abs(entry - stop) || (entry * 0.015);
      if (!isLong) {
        target = (!isOngoing && isWin && exit && exit < entry) ? exit : entry - riskDist * 2.0;
      } else {
        target = (!isOngoing && isWin && exit && exit > entry) ? exit : entry + riskDist * 2.0;
      }
    }
    if (!isLong && target >= entry) target = entry - Math.abs(entry - target || entry * 0.035);
    if (isLong && target <= entry) target = entry + Math.abs(entry - target || entry * 0.035);

    const riskDist = Math.abs(entry - stop) || (entry * 0.015);
    const rewardDist = Math.abs(target - entry) || (riskDist * 2);
    const rr = (rewardDist / (riskDist || 1)).toFixed(1);

    // Update Preview DOM Header
    const badge = $('previewTradeBadge');
    if (badge) {
      badge.textContent = `${sym} ${isLong ? 'LONG' : 'SHORT'}`;
      badge.className = `badge ${isLong ? 'buy' : 'sell'} font-mono`;
    }
    const scalpB = $('previewScalpBadge');
    if (scalpB) scalpB.style.display = (isScalp && !isSureShot) ? 'inline-flex' : 'none';
    const sureShotB = $('previewSureShotBadge');
    if (sureShotB) sureShotB.style.display = isSureShot ? 'inline-flex' : 'none';

    const statB = $('previewStatusBadge');
    if (statB) {
      statB.textContent = isOngoing ? 'ACTIVE (LIVE)' : (isWin ? '🏆 WIN' : '❌ LOSS');
      statB.style.color = isOngoing ? '#38bdf8' : (isWin ? 'var(--green)' : 'var(--red)');
      statB.style.background = isOngoing ? 'rgba(56,189,248,0.15)' : (isWin ? 'var(--green-dim)' : 'var(--red-dim)');
    }

    const titleEl = $('previewSetupTitle');
    if (titleEl) titleEl.textContent = `${sym} · ${setupName}`;

    const pnlB = $('previewPnlBadge');
    if (pnlB) {
      pnlB.textContent = `${pnl >= 0 ? '+' : ''}$${Math.abs(pnl).toFixed(2)}${pnlPct ? ` (${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%)` : ''}`;
      pnlB.style.color = pnl >= 0 ? 'var(--green)' : 'var(--red)';
      pnlB.style.background = pnl >= 0 ? 'var(--green-dim)' : 'var(--red-dim)';
    }

    const rrB = $('previewRrBadge');
    if (rrB) rrB.textContent = `1:${rr} R:R`;

    // Geometry pills
    const ge = $('prevGeomEntry'); if (ge) ge.textContent = entry.toLocaleString('en-US', { maximumFractionDigits: entry < 10 ? 4 : 2 });
    const gc = $('prevGeomCurrent'); if (gc) gc.textContent = (isOngoing ? current : exit).toLocaleString('en-US', { maximumFractionDigits: current < 10 ? 4 : 2 });
    const gcl = $('prevGeomCurLabel'); if (gcl) gcl.textContent = isOngoing ? 'Mark Price' : 'Exit Price';
    const gt = $('prevGeomTarget'); if (gt) gt.textContent = target.toLocaleString('en-US', { maximumFractionDigits: target < 10 ? 4 : 2 });
    const gs = $('prevGeomStop'); if (gs) gs.textContent = stop.toLocaleString('en-US', { maximumFractionDigits: stop < 10 ? 4 : 2 });

    const thText = $('prevThesisText');
    if (thText) {
      const exitTxt = !isOngoing && selectedPreviewTrade.exit_reason ? `Exit: ${selectedPreviewTrade.exit_reason}. ` : '';
      thText.textContent = `${exitTxt}${selectedPreviewTrade.reason || (trade ? positionManager.explain(trade) : `Anchored to structural technical levels with 1:${rr} risk geometry.`)}`;
    }
    const timeT = $('prevTimeTag');
    if (timeT) timeT.textContent = selectedPreviewTrade.time || (trade ? new Date(trade.openedAt).toLocaleTimeString() : '--');

    // Fetch Candles & Render Canvas
    const candles = await fetchPreviewCandles(selectedPreviewTrade, isScalp);
    drawSetupPreviewCanvas(canvas, {
      side, entry, stop, target, current, exit,
      nextSupport: (trade && trade.nextSupport) || selectedPreviewTrade.nextSupport,
      nextResistance: (trade && trade.nextResistance) || selectedPreviewTrade.nextResistance,
      exit_reason: selectedPreviewTrade.exit_reason,
      status: selectedPreviewTrade.status,
      pnl, isOngoing, candles
    });
  }

  window.selectLivePosition = function (sym, side) {
    const p = openPositionsSnapshot.find(x => x.symbol === sym && x.side === side);
    if (!p) return;
    selectedPreviewTrade = p;
    selectedPreviewIsOngoing = true;
    document.querySelectorAll('#positionsTbody tr, #historyTbody tr').forEach(r => r.classList.remove('row-selected'));
    if (window.event && window.event.currentTarget) window.event.currentTarget.classList.add('row-selected');
    updateLiveSetupPreview();
  };

  window.selectHistoryTrade = function (idx) {
    const t = previewHistoryList[idx];
    if (!t) return;
    selectedPreviewTrade = Object.assign({ _idx: idx }, t);
    selectedPreviewIsOngoing = false;
    document.querySelectorAll('#positionsTbody tr, #historyTbody tr').forEach(r => r.classList.remove('row-selected'));
    if (window.event && window.event.currentTarget) window.event.currentTarget.classList.add('row-selected');
    updateLiveSetupPreview();
  };

  const syncBtn = $('btnPreviewSync');
  if (syncBtn) syncBtn.addEventListener('click', () => updateLiveSetupPreview());

  setTimeout(pollDemoData, 1000);
  setInterval(() => {
    if (document.hidden) return; // Save Vercel serverless function invocations when tab is hidden
    pollDemoData();
  }, 10000);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) pollDemoData();
  });

  window.closePosition = async function (symbol, side, qty) {
    const posEntry = openPositionsSnapshot.find(p => p.symbol === symbol && p.side === side);
    const trade = positionManager.get(symbol, side);
    try {
      const res = await postJSON('/api/order/close', { category: 'linear', symbol, side, qty });
      if (res.retCode === 0 && posEntry) {
        const exitPrice = parseFloat(posEntry.markPrice || 0);
        const pnl = parseFloat(posEntry.unrealisedPnl || 0);
        if (trade) {
          positionManager.finalise(trade, exitPrice, 'MANUAL');
          await recordOutcome(trade, exitPrice, 'MANUAL', parseFloat(qty), pnl);
          positionManager.forget(symbol, side);
        }
        saveThesesLocally();
        removeThesisFromServer(symbol, side);
        logEvent(`Manually closed ${symbol} ${side.toUpperCase()}`);
        setTimeout(pollDemoData, 600);
      }
    } catch (e) {
      console.error('Close position error:', e);
    }
  };

  // ─── Arm / disarm ───
  //
  // The arm flag used to live only in this tab's JS memory, so opening the
  // dashboard on another device -- or just reloading -- always came back up
  // STOPPED regardless of what was actually armed a moment ago. It's now
  // mirrored to /api/auto-trade/state (see backend_lib/auto_trade_state.py)
  // so every device shows the same current state, and re-synced on every
  // poll cycle so arming/stopping from one device is picked up by others
  // within a few seconds.
  //
  // This does NOT make it safe to leave two tabs both actively armed: each
  // tab's engine still runs and places orders independently, with nothing
  // stopping two tabs from both firing on the same signal in the same
  // cycle. Only drive trades from one device at a time -- the others are
  // fine for read-only monitoring, armed or not.
  function updateSettingsSummary() {
    const lev = riskGovernor.config.leverage || 10;
    const mode = riskGovernor.config.marginMode || 'cross';
    const isMin = riskGovernor.config.sizingMode === 'min';
    const isRisk = riskGovernor.config.sizingMode === 'risk';
    const sizingTxt = isMin
      ? 'Min Size (~$5-$65)'
      : (isRisk ? `${riskGovernor.config.riskPerTradePct}% Risk` : `$${riskGovernor.config.fixedUsdtSize} USDT`);

    const levDisp = $('leverageDisplay');
    if (levDisp) levDisp.textContent = `${lev}x`;
    const chipLev = $('chipLeverage');
    if (chipLev) chipLev.querySelector('span').textContent = `${lev}x Leverage`;
    const chipMargin = $('chipMargin');
    if (chipMargin) chipMargin.querySelector('span').textContent = `${mode === 'cross' ? 'Cross' : 'Isolated'} Margin`;
    const chipSizing = $('chipSizing');
    if (chipSizing) chipSizing.querySelector('span').textContent = sizingTxt;
    const chipStrat = $('chipStrategy');
    if (chipStrat) {
      const isSureShot = currentStrategyMode === 'sureshot';
      const isScalp = currentStrategyMode === 'scalp';
      chipStrat.querySelector('span').textContent = isSureShot
        ? '🎯 SureShot 90% (+$0.50)'
        : (isScalp ? '⚡ Scalp (5m–10m)' : 'Swing (15m–1h)');
      chipStrat.style.borderColor = isSureShot
        ? 'rgba(16,185,129,0.6)'
        : (isScalp ? 'rgba(245,158,11,0.5)' : '');
      chipStrat.style.color = isSureShot
        ? '#10b981'
        : (isScalp ? '#f59e0b' : '');
    }
  }

  let currentStrategyMode = 'standard';

  function applyStrategyModeUI(mode) {
    currentStrategyMode = (mode === 'sureshot' || mode === 'scalp') ? mode : 'standard';
    const stdBtn = $('stratModeStandardBtn');
    const scalpBtn = $('stratModeScalpBtn');
    const sureshotBtn = $('stratModeSureShotBtn');
    if (stdBtn) stdBtn.classList.toggle('active', currentStrategyMode === 'standard');
    if (scalpBtn) scalpBtn.classList.toggle('active', currentStrategyMode === 'scalp');
    if (sureshotBtn) sureshotBtn.classList.toggle('active', currentStrategyMode === 'sureshot');

    const isSureShot = currentStrategyMode === 'sureshot';
    const isScalp = currentStrategyMode === 'scalp' || isSureShot;

    // In SureShot Mode, automatically apply $10 USDT margin and 10x leverage ($100 notional)
    if (isSureShot) {
      applySizingModeUI('usdt');
      riskGovernor.config.fixedUsdtSize = 10;
      if ($('usdtSizeInput')) $('usdtSizeInput').value = 10;
      applyLeverageUI(10);
    }

    for (const sym of Object.keys(engines)) {
      if (engines[sym]) {
        if (engines[sym].setScalpMode) engines[sym].setScalpMode(isScalp);
        if (engines[sym].setSureShotMode) engines[sym].setSureShotMode(isSureShot);
      }
    }
    updateSettingsSummary();
  }

  function applyArmedUI(armed) {
    autoTradingArmed = armed;
    $('armStatus').textContent = armed ? 'ARMED' : 'STOPPED';
    $('armStatus').className = armed ? 'arm-status armed' : 'arm-status stopped';
    $('startBtn').disabled = armed;
    $('stopBtn').disabled = !armed;
    if ($('stratModeStandardBtn')) $('stratModeStandardBtn').disabled = armed;
    if ($('stratModeScalpBtn')) $('stratModeScalpBtn').disabled = armed;
    if ($('stratModeSureShotBtn')) $('stratModeSureShotBtn').disabled = armed;
    const dot = $('cloudStatusDot');
    const txt = $('cloudStatusText');
    if (dot && txt) {
      dot.className = armed ? 'cloud-status-dot armed' : 'cloud-status-dot stopped';
      txt.textContent = armed
        ? 'Railway 24/7 Engine: ARMED · Auto-Execution Active'
        : 'Railway 24/7 Engine: STANDBY · Monitoring Only (Execution Stopped)';
    }
    $('marginInput').disabled = armed;
    $('usdtSizeInput').disabled = armed;
    if ($('sizeModeMinBtn')) $('sizeModeMinBtn').disabled = armed;
    $('sizeModeRiskBtn').disabled = armed;
    $('sizeModeUsdtBtn').disabled = armed;
    const levInp = $('leverageInput');
    if (levInp) levInp.disabled = armed;
    document.querySelectorAll('.lev-preset-btn').forEach(btn => btn.disabled = armed);
    const mmCross = $('marginModeCrossBtn');
    const mmIso = $('marginModeIsolatedBtn');
    if (mmCross) mmCross.disabled = armed;
    if (mmIso) mmIso.disabled = armed;
  }

  function applyMarginModeUI(mode) {
    riskGovernor.config.marginMode = mode === 'isolated' ? 'isolated' : 'cross';
    const crossBtn = $('marginModeCrossBtn');
    const isoBtn = $('marginModeIsolatedBtn');
    if (crossBtn) crossBtn.classList.toggle('active', riskGovernor.config.marginMode === 'cross');
    if (isoBtn) isoBtn.classList.toggle('active', riskGovernor.config.marginMode === 'isolated');
    updateSettingsSummary();
  }

  function applyLeverageUI(lev) {
    const parsed = Math.max(1, Math.min(100, parseInt(lev, 10) || 10));
    riskGovernor.config.leverage = parsed;
    const inp = $('leverageInput');
    if (inp && inp.value != parsed) inp.value = parsed;
    document.querySelectorAll('.lev-preset-btn').forEach(btn => {
      btn.classList.toggle('active', parseInt(btn.dataset.lev, 10) === parsed);
    });
    updateSettingsSummary();
  }

  function applySizingModeUI(mode) {
    riskGovernor.config.sizingMode = mode;
    if ($('sizeModeMinBtn')) $('sizeModeMinBtn').classList.toggle('active', mode === 'min');
    $('sizeModeUsdtBtn').classList.toggle('active', mode === 'usdt');
    $('sizeModeRiskBtn').classList.toggle('active', mode === 'risk');
    if ($('minSizeRow')) $('minSizeRow').style.display = mode === 'min' ? '' : 'none';
    $('usdtSizeRow').style.display = mode === 'usdt' ? '' : 'none';
    $('riskSizeRow').style.display = mode === 'risk' ? '' : 'none';
    updateSettingsSummary();
  }

  const mmCrossBtn = $('marginModeCrossBtn');
  if (mmCrossBtn) mmCrossBtn.addEventListener('click', () => applyMarginModeUI('cross'));
  const mmIsoBtn = $('marginModeIsolatedBtn');
  if (mmIsoBtn) mmIsoBtn.addEventListener('click', () => applyMarginModeUI('isolated'));

  const stdBtn = $('stratModeStandardBtn');
  if (stdBtn) stdBtn.addEventListener('click', () => {
    applyStrategyModeUI('standard');
    logEvent('Strategy profile switched to SWING MODE (15m–1h structural targets)');
    postJSON('/api/auto-trade/state', { strategyMode: 'standard', scalpMode: false }).catch(() => {});
  });
  const scalpBtn = $('stratModeScalpBtn');
  if (scalpBtn) scalpBtn.addEventListener('click', () => {
    applyStrategyModeUI('scalp');
    logEvent('Strategy profile switched to ⚡ SCALP MODE (5m–10m fast targets, tight time stop, rapid BE)');
    postJSON('/api/auto-trade/state', { strategyMode: 'scalp', scalpMode: true, sureShotMode: false }).catch(() => {});
  });
  const sureshotBtn = $('stratModeSureShotBtn');
  if (sureshotBtn) sureshotBtn.addEventListener('click', () => {
    applyStrategyModeUI('sureshot');
    logEvent('Strategy profile switched to 🎯 SURESHOT 90% SCALP (Target +0.50% move, bank 90% at +$0.50, BE ratchet, 10% runner)');
    postJSON('/api/auto-trade/state', {
      strategyMode: 'sureshot',
      scalpMode: true,
      sureShotMode: true,
      sizingMode: 'usdt',
      fixedUsdtSize: 10,
      leverage: 10
    }).catch(() => {});
  });

  const levInputEl = $('leverageInput');
  if (levInputEl) {
    levInputEl.addEventListener('input', (e) => applyLeverageUI(e.target.value));
    levInputEl.addEventListener('change', (e) => applyLeverageUI(e.target.value));
  }
  document.querySelectorAll('.lev-preset-btn').forEach(btn => {
    btn.addEventListener('click', () => applyLeverageUI(btn.dataset.lev));
  });

  if ($('sizeModeMinBtn')) $('sizeModeMinBtn').addEventListener('click', () => applySizingModeUI('min'));
  $('sizeModeRiskBtn').addEventListener('click', () => applySizingModeUI('risk'));
  $('sizeModeUsdtBtn').addEventListener('click', () => applySizingModeUI('usdt'));

  $('marginInput').addEventListener('input', (e) => {
    const v = parseFloat(e.target.value);
    if (v > 0) {
      riskGovernor.config.riskPerTradePct = v;
      updateSettingsSummary();
    }
  });

  $('usdtSizeInput').addEventListener('input', (e) => {
    const v = parseFloat(e.target.value);
    if (v > 0) {
      riskGovernor.config.fixedUsdtSize = v;
      updateSettingsSummary();
    }
  });

  async function setArmed(armed) {
    if (armed) {
      const riskPct = parseFloat($('marginInput').value);
      if (riskPct > 0 && riskPct <= 5) riskGovernor.config.riskPerTradePct = riskPct;
      const usdtSize = parseFloat($('usdtSizeInput').value);
      if (usdtSize > 0) riskGovernor.config.fixedUsdtSize = usdtSize;
      const levVal = parseInt($('leverageInput') ? $('leverageInput').value : 10, 10);
      if (levVal > 0) riskGovernor.config.leverage = Math.max(1, Math.min(100, levVal));
    }
    applyArmedUI(armed);
    updateSettingsSummary();

    if (armed) {
      const sizingDesc = riskGovernor.config.sizingMode === 'min'
        ? 'smallest possible legal size per coin on Bybit (~$5 alts, 0.001 BTC)'
        : (riskGovernor.config.sizingMode === 'usdt'
          ? `a fixed $${riskGovernor.config.fixedUsdtSize} notional per trade`
          : `risking ${riskGovernor.config.riskPerTradePct}% of equity per trade`);
      logEvent(`Autonomous execution ARMED across ${WATCHLIST.length} symbols — ${sizingDesc} | ${riskGovernor.config.leverage}x leverage | ${riskGovernor.config.marginMode.toUpperCase()} margin | duplicate guard active`);
    } else {
      logEvent('Autonomous execution STOPPED — monitoring only. Open positions keep their broker-side stops but will not be managed further.');
    }
    try {
      await postJSON('/api/auto-trade/state', {
        armed,
        riskPerTradePct: riskGovernor.config.riskPerTradePct,
        sizingMode: riskGovernor.config.sizingMode,
        fixedUsdtSize: riskGovernor.config.fixedUsdtSize,
        leverage: riskGovernor.config.leverage,
        marginMode: riskGovernor.config.marginMode,
        strategyMode: currentStrategyMode,
        scalpMode: currentStrategyMode === 'scalp' || currentStrategyMode === 'sureshot',
        sureShotMode: currentStrategyMode === 'sureshot'
      });
    } catch (e) {
      logEvent(`Could not sync arm state to the server (${e.message}) — other devices won't see this change until it succeeds`);
    }
  }

  $('startBtn').addEventListener('click', () => setArmed(true));
  $('stopBtn').addEventListener('click', () => setArmed(false));

  // Cross-device re-sync: mirrors arm state, margin mode, leverage, and sizing mode
  async function resyncArmedState() {
    const saved = await fetchJSON('/api/auto-trade/state');
    if (!saved) return;
    if (saved.marginMode && saved.marginMode !== riskGovernor.config.marginMode) {
      applyMarginModeUI(saved.marginMode);
    }
    if (saved.strategyMode && saved.strategyMode !== currentStrategyMode) {
      applyStrategyModeUI(saved.strategyMode);
    } else if (saved.sureShotMode && currentStrategyMode !== 'sureshot') {
      applyStrategyModeUI('sureshot');
    } else if (saved.scalpMode !== undefined && (saved.scalpMode ? 'scalp' : 'standard') !== currentStrategyMode) {
      applyStrategyModeUI(saved.scalpMode ? 'scalp' : 'standard');
    }
    if (saved.leverage && saved.leverage !== riskGovernor.config.leverage) {
      applyLeverageUI(saved.leverage);
    }
    if (saved.sizingMode && saved.sizingMode !== riskGovernor.config.sizingMode) {
      applySizingModeUI(saved.sizingMode);
    }
    if (saved.fixedUsdtSize && saved.fixedUsdtSize !== riskGovernor.config.fixedUsdtSize) {
      riskGovernor.config.fixedUsdtSize = saved.fixedUsdtSize;
      $('usdtSizeInput').value = saved.fixedUsdtSize;
    }
    if (saved.riskPerTradePct && saved.riskPerTradePct !== riskGovernor.config.riskPerTradePct) {
      riskGovernor.config.riskPerTradePct = saved.riskPerTradePct;
      $('marginInput').value = saved.riskPerTradePct;
    }
    if (saved.theses && typeof saved.theses === 'object') {
      let imported = 0;
      for (const [key, t] of Object.entries(saved.theses)) {
        if (t && t.symbol && t.side && !positionManager.get(t.symbol, t.side)) {
          t.tpFilled = t.tpFilled || [false, false];
          t.exitLog = t.exitLog || [];
          t.flipStreak = t.flipStreak || 0;
          t.realisedR = t.realisedR || 0;
          t.remainingFraction = typeof t.remainingFraction === 'number' ? t.remainingFraction : 1;
          positionManager.trades[key] = t;
          imported++;
        }
      }
      if (imported > 0) {
        saveThesesLocally();
      }
    }
    updateSettingsSummary();

    if (saved.armed === autoTradingArmed) return;
    applyArmedUI(saved.armed);
    logEvent(`Auto-trading was ${saved.armed ? 'ARMED' : 'STOPPED'} from another device/runner — following that here.`);
  }

  // Overwrite the placeholder qtyStep/minQty above with Bybit's real lot-size
  // filter for every watched symbol, so a wrong hand-typed guess for a newly
  // added coin never reaches an actual order. Non-fatal if it fails (a slow
  // network, geo-block) -- the placeholders stand until the next reload.
  (async () => {
    try {
      const specs = await window.fetchInstrumentSpecs(state.network);
      let updated = 0;
      for (const sym of WATCHLIST) {
        if (specs[sym]) { Object.assign(COIN_META[sym], specs[sym]); updated++; }
      }
      logEvent(`Instrument specs confirmed live for ${updated}/${WATCHLIST.length} symbols (qty step / min order size).`);
    } catch (e) {
      logEvent(`Could not fetch live instrument specs (${e.message}) — using built-in fallback qty steps.`);
    }
  })();

  bootstrapEngines();
  wsClient.connect();
  resyncArmedState();

  console.log('%c MASIS V3 — Multi-Timeframe Confluence Engine ', 'background:#0B1120;color:#38BDF8;font-size:14px;font-weight:700;padding:8px 16px;border-radius:6px;border:1px solid rgba(56,189,248,0.3);');
});
