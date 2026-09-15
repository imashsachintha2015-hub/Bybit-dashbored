/**
 * MASIS V3 — Multi-Timeframe Confluence Decision Engine.
 *
 * WHAT CHANGED FROM V2, AND WHY
 *
 * V2 ran a single 1-minute series through a chain of heuristics and then voted
 * on direction by counting bullet points. Four structural problems came out of
 * that, and all four are addressed here:
 *
 *  1. ONE TIMEFRAME. Every read was 1-minute. At that resolution the EMA stack
 *     and "break of structure" flip many times an hour in tape that is going
 *     nowhere, so the engine emitted directional calls continuously regardless
 *     of whether a directional opportunity existed. V3 requires a higher
 *     timeframe to establish regime and bias before the lower timeframe is
 *     allowed to propose anything at all.
 *
 *  2. UNCONFIRMED BARS. V2 computed structure from the forming candle. Its
 *     break-of-structure test compared the live price against a rolling maximum
 *     that *included the current bar's own high*, so the test could essentially
 *     only pass on the exact tick that printed a new high — and every indicator
 *     downstream flickered intra-bar. V3 computes all structure from confirmed
 *     candles only; live price is used solely for entry distance and risk.
 *
 *  3. CORRELATED EVIDENCE COUNTED AS INDEPENDENT. "Price > VWAP", "EMA9 >
 *     EMA21" and "delta > 0" are three ways of saying price rose recently. V2
 *     needed three bullets to authorise a trade, so it authorised one the
 *     moment price ticked up — buying strength at highs with a stop under
 *     nothing. V3 replaces voting with named setups, each requiring a specific
 *     structural context and each carrying an invalidation level that exists on
 *     the chart.
 *
 *  4. A TRUST SCORE THAT WAS A CONSTANT. V2 built its score from four
 *     hard-coded numbers (85/85/90/70), so an authorised call always scored
 *     ~83 and the "minimum trust 65" filter downstream could never reject
 *     anything. V3's score is a weighted function of the actual evidence, and
 *     it is the same number the backtest reports, so the grades mean something
 *     measurable.
 *
 * The engine is deliberately biased toward NOT trading. Most of the time the
 * correct output is NO_TRADE, and saying so is the feature.
 */
(function (root, factory) {
  const deps = typeof require === 'function'
    ? {
        I: require('./agents/indicators.js'),
        Regime: require('./agents/regime-agent.js'),
        Playbooks: require('./agents/playbooks.js'),
        Flow: require('./agents/flow-agent.js'),
        Panel: require('./swarm/panel.js'),
        Debate: require('./swarm/debate.js'),
        MetaLearner: require('./swarm/meta-learner.js')
      }
    : {
        I: root.MasisIndicators,
        Regime: root.MasisRegimeAgent,
        Playbooks: root.MasisPlaybooks,
        Flow: root.MasisFlowAgent,
        Panel: root.SwarmPanel,
        Debate: root.SwarmDebate,
        MetaLearner: root.SwarmMetaLearner
      };
  const api = factory(deps);
  if (typeof module === 'object' && module.exports) module.exports = api;
  else { root.MasisEngine = api.MasisEngine; root.MasisEngineModule = api; }
})(typeof globalThis !== 'undefined' ? globalThis : this, function ({ I, Regime, Playbooks, Flow, Panel, Debate, MetaLearner }) {

  /**
   * Timeframes, in Bybit interval notation.
   *
   * These are 5m/15m/1h rather than the 1m/5m/15m the first cut used, and the
   * reason is cost, measured rather than assumed. A round trip on Bybit linear
   * perps costs roughly 0.15% of notional once taker fees and slippage are
   * counted on both sides. Expressed against the trade's own risk, that is
   * 0.15% / (stop distance as % of price) — so on 5-minute structure, where
   * stops land around 0.3-0.5%, fees eat 30-50% of every 1R the system earns.
   * The backtest bears this out: on 5m structure the engine is negative at
   * EVERY parameter setting tried (-0.30R to -0.41R expectancy), while the same
   * logic on 15m structure, with proportionally wider stops, is roughly
   * break-even. Trading a faster timeframe with taker entries is not a tuning
   * problem; it is a losing proposition by arithmetic.
   */
  const TIMEFRAMES = { ltf: '5', mtf: '15', htf: '60' };

  /** Minimum grade the engine will authorise for autonomous execution.
   * B-grade setups are shown to the operator but not traded automatically. */
  const AUTO_TRADE_MIN_GRADE = 'A';
  const GRADE_RANK = { 'A+': 3, 'A': 2, 'B': 1, 'C': 0, 'D': 0 };

  /** Minimum 24h quote-currency turnover (USDT) a symbol must print to be
   * considered tradable at all. The watchlist spans large-cap majors down to
   * thin alts; regime and spread gates are both self-relative (they compare a
   * symbol against its own recent range), so neither one catches a coin that
   * is simply too thin for real size — a book can look tight for a moment on
   * a handful of resting orders. This is the one absolute floor in the gate
   * stack, deliberately: liquidity below it doesn't get more tradable just
   * because the tape looks orderly. */
  const MIN_24H_TURNOVER_USD = 5_000_000;

  class MasisEngine {
    constructor(options = {}) {
      this.symbol = options.symbol || 'BTCUSDT';
      this.maxCandles = options.maxCandles || 400;
      this.autoTradeMinGrade = options.autoTradeMinGrade || AUTO_TRADE_MIN_GRADE;
      this.minLiquidityUsd = options.minLiquidityUsd || MIN_24H_TURNOVER_USD;
      // Injectable clock. The live system uses the wall clock; the backtest
      // supplies the timestamp of the bar being replayed, so staleness and
      // recency checks mean the same thing in both.
      this.now = options.now || (() => Date.now());

      this.candles = { ltf: [], mtf: [], htf: [] };
      this.ticker = {};
      this.flow = new Flow.FlowAgent();

      this.whaleSignal = { dominantSide: 'NEUTRAL', netDeltaUsd: 0, whaleCount: 0 };
      this.newsSignal = { sentimentScore: 0, sentimentLabel: 'NEUTRAL', headline: '', highImpactNegativeAt: 0 };
      this.macroSignal = { btcDominance: 0, marketCapChange24hPct: 0, riskOff: false };
      this.setupPerformance = {};   // setupName -> { wins, losses, recentLossStreak }
      this.llmVerdict = null;       // { verdict, confidence, at, fingerprint }

      this.atrPctHistory = [];
      this.lastState = null;

      // Derivatives context (open interest, funding, crowd positioning, real
      // taker volume), fed hourly. Without it the Positioning analyst reports
      // UNAVAILABLE rather than pretending the reading is neutral.
      this.derivs = [];

      // ── Per-coin position tracking (Research: one trade per coin at a time) ──
      // Multiple coins can have simultaneous trades, but the SAME coin cannot.
      // This is the engine-side guard; the server has its own Bybit-API-level
      // guard as a second layer.
      this.activePositions = new Set();

      // ── BTC dominance trend (Research §7.1: rotation hierarchy) ──
      // Rolling window to detect whether BTC.D is rising. When it is, altcoin
      // longs are blocked — capital is flowing TO Bitcoin, not away from it.
      this.btcDominanceHistory = [];

      // The swarm. The meta-learner persists across trades so analyst weights
      // reflect measured reliability rather than assumption.
      this.metaLearner = options.metaLearner ||
        (MetaLearner ? new MetaLearner.MetaLearner() : null);
      this.lastPanel = [];
      this.lastVerdict = null;
      /**
       * How much authority the analyst panel has over execution. Three modes,
       * and the default is set by measurement rather than by preference:
       *
       *   'off'      — panel does not run.
       *   'advisory' — panel runs, is displayed, and its FATAL vetoes block
       *                trades (entering into a liquidity shelf, joining crowded
       *                and well-paid positioning, leaning on a spoofed level,
       *                trading into a fresh trap). Its directional opinion does
       *                NOT have to agree with the setup.
       *   'gating'   — as advisory, plus the panel's thesis must match the
       *                setup's direction.
       *
       * Default is 'advisory'. On the backtest window, full gating measured
       * WORSE than no panel at all (-0.389R vs -0.092R), while the panel's
       * fatal vetoes are risk-management rules that are sound on their own
       * terms. Shipping the mode that measured worse because it sounds more
       * sophisticated would be the same mistake V2 made with its agent names.
       */
      // 'off'      -- panel does not run at all
      // 'observe'   -- panel runs and is reported, but changes NO decision
      // 'advisory'  -- panel runs and its fatal hazard vetoes block entries
      // 'gating'    -- advisory, plus direction agreement and score modulation
      //
      // 'observe' exists because the choice used to be between blindness and
      // cost. Part IX measured advisory mode rejecting ~70% of setups through
      // its fatal vetoes, taking total R from +46.5 to +17.8, so the panel was
      // switched off and its reasoning went with it. Observe keeps the analysis
      // visible and recorded while leaving execution untouched, so whether
      // those vetoes were right becomes measurable instead of assumed.
      this.swarmMode = options.swarmMode || (options.swarmEnabled === false ? 'off' : 'advisory');
      this.swarmEnabled = this.swarmMode !== 'off';
      this.swarmDecides = this.swarmMode === 'advisory' || this.swarmMode === 'gating';
    }

    // ─── Ingestion ───

    /** Bulk-seed a timeframe from the REST kline backfill. */
    seedCandles(tf, candles) {
      if (!candles || !candles.length || !this.candles[tf]) return;
      this.candles[tf] = candles
        .slice(-this.maxCandles)
        .map(c => ({
          start: +c.start, open: +c.open, high: +c.high, low: +c.low,
          close: +c.close, volume: +c.volume, confirm: true
        }));
    }

    processKline(tf, k) {
      if (!k || !this.candles[tf]) return;
      const candle = {
        start: parseInt(k.start),
        open: parseFloat(k.open), high: parseFloat(k.high),
        low: parseFloat(k.low), close: parseFloat(k.close),
        volume: parseFloat(k.volume), confirm: !!k.confirm
      };
      const arr = this.candles[tf];
      const last = arr[arr.length - 1];
      if (last && last.start === candle.start) arr[arr.length - 1] = candle;
      else arr.push(candle);
      if (arr.length > this.maxCandles) arr.splice(0, arr.length - this.maxCandles);

      if (tf === 'htf' && candle.confirm) {
        const confirmed = this.confirmed('htf');
        if (confirmed.length > 20) {
          this.atrPctHistory.push(I.atrPct(confirmed, 14));
          if (this.atrPctHistory.length > 300) this.atrPctHistory.shift();
        }
      }
    }

    processTicker(t) { if (t) this.ticker = Object.assign({}, this.ticker, t); }
    processTrades(arr) { this.flow.ingestTrades(arr); }
    processOrderBook(b) { this.flow.ingestBook(b); }

    setWhaleSignal(s) { if (s) this.whaleSignal = Object.assign({}, this.whaleSignal, s); }
    setMacroSignal(s) {
      if (!s) return;
      this.macroSignal = Object.assign({}, this.macroSignal, s);
      // Track BTC dominance trend for the rotation gate (Research §7.1)
      if (typeof s.btcDominance === 'number' && s.btcDominance > 0) {
        this.btcDominanceHistory.push(s.btcDominance);
        if (this.btcDominanceHistory.length > 48) this.btcDominanceHistory.shift(); // ~2 days of hourly samples
      }
    }
    setNewsSignal(s) {
      if (!s) return;
      this.newsSignal = Object.assign({}, this.newsSignal, s);
      if (s.sentimentScore <= -0.5 && s.impact === 'HIGH') {
        this.newsSignal.highImpactNegativeAt = Date.now();
      }
    }
    setSetupPerformance(p) { if (p) this.setupPerformance = p; }
    setDerivatives(rows) { if (Array.isArray(rows)) this.derivs = rows; }

    // ── Per-coin position tracking API (Research: one trade per coin) ──
    markPositionOpen(symbol) { this.activePositions.add(symbol); }
    markPositionClosed(symbol) { this.activePositions.delete(symbol); }
    hasActivePosition(symbol) { return this.activePositions.has(symbol || this.symbol); }
    setLlmVerdict(v) { this.llmVerdict = v; }

    reset() {
      this.candles = { ltf: [], mtf: [], htf: [] };
      this.ticker = {};
      this.flow.reset();
      this.atrPctHistory = [];
      this.llmVerdict = null;
    }

    /** Confirmed candles only. The forming bar is never used for analysis —
     * see note 2 in the header. */
    confirmed(tf) {
      const arr = this.candles[tf] || [];
      if (!arr.length) return [];
      return arr[arr.length - 1].confirm ? arr : arr.slice(0, -1);
    }

    get price() {
      const t = parseFloat(this.ticker.lastPrice || 0);
      if (t) return t;
      const c = this.confirmed('ltf');
      return c.length ? c[c.length - 1].close : 0;
    }

    // ─── Data validity ───
    validateData() {
      const reasons = [];
      let quality = 100;

      if (!this.price) {
        return { status: 'INVALID', quality: 0, reasons: ['No price available from ticker or klines'] };
      }
      const book = this.flow.bookState();
      if (book.available) {
        // Only a book that actually quotes both sides can be crossed. A book
        // reporting zeros is an absent book, which is a degradation, not a
        // reason to halt.
        if (book.bestBid > 0 && book.bestAsk > 0 && book.bestBid >= book.bestAsk) {
          return { status: 'INVALID', quality: 0, reasons: [`Crossed book: bid ${book.bestBid} >= ask ${book.bestAsk}`] };
        }
        if (book.spreadPct > 0.35) {
          quality -= 40;
          reasons.push(`Spread ${book.spreadPct.toFixed(3)}% is too wide for disciplined execution`);
        }
      } else {
        quality -= 15;
        reasons.push('Order book unavailable — execution-quality checks are degraded');
      }

      const c = this.confirmed('ltf');
      if (c.length) {
        const lastBarAge = this.now() - (c[c.length - 1].start + 60000);
        if (lastBarAge > 5 * 60 * 1000) {
          quality -= 35;
          reasons.push(`Most recent confirmed 1m bar is ${Math.round(lastBarAge / 60000)} min stale — feed may have stalled`);
        }
      }

      const status = quality >= 80 ? 'VALID' : quality >= 45 ? 'DEGRADED' : 'INVALID';
      return { status, quality: Math.max(0, quality), reasons };
    }

    // ─── Hard confluence gates, applied AFTER a setup is found ───
    // These can only ever remove a trade, never create one. A setup that fails
    // any of them is reported with the specific reason rather than silently
    // downgraded, so the operator can see what the system is reacting to.
    applyGates(candidate, regime) {
      const gates = {};
      const blocks = [];

      const validity = this.validateData();
      gates.dataValid = validity.status !== 'INVALID';
      if (!gates.dataValid) blocks.push(`Data validity: ${validity.reasons[0]}`);

      gates.regimeTradable = regime.tradable;
      if (!gates.regimeTradable) blocks.push(`Regime ${regime.regime} is not tradable`);

      const book = this.flow.bookState();
      gates.spreadAcceptable = !book.available || book.spreadPct < 0.12;
      if (!gates.spreadAcceptable) blocks.push(`Spread ${book.spreadPct.toFixed(3)}% exceeds the 0.12% execution ceiling`);

      // Liquidity floor: a symbol thin enough here doesn't get a pass just
      // because its regime and spread happen to look clean at this instant —
      // see MIN_24H_TURNOVER_USD's comment for why this is the one absolute
      // (non-self-relative) gate in the stack. No ticker snapshot yet is not
      // treated as a failure, the same convention spreadAcceptable uses for
      // an unavailable book.
      const turnover24h = parseFloat(this.ticker.turnover24h);
      gates.liquidityAcceptable = !Number.isFinite(turnover24h) || turnover24h >= this.minLiquidityUsd;
      if (!gates.liquidityAcceptable) {
        blocks.push(`24h turnover $${Math.round(turnover24h).toLocaleString()} is below the $${this.minLiquidityUsd.toLocaleString()} floor — too thin to trade cleanly`);
      }

      if (!candidate) {
        gates.setupPresent = false;
        blocks.push('No playbook pattern is present — this is the normal state, not a failure');
        return { gates, blocks, validity };
      }
      gates.setupPresent = true;

      // Geometry must be worth the risk after costs.
      gates.rewardViable = candidate.geometry.viable;
      if (!gates.rewardViable) blocks.push(candidate.geometry.rejectReason);

      // News: a high-impact negative catalyst inside 30 minutes vetoes longs
      // outright. Directional technicals do not survive a live exchange hack.
      const newsRecent = this.now() - (this.newsSignal.highImpactNegativeAt || 0) < 30 * 60 * 1000;
      gates.newsAligned = !(candidate.direction === 'LONG' && (newsRecent || this.newsSignal.sentimentScore <= -0.45))
                       && !(candidate.direction === 'SHORT' && this.newsSignal.sentimentScore >= 0.45);
      if (!gates.newsAligned) {
        blocks.push(`News contradicts the ${candidate.direction} thesis (${this.newsSignal.sentimentLabel} ${this.newsSignal.sentimentScore}): "${this.newsSignal.headline}"`);
      }

      // Macro: a broad risk-off tape blocks longs. It does not block shorts.
      gates.macroAligned = !(this.macroSignal.riskOff && candidate.direction === 'LONG');
      if (!gates.macroAligned) {
        blocks.push(`Macro risk-off (total market cap ${this.macroSignal.marketCapChange24hPct}% in 24h) — not taking longs into a broad bleed`);
      }

      // ── Research §9.2: Do Not Fight Extreme Funding ──
      // Holding long leverage during extreme positive funding (>0.05%/period)
      // introduces severe negative carry drag. The derivatives analyst already
      // detects crowded positioning; this promotes it from a concern to a hard
      // gate. Crowded long + direction LONG → blocked. Crowded short + SHORT → blocked.
      const derivsPanel = (this.lastPanel || []).find(p => p.id === 'derivatives');
      const derivsFacts = derivsPanel && derivsPanel.facts ? derivsPanel.facts : {};
      const fundingBlocksLong = derivsFacts.crowdedLong === true && candidate.direction === 'LONG';
      const fundingBlocksShort = derivsFacts.crowdedShort === true && candidate.direction === 'SHORT';
      gates.fundingNotExtreme = !fundingBlocksLong && !fundingBlocksShort;
      if (fundingBlocksLong) {
        blocks.push(`Extreme funding: longs are crowded and paying to hold (funding z ${derivsFacts.fundingZ || '?'}, LS ratio z ${derivsFacts.lsZ || '?'}) — research §9.2: do not fight extreme positive funding, wait for a margin flush`);
      }
      if (fundingBlocksShort) {
        blocks.push(`Extreme funding: shorts are crowded and paying to hold (funding z ${derivsFacts.fundingZ || '?'}, LS ratio z ${derivsFacts.lsZ || '?'}) — joining a crowded short is standing in front of a squeeze`);
      }

      // ── Research §7.1 & §9.4: BTC Dominance Rotation Hierarchy ──
      // "Do not trade high-beta altcoins while Bitcoin is breaking out with
      // rising dominance." When BTC.D is trending up, capital is concentrating
      // in BTC and altcoins bleed on native pairs. Block alt longs.
      const isAltcoin = this.symbol !== 'BTCUSDT';
      let btcDomRising = false;
      if (this.btcDominanceHistory.length >= 3) {
        const recent = this.btcDominanceHistory.slice(-5);
        const first = recent[0];
        const last = recent[recent.length - 1];
        btcDomRising = last > first && (last - first) > 0.3; // 0.3pp rise = meaningful
      }
      gates.rotationAligned = !(isAltcoin && btcDomRising && candidate.direction === 'LONG');
      if (!gates.rotationAligned) {
        blocks.push(`BTC dominance is rising (${this.btcDominanceHistory.slice(-1)[0]?.toFixed(1) || '?'}%) — research §7.1: capital is concentrating in BTC, altcoin longs are blocked until dominance stabilises`);
      }

      // ── Research §2.1: Liquidation Cascade Detection ──
      // When OI was at highs with extreme funding and is now dropping fast,
      // a Hawkes-style cascade may be in progress. Block ALL entries until
      // the cascade completes (OI resets and funding normalises).
      let cascadeActive = false;
      if (derivsFacts.available) {
        const oiDropping = derivsFacts.oiChange6h != null && derivsFacts.oiChange6h < -3.0;
        const wasCrowded = derivsFacts.crowdedLong || derivsFacts.crowdedShort;
        cascadeActive = oiDropping && wasCrowded;
      }
      gates.noCascade = !cascadeActive;
      if (cascadeActive) {
        blocks.push(`Liquidation cascade detected: OI dropping ${derivsFacts.oiChange6h}% with previously crowded positioning — Hawkes self-exciting process may not have completed. Wait for absorption (OI reset, funding flat/negative)`);
      }

      // ── Per-coin position guard ──
      // Only one trade per coin at a time. Multiple coins can trade simultaneously.
      gates.noDuplicatePosition = !this.activePositions.has(this.symbol);
      if (!gates.noDuplicatePosition) {
        blocks.push(`Already holding a live ${this.symbol} position — one trade per coin at a time, no spamming`);
      }

      // Whale flow: only a *confirmed* opposing flow blocks. A single large
      // print is not evidence of anything, which is why a minimum count applies.
      const whaleOpposes = this.whaleSignal.whaleCount >= 4 &&
        ((candidate.direction === 'LONG' && this.whaleSignal.dominantSide === 'SELL') ||
         (candidate.direction === 'SHORT' && this.whaleSignal.dominantSide === 'BUY'));
      gates.whaleAligned = !whaleOpposes;
      if (whaleOpposes) {
        blocks.push(`Confirmed opposing whale flow: ${this.whaleSignal.dominantSide} $${Math.abs(this.whaleSignal.netDeltaUsd).toLocaleString()} net across ${this.whaleSignal.whaleCount} large prints`);
      }

      // Spoof guard: do not lean on a level being held up by a vanishing wall.
      const spoof = this.flow.detectSpoof();
      const spoofAgainst = spoof.detected &&
        ((candidate.direction === 'LONG' && spoof.side === 'BID') || (candidate.direction === 'SHORT' && spoof.side === 'ASK'));
      gates.noSpoofedLevel = !spoofAgainst;
      if (spoofAgainst) {
        blocks.push(`A large ${spoof.side} wall appeared then vanished without trading — the level this entry leans on may not be real`);
      }

      // Adaptive: a pattern that has just lost repeatedly sits out until the
      // tape that suits it returns.
      const perf = this.setupPerformance[candidate.name] || {};
      const streak = perf.recentLossStreak || 0;
      gates.adaptiveOk = streak < 3;
      if (!gates.adaptiveOk) {
        blocks.push(`${candidate.name} has lost ${streak} times consecutively in recent history — sitting this pattern out until conditions change`);
      }

      // Optional LLM second opinion, only honoured if it is fresh and about
      // this exact setup. A stale verdict is ignored rather than trusted.
      if (this.llmVerdict && this.now() - this.llmVerdict.at < 15 * 60 * 1000 &&
          this.llmVerdict.symbol === this.symbol && this.llmVerdict.direction === candidate.direction) {
        gates.llmConfirmed = this.llmVerdict.verdict !== 'VETO';
        if (!gates.llmConfirmed) blocks.push(`Supervisor model vetoed: ${this.llmVerdict.rationale || 'no rationale returned'}`);
      }

      return { gates, blocks, validity };
    }

    // ─── Main evaluation ───
    getState() {
      const nowStr = new Date(this.now()).toISOString();
      const validity = this.validateData();

      const ltf = this.confirmed('ltf');
      const mtf = this.confirmed('mtf');
      const htf = this.confirmed('htf');

      if (validity.status === 'INVALID') {
        return this.buildOutput({
          decision: 'HALTED', regime: null, candidate: null, nowStr, validity,
          blocks: validity.reasons, gates: { dataValid: false }
        });
      }
      if (htf.length < 40 || mtf.length < 40) {
        return this.buildOutput({
          decision: 'WARMING_UP', regime: null, candidate: null, nowStr, validity,
          blocks: [`Building higher-timeframe history (15m: ${htf.length}/40, 5m: ${mtf.length}/40) — the engine will not trade on a partial picture`],
          gates: {}
        });
      }

      const regime = Regime.classify({ htf, mtf, ltf }, { atrPctHistory: this.atrPctHistory });
      const atrMtf = I.atr(mtf, 14);
      const flowSnapshot = this.flow.snapshot(mtf[mtf.length - 1], atrMtf, mtf, this.price);

      // ─── The swarm reads the market, then argues about it ───
      // The panel runs first and independently of any setup. This ordering is
      // deliberate: the analysts must not be shown a candidate and asked to
      // justify it, because that is how a panel becomes a rubber stamp.
      const swarmCtx = {
        mtf, htf, ltf, price: this.price, atrMtf,
        derivs: this.derivs, flowAgent: this.flow,
        now: this.now(), symbol: this.symbol,
        // Analysts whose model is only valid in certain conditions need to know
        // the conditions. Without this a mean-reversion analyst votes against
        // trends and a trap-fade analyst votes against momentum.
        regime: regime.regime, bias: regime.bias
      };
      const panel = (Panel && this.swarmEnabled) ? Panel.run(swarmCtx) : [];
      const metaWeights = this.metaLearner ? this.metaLearner.weights(regime.regime) : {};
      const verdict = (Debate && this.swarmEnabled)
        ? Debate.deliberate(panel, metaWeights)
        : { thesis: null, conviction: 0, blocked: false, blockers: [], transcript: [], fatalVetoes: [] };
      this.lastPanel = panel;
      this.lastVerdict = verdict;

      // Liquidity pools become real targets. A target placed at the next shelf
      // of resting stops is a place price is actually drawn to; an arbitrary R
      // multiple is a place price has no particular reason to reach.
      const liquidityRead = panel.find(p => p.id === 'liquidity');
      const swarmLevels = [];
      for (const p of panel) for (const l of (p.levels || [])) swarmLevels.push(l);

      const candidates = Playbooks.evaluate({
        regime, htf, mtf, ltf,
        flow: flowSnapshot,
        atrMtf,
        price: this.price,
        symbol: this.symbol,
        liquidityPools: liquidityRead ? liquidityRead.levels : [],
        swarmLevels
      });

      const best = candidates.length ? candidates[0] : null;
      const { gates, blocks } = this.applyGates(best, regime);

      // ─── The swarm gate ───
      // A playbook pattern is the mechanics of an entry. It is not permission
      // to take one. The panel must independently want this direction.
      if (best && this.swarmMode === 'gating') {
        gates.swarmAgrees = verdict.thesis === best.direction;
        if (!gates.swarmAgrees) {
          if (verdict.blocked) {
            blocks.push(`Analyst panel stood down: ${verdict.blockers[0] || 'no surviving thesis'} — the ${best.name} pattern is present but nobody on the panel wants this trade`);
          } else if (verdict.thesis) {
            blocks.push(`Analyst panel reads ${verdict.thesis} while the ${best.name} pattern points ${best.direction} — taking a setup the panel disagrees with is how a pattern-matcher trades into a wall`);
          } else {
            blocks.push('Analyst panel reached no directional consensus');
          }
        }
      }

      // Fatal vetoes apply in BOTH advisory and gating mode. These are hazard
      // rules, not opinions: they describe a specific reason this entry is
      // structurally bad, and none of them depends on the panel guessing
      // direction correctly.
      let observedVetoes = [];
      if (best && this.swarmEnabled) {
        const fatal = (verdict.fatalVetoes || []).filter(f => {
          const target = Debate && Debate.vetoTarget ? Debate.vetoTarget(f.veto) : null;
          return !target || target === best.direction;
        });
        if (this.swarmDecides) {
          gates.noFatalHazard = fatal.length === 0;
          for (const f of fatal) blocks.push(`${f.source}: ${f.veto}`);
        } else {
          // Observe mode: record what the panel WOULD have blocked without
          // acting on it, so the veto's value can be settled from outcomes
          // rather than argued from priors.
          observedVetoes = fatal.map(f => `${f.source}: ${f.veto}`);
        }
      }
      this.lastObservedVetoes = observedVetoes;

      const allPassed = Object.values(gates).every(v => v === true);

      // The final grade is the playbook's pattern quality tempered by how
      // strongly the panel actually wants it. A textbook pattern the analysts
      // are lukewarm about is not an A setup, and V2's failure was precisely
      // treating pattern presence as sufficient.
      if (best && this.swarmMode === 'gating' && verdict.thesis === best.direction) {
        // The panel MODULATES the pattern's quality rather than being averaged
        // into it. Averaging a 0-1 conviction with a 0-100 score (the first cut
        // did) drags every setup toward the middle and pushes nearly all of
        // them below the trading threshold — the panel ends up acting as a
        // blanket dampener rather than a discriminator. A conviction of 0.5 is
        // neutral here; below it the pattern is marked down, above it, up.
        const modifier = I.clamp(0.7 + verdict.conviction * 0.6, 0.7, 1.3);
        best.patternScore = best.score;
        best.panelConviction = verdict.conviction;
        best.score = Math.round(I.clamp(best.score * modifier, 0, 100));
        best.grade = Playbooks.grade(best.score);
      }

      let decision = 'NO_TRADE';
      if (best && allPassed) {
        const rank = GRADE_RANK[best.grade] || 0;
        if (rank >= (GRADE_RANK[this.autoTradeMinGrade] || 2)) {
          decision = best.direction === 'LONG' ? 'BUY' : 'SELL';
        } else {
          decision = 'WATCH';
          blocks.push(`Setup is grade ${best.grade}; autonomous execution requires ${this.autoTradeMinGrade} or better. Shown for the operator, not traded.`);
        }
      } else if (best) {
        decision = 'WAIT';
      }

      const out = this.buildOutput({
        decision, regime, candidate: best, nowStr, validity, gates, blocks,
        flowSnapshot, alternatives: candidates.slice(1, 3)
      });
      this.lastState = out;
      return out;
    }

    buildOutput({ decision, regime, candidate, nowStr, validity, gates = {}, blocks = [], flowSnapshot, alternatives = [] }) {
      const g = candidate ? candidate.geometry : null;
      const candidateScore = candidate && candidate.patternScore != null ? candidate.patternScore : (candidate ? candidate.score : 0);
      const candidatePanelConviction = candidate && candidate.panelConviction != null ? candidate.panelConviction : null;
      return {
        symbol: this.symbol,
        timestamp: nowStr,
        decision,
        // Score and grade describe the SETUP, and are 0 when there is no setup —
        // unlike V2, where an idle engine still reported a trust score in the 50s.
        score: candidate ? candidate.score : 0,
        grade: candidate ? candidate.grade : null,
        setupType: candidate ? candidate.name : (decision === 'NO_TRADE' ? 'NONE' : decision),
        direction: candidate ? candidate.direction : null,
        narrative: candidate ? candidate.narrative : null,
        components: candidate ? candidate.components : [],
        entry: g ? g.entry : null,
        stopLoss: g ? g.stop : null,
        invalidation: g ? g.invalidation : null,
        takeProfit: g ? g.targets : null,
        riskReward: g ? g.rrToTp2 : null,
        riskPct: g ? g.riskPct : null,

        regime: regime ? regime.regime : 'UNKNOWN',
        bias: regime ? regime.bias : 'NEUTRAL',
        regimeConfidence: regime ? regime.confidence : 0,
        regimeReasons: regime ? regime.reasons : [],
        regimeMetrics: regime ? regime.metrics : {},

        gateChecks: gates,
        blockers: blocks,
        alternatives: alternatives.map(a => ({ name: a.name, direction: a.direction, score: a.score, grade: a.grade })),

        dataQuality: validity.quality,
        dataStatus: validity.status,

        flow: flowSnapshot ? {
          available: flowSnapshot.available,
          takerRatio5m: +flowSnapshot.flow5m.ratio.toFixed(3),
          cumDelta: Math.round(flowSnapshot.cumDelta),
          absorption: flowSnapshot.absorption.side,
          burst: flowSnapshot.burst.detected ? flowSnapshot.burst.side : 'NONE',
          spread: flowSnapshot.book.available ? +flowSnapshot.book.spreadPct.toFixed(4) : null,
          imbalance: flowSnapshot.book.available ? +flowSnapshot.book.imbalance.toFixed(3) : null,
          spoof: flowSnapshot.spoof.detected ? flowSnapshot.spoof.side : null,
          fvg: flowSnapshot.fvg && flowSnapshot.fvg.detected ? {
            side: flowSnapshot.fvg.side,
            gapHigh: flowSnapshot.fvg.gapHigh,
            gapLow: flowSnapshot.fvg.gapLow,
            midpoint: flowSnapshot.fvg.midpoint,
            evidence: flowSnapshot.fvg.evidence
          } : null,
          orderBlock: flowSnapshot.orderBlock && flowSnapshot.orderBlock.detected ? {
            side: flowSnapshot.orderBlock.side,
            high: flowSnapshot.orderBlock.high,
            low: flowSnapshot.orderBlock.low,
            midpoint: flowSnapshot.orderBlock.midpoint,
            evidence: flowSnapshot.orderBlock.evidence
          } : null
        } : null,

        panel: (this.lastPanel || []).map(p => ({
          id: p.id, name: p.name, read: p.read,
          conviction: +(p.conviction || 0).toFixed(2),
          evidence: p.evidence, vetoes: p.vetoes, facts: p.facts
        })),
        verdict: this.lastVerdict ? {
          thesis: this.lastVerdict.thesis,
          rejectedThesis: this.lastVerdict.rejectedThesis,
          conviction: this.lastVerdict.conviction,
          blocked: this.lastVerdict.blocked,
          transcript: this.lastVerdict.transcript,
          supporters: this.lastVerdict.supporters,
          opposition: this.lastVerdict.opposition,
          fatalVetoes: this.lastVerdict.fatalVetoes,
          longScore: this.lastVerdict.longScore,
          shortScore: this.lastVerdict.shortScore
        } : null,
        patternScore: candidateScore,
        panelConviction: candidatePanelConviction,
        swarmMode: this.swarmMode,
        // Present only in observe mode: vetoes the panel raised that were not
        // acted on. Empty in advisory/gating, where they became blocks.
        observedVetoes: this.lastObservedVetoes || [],
        analystWeights: this.metaLearner ? this.metaLearner.weights(regime ? regime.regime : 'UNKNOWN') : {},
        whale: Object.assign({}, this.whaleSignal),
        news: { label: this.newsSignal.sentimentLabel, score: this.newsSignal.sentimentScore, headline: this.newsSignal.headline },
        macro: Object.assign({}, this.macroSignal),
        llmVerdict: this.llmVerdict
      };
    }
  }

  return { MasisEngine, TIMEFRAMES, GRADE_RANK, AUTO_TRADE_MIN_GRADE, MIN_24H_TURNOVER_USD };
});
