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
  const COIN_META = {
    BTCUSDT: { name: 'Bitcoin', short: 'BTC', color: '#F7931A', icon: 'fa-btc', brand: true, qtyStep: 0.001, minQty: 0.001 },
    ETHUSDT: { name: 'Ethereum', short: 'ETH', color: '#627EEA', icon: 'fa-ethereum', brand: true, qtyStep: 0.01, minQty: 0.01 },
    SOLUSDT: { name: 'Solana', short: 'SOL', color: '#14F195', icon: 'fa-bolt', brand: false, qtyStep: 0.1, minQty: 0.1 },
    XRPUSDT: { name: 'XRP', short: 'XRP', color: '#25A9E0', icon: 'fa-coins', brand: false, qtyStep: 1, minQty: 1 },
    DOGEUSDT: { name: 'Dogecoin', short: 'DOGE', color: '#C2A633', icon: 'fa-dog', brand: false, qtyStep: 1, minQty: 1 },
    ADAUSDT: { name: 'Cardano', short: 'ADA', color: '#0033AD', icon: 'fa-coins', brand: false, qtyStep: 1, minQty: 1 },
    AVAXUSDT: { name: 'Avalanche', short: 'AVAX', color: '#E84142', icon: 'fa-mountain', brand: false, qtyStep: 0.1, minQty: 0.1 },
    LINKUSDT: { name: 'Chainlink', short: 'LINK', color: '#2A5ADA', icon: 'fa-link', brand: false, qtyStep: 0.1, minQty: 0.1 },
    DOTUSDT: { name: 'Polkadot', short: 'DOT', color: '#E6007A', icon: 'fa-circle-dot', brand: false, qtyStep: 0.1, minQty: 0.1 },
    LTCUSDT: { name: 'Litecoin', short: 'LTC', color: '#345D9D', icon: 'fa-litecoin-sign', brand: true, qtyStep: 0.01, minQty: 0.01 },
    BCHUSDT: { name: 'Bitcoin Cash', short: 'BCH', color: '#8DC351', icon: 'fa-coins', brand: false, qtyStep: 0.01, minQty: 0.01 },
    ATOMUSDT: { name: 'Cosmos', short: 'ATOM', color: '#2E3148', icon: 'fa-atom', brand: false, qtyStep: 0.1, minQty: 0.1 },
    NEARUSDT: { name: 'NEAR Protocol', short: 'NEAR', color: '#00C08B', icon: 'fa-coins', brand: false, qtyStep: 0.1, minQty: 0.1 },
    APTUSDT: { name: 'Aptos', short: 'APT', color: '#00D2A0', icon: 'fa-coins', brand: false, qtyStep: 0.1, minQty: 0.1 },
    ARBUSDT: { name: 'Arbitrum', short: 'ARB', color: '#28A0F0', icon: 'fa-layer-group', brand: false, qtyStep: 1, minQty: 1 },
    OPUSDT: { name: 'Optimism', short: 'OP', color: '#FF0420', icon: 'fa-layer-group', brand: false, qtyStep: 1, minQty: 1 },
    SUIUSDT: { name: 'Sui', short: 'SUI', color: '#6FBCF0', icon: 'fa-coins', brand: false, qtyStep: 1, minQty: 1 },
    TONUSDT: { name: 'Toncoin', short: 'TON', color: '#0088CC', icon: 'fa-paper-plane', brand: false, qtyStep: 0.1, minQty: 0.1 },
    TRXUSDT: { name: 'TRON', short: 'TRX', color: '#EF0027', icon: 'fa-coins', brand: false, qtyStep: 1, minQty: 1 },
    SHIB1000USDT: { name: 'Shiba Inu', short: 'SHIB', color: '#F00500', icon: 'fa-dog', brand: false, qtyStep: 1000, minQty: 1000 },
    UNIUSDT: { name: 'Uniswap', short: 'UNI', color: '#FF007A', icon: 'fa-coins', brand: false, qtyStep: 0.1, minQty: 0.1 },
    FILUSDT: { name: 'Filecoin', short: 'FIL', color: '#0090FF', icon: 'fa-coins', brand: false, qtyStep: 0.1, minQty: 0.1 },
    ETCUSDT: { name: 'Ethereum Classic', short: 'ETC', color: '#328332', icon: 'fa-ethereum', brand: false, qtyStep: 0.01, minQty: 0.01 },
    XLMUSDT: { name: 'Stellar', short: 'XLM', color: '#14B6E7', icon: 'fa-coins', brand: false, qtyStep: 1, minQty: 1 },
    ICPUSDT: { name: 'Internet Computer', short: 'ICP', color: '#3B00B9', icon: 'fa-coins', brand: false, qtyStep: 0.1, minQty: 0.1 },
    HBARUSDT: { name: 'Hedera', short: 'HBAR', color: '#000000', icon: 'fa-coins', brand: false, qtyStep: 1, minQty: 1 },
    INJUSDT: { name: 'Injective', short: 'INJ', color: '#00D2FF', icon: 'fa-syringe', brand: false, qtyStep: 0.1, minQty: 0.1 },
    SEIUSDT: { name: 'Sei', short: 'SEI', color: '#8B2FE8', icon: 'fa-coins', brand: false, qtyStep: 1, minQty: 1 },
    AAVEUSDT: { name: 'Aave', short: 'AAVE', color: '#B6509E', icon: 'fa-ghost', brand: false, qtyStep: 0.01, minQty: 0.01 },
    ALGOUSDT: { name: 'Algorand', short: 'ALGO', color: '#000000', icon: 'fa-coins', brand: false, qtyStep: 1, minQty: 1 }
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
      shadowTracker.active[sym] = {
        id: `${sym}-${Date.now()}`,
        symbol: sym,
        direction: s.direction,
        grade: s.grade,
        setupType: s.setupType,
        entry: s.entry,
        stop: s.stopLoss,
        target: s.takeProfit[0],
        startedAt: Date.now(),
        gatesPassed: (s.blockers || []).length === 0,
        decision: s.decision
      };
    }

    const active = shadowTracker.active[sym];
    if (!active) return;
    const price = s.entry && lastKnown[sym] ? (lastKnown[sym].price || s.entry) : s.entry;
    if (!price) return;

    const hitTarget = active.direction === 'LONG' ? price >= active.target : price <= active.target;
    const hitStop = active.direction === 'LONG' ? price <= active.stop : price >= active.stop;
    if (hitTarget || hitStop) {
      shadowTracker.history.unshift({
        ...active,
        status: hitTarget ? 'WIN' : 'LOSS',
        resolvedAt: Date.now(),
        exitPrice: price
      });
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

  WATCHLIST.forEach(sym => {
    engines[sym] = new MasisEngine({ symbol: sym, metaLearner, swarmMode: 'advisory' });
    whaleTrackers[sym] = typeof WhaleTracker !== 'undefined' ? new WhaleTracker({ symbol: sym }) : null;
  });

  // ─── Shared supervisory agents ───
  const riskGovernor = new MasisRiskGovernor.RiskGovernor({ riskPerTradePct: 0.5 });
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

  function logEvent(msg) {
    const time = new Date().toTimeString().split(' ')[0];
    guardianLogEntries.unshift(`[${time}] ${msg}`);
    if (guardianLogEntries.length > 40) guardianLogEntries.pop();
    const el = $('guardianLog');
    if (el) el.innerHTML = guardianLogEntries.map(e => `<div class="guardian-log-item">${e}</div>`).join('');
  }

  function roundQty(symbol, qty) {
    const step = (COIN_META[symbol] || {}).qtyStep || 0.001;
    const decimals = Math.max(0, Math.round(-Math.log10(step)));
    return +(Math.floor(qty / step) * step).toFixed(decimals);
  }

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

  async function fetchJSON(url) {
    try { const r = await fetch(url); return await readJSONResponse(r, url); }
    catch (e) { console.warn('API fetch error:', url, e.message); return null; }
  }

  async function postJSON(url, body) {
    const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    return readJSONResponse(r, url);
  }

  // ─────────────────────────────────────────────────────────────────────
  // Supervisor consultation — gated, so a decision-grade call is the only
  // kind that ever costs anything.
  // ─────────────────────────────────────────────────────────────────────
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
      renderSupervisorPanel(sym, res);
    } catch (e) {
      llmGovernor.failCall();
      logEvent(`Supervisor call failed for ${sym}: ${e.message} — local gates stand unmodified`);
    }
  }

  function renderSupervisorPanel(sym, res) {
    const out = $('deepseekOutput'), meta = $('deepseekMeta');
    if (!out || !meta) return;
    out.style.display = 'block';
    out.textContent = [
      `VERDICT: ${res.verdict}  (confidence ${res.confidence}/100)`,
      res.rationale ? `Rationale: ${res.rationale}` : '',
      res.risk ? `Biggest risk: ${res.risk}` : '',
      res.is_fallback ? `\n[Local fallback — ${res.fallback_reason}]\nThe verdict above did not come from the model. Local gates decide on their own; an unreachable supervisor never blocks a valid trade and never approves an invalid one.` : ''
    ].filter(Boolean).join('\n');
    const st = llmGovernor.status();
    meta.textContent = `${sym} · ${new Date().toTimeString().split(' ')[0]} · ${res.is_fallback ? 'local' : 'model'} · ${st.callsToday}/${st.dailyBudget} calls today · ${st.totalSkipped} skipped`;
  }

  // ─────────────────────────────────────────────────────────────────────
  // Execution
  // ─────────────────────────────────────────────────────────────────────
  async function executeEntry(sym, s) {
    if (!autoTradingArmed) return;
    if (s.decision !== 'BUY' && s.decision !== 'SELL') return;
    if (!s.stopLoss || !s.takeProfit || !s.takeProfit.length) return;
    const key = `entry-${sym}`;
    if (inFlightActions.has(key)) return;

    const gate = riskGovernor.canOpen({ symbol: sym, openPositions: openPositionsSnapshot });
    if (!gate.allowed) {
      logEvent(`${s.decision} ${sym} (${s.setupType}, grade ${s.grade}) not taken — ${gate.reasons[0]}`);
      return;
    }

    const price = lastKnown[sym] ? lastKnown[sym].price : 0;
    if (!price) return;

    const meta = COIN_META[sym] || {};
    const sized = riskGovernor.sizePosition({
      entry: price, stop: s.stopLoss, equity: accountEquity, symbol: sym,
      qtyStep: meta.qtyStep, minQty: meta.minQty
    });
    if (!sized.qty) {
      logEvent(`${sym} entry skipped — ${sized.rejected}`);
      return;
    }
    const qty = roundQty(sym, sized.qty);
    const side = s.decision === 'BUY' ? 'Buy' : 'Sell';

    inFlightActions.add(key);
    try {
      // Only the STOP is attached broker-side. The old build also attached
      // takeProfit[0] at full size, which closed the whole position at 1.1R and
      // made the three-level ladder below unreachable. The stop is the one order
      // that must survive a browser crash; the targets are managed here.
      const res = await postJSON('/api/order/place', {
        category: 'linear', symbol: sym, side, orderType: 'Market',
        qty, stopLoss: s.stopLoss
      });
      if (res.retCode === 0) {
        positionManager.open({
          symbol: sym, side,
          entryPrice: price,
          stopLoss: s.stopLoss,
          invalidation: s.invalidation,
          targets: s.takeProfit,
          riskDist: Math.abs(price - s.stopLoss),
          qty,
          originalQty: qty,
          riskAmount: sized.riskAmount,
          setupName: s.setupType,
          grade: s.grade,
          score: s.score,
          regime: s.regime,
          narrative: s.narrative
        });
        logEvent(`ENTERED ${side.toUpperCase()} ${sym} qty=${qty} @ ~${price.toLocaleString()} | ${s.setupType} grade ${s.grade} (${s.score}/100) | SL ${s.stopLoss} · invalidation ${s.invalidation} | risking $${sized.riskAmount} (${riskGovernor.config.riskPerTradePct}% of equity) | R:R ${s.riskReward}`);
      } else {
        logEvent(`Order rejected for ${sym}: ${res.retMsg || 'unknown error'}`);
      }
      setTimeout(pollDemoData, 900);
    } catch (e) {
      logEvent(`Entry failed for ${sym}: ${e.message}`);
    } finally {
      inFlightActions.delete(key);
    }
  }

  async function recordOutcome(trade, exitPrice, reason, qtyClosed, realisedPnl) {
    const r = trade.riskDist ? ((trade.side === 'Buy' ? exitPrice - trade.entryPrice : trade.entryPrice - exitPrice) / trade.riskDist) : 0;
    try {
      await postJSON('/api/trades/record', {
        symbol: trade.symbol, side: trade.side.toUpperCase(),
        entry: trade.entryPrice, exit: exitPrice,
        pnl: realisedPnl, setup_type: trade.setupName,
        grade: trade.grade, score: trade.score,
        r_multiple: +r.toFixed(3), exit_reason: reason,
        reason: positionManager.explain(trade)
      });
    } catch (e) {
      logEvent(`Failed to record outcome for ${trade.symbol}: ${e.message}`);
    }
    if (trade.riskAmount) riskGovernor.recordOutcome({ symbol: trade.symbol, pnl: realisedPnl, rMultiple: r });
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
      const trade = positionManager.get(pos.symbol, pos.side);
      const s = latestStates[pos.symbol];

      if (!trade) {
        cards.push(`<div class="guardian-position">
          <span class="guardian-pill safe">${pos.symbol} ${pos.side.toUpperCase()} — UNMANAGED</span>
          <ul class="guardian-reasons"><li>Opened outside this session, so there is no entry thesis or invalidation level to manage against. Monitoring only; it will not be auto-closed.</li></ul>
        </div>`);
        continue;
      }

      const mark = parseFloat(pos.markPrice || 0);
      const confirmed = engines[pos.symbol] ? engines[pos.symbol].confirmed('mtf') : [];
      const action = positionManager.evaluate(trade, {
        price: mark,
        confirmedCandle: confirmed.length ? confirmed[confirmed.length - 1] : null,
        atr: s && s.regimeMetrics ? s.regimeMetrics.htfAtr : null,
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
        verdict = `TAKING TP${action.tpIndex + 1}`;
        reasons.push(action.detail);
        if (autoTradingArmed) {
          const sliceQty = trade.originalQty * action.fraction;
          positionManager.markTpFilled(trade, action.tpIndex, mark);
          await closePositionSlice(pos, trade, sliceQty, `TP${action.tpIndex + 1}`, action.detail);
        }
      } else if (action.action === 'MOVE_STOP') {
        verdict = action.reason === 'BREAK_EVEN' ? 'STOP → BREAK-EVEN' : 'TRAILING STOP';
        reasons.push(action.detail);
        if (autoTradingArmed) {
          positionManager.markStopMoved(trade, action.newStop, action.reason);
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

      cards.push(`<div class="guardian-position">
        <span class="guardian-pill ${pillClass}">${pos.symbol} ${pos.side.toUpperCase()} — ${verdict}</span>
        <div class="guardian-meta font-mono">${trade.setupName} · grade ${trade.grade} · entry ${trade.entryPrice} · stop ${trade.stopLoss} · invalidation ${trade.invalidation} · TP ${(trade.targets || []).join(' / ')}</div>
        ${reasons.length ? `<ul class="guardian-reasons">${reasons.map(x => `<li>${x}</li>`).join('')}</ul>` : ''}
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

      if (s.grade === 'A' || s.grade === 'A+') maybeConsultSupervisor(sym, s);
      if (s.decision === 'BUY' || s.decision === 'SELL') executeEntry(sym, s);

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
      el.innerHTML = '<div class="analyst-empty">Analyst panel is warming up — it needs higher-timeframe history before it will offer a read.</div>';
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
    set('DIV-08', `Risk/trade: ${gov.riskPerTradePct}%`, `Day: ${gov.realisedPnlToday >= 0 ? '+' : ''}${gov.realisedPnlToday}`, gov.paused ? 'evaluating' : 'active', gov.paused ? gov.pauseReason : 'ENFORCED');
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
          // positionValue is Bybit's own reported "Position value" for this
          // position (confirmed field on GET /v5/position/list) -- use it
          // directly so this matches what Bybit itself calls the size,
          // rather than our own avgPrice x size approximation (which is the
          // ENTRY value and drifts from it as price moves). Only fall back
          // to computing it if Bybit didn't send the field.
          const notional = parseFloat(p.positionValue || 0) || (entryPrice * parseFloat(p.size || 0));
          // ROI% is against margin (positionIM) when Bybit reports it -- that's
          // the actual return on capital committed to the trade. Falling back
          // to notional only covers the rare case IM isn't reported yet.
          const pnlPct = im > 0 ? (pnl / im) * 100 : (notional > 0 ? (pnl / notional) * 100 : 0);
          const sl = parseFloat(p.stopLoss || 0);
          const trade = positionManager.get(p.symbol, p.side);
          const rTxt = trade ? `${positionManager.currentR(trade, parseFloat(p.markPrice || 0)).toFixed(2)}R` : '--';
          return `<tr>
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
            <td><button class="close-btn-sm" onclick="closePosition('${p.symbol}','${p.side}','${p.size}')">Close</button></td>
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
      // Sample size sits beside the win rate. A win rate over a handful of
      // trades is not a measurement, and the previous dashboard presented one
      // computed from four invented rows as though it were.
      $('winRateVal').title = n < 30
        ? `Only ${n} closed trades. A win rate needs a few hundred before it means anything; treat this as a running tally, not a measurement.`
        : `${n} closed trades. Expectancy ${perf.expectancy_r != null ? perf.expectancy_r + 'R' : 'n/a'} over ${perf.r_sample_size} R-tracked trades.`;

      const netPnl = perf.net_pnl || 0;
      $('pnlVal').textContent = (netPnl >= 0 ? '+$' : '-$') + Math.abs(netPnl).toLocaleString('en-US', { minimumFractionDigits: 2 });
      $('pnlVal').style.color = netPnl >= 0 ? 'var(--green)' : 'var(--red)';
      $('wlVal').textContent = `${perf.win_count || 0}W/${perf.loss_count || 0}L`;

      const history = perf.trade_history || [];
      setupPerformance = computeSetupPerformance(history);
      $('tradeCount').textContent = `${history.length} trades`;
      const histTbody = $('historyTbody');
      if (!history.length) {
        histTbody.innerHTML = '<tr><td colspan="10" class="empty-msg">No trade history yet</td></tr>';
      } else {
        histTbody.innerHTML = history.map(t => {
          const pnlClass = t.status === 'WIN' ? 'text-green' : 'text-red';
          const reasonFull = (t.reason || '').replace(/"/g, '&quot;');
          const rTxt = typeof t.r_multiple === 'number' ? `${t.r_multiple >= 0 ? '+' : ''}${t.r_multiple}R` : '--';
          return `<tr>
            <td>${t.id}</td><td>${t.time}</td><td>${t.symbol}</td>
            <td><span class="badge ${String(t.side).toLowerCase() === 'buy' ? 'buy' : 'sell'}">${t.side}</span></td>
            <td>${t.entry ? t.entry.toLocaleString() : '--'}</td>
            <td>${t.exit ? t.exit.toLocaleString() : '--'}</td>
            <td class="${pnlClass}">${t.pnl >= 0 ? '+' : ''}$${Math.abs(t.pnl).toFixed(2)}</td>
            <td class="${pnlClass}">${rTxt}</td>
            <td class="font-mono" style="font-size:9px;">${t.setup_type || '--'}${t.grade ? ' ' + t.grade : ''}</td>
            <td style="font-size:9px;" title="${reasonFull}">${t.exit_reason || '--'}</td>
          </tr>`;
        }).join('');
      }
    }
  }

  setTimeout(pollDemoData, 1000);
  setInterval(pollDemoData, 5000);

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
  function applyArmedUI(armed) {
    autoTradingArmed = armed;
    $('armStatus').textContent = armed ? 'ARMED' : 'STOPPED';
    $('armStatus').className = armed ? 'arm-status armed' : 'arm-status stopped';
    $('startBtn').disabled = armed;
    $('stopBtn').disabled = !armed;
    $('marginInput').disabled = armed;
    $('usdtSizeInput').disabled = armed;
    $('sizeModeRiskBtn').disabled = armed;
    $('sizeModeUsdtBtn').disabled = armed;
  }

  function applySizingModeUI(mode) {
    riskGovernor.config.sizingMode = mode;
    $('sizeModeRiskBtn').classList.toggle('active', mode === 'risk');
    $('sizeModeUsdtBtn').classList.toggle('active', mode === 'usdt');
    $('riskSizeRow').style.display = mode === 'risk' ? '' : 'none';
    $('usdtSizeRow').style.display = mode === 'usdt' ? '' : 'none';
  }

  $('sizeModeRiskBtn').addEventListener('click', () => applySizingModeUI('risk'));
  $('sizeModeUsdtBtn').addEventListener('click', () => applySizingModeUI('usdt'));

  async function setArmed(armed) {
    if (armed) {
      const riskPct = parseFloat($('marginInput').value);
      if (riskPct > 0 && riskPct <= 5) riskGovernor.config.riskPerTradePct = riskPct;
      const usdtSize = parseFloat($('usdtSizeInput').value);
      if (usdtSize > 0) riskGovernor.config.fixedUsdtSize = usdtSize;
    }
    applyArmedUI(armed);
    if (armed) {
      const sizingDesc = riskGovernor.config.sizingMode === 'usdt'
        ? `a fixed $${riskGovernor.config.fixedUsdtSize} notional per trade`
        : `risking ${riskGovernor.config.riskPerTradePct}% of equity per trade`;
      logEvent(`Autonomous execution ARMED across ${WATCHLIST.length} symbols — ${sizingDesc}, max ${riskGovernor.config.maxConcurrentPositions} concurrent, daily loss limit ${riskGovernor.config.maxDailyLossPct}%`);
    } else {
      logEvent('Autonomous execution STOPPED — monitoring only. Open positions keep their broker-side stops but will not be managed further.');
    }
    try {
      await postJSON('/api/auto-trade/state', {
        armed,
        riskPerTradePct: riskGovernor.config.riskPerTradePct,
        sizingMode: riskGovernor.config.sizingMode,
        fixedUsdtSize: riskGovernor.config.fixedUsdtSize
      });
    } catch (e) {
      logEvent(`Could not sync arm state to the server (${e.message}) — other devices won't see this change until it succeeds`);
    }
  }

  $('startBtn').addEventListener('click', () => setArmed(true));
  $('stopBtn').addEventListener('click', () => setArmed(false));

  // Cross-device re-sync: if the server's arm state differs from ours --
  // because another device changed it, or (on first load) because we
  // haven't adopted it yet -- follow it. Called from pollDemoData()'s very
  // first tick (1s after load) as well as every cycle after, so a page
  // load picks up whatever's currently armed instead of always starting
  // STOPPED, with no separate startup fetch needed.
  async function resyncArmedState() {
    const saved = await fetchJSON('/api/auto-trade/state');
    if (!saved) return;
    if (saved.sizingMode && saved.sizingMode !== riskGovernor.config.sizingMode) {
      applySizingModeUI(saved.sizingMode);
    }
    if (saved.fixedUsdtSize && saved.fixedUsdtSize !== riskGovernor.config.fixedUsdtSize) {
      riskGovernor.config.fixedUsdtSize = saved.fixedUsdtSize;
      $('usdtSizeInput').value = saved.fixedUsdtSize;
    }
    if (saved.armed === autoTradingArmed) return;
    if (saved.armed) riskGovernor.config.riskPerTradePct = saved.riskPerTradePct || riskGovernor.config.riskPerTradePct;
    $('marginInput').value = riskGovernor.config.riskPerTradePct;
    applyArmedUI(saved.armed);
    logEvent(`Auto-trading was ${saved.armed ? 'ARMED' : 'STOPPED'} from another device — following that here.`);
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

  console.log('%c MASIS V3 — Multi-Timeframe Confluence Engine ', 'background:#0B1120;color:#38BDF8;font-size:14px;font-weight:700;padding:8px 16px;border-radius:6px;border:1px solid rgba(56,189,248,0.3);');
});
