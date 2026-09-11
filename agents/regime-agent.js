/**
 * DIV-03 — Regime & Bias Agent (multi-timeframe).
 *
 * Why this exists: the previous engine classified "market state" off a single
 * 1-minute series. On 1m data, EMA stacks and break-of-structure flip several
 * times an hour in a market that is objectively going nowhere, so the engine
 * produced a steady stream of directional calls in tape that had no direction to
 * trade. Roughly half of those are losers before costs, and after fees and
 * spread they are losers on average.
 *
 * This agent answers two questions, in order, off the HIGHER timeframes:
 *   1. Is this tape tradable at all right now?  (regime)
 *   2. If so, which way is the wind blowing?     (bias)
 * Anything the lower timeframe sees that disagrees with the answers here is
 * treated as noise, not as a signal.
 */
(function (root, factory) {
  const api = factory(typeof require === 'function' ? require('./indicators.js') : root.MasisIndicators);
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.MasisRegimeAgent = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function (I) {

  const REGIME = {
    TREND: 'TREND',            // directional, efficient — continuation setups are valid
    RANGE: 'RANGE',            // bounded, mean-reverting — only fades at the extremes
    CHOP: 'CHOP',              // low efficiency, no edge either way — stand down
    VOLATILE: 'VOLATILE',      // volatility spike / news shock — stand down until it settles
    UNKNOWN: 'UNKNOWN'
  };

  /**
   * @param {object} tfData  { htf: candles[], mtf: candles[], ltf: candles[] } — all CONFIRMED candles
   * @param {object} opts    { atrPctHistory: number[] } rolling history for volatility percentile
   */
  function classify(tfData, opts = {}) {
    const htf = tfData.htf || [];
    const mtf = tfData.mtf || [];

    if (htf.length < 40 || mtf.length < 40) {
      return {
        regime: REGIME.UNKNOWN,
        bias: 'NEUTRAL',
        tradable: false,
        confidence: 0,
        reasons: [`Insufficient higher-timeframe history (HTF ${htf.length}/40, MTF ${mtf.length}/40) — engine will not trade blind`],
        metrics: {}
      };
    }

    const htfCloses = htf.map(c => c.close);
    const mtfCloses = mtf.map(c => c.close);

    const htfAtr = I.atr(htf, 14);
    const htfAtrPct = I.atrPct(htf, 14);
    const htfEr = I.efficiencyRatio(htfCloses, 20);
    const mtfEr = I.efficiencyRatio(mtfCloses, 20);
    const htfAdx = I.adx(htf, 14);
    const htfSlope = I.slopeInAtr(htfCloses, htfAtr, 20);

    const ema21 = I.ema(htfCloses, 21);
    const ema50 = I.ema(htfCloses, 50);
    const lastClose = htfCloses[htfCloses.length - 1];
    const htfStruct = I.marketStructure(htf, 2);
    const mtfStruct = I.marketStructure(mtf, 2);

    // Volatility percentile: is the current ATR unusual *for this symbol*?
    // A constant threshold can't work across BTC (~0.05%/bar) and DOGE (~0.4%/bar).
    const volHistory = opts.atrPctHistory && opts.atrPctHistory.length >= 20
      ? opts.atrPctHistory
      : htf.slice(-60).map((_, i, arr) => (i > 14 ? I.atrPct(htf.slice(0, htf.length - arr.length + i + 1), 14) : null)).filter(v => v !== null);
    const volPercentile = I.percentileRank(volHistory, htfAtrPct);

    const reasons = [];
    let regime = REGIME.UNKNOWN;
    let confidence = 50;

    // ─── Kill-switch regimes first (these override any directional read) ───
    if (volPercentile > 0.95 && htfEr < 0.45) {
      regime = REGIME.VOLATILE;
      confidence = 80;
      reasons.push(`Volatility in the top 5% of its own recent range (ATR ${(htfAtrPct * 100).toFixed(2)}%) without directional efficiency (ER ${htfEr.toFixed(2)}) — shock tape, stops get run in both directions`);
    } else if (htfEr < 0.28 && htfAdx < 20) {
      regime = REGIME.CHOP;
      confidence = 75;
      reasons.push(`Efficiency ratio ${htfEr.toFixed(2)} with ADX ${htfAdx.toFixed(1)} — price is travelling a long path to go nowhere; no directional edge exists to extract`);
    } else if (htfEr >= 0.38 && htfAdx >= 22 && Math.abs(htfSlope) >= 0.08) {
      regime = REGIME.TREND;
      confidence = Math.round(I.clamp(45 + htfEr * 60 + (htfAdx - 22) * 0.8, 50, 92));
      reasons.push(`Directional regime: ER ${htfEr.toFixed(2)}, ADX ${htfAdx.toFixed(1)}, slope ${htfSlope.toFixed(2)} ATR/bar`);
    } else if (htfStruct.structure === 'RANGE' || (htfEr < 0.38 && htfAdx < 25)) {
      regime = REGIME.RANGE;
      confidence = 65;
      reasons.push(`Bounded auction: HTF structure ${htfStruct.structure}, ER ${htfEr.toFixed(2)}, ADX ${htfAdx.toFixed(1)} — only the range extremes carry an edge`);
    } else {
      regime = REGIME.CHOP;
      confidence = 55;
      reasons.push(`No regime met its threshold cleanly (ER ${htfEr.toFixed(2)}, ADX ${htfAdx.toFixed(1)}) — defaulting to stand-down rather than guessing`);
    }

    // ─── Bias: only meaningful in a TREND regime, and it must be agreed by
    // structure, the EMA stack and price location. Any one of them alone is
    // the kind of weak evidence that produced the old engine's false calls. ───
    let bias = 'NEUTRAL';
    const stackUp = lastClose > ema21 && ema21 > ema50;
    const stackDown = lastClose < ema21 && ema21 < ema50;

    if (regime === REGIME.TREND) {
      if (stackUp && htfStruct.structure === 'UPTREND') {
        bias = 'LONG';
        reasons.push('HTF bias LONG: higher-highs/higher-lows confirmed by price > EMA21 > EMA50');
      } else if (stackDown && htfStruct.structure === 'DOWNTREND') {
        bias = 'SHORT';
        reasons.push('HTF bias SHORT: lower-highs/lower-lows confirmed by price < EMA21 < EMA50');
      } else {
        // Trending by strength metrics but structure and EMAs disagree — this is
        // usually the late stage of a move, exactly where continuation entries
        // have the worst payoff. Downgrade rather than pick a side.
        regime = REGIME.CHOP;
        bias = 'NEUTRAL';
        confidence = 55;
        reasons.push(`Trend strength present but structure (${htfStruct.structure}) and EMA stack disagree — treating as untradeable rather than picking a side late in a move`);
      }
    } else if (regime === REGIME.RANGE) {
      bias = 'NEUTRAL';
    }

    const tradable = regime === REGIME.TREND || regime === REGIME.RANGE;
    if (!tradable) reasons.push('Regime gate CLOSED — no new entries will be authorised until the tape changes character');

    return {
      regime,
      bias,
      tradable,
      confidence,
      reasons,
      metrics: {
        htfEr: +htfEr.toFixed(3),
        mtfEr: +mtfEr.toFixed(3),
        htfAdx: +htfAdx.toFixed(1),
        htfSlope: +htfSlope.toFixed(3),
        htfAtr,
        htfAtrPct: +(htfAtrPct * 100).toFixed(3),
        volPercentile: +volPercentile.toFixed(2),
        htfStructure: htfStruct.structure,
        mtfStructure: mtfStruct.structure,
        htfEma21: ema21,
        htfEma50: ema50,
        htfStruct,
        mtfStruct
      }
    };
  }

  return { classify, REGIME };
});
