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

    // ── Dynamic S/R-Anchored 3-Target Geometry ──
    // Headroom: how far can price travel before it runs into the opposing structure?
    const opposing = direction === 'LONG' ? structure.nextResistance : structure.nextSupport;
    const secondLevel = direction === 'LONG' ? structure.secondResistance : structure.secondSupport;
    let headroomR = Infinity;
    if (opposing != null) {
      headroomR = Math.abs(opposing - entry) / riskDist;
    }

    // Default 3-target placement: 1.5R / 2.5R / 4.0R
    let tp1 = direction === 'LONG' ? entry + riskDist * 1.5 : entry - riskDist * 1.5;
    let tp2 = direction === 'LONG' ? entry + riskDist * 2.5 : entry - riskDist * 2.5;
    let tp3 = direction === 'LONG' ? entry + riskDist * 4.0 : entry - riskDist * 4.0;
    let cappedByStructure = false;

    // Minimum headroom floor: less than 1.5R means trading directly into an opposing S/R wall.
    // Shorting into a support floor or buying into a resistance ceiling guarantees stop runs.
    if (headroomR < 1.5) {
      return {
        entry: +entry.toFixed(6), stop: +stop.toFixed(6), invalidation: +invalidationLevel.toFixed(6),
        targets: [+tp1.toFixed(6), +tp2.toFixed(6), +tp3.toFixed(6)],
        riskDist, rrToTp2: +headroomR.toFixed(2), headroomR: +headroomR.toFixed(2),
        riskPct: +((riskDist / entry) * 100).toFixed(3), cappedByStructure: true, viable: false,
        nextResistance: structure.nextResistance, nextSupport: structure.nextSupport,
        rejectReason: `Only ${headroomR.toFixed(2)}R of headroom before opposing ${direction === 'LONG' ? 'resistance' : 'support'} at ${opposing.toFixed(4)} — less than 1.5R clearance`
      };
    }

    const riskPct = (riskDist / entry) * 100;
    if (riskPct < 0.60) {
      return {
        entry: +entry.toFixed(6), stop: +stop.toFixed(6), invalidation: +invalidationLevel.toFixed(6),
        targets: [+tp1.toFixed(6), +tp2.toFixed(6), +tp3.toFixed(6)],
        riskDist, rrToTp2: +headroomR.toFixed(2), headroomR: isFinite(headroomR) ? +headroomR.toFixed(2) : null,
        riskPct: +riskPct.toFixed(3), cappedByStructure: true, viable: false,
        nextResistance: structure.nextResistance, nextSupport: structure.nextSupport,
        rejectReason: `Stop distance ${riskPct.toFixed(3)}% is below 0.60% minimum — micro-stops are wiped out by taker fees and spread`
      };
    }

    const shy = (atrValue || 0) * 0.15;

    // Anchor targets to real structural levels:
    // TP1 → nearest S/R wall (if 1.5R–2.5R headroom)
    // TP2 → second structural level (if available)
    // TP3 → runner beyond, or fib extension
    if (opposing != null && headroomR >= 1.5 && headroomR < 2.5) {
      tp1 = direction === 'LONG' ? opposing - shy : opposing + shy;
      if (secondLevel != null) {
        tp2 = direction === 'LONG' ? secondLevel - shy : secondLevel + shy;
      } else {
        tp2 = direction === 'LONG' ? entry + riskDist * 2.5 : entry - riskDist * 2.5;
      }
      tp3 = direction === 'LONG' ? entry + riskDist * 4.0 : entry - riskDist * 4.0;
      cappedByStructure = true;
    } else if (opposing != null && headroomR < 3.5) {
      // Park TP1 at standard 1.5R, TP2 just short of the opposing level
      tp2 = direction === 'LONG' ? opposing - shy : opposing + shy;
      if (secondLevel != null) {
        tp3 = direction === 'LONG' ? secondLevel - shy : secondLevel + shy;
      }
      cappedByStructure = true;
    }

    const rrToTp1 = Math.abs(tp1 - entry) / riskDist;
    const rrToTp2 = Math.abs(tp2 - entry) / riskDist;
    const rrToTp3 = Math.abs(tp3 - entry) / riskDist;
    const netEdgePct = Math.abs(tp1 - entry) / entry - ROUND_TRIP_COST_PCT;

    return {
      entry: +entry.toFixed(6),
      stop: +stop.toFixed(6),
      invalidation: +invalidationLevel.toFixed(6),
      targets: [+tp1.toFixed(6), +tp2.toFixed(6), +tp3.toFixed(6)],
      riskDist,
      rrToTp1: +rrToTp1.toFixed(2),
      rrToTp2: +rrToTp2.toFixed(2),
      rrToTp3: +rrToTp3.toFixed(2),
      headroomR: isFinite(headroomR) ? +headroomR.toFixed(2) : null,
      riskPct: +riskPct.toFixed(3),
      cappedByStructure,
      nextResistance: structure.nextResistance,
      nextSupport: structure.nextSupport,
      secondResistance: structure.secondResistance || null,
      secondSupport: structure.secondSupport || null,
      viable: (rrToTp2 >= MIN_RR || rrToTp1 >= 1.0) && netEdgePct > 0,
      rejectReason: (rrToTp2 < MIN_RR && rrToTp1 < 1.0)
        ? `Reward:risk to targets is below minimum floor`
        : (netEdgePct <= 0 ? 'First target sits inside the round-trip fee + spread band \u2014 no net edge to capture' : null)
    };
  }

  /**
   * Fast 5m-10m Scalp Risk Geometry.
   * Tight invalidation (0.5x ATR buffer), quick profit coverage (TP1 @ 0.6R-0.8R),
   * and a secondary target (TP2 @ 1.2R-1.5R) designed to close within 1-2 candles (5-10 min).
   */
  function buildScalpRiskGeometry(direction, entry, invalidationLevel, atrValue, structure) {
    const buffer = (atrValue || 0) * 0.5; // tight buffer for scalps
    const stop = direction === 'LONG'
      ? invalidationLevel - buffer
      : invalidationLevel + buffer;

    const riskDist = Math.abs(entry - stop);
    if (riskDist <= 0) return null;

    const opposing = direction === 'LONG' ? structure.nextResistance : structure.nextSupport;
    let headroomR = Infinity;
    if (opposing != null) {
      headroomR = Math.abs(opposing - entry) / riskDist;
    }

    // Minimum scalp headroom: at least 0.5R before hitting opposing wall
    if (headroomR < 0.5) {
      return {
        entry: +entry.toFixed(6), stop: +stop.toFixed(6), invalidation: +invalidationLevel.toFixed(6),
        targets: [+(direction === 'LONG' ? entry + riskDist * 0.65 : entry - riskDist * 0.65).toFixed(6)],
        riskDist, rrToTp1: +headroomR.toFixed(2), headroomR: +headroomR.toFixed(2),
        riskPct: +((riskDist / entry) * 100).toFixed(3), cappedByStructure: true, viable: false,
        rejectReason: `Scalp headroom ${headroomR.toFixed(2)}R is below 0.5R floor before S/R wall`
      };
    }

    const shy = (atrValue || 0) * 0.10;
    let tp1 = direction === 'LONG' ? entry + riskDist * 0.65 : entry - riskDist * 0.65;
    let tp2 = direction === 'LONG' ? entry + riskDist * 1.30 : entry - riskDist * 1.30;
    let cappedByStructure = false;

    if (opposing != null && headroomR >= 0.5 && headroomR <= 1.2) {
      tp1 = direction === 'LONG' ? opposing - shy : opposing + shy;
      tp2 = direction === 'LONG' ? entry + riskDist * 1.3 : entry - riskDist * 1.3;
      cappedByStructure = true;
    }

    const rrToTp1 = Math.abs(tp1 - entry) / riskDist;
    const rrToTp2 = Math.abs(tp2 - entry) / riskDist;
    const netEdgePct = Math.abs(tp1 - entry) / entry - ROUND_TRIP_COST_PCT;

    return {
      entry: +entry.toFixed(6),
      stop: +stop.toFixed(6),
      invalidation: +invalidationLevel.toFixed(6),
      targets: [+tp1.toFixed(6), +tp2.toFixed(6)],
      riskDist,
      rrToTp1: +rrToTp1.toFixed(2),
      rrToTp2: +rrToTp2.toFixed(2),
      headroomR: isFinite(headroomR) ? +headroomR.toFixed(2) : null,
      riskPct: +((riskDist / entry) * 100).toFixed(3),
      cappedByStructure,
      nextResistance: structure.nextResistance,
      nextSupport: structure.nextSupport,
      viable: rrToTp1 >= 0.5 && netEdgePct > 0,
      rejectReason: rrToTp1 < 0.5 ? 'Scalp RR below 0.5R' : (netEdgePct <= 0 ? 'Inside fee band' : null)
    };
  }

  /**
   * Where is the trade actually going? Multiple level sources, ranked by
   * reliability, are folded together into one unified picture:
   *   1. Swing pivots (structural)
   *   2. Liquidity pools (mechanical — price is drawn to resting stops)
   *   3. Volume Profile levels (POC, VAH, VAL, HVN — price stalls at acceptance)
   *   4. Fibonacci levels (institutional — desks key off these for targets)
   *   5. Swarm analyst levels (anything the analyst panel mapped)
   *
   * The output feeds directly into target placement and S/R collision detection.
   */
  function nearestLevels(structure, price, atrValue, liquidityPools, vpLevels, fibTargets) {
    const minDist = (atrValue || 0) * 0.33;
    const recentHighs = structure.swingHighs.slice(-12);
    const recentLows = structure.swingLows.slice(-12);
    const above = recentHighs.filter(h => h.price > price + minDist).map(h => ({ price: h.price, src: 'swing' }));
    const below = recentLows.filter(l => l.price < price - minDist).map(l => ({ price: l.price, src: 'swing' }));

    // Liquidity pools
    for (const pool of (liquidityPools || [])) {
      if (!pool || typeof pool.price !== 'number') continue;
      if ((pool.magnet != null) && pool.magnet < 0.18) continue;
      if (pool.price > price + minDist) above.push({ price: pool.price, src: pool.kind || 'pool', magnet: pool.magnet });
      else if (pool.price < price - minDist) below.push({ price: pool.price, src: pool.kind || 'pool', magnet: pool.magnet });
    }

    // Volume Profile levels (POC, VAH, VAL, HVN)
    for (const vp of (vpLevels || [])) {
      if (!vp || typeof vp.price !== 'number') continue;
      if (vp.price > price + minDist) above.push({ price: vp.price, src: vp.kind || 'VP' });
      else if (vp.price < price - minDist) below.push({ price: vp.price, src: vp.kind || 'VP' });
    }

    // Fibonacci extension/retracement levels
    for (const fib of (fibTargets || [])) {
      if (!fib || typeof fib.price !== 'number') continue;
      if (fib.price > price + minDist) above.push({ price: fib.price, src: `fib_${fib.ratio || '?'}` });
      else if (fib.price < price - minDist) below.push({ price: fib.price, src: `fib_${fib.ratio || '?'}` });
    }

    above.sort((a, b) => a.price - b.price);
    below.sort((a, b) => b.price - a.price);

    // Cluster detection: when two levels from different sources sit within 0.2 ATR
    // of each other, they form a confluence cluster — the stronger the cluster,
    // the more reliable the target. Promote the first cluster to primary.
    const clusterDist = (atrValue || 1) * 0.2;
    const clusterAbove = above.length >= 2 && Math.abs(above[0].price - above[1].price) <= clusterDist;
    const clusterBelow = below.length >= 2 && Math.abs(below[0].price - below[1].price) <= clusterDist;

    return {
      nextResistance: above.length ? above[0].price : null,
      nextSupport: below.length ? below[0].price : null,
      secondResistance: above.length > 1 ? above[1].price : null,
      secondSupport: below.length > 1 ? below[1].price : null,
      resistanceSource: above.length ? above[0].src : null,
      supportSource: below.length ? below[0].src : null,
      resistanceCluster: clusterAbove,
      supportCluster: clusterBelow,
      allAbove: above.slice(0, 5),
      allBelow: below.slice(0, 5)
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

    // RSI divergence confluence — a divergence at the pullback extreme is powerful confirmation
    const mtfDiv = I.rsiDivergence(mtf, 14, 2);
    if (mtfDiv && ((dir === 'LONG' && mtfDiv.side === 'BULLISH') || (dir === 'SHORT' && mtfDiv.side === 'BEARISH'))) {
      components.push(component('divergence', 'RSI divergence confirms the pullback rejection', 12,
        mtfDiv.type === 'HIDDEN' ? 0.9 : 1.0,
        `${mtfDiv.type} ${mtfDiv.side} RSI divergence detected at the pullback extreme`));
    } else {
      components.push(component('divergence', 'RSI divergence confirms the pullback rejection', 12,
        0.25, mtfDiv ? 'RSI divergence present but in the wrong direction' : 'No RSI divergence detected at this pullback'));
    }

    // Fibonacci golden pocket bonus: if pullback reached the 0.618-0.786 zone
    const fibRetracements = struct.lastSwingHigh && struct.lastSwingLow
      ? I.fibLevels(struct.lastSwingHigh.price, struct.lastSwingLow.price) : [];
    const goldenPocket = fibRetracements.find(f => f.ratio === 0.618);
    let goldenPocketBonus = 0;
    if (goldenPocket) {
      const gpPrice = dir === 'LONG' ? goldenPocket.priceFromHigh : goldenPocket.priceFromLow;
      const gpDist = Math.abs(price - gpPrice) / atrMtf;
      if (gpDist < 0.4) goldenPocketBonus = 10; // Within 0.4 ATR of the golden pocket
    }

    // VP and Fib-enriched target structure
    const vpLevels = ctx.vpLevels || [];
    const fibTargets = fibRetracements.map(f => ({ price: dir === 'LONG' ? f.priceFromLow : f.priceFromHigh, ratio: f.ratio }));
    const structure = nearestLevels(struct, price, atrMtf, ctx.liquidityPools, vpLevels, fibTargets);
    const geometry = buildRiskGeometry(dir, price, swingExtreme, atrMtf, structure);
    if (!geometry) return null;

    components.push(component('geometry', 'Reward:risk after structural target cap', 10,
      I.clamp((geometry.rrToTp2 - 1.5) / 1.8, 0, 1),
      `R:R to TP2 is ${geometry.rrToTp2}${geometry.cappedByStructure ? ' (capped by the next opposing level)' : ''}`));

    const score = scoreComponents(components) + goldenPocketBonus;
    return {
      name: 'TREND_PULLBACK',
      direction: dir,
      score: Math.min(100, score), grade: grade(Math.min(100, score)),
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

    // RSI divergence — a sweep + divergence is one of the highest-probability setups
    const mtfDiv = I.rsiDivergence(mtf, 14, 2);
    if (mtfDiv && ((dir === 'LONG' && mtfDiv.side === 'BULLISH') || (dir === 'SHORT' && mtfDiv.side === 'BEARISH'))) {
      components.push(component('divergence', 'RSI divergence at the sweep extreme', 12,
        1.0, `${mtfDiv.type} ${mtfDiv.side} RSI divergence confirms the sweep rejection`));
    } else {
      components.push(component('divergence', 'RSI divergence at the sweep extreme', 12,
        0.25, 'No confirming RSI divergence at the sweep'));
    }

    const vpLevels = ctx.vpLevels || [];
    const structure = nearestLevels(struct, price, atrMtf, ctx.liquidityPools, vpLevels);
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

  // ───────────────────────────────────────────────────────────────────────
  // Playbook 5 — SQUEEZE_FADE
  // The single highest R:R setup in crypto derivatives. Triggers when the
  // crowd is crowded, funding is extreme, and price begins to move against
  // them. Liquidation cascades produce 3–10R moves because the exits are
  // forced, not voluntary.
  // ───────────────────────────────────────────────────────────────────────
  function squeezeFade(ctx) {
    const { regime, mtf, flow, atrMtf, derivsPanel } = ctx;
    if (!regime.tradable) return null;
    if (mtf.length < 40) return null;

    // Requires derivatives data from the panel
    const derivsFacts = derivsPanel && derivsPanel.facts ? derivsPanel.facts : {};
    if (!derivsFacts.available) return null;

    // Detect crowd direction from derivatives
    let dir = null;
    if (derivsFacts.crowdedLong && derivsFacts.fundingZ != null && derivsFacts.fundingZ > 1.2) {
      dir = 'SHORT'; // Crowd is long and paying → fade them
    } else if (derivsFacts.crowdedShort && derivsFacts.fundingZ != null && derivsFacts.fundingZ < -1.2) {
      dir = 'LONG';  // Crowd is short and paying → fade them
    }
    if (!dir) return null;

    // Need a structural trigger — don't fade positioning alone
    const struct = I.marketStructure(mtf, 2);
    const last = mtf[mtf.length - 1];
    const price = ctx.price;

    // The structural trigger: price must be starting to move against the crowd
    // (BOS or CHoCH in the squeeze direction, or a sweep that trapped the crowd)
    const priceMovingAgainstCrowd = dir === 'LONG'
      ? last.close > last.open && (struct.structure === 'RANGE' || struct.structure === 'DOWNTREND')
      : last.close < last.open && (struct.structure === 'RANGE' || struct.structure === 'UPTREND');
    if (!priceMovingAgainstCrowd) return null;

    const components = [];

    // 1. Crowdedness intensity
    const fundingZ = Math.abs(derivsFacts.fundingZ || 0);
    components.push(component('crowdedness', 'Positioning is extreme and one-sided', 28,
      I.clamp((fundingZ - 1.0) / 1.5, 0.3, 1),
      `Funding z-score ${(derivsFacts.fundingZ || 0).toFixed(2)}, L/S ratio z ${(derivsFacts.lsZ || 0).toFixed(2)}`));

    // 2. OI compression (leverage stacked = cascade fuel)
    const oiZ = Math.abs(derivsFacts.oiZ || 0);
    components.push(component('leverageFuel', 'Open interest is at extremes (cascade fuel)', 22,
      I.clamp(oiZ / 2.0, 0.2, 1),
      `OI z-score ${(derivsFacts.oiZ || 0).toFixed(2)} \u2014 ${oiZ > 1.5 ? 'heavy leverage stacked, cascade potential is high' : 'moderate leverage'}`));

    // 3. Structural backing
    const pivot = dir === 'LONG' ? struct.lastSwingLow : struct.lastSwingHigh;
    components.push(component('structuralBacking', 'Structural level provides invalidation', 18,
      pivot ? 0.85 : 0.3,
      pivot ? `Invalidation at ${pivot.price.toFixed(4)}` : 'No clean structural invalidation — stop placement is less reliable'));

    // 4. Flow confirmation
    if (flow.available) {
      const agrees = dir === 'LONG' ? flow.flow5m.ratio - 0.5 : 0.5 - flow.flow5m.ratio;
      components.push(component('flowConfirm', 'Taker flow is turning against the crowd', 18,
        I.clamp(agrees / 0.12, 0, 1),
        `5m taker ratio ${(flow.flow5m.ratio * 100).toFixed(0)}% buy`));
    } else {
      components.push(component('flowConfirm', 'Taker flow is turning against the crowd', 18, 0.25,
        'Live tape unavailable'));
    }

    // 5. RSI divergence bonus
    const mtfDiv = I.rsiDivergence(mtf, 14, 2);
    const divAgrees = mtfDiv && ((dir === 'LONG' && mtfDiv.side === 'BULLISH') || (dir === 'SHORT' && mtfDiv.side === 'BEARISH'));
    components.push(component('divergence', 'RSI divergence against the crowd', 14,
      divAgrees ? 1.0 : 0.25,
      divAgrees ? `${mtfDiv.type} ${mtfDiv.side} RSI divergence detected` : 'No confirming RSI divergence'));

    const invalidation = pivot ? pivot.price : (dir === 'LONG' ? price - atrMtf * 2.5 : price + atrMtf * 2.5);
    const vpLevels = ctx.vpLevels || [];
    const structure = nearestLevels(struct, price, atrMtf, ctx.liquidityPools, vpLevels);
    const geometry = buildRiskGeometry(dir, price, invalidation, atrMtf, structure);
    if (!geometry) return null;

    const score = scoreComponents(components);
    return {
      name: 'SQUEEZE_FADE',
      direction: dir,
      score, grade: grade(score),
      components, geometry,
      narrative: `${dir === 'SHORT' ? 'Longs' : 'Shorts'} are crowded (funding z ${(derivsFacts.fundingZ || 0).toFixed(2)}) and price is beginning to move against them. OI z ${(derivsFacts.oiZ || 0).toFixed(2)} suggests heavy leverage. A liquidation cascade is the highest-R:R setup in crypto because the exits are forced, not voluntary.`
    };
  }

  // ───────────────────────────────────────────────────────────────────────
  // Scalp Playbook 1 — SCALP_MOMENTUM_BURST (5m breakout / momentum pulse)
  // Targets 5m to 10m quick burst in direction of short-term momentum.
  // ───────────────────────────────────────────────────────────────────────
  function scalpMomentumBurst(ctx) {
    const { ltf, flow, price } = ctx;
    if (!ltf || ltf.length < 15) return null;
    const atrLtf = ctx.atrLtf || (I.atr ? I.atr(ltf, 14) : 0);
    if (!atrLtf || atrLtf <= 0) return null;

    const last = ltf[ltf.length - 1];
    const range = Math.max(last.high - last.low, 1e-9);
    const body = last.close - last.open;
    const bodyPct = Math.abs(body) / range;
    const dir = body > 0 ? 'LONG' : 'SHORT';

    if (bodyPct < 0.45) return null;

    const closeNearExtreme = dir === 'LONG'
      ? (last.high - last.close) / range <= 0.30
      : (last.close - last.low) / range <= 0.30;
    if (!closeNearExtreme) return null;

    const recentVols = ltf.slice(-11, -1).map(c => c.volume);
    const avgVol = recentVols.length ? (recentVols.reduce((a, b) => a + b, 0) / recentVols.length) : 0;
    const volRatio = avgVol > 0 ? last.volume / avgVol : 1.0;
    if (volRatio < 1.25) return null;

    const components = [];
    components.push(component('volumeSurge', '5m volume burst confirms active participation', 25,
      I.clamp((volRatio - 1.2) / 1.5, 0, 1),
      `5m volume is ${volRatio.toFixed(1)}x the 10-bar average`));

    components.push(component('barConviction', 'Decisive body closing at extreme', 25,
      I.clamp((bodyPct - 0.45) / 0.4, 0, 1),
      `Body is ${(bodyPct * 100).toFixed(0)}% of range, closing near extreme`));

    const ltfCloses = ltf.map(c => c.close);
    const rsiVal = I.rsi(ltfCloses, 14);
    const rsiPrev = I.rsi(ltfCloses.slice(0, -1), 14);
    const rsiAgrees = dir === 'LONG' ? (rsiVal > 52 && rsiVal > rsiPrev && rsiVal < 78) : (rsiVal < 48 && rsiVal < rsiPrev && rsiVal > 22);
    components.push(component('rsiMomentum', '5m RSI confirms directional thrust', 25,
      rsiAgrees ? 0.90 : 0.30,
      `5m RSI is ${rsiVal.toFixed(1)} (${rsiAgrees ? 'aligned & accelerating' : 'unaligned'})`));

    if (flow && flow.available) {
      const wantBuy = dir === 'LONG';
      const ratio = flow.flow5m.ratio;
      const agrees = wantBuy ? ratio - 0.5 : 0.5 - ratio;
      components.push(component('flowConfirm', 'Tape taker flow agrees with burst', 25,
        I.clamp(agrees / 0.15, 0, 1),
        `5m taker ratio ${(ratio * 100).toFixed(0)}% buy`));
    } else {
      components.push(component('flowConfirm', 'Tape taker flow agrees with burst', 25, 0.40,
        'Live flow neutral'));
    }

    const structLtf = I.marketStructure(ltf, 2);
    const vpLevels = ctx.vpLevels || [];
    const structure = nearestLevels(structLtf, price, atrLtf, ctx.liquidityPools, vpLevels);
    const invalidation = dir === 'LONG' ? last.low : last.high;
    const geometry = buildScalpRiskGeometry(dir, price, invalidation, atrLtf, structure);
    if (!geometry || !geometry.viable) return null;

    const score = scoreComponents(components);
    return {
      name: 'SCALP_MOMENTUM_BURST',
      direction: dir,
      score, grade: grade(score),
      components, geometry,
      isScalp: true,
      horizon: '5m-10m',
      narrative: `5m volume burst (${volRatio.toFixed(1)}x) and strong directional candle in ${dir}. Fast momentum scalp targeting 0.65R (TP1) & 1.3R (TP2) with 10-15m max hold.`
    };
  }

  // ───────────────────────────────────────────────────────────────────────
  // Scalp Playbook 2 — SCALP_EXHAUSTION_FADE (5m Mean Reversion)
  // Catches quick 5-10 minute pullbacks after an overextended blow-off wick.
  // ───────────────────────────────────────────────────────────────────────
  function scalpExhaustionFade(ctx) {
    const { ltf, flow, price } = ctx;
    if (!ltf || ltf.length < 15) return null;
    const atrLtf = ctx.atrLtf || (I.atr ? I.atr(ltf, 14) : 0);
    if (!atrLtf || atrLtf <= 0) return null;

    const last = ltf[ltf.length - 1];
    const range = Math.max(last.high - last.low, 1e-9);
    const ltfCloses = ltf.map(c => c.close);
    const rsiVal = I.rsi(ltfCloses, 14);

    const upperWick = last.high - Math.max(last.open, last.close);
    const lowerWick = Math.min(last.open, last.close) - last.low;
    const upperWickPct = upperWick / range;
    const lowerWickPct = lowerWick / range;

    let dir = null;
    let wickPct = 0;
    let invalidation = 0;

    if (rsiVal >= 68 && upperWickPct >= 0.35) {
      dir = 'SHORT';
      wickPct = upperWickPct;
      invalidation = last.high;
    } else if (rsiVal <= 32 && lowerWickPct >= 0.35) {
      dir = 'LONG';
      wickPct = lowerWickPct;
      invalidation = last.low;
    }

    if (!dir) return null;

    const components = [];
    const rsiExtreme = dir === 'SHORT' ? (rsiVal - 65) / 20 : (35 - rsiVal) / 20;
    components.push(component('rsiExhaustion', '5m RSI pushed into overbought/oversold extreme', 30,
      I.clamp(rsiExtreme, 0.4, 1),
      `5m RSI reached ${rsiVal.toFixed(1)} before stalling`));

    components.push(component('wickRejection', 'Clear wick rejection against the trend', 30,
      I.clamp((wickPct - 0.30) / 0.35, 0.2, 1),
      `Rejection wick is ${(wickPct * 100).toFixed(0)}% of the 5m candle range`));

    const ema9 = I.ema(ltfCloses, 9);
    const distToMean = Math.abs(price - ema9);
    const distAtr = distToMean / atrLtf;
    components.push(component('meanReversionRoom', 'Sufficient room back to 5m short-term EMA', 20,
      I.clamp(distAtr / 0.8, 0.2, 1),
      `Distance to 5m EMA9 is ${distAtr.toFixed(2)} ATR`));

    if (flow && flow.available) {
      const agrees = dir === 'SHORT' ? 0.5 - flow.flow5m.ratio : flow.flow5m.ratio - 0.5;
      components.push(component('flowExhaustion', 'Taker flow slowing down', 20,
        I.clamp((agrees + 0.1) / 0.25, 0.2, 1),
        `5m taker ratio ${(flow.flow5m.ratio * 100).toFixed(0)}% buy`));
    } else {
      components.push(component('flowExhaustion', 'Taker flow slowing down', 20, 0.40, 'Neutral flow'));
    }

    const structLtf = I.marketStructure(ltf, 2);
    const vpLevels = ctx.vpLevels || [];
    const structure = nearestLevels(structLtf, price, atrLtf, ctx.liquidityPools, vpLevels);
    const geometry = buildScalpRiskGeometry(dir, price, invalidation, atrLtf, structure);
    if (!geometry || !geometry.viable) return null;

    const score = scoreComponents(components);
    return {
      name: 'SCALP_EXHAUSTION_FADE',
      direction: dir,
      score, grade: grade(score),
      components, geometry,
      isScalp: true,
      horizon: '5m-10m',
      narrative: `5m exhaustion wick (${(wickPct * 100).toFixed(0)}% range) with RSI ${rsiVal.toFixed(1)}. Fast mean-reversion scalp back toward 5m EMA9.`
    };
  }

  // ───────────────────────────────────────────────────────────────────────
  // Scalp Playbook 3 — SCALP_ORDERFLOW_IMBALANCE (Tape / Delta Imbalance)
  // Exploits aggressive taker market orders consuming book liquidity.
  // ───────────────────────────────────────────────────────────────────────
  function scalpOrderflowImbalance(ctx) {
    const { ltf, flow, price } = ctx;
    if (!flow || !flow.available || !flow.flow5m) return null;
    if (!ltf || ltf.length < 15) return null;
    const atrLtf = ctx.atrLtf || (I.atr ? I.atr(ltf, 14) : 0);
    if (!atrLtf || atrLtf <= 0) return null;

    const ratio = flow.flow5m.ratio;
    let dir = null;
    if (ratio >= 0.64) dir = 'LONG';
    else if (ratio <= 0.36) dir = 'SHORT';
    if (!dir) return null;

    if (flow.spread != null && (flow.spread / price) > 0.0006) return null;

    const last = ltf[ltf.length - 1];
    const prev = ltf[ltf.length - 2];

    const components = [];
    const imbalance = dir === 'LONG' ? (ratio - 0.5) / 0.35 : (0.5 - ratio) / 0.35;
    components.push(component('takerImbalance', 'Aggressive taker buying/selling imbalance', 35,
      I.clamp(imbalance, 0.4, 1),
      `5m taker flow ratio is ${(ratio * 100).toFixed(1)}% buy`));

    const microAgrees = dir === 'LONG' ? last.close >= prev.close : last.close <= prev.close;
    components.push(component('microTrend', '5m price action moving in direction of flow', 30,
      microAgrees ? 0.90 : 0.35,
      microAgrees ? '5m bar confirms tape direction' : '5m bar slightly lagging tape'));

    const bookAgrees = flow.imbalance ? ((dir === 'LONG' && flow.imbalance > 0) || (dir === 'SHORT' && flow.imbalance < 0)) : false;
    components.push(component('bookImbalance', 'Order book bid/ask depth support', 20,
      bookAgrees ? 0.85 : 0.40,
      bookAgrees ? 'Orderbook depth favors setup direction' : 'Orderbook depth neutral'));

    components.push(component('spreadTightness', 'Tight spread ensures low execution friction', 15,
      0.90, 'Spread within tight scalp tolerances'));

    const invalidation = dir === 'LONG' ? Math.min(last.low, prev.low) : Math.max(last.high, prev.high);
    const structLtf = I.marketStructure(ltf, 2);
    const vpLevels = ctx.vpLevels || [];
    const structure = nearestLevels(structLtf, price, atrLtf, ctx.liquidityPools, vpLevels);
    const geometry = buildScalpRiskGeometry(dir, price, invalidation, atrLtf, structure);
    if (!geometry || !geometry.viable) return null;

    const score = scoreComponents(components);
    return {
      name: 'SCALP_ORDERFLOW_IMBALANCE',
      direction: dir,
      score, grade: grade(score),
      components, geometry,
      isScalp: true,
      horizon: '5m-10m',
      narrative: `Taker flow imbalance (${(ratio * 100).toFixed(0)}% buy) driving order book. Fast tape scalp targeting 0.65R / 1.30R within 5-10m.`
    };
  }

  const ALL = [trendPullback, sweepReclaim, rangeFade, breakoutRetest, squeezeFade];
  const SCALP_PLAYBOOKS = [scalpMomentumBurst, scalpExhaustionFade, scalpOrderflowImbalance];

  /** Runs playbooks based on mode (standard swing or scalp), returns candidates sorted best-first. */
  function evaluate(ctx) {
    // Enrich context with VP levels from the panel for target placement
    const vpRead = (ctx.swarmLevels || []).filter(l => l && (l.src === 'VP' || l.kind === 'VP_POC' || l.kind === 'VP_VAH' || l.kind === 'VP_VAL' || l.kind === 'VP_HVN'));
    const enrichedCtx = Object.assign({}, ctx, { vpLevels: vpRead });

    // Find derivatives panel for squeeze playbook
    if (ctx.swarmLevels) {
      const panel = ctx.panel || [];
      const derivsPanel = panel.find(p => p && p.id === 'derivatives');
      if (derivsPanel) enrichedCtx.derivsPanel = derivsPanel;
    }

    const playbookList = ctx.scalpMode
      ? SCALP_PLAYBOOKS
      : (ctx.includeScalp ? [...ALL, ...SCALP_PLAYBOOKS] : ALL);

    const out = [];
    for (const fn of playbookList) {
      try {
        const c = fn(enrichedCtx);
        if (c && c.geometry) out.push(c);
      } catch (e) {
        out.push({ name: fn.name, error: e.message, score: 0, grade: 'D' });
      }
    }
    return out.filter(c => !c.error).sort((a, b) => b.score - a.score);
  }

  function evaluateScalp(ctx) {
    return evaluate(Object.assign({}, ctx, { scalpMode: true }));
  }

  return { evaluate, evaluateScalp, grade, MIN_RR, ROUND_TRIP_COST_PCT, buildRiskGeometry, buildScalpRiskGeometry, ALL, SCALP_PLAYBOOKS };
});
