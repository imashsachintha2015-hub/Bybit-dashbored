/**
 * DIV-05 — Setup Playbooks.
 *
 * The previous engine decided direction by counting bullet points: if three
 * bullish observations existed and at most one bearish one, it called a BUY.
 * That has two fatal properties. First, the observations are heavily
 * correlated — "price > VWAP", "EMA9 > EMA21" and "delta > 0" are three
 * restatements of "price went up recently", so the count reaches three the
 * moment price ticks up, in any tape, with no edge attached. Second, nothing
 * in it references *where* price is relative to structure, which is the entire
 * question: buying strength at the top of a range and buying strength off a
 * defended low are opposite trades with opposite outcomes.
 *
 * So direction is no longer voted on. A trade exists only when a named,
 * pre-defined pattern is actually present, with a structural invalidation level
 * that can be pointed at on the chart. Each playbook scores its own quality
 * from weighted, deliberately *non-redundant* components.
 */
(function (root, factory) {
  const api = factory(typeof require === 'function' ? require('./indicators.js') : root.MasisIndicators);
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.MasisPlaybooks = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function (I) {

  /** Minimum reward:risk to TP2 for a setup to be worth taking at all.
   * With realistic costs (taker in/out + spread ≈ 0.11% round trip on Bybit
   * linear perps) and a realistic hit rate, anything under this has negative
   * expectancy no matter how pretty the chart looks. */
  const MIN_RR = 1.6;

  /** Round-trip cost estimate in price terms, used to reject setups whose
   * target is inside the noise/cost band. */
  const ROUND_TRIP_COST_PCT = 0.0011;

  function component(key, label, weight, score, detail) {
    return { key, label, weight, score: I.clamp(score, 0, 1), detail };
  }

  function scoreComponents(components) {
    let total = 0, weighted = 0;
    for (const c of components) {
      total += c.weight;
      weighted += c.weight * c.score;
    }
    return total > 0 ? Math.round((weighted / total) * 100) : 0;
  }

  function grade(score) {
    if (score >= 82) return 'A+';
    if (score >= 74) return 'A';
    if (score >= 64) return 'B';
    if (score >= 52) return 'C';
    return 'D';
  }

  /**
   * Builds the risk geometry for a candidate. The stop goes BEYOND the
   * structural invalidation with an ATR buffer, never at a fixed ATR multiple
   * from entry — a stop placed without reference to structure is just a
   * randomly-positioned donation, and was why the old engine's 1.2-ATR stops
   * were taken out by ordinary noise before the idea had a chance to work.
   */
  function buildRiskGeometry(direction, entry, invalidationLevel, atrValue, structure) {
    // Research §8.4: ATR volatility buffer k ∈ [1.0, 1.5] provides empirical
    // protection against intraday noise and wick hunts. The previous 0.35
    // multiplier was inside the noise band and got stopped out by normal
    // retrace before ideas had time to work.
    const buffer = atrValue * 1.0;
    const stop = direction === 'LONG'
      ? invalidationLevel - buffer
      : invalidationLevel + buffer;

    const riskDist = Math.abs(entry - stop);
    if (riskDist <= 0) return null;

    // Research §8.4: 50/50 Scaled Liquidity Exit Protocol.
    // TP1 at 2.0R: bank 50%, move stop to break-even (minimum 2:1 R:R).
    // TP2 at 3.5R: trailing runner for macro trend capture.
    const tp1 = direction === 'LONG' ? entry + riskDist * 2.0 : entry - riskDist * 2.0;
    let tp2 = direction === 'LONG' ? entry + riskDist * 3.5 : entry - riskDist * 3.5;

    // Headroom: how far can price travel before it runs into the structure that
    // is most likely to stop it? Measured in R, because that is the unit the
    // decision is made in.
    const opposing = direction === 'LONG' ? structure.nextResistance : structure.nextSupport;
    let headroomR = Infinity;
    if (opposing != null) {
      headroomR = Math.abs(opposing - entry) / riskDist;
    }

    // A level sitting closer than the first target means there is genuinely
    // nowhere for the trade to go — reject it. But a level beyond TP1 is a
    // reason to take profit slightly earlier, not a reason to skip the trade;
    // the previous cap-to-the-level rule conflated the two and rejected roughly
    // seven out of eight otherwise-valid setups.
    let cappedByStructure = false;
    if (headroomR < 2.0) {
      return {
        entry: +entry.toFixed(6), stop: +stop.toFixed(6), invalidation: +invalidationLevel.toFixed(6),
        targets: [+tp1.toFixed(6), +tp2.toFixed(6)],
        riskDist, rrToTp2: +headroomR.toFixed(2), headroomR: +headroomR.toFixed(2),
        riskPct: +((riskDist / entry) * 100).toFixed(3), cappedByStructure: true, viable: false,
        rejectReason: `Only ${headroomR.toFixed(2)}R of headroom before the next opposing level at ${opposing.toFixed(4)} — need at least 2.0R for the 50/50 protocol's first exit`
      };
    }
    if (opposing != null && headroomR < 3.5) {
      // Park TP2 just short of the opposing level rather than at the full 3.5R,
      // so the fill happens before the resting orders there do the stopping.
      const shy = atrValue * 0.15;
      tp2 = direction === 'LONG' ? opposing - shy : opposing + shy;
      cappedByStructure = true;
    }

    const rrToTp2 = Math.abs(tp2 - entry) / riskDist;
    const netEdgePct = Math.abs(tp1 - entry) / entry - ROUND_TRIP_COST_PCT;

    return {
      entry: +entry.toFixed(6),
      stop: +stop.toFixed(6),
      invalidation: +invalidationLevel.toFixed(6),
      targets: [+tp1.toFixed(6), +tp2.toFixed(6)],
      riskDist,
      rrToTp2: +rrToTp2.toFixed(2),
      headroomR: isFinite(headroomR) ? +headroomR.toFixed(2) : null,
      riskPct: +((riskDist / entry) * 100).toFixed(3),
      cappedByStructure,
      viable: rrToTp2 >= MIN_RR && netEdgePct > 0,
      rejectReason: rrToTp2 < MIN_RR
        ? `Reward:risk to TP2 is ${rrToTp2.toFixed(2)} — below the ${MIN_RR} floor`
        : (netEdgePct <= 0 ? 'First target sits inside the round-trip fee + spread band — no net edge to capture' : null)
    };
  }

  /** Nearest untested pivot above/below current price, from confirmed swings. */
  /**
   * Where is the trade actually going? Swing pivots are one answer; the
   * Liquidity Cartographer's pools are a better one, because price is drawn to
   * resting stops for a mechanical reason — that is where size can be filled.
   * When a pool sits beyond the first target it becomes the target.
   */
  function nearestLevels(structure, price, atrValue, liquidityPools) {
    // Micro-pivots a few ticks away are noise, not resistance. Require a level
    // to sit at least a third of an ATR away before it is allowed to influence
    // target placement, and only look at the most recent pivots — an old level
    // that price has since traded through many times stops nothing.
    const minDist = (atrValue || 0) * 0.33;
    const recentHighs = structure.swingHighs.slice(-12);
    const recentLows = structure.swingLows.slice(-12);
    const above = recentHighs.filter(h => h.price > price + minDist).map(h => ({ price: h.price, src: 'swing' }));
    const below = recentLows.filter(l => l.price < price - minDist).map(l => ({ price: l.price, src: 'swing' }));

    // Fold in mapped liquidity pools, weighted by magnet strength — a heavy,
    // untested shelf is a more reliable destination than a random old pivot.
    for (const pool of (liquidityPools || [])) {
      if (!pool || typeof pool.price !== 'number') continue;
      if ((pool.magnet != null) && pool.magnet < 0.18) continue;
      if (pool.price > price + minDist) above.push({ price: pool.price, src: pool.kind || 'pool', magnet: pool.magnet });
      else if (pool.price < price - minDist) below.push({ price: pool.price, src: pool.kind || 'pool', magnet: pool.magnet });
    }

    above.sort((a, b) => a.price - b.price);
    below.sort((a, b) => b.price - a.price);
    return {
      nextResistance: above.length ? above[0].price : null,
      nextSupport: below.length ? below[0].price : null,
      resistanceSource: above.length ? above[0].src : null,
      supportSource: below.length ? below[0].src : null
    };
  }

  // ───────────────────────────────────────────────────────────────────────
  // Playbook 1 — TREND_PULLBACK
  // The highest-expectancy pattern available to a retail-latency system: an
  // established higher-timeframe trend, a pullback into value, and a confirmed
  // rejection *out* of that value area in the trend direction. Crucially it
  // buys weakness inside strength, not strength on top of strength, so the
  // stop sits under a defended level rather than under nothing.
  // ───────────────────────────────────────────────────────────────────────
  function trendPullback(ctx) {
    const { regime, mtf, ltf, flow, atrMtf } = ctx;
    if (regime.regime !== 'TREND' || (regime.bias !== 'LONG' && regime.bias !== 'SHORT')) return null;
    if (mtf.length < 40 || ltf.length < 10) return null;

    const dir = regime.bias;
    const closes = mtf.map(c => c.close);
    const ema21 = I.ema(closes, 21);
    const mtfVwap = I.vwap(mtf.slice(-60));
    const struct = I.marketStructure(mtf, 2);
    const last = mtf[mtf.length - 1];
    const prev = mtf[mtf.length - 2];
    const price = ctx.price;

    // Value zone = the band between EMA21 and VWAP, widened by a third of ATR.
    const zoneHi = Math.max(ema21, mtfVwap) + atrMtf * 0.33;
    const zoneLo = Math.min(ema21, mtfVwap) - atrMtf * 0.33;

    // 1. Did price actually pull back into value in the last few bars?
    const recent = mtf.slice(-4);
    const touchedValue = dir === 'LONG'
      ? recent.some(c => c.low <= zoneHi)
      : recent.some(c => c.high >= zoneLo);
    if (!touchedValue) return null;

    // 2. Is the most recent CONFIRMED bar a rejection back in the trend direction?
    const body = last.close - last.open;
    // The reclaim bar is the bar that turns back UP out of the pullback. It
    // does not need to be trading inside the value zone itself — requiring that
    // (as the first cut of this did) collapsed the setup to a handful of
    // occurrences, because in a healthy trend the turn usually happens on the
    // bar after the touch, not on the touch bar.
    const rejected = dir === 'LONG'
      ? (body > 0 && last.close > prev.close && last.close > zoneLo)
      : (body < 0 && last.close < prev.close && last.close < zoneHi);
    if (!rejected) return null;

    // 3. Structural invalidation = the swing that would end the pullback thesis.
    const pivot = dir === 'LONG' ? struct.lastSwingLow : struct.lastSwingHigh;
    if (!pivot) return null;
    const swingExtreme = dir === 'LONG'
      ? Math.min(pivot.price, ...recent.map(c => c.low))
      : Math.max(pivot.price, ...recent.map(c => c.high));

    // ─── Scoring: each component is a genuinely different question ───
    const components = [];

    components.push(component('htfRegime', 'Higher-timeframe regime quality', 22,
      I.clamp((regime.metrics.htfEr - 0.30) / 0.35, 0, 1),
      `HTF efficiency ratio ${regime.metrics.htfEr} / ADX ${regime.metrics.htfAdx}`));

    const depth = dir === 'LONG'
      ? (zoneHi - Math.min(...recent.map(c => c.low))) / atrMtf
      : (Math.max(...recent.map(c => c.high)) - zoneLo) / atrMtf;
    components.push(component('pullbackDepth', 'Pullback reached value without breaking it', 18,
      depth > 0 && depth < 1.2 ? 1 - Math.abs(depth - 0.4) / 1.2 : 0.15,
      `Pullback penetrated the value zone by ${depth.toFixed(2)} ATR`));

    const bodyRatio = Math.abs(body) / Math.max(last.high - last.low, 1e-9);
    components.push(component('rejectionQuality', 'Rejection candle conviction', 16,
      I.clamp((bodyRatio - 0.35) / 0.45, 0, 1),
      `Confirming bar body is ${(bodyRatio * 100).toFixed(0)}% of its range`));

    if (flow.available) {
      const wantBuy = dir === 'LONG';
      const ratio = flow.flow5m.ratio;
      const agrees = wantBuy ? ratio - 0.5 : 0.5 - ratio;
      components.push(component('flowConfirm', 'Taker flow confirms the rejection', 20,
        I.clamp(agrees / 0.18, 0, 1),
        `5m taker ratio ${(ratio * 100).toFixed(0)}% buy`));
    } else {
      components.push(component('flowConfirm', 'Taker flow confirms the rejection', 20, 0.25,
        'Live tape unavailable — scored as unconfirmed, not as neutral'));
    }

    // Overextension: entering after price has already run is where continuation
    // setups go to die. Distance from value is a penalty, not a bonus.
    const distFromValue = dir === 'LONG' ? (price - zoneHi) / atrMtf : (zoneLo - price) / atrMtf;
    components.push(component('entryLocation', 'Entry is close to value, not extended', 14,
      distFromValue <= 0.2 ? 1 : I.clamp(1 - (distFromValue - 0.2) / 1.3, 0, 1),
      `Entry sits ${Math.max(distFromValue, 0).toFixed(2)} ATR beyond the value zone`));

    const structure = nearestLevels(struct, price, atrMtf, ctx.liquidityPools);
    const geometry = buildRiskGeometry(dir, price, swingExtreme, atrMtf, structure);
    if (!geometry) return null;

    components.push(component('geometry', 'Reward:risk after structural target cap', 10,
      I.clamp((geometry.rrToTp2 - 1.5) / 1.8, 0, 1),
      `R:R to TP2 is ${geometry.rrToTp2}${geometry.cappedByStructure ? ' (capped by the next opposing level)' : ''}`));

    const score = scoreComponents(components);
    return {
      name: 'TREND_PULLBACK',
      direction: dir,
      score, grade: grade(score),
      components, geometry,
      narrative: `${dir === 'LONG' ? 'Uptrend' : 'Downtrend'} on the higher timeframe pulled back into the EMA21/VWAP value band and produced a confirmed rejection bar back in the trend direction. Invalidation is the ${dir === 'LONG' ? 'swing low' : 'swing high'} at ${swingExtreme.toFixed(4)} — if that gives way the pullback was a reversal and the idea is simply wrong.`
    };
  }

  // ───────────────────────────────────────────────────────────────────────
  // Playbook 2 — SWEEP_RECLAIM
  // Price runs the stops resting beyond an obvious pivot, fails to accept, and
  // closes back inside. The traders who were stopped out (and the breakout
  // traders who chased) are the fuel for the move back. This setup carries the
  // tightest, most objective invalidation available: the extreme of the sweep.
  // ───────────────────────────────────────────────────────────────────────
  function sweepReclaim(ctx) {
    const { regime, mtf, flow, atrMtf } = ctx;
    if (!regime.tradable) return null;
    if (mtf.length < 40) return null;

    const struct = I.marketStructure(mtf, 2);
    const last = mtf[mtf.length - 1];
    const price = ctx.price;

    let dir = null, sweptLevel = null, sweepExtreme = null;

    // Bullish: took out a prior swing low, closed back above it.
    if (struct.priorSwingLow && last.low < struct.priorSwingLow.price && last.close > struct.priorSwingLow.price) {
      dir = 'LONG';
      sweptLevel = struct.priorSwingLow.price;
      sweepExtreme = last.low;
    } else if (struct.priorSwingHigh && last.high > struct.priorSwingHigh.price && last.close < struct.priorSwingHigh.price) {
      dir = 'SHORT';
      sweptLevel = struct.priorSwingHigh.price;
      sweepExtreme = last.high;
    }
    if (!dir) return null;

    // Never fade in the face of an established opposing higher-timeframe trend.
    // Counter-trend sweeps do work, but not often enough to pay for themselves.
    if (regime.regime === 'TREND' && regime.bias && regime.bias !== 'NEUTRAL' && regime.bias !== dir) return null;

    const components = [];

    const penetration = Math.abs(sweptLevel - sweepExtreme) / atrMtf;
    components.push(component('sweepDepth', 'Stops were genuinely run, then rejected', 22,
      penetration > 0.08 ? I.clamp(1 - Math.abs(penetration - 0.45) / 0.9, 0.15, 1) : 0.1,
      `Level pierced by ${penetration.toFixed(2)} ATR before the reclaim`));

    const reclaimStrength = Math.abs(last.close - sweptLevel) / Math.max(last.high - last.low, 1e-9);
    components.push(component('reclaimQuality', 'Close is decisively back inside', 20,
      I.clamp(reclaimStrength / 0.5, 0, 1),
      `Close reclaimed ${(reclaimStrength * 100).toFixed(0)}% of the bar back inside the level`));

    const absorption = flow.absorption;
    const absorbAgrees = absorption.detected &&
      ((dir === 'LONG' && absorption.side === 'BULLISH') || (dir === 'SHORT' && absorption.side === 'BEARISH'));
    components.push(component('absorption', 'Passive absorption at the extreme', 18,
      absorbAgrees ? I.clamp(absorption.strength / 100, 0.5, 1) : (flow.available ? 0.2 : 0.25),
      absorbAgrees ? absorption.evidence[0] : 'No confirming absorption detected at the sweep extreme'));

    const div = flow.divergence;
    const divAgrees = div.detected &&
      ((dir === 'LONG' && div.side === 'BULLISH') || (dir === 'SHORT' && div.side === 'BEARISH'));
    components.push(component('divergence', 'Cumulative delta diverges from the extreme', 14,
      divAgrees ? 1 : (flow.available ? 0.25 : 0.25),
      divAgrees ? div.evidence[0] : 'No delta divergence at the sweep'));

    components.push(component('regimeFit', 'Regime supports a reversion entry', 12,
      regime.regime === 'RANGE' ? 1 : (regime.bias === dir ? 0.85 : 0.35),
      `Regime ${regime.regime}, bias ${regime.bias}`));

    const structure = nearestLevels(struct, price, atrMtf, ctx.liquidityPools);
    const geometry = buildRiskGeometry(dir, price, sweepExtreme, atrMtf, structure);
    if (!geometry) return null;

    components.push(component('geometry', 'Reward:risk after structural target cap', 14,
      I.clamp((geometry.rrToTp2 - 1.5) / 1.8, 0, 1),
      `R:R to TP2 is ${geometry.rrToTp2}`));

    const score = scoreComponents(components);
    return {
      name: `SWEEP_RECLAIM`,
      direction: dir,
      score, grade: grade(score),
      components, geometry,
      narrative: `Liquidity resting beyond the ${dir === 'LONG' ? 'swing low' : 'swing high'} at ${sweptLevel.toFixed(4)} was taken (${penetration.toFixed(2)} ATR of penetration) and the bar closed back inside. Invalidation is the sweep extreme at ${sweepExtreme.toFixed(4)}: a second push through it means the level is genuinely breaking, not being defended.`
    };
  }

  // ───────────────────────────────────────────────────────────────────────
  // Playbook 3 — RANGE_FADE
  // Only legal in a confirmed RANGE regime, only at the outer quartile of the
  // range, only with absorption. Deliberately the most restricted playbook:
  // fading is where an over-eager system does the most damage, because the
  // setup "looks" valid at every point inside the range.
  // ───────────────────────────────────────────────────────────────────────
  function rangeFade(ctx) {
    const { regime, mtf, flow, atrMtf } = ctx;
    if (regime.regime !== 'RANGE') return null;
    if (mtf.length < 40) return null;

    const window = mtf.slice(-40);
    const rangeHigh = Math.max(...window.map(c => c.high));
    const rangeLow = Math.min(...window.map(c => c.low));
    const height = rangeHigh - rangeLow;
    if (height <= atrMtf * 2) return null; // range too tight to pay for the round trip

    const price = ctx.price;
    const position = (price - rangeLow) / height; // 0 = low, 1 = high

    let dir = null;
    if (position >= 0.80) dir = 'SHORT';
    else if (position <= 0.20) dir = 'LONG';
    if (!dir) return null;

    // Absorption is mandatory here — a range extreme without a defender is just
    // the bar before a breakout, and fading a breakout is the worst trade there is.
    const absorption = flow.absorption;
    const absorbAgrees = absorption.detected &&
      ((dir === 'LONG' && absorption.side === 'BULLISH') || (dir === 'SHORT' && absorption.side === 'BEARISH'));
    if (!absorbAgrees) return null;

    const components = [];
    components.push(component('rangeQuality', 'Range is well-defined and wide enough', 22,
      I.clamp((height / atrMtf - 2) / 4, 0, 1),
      `Range height ${(height / atrMtf).toFixed(1)} ATR`));
    components.push(component('extremeLocation', 'Entry is at the outer edge', 22,
      dir === 'SHORT' ? I.clamp((position - 0.80) / 0.18, 0, 1) : I.clamp((0.20 - position) / 0.18, 0, 1),
      `Price sits at ${(position * 100).toFixed(0)}% of range height`));
    components.push(component('absorption', 'Defender present at the extreme', 26,
      I.clamp(absorption.strength / 100, 0.5, 1), absorption.evidence[0]));
    components.push(component('noBreakoutPressure', 'No distributed burst pushing through', 14,
      flow.burst.detected && ((dir === 'SHORT' && flow.burst.side === 'BUY') || (dir === 'LONG' && flow.burst.side === 'SELL')) ? 0 : 1,
      flow.burst.detected ? `Flow burst ${flow.burst.side} in progress` : 'No breakout-scale burst in the tape'));

    const struct = I.marketStructure(mtf, 2);
    const invalidation = dir === 'LONG' ? rangeLow : rangeHigh;
    const midpoint = rangeLow + height * 0.5;
    const geometry = buildRiskGeometry(dir, price, invalidation, atrMtf, {
      nextResistance: dir === 'LONG' ? midpoint : null,
      nextSupport: dir === 'SHORT' ? midpoint : null
    });
    if (!geometry) return null;
    void struct;

    components.push(component('geometry', 'Reward:risk to the range midpoint', 16,
      I.clamp((geometry.rrToTp2 - 1.5) / 1.8, 0, 1),
      `R:R to TP2 is ${geometry.rrToTp2} (target capped at the range midpoint)`));

    const score = scoreComponents(components);
    return {
      name: 'RANGE_FADE',
      direction: dir,
      score, grade: grade(score),
      components, geometry,
      narrative: `Bounded auction with price at the ${dir === 'SHORT' ? 'upper' : 'lower'} edge (${(position * 100).toFixed(0)}% of range) and a confirmed passive defender absorbing the aggression. Target is the range midpoint, not the far side — the far side only pays when the range is about to break, and that is not what this setup is for.`
    };
  }

  // ───────────────────────────────────────────────────────────────────────
  // Playbook 4 — BREAKOUT_RETEST
  // Never buys the break itself (that is where the stop-run liquidity is);
  // waits for the retest to hold. Costs some moves entirely. That is the point.
  // ───────────────────────────────────────────────────────────────────────
  function breakoutRetest(ctx) {
    const { regime, mtf, flow, atrMtf } = ctx;
    // A breakout only means something when there is a trend to continue. Inside
    // a bounded auction the same bar pattern is, far more often than not, the
    // false break that the range's participants are fading — the backtest
    // showed this playbook firing in RANGE regimes and losing there.
    if (regime.regime !== 'TREND' || (regime.bias !== 'LONG' && regime.bias !== 'SHORT')) return null;
    if (mtf.length < 45) return null;

    const struct = I.marketStructure(mtf, 2);
    const price = ctx.price;
    const last = mtf[mtf.length - 1];
    const window = mtf.slice(-30, -3);
    if (window.length < 15) return null;

    const consolHigh = Math.max(...window.map(c => c.high));
    const consolLow = Math.min(...window.map(c => c.low));
    const recent = mtf.slice(-3);

    let dir = null, brokenLevel = null;
    if (recent.some(c => c.close > consolHigh) && last.low <= consolHigh + atrMtf * 0.4 && last.close > consolHigh) {
      dir = 'LONG'; brokenLevel = consolHigh;
    } else if (recent.some(c => c.close < consolLow) && last.high >= consolLow - atrMtf * 0.4 && last.close < consolLow) {
      dir = 'SHORT'; brokenLevel = consolLow;
    }
    if (!dir) return null;
    if (regime.bias !== dir) return null;

    const components = [];

    const breakoutVol = I.sma(recent.map(c => c.volume), 3);
    const baseVol = I.sma(window.map(c => c.volume), window.length);
    components.push(component('breakVolume', 'Break was paid for with volume', 20,
      baseVol > 0 ? I.clamp((breakoutVol / baseVol - 1) / 1.2, 0, 1) : 0.3,
      `Breakout volume ${(baseVol > 0 ? breakoutVol / baseVol : 1).toFixed(2)}x the consolidation average`));

    const holdDist = dir === 'LONG' ? (last.close - brokenLevel) / atrMtf : (brokenLevel - last.close) / atrMtf;
    components.push(component('retestHold', 'Retest held the broken level', 24,
      I.clamp(holdDist / 0.5, 0, 1),
      `Close holds ${holdDist.toFixed(2)} ATR on the correct side of the retested level`));

    if (flow.available) {
      const agrees = dir === 'LONG' ? flow.flow5m.ratio - 0.5 : 0.5 - flow.flow5m.ratio;
      components.push(component('flowConfirm', 'Flow supports continuation off the retest', 20,
        I.clamp(agrees / 0.15, 0, 1), `5m taker ratio ${(flow.flow5m.ratio * 100).toFixed(0)}% buy`));
    } else {
      components.push(component('flowConfirm', 'Flow supports continuation off the retest', 20, 0.25,
        'Live tape unavailable — scored as unconfirmed'));
    }

    const spoof = flow.spoof;
    const spoofAgainst = spoof.detected && ((dir === 'LONG' && spoof.side === 'BID') || (dir === 'SHORT' && spoof.side === 'ASK'));
    components.push(component('noSpoof', 'Level is not being held up by a vanishing wall', 12,
      spoofAgainst ? 0 : 1,
      spoofAgainst ? `A large ${spoof.side} wall appeared then vanished without trading — the level supporting this entry may not be real` : 'No vanishing-wall pattern at the retested level'));

    components.push(component('regimeFit', 'Regime supports continuation', 10,
      regime.regime === 'TREND' && regime.bias === dir ? 1 : 0.45,
      `Regime ${regime.regime}, bias ${regime.bias}`));

    const structure = nearestLevels(struct, price, atrMtf, ctx.liquidityPools);
    const invalidation = dir === 'LONG' ? Math.min(brokenLevel, Math.min(...recent.map(c => c.low))) : Math.max(brokenLevel, Math.max(...recent.map(c => c.high)));
    const geometry = buildRiskGeometry(dir, price, invalidation, atrMtf, structure);
    if (!geometry) return null;

    components.push(component('geometry', 'Reward:risk after structural target cap', 14,
      I.clamp((geometry.rrToTp2 - 1.5) / 1.8, 0, 1), `R:R to TP2 is ${geometry.rrToTp2}`));

    const score = scoreComponents(components);
    return {
      name: 'BREAKOUT_RETEST',
      direction: dir,
      score, grade: grade(score),
      components, geometry,
      narrative: `Consolidation between ${consolLow.toFixed(4)} and ${consolHigh.toFixed(4)} broke ${dir === 'LONG' ? 'up' : 'down'} and the retest of ${brokenLevel.toFixed(4)} held on a confirmed close. The break itself was not traded — that is where the stop-run liquidity sits.`
    };
  }

  const ALL = [trendPullback, sweepReclaim, rangeFade, breakoutRetest];

  /** Runs every playbook, returns candidates sorted best-first. */
  function evaluate(ctx) {
    const out = [];
    for (const fn of ALL) {
      try {
        const c = fn(ctx);
        if (c && c.geometry) out.push(c);
      } catch (e) {
        out.push({ name: fn.name, error: e.message, score: 0, grade: 'D' });
      }
    }
    return out.filter(c => !c.error).sort((a, b) => b.score - a.score);
  }

  return { evaluate, grade, MIN_RR, ROUND_TRIP_COST_PCT, buildRiskGeometry, ALL };
});
