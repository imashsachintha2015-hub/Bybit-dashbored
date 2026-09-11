/**
 * ANALYST — Trap Forensics.
 *
 * A trap is not a chart pattern. It is a sequence: obvious liquidity is
 * advertised, participants are induced to commit in one direction, that
 * liquidity is taken, and price immediately leaves without them. The victims'
 * forced exit is the fuel for the move the other way.
 *
 * Detecting one requires three things in combination, which is why neither V2
 * nor V3 could do it. V2 had a single "sweep and rejection" check on a rolling
 * max that included the current bar. V3 added a proper sweep playbook but still
 * asked only "did price poke a level and close back". That is one third of a trap.
 *
 * The full signature this analyst requires:
 *   1. INDUCEMENT — there was something obvious to trade. An untested pool from
 *      the Liquidity Cartographer, or a clean breakout level. Without bait
 *      there is nobody to trap.
 *   2. RAID — price took that liquidity. Measurable penetration, not a graze.
 *   3. REJECTION — it failed to hold and closed back through, fast. The speed
 *      matters: a level reclaimed three bars later is a range, not a trap.
 *   4. CORROBORATION — participation confirms victims exist. Volume on the raid
 *      above its recent norm, and ideally aggression in the trapped direction.
 *
 * Trap types classified:
 *   SFP            — swing failure pattern; the cleanest, tightest-stop version
 *   FAILED_BREAKOUT— break of a consolidation that could not hold acceptance
 *   LIQUIDITY_GRAB — deep raid of a named pool with immediate full reclaim
 *   STOP_HUNT      — fast spike well beyond a pool, returning inside one bar
 */
(function (root, factory) {
  const api = factory(typeof require === 'function' ? require('../agents/indicators.js') : root.MasisIndicators);
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.AnalystTraps = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function (I) {

  function relVolume(candles, index, lookback = 20) {
    const from = Math.max(0, index - lookback);
    const window = candles.slice(from, index);
    if (!window.length) return 1;
    const avg = window.reduce((a, c) => a + c.volume, 0) / window.length;
    return avg > 0 ? candles[index].volume / avg : 1;
  }

  /** Wick beyond a level, as a share of the bar's own range. The tail IS the
   * evidence: it is the price path that filled the trapped orders. */
  function rejectionShare(candle, level, side) {
    const range = candle.high - candle.low;
    if (range <= 0) return 0;
    if (side === 'high') {
      const beyond = candle.high - Math.max(candle.open, candle.close, level);
      return Math.max(0, beyond) / range;
    }
    const beyond = Math.min(candle.open, candle.close, level) - candle.low;
    return Math.max(0, beyond) / range;
  }

  /**
   * Scans the last few confirmed bars for a completed trap against any pool the
   * Liquidity Cartographer mapped, plus structural swing levels.
   */
  function detectTrap(mtf, pools, atr) {
    const n = mtf.length;
    if (n < 25 || !atr) return null;

    const struct = I.marketStructure(mtf, 2);
    const candidates = [];

    // Pools from the cartographer, plus the most recent structural swings —
    // a trap can form against a level no pool logic named.
    const levels = (pools || []).map(p => ({ price: p.price, side: p.side, kind: p.kind, weight: p.weight || 0.6 }));
    if (struct.priorSwingHigh) levels.push({ price: struct.priorSwingHigh.price, side: 'high', kind: 'SWING_HIGH', weight: 0.7 });
    if (struct.priorSwingLow) levels.push({ price: struct.priorSwingLow.price, side: 'low', kind: 'SWING_LOW', weight: 0.7 });
    if (struct.lastSwingHigh) levels.push({ price: struct.lastSwingHigh.price, side: 'high', kind: 'SWING_HIGH', weight: 0.6 });
    if (struct.lastSwingLow) levels.push({ price: struct.lastSwingLow.price, side: 'low', kind: 'SWING_LOW', weight: 0.6 });

    // Only the last 3 confirmed bars: a trap is an event, and its edge decays
    // fast. Acting on a trap from ten bars ago is just late.
    for (let back = 1; back <= 3; back++) {
      const idx = n - back;
      if (idx < 20) continue;
      const bar = mtf[idx];

      for (const lvl of levels) {
        const raided = lvl.side === 'high' ? bar.high > lvl.price : bar.low < lvl.price;
        if (!raided) continue;

        // Must have closed back on the correct side — the raid failed.
        const reclaimed = lvl.side === 'high' ? bar.close < lvl.price : bar.close > lvl.price;
        if (!reclaimed) continue;

        const penetration = Math.abs((lvl.side === 'high' ? bar.high : bar.low) - lvl.price) / atr;
        if (penetration < 0.06) continue;           // a graze, not a raid
        if (penetration > 2.2) continue;            // that is a breakout that came back, different animal

        const rejection = rejectionShare(bar, lvl.price, lvl.side);
        if (rejection < 0.28) continue;             // no meaningful tail = no forced fills

        const rvol = relVolume(mtf, idx, 20);
        if (rvol < 1.05) continue;                  // nobody was there to be trapped

        // Did price stay rejected on the bars after? A trap that is immediately
        // re-raided was not a trap, it was the first attempt at a breakout.
        let heldAfter = true;
        for (let k = idx + 1; k < n; k++) {
          if (lvl.side === 'high' && mtf[k].close > lvl.price) { heldAfter = false; break; }
          if (lvl.side === 'low' && mtf[k].close < lvl.price) { heldAfter = false; break; }
        }
        if (!heldAfter) continue;

        // Classify.
        let type = 'LIQUIDITY_GRAB';
        const bodyShare = Math.abs(bar.close - bar.open) / Math.max(bar.high - bar.low, 1e-9);
        if (rejection > 0.55 && bodyShare < 0.35) type = 'STOP_HUNT';
        else if (lvl.kind.includes('EQH') || lvl.kind.includes('EQL') || lvl.kind.includes('SWING')) type = 'SFP';
        else if (lvl.kind.includes('HIGH') || lvl.kind.includes('LOW')) type = 'FAILED_BREAKOUT';

        // Direction of the trade the trap creates: victims were positioned the
        // way the raid pointed, so the opportunity is the other way.
        const direction = lvl.side === 'high' ? 'SHORT' : 'LONG';

        // Quality: how convincing is each leg of the signature.
        const inducement = Math.min(1, lvl.weight);
        const raidQuality = 1 - Math.abs(penetration - 0.45) / 1.6;
        const participation = Math.min(1, (rvol - 1) / 1.6);
        const freshness = 1 - (back - 1) * 0.22;

        const quality = Math.max(0, Math.min(1,
          inducement * 0.28 + Math.max(0, raidQuality) * 0.24 + rejection * 0.24 + participation * 0.14 + freshness * 0.10));

        candidates.push({
          type, direction, level: lvl.price, levelKind: lvl.kind,
          barsAgo: back, penetrationAtr: +penetration.toFixed(2),
          rejectionShare: +rejection.toFixed(2), relVolume: +rvol.toFixed(2),
          quality: +quality.toFixed(3),
          extreme: lvl.side === 'high' ? bar.high : bar.low,
          bar
        });
      }
    }

    if (!candidates.length) return null;
    candidates.sort((a, b) => b.quality - a.quality);
    return candidates[0];
  }

  function analyse(ctx) {
    const { mtf, atrMtf, liquidityPools } = ctx;
    const none = {
      id: 'traps', name: 'Trap Forensics', read: 'NEUTRAL', conviction: 0,
      evidence: ['No completed trap signature in the last three bars'], levels: [], vetoes: [], facts: { trap: null }
    };
    if (!mtf || mtf.length < 25 || !atrMtf) return none;

    const trap = detectTrap(mtf, liquidityPools, atrMtf);
    if (!trap) return none;

    // A trap fade that opposes an established trend is a counter-trend trade
    // wearing a pattern's clothing. The trap is still reported — the hazard it
    // creates for the other direction is real — but it does not get to vote for
    // a direction the regime is running against.
    const trending = ctx.regime === 'TREND';
    const trendBias = ctx.bias;
    const opposesTrend = trending && trendBias && trendBias !== 'NEUTRAL' && trendBias !== trap.direction;

    const evidence = [
      `${trap.type} against ${trap.levelKind} at ${trap.level.toFixed(4)}, ${trap.barsAgo} bar(s) ago`,
      `Raid went ${trap.penetrationAtr} ATR beyond the level and closed back inside — the orders filled out there are now offside`,
      `${(trap.rejectionShare * 100).toFixed(0)}% of that bar's range is rejection wick on ${trap.relVolume}x average volume, so there was real participation to trap`,
      `Opportunity is ${trap.direction}: whoever was induced ${trap.direction === 'SHORT' ? 'long above' : 'short below'} the level has to cover`
    ];

    const vetoes = [];
    // A fresh trap in one direction is an active hazard for the other.
    if (trap.direction === 'SHORT') {
      vetoes.push(`FRESH_BULL_TRAP: highs were raided and rejected ${trap.barsAgo} bar(s) ago — a long here is buying exactly what the trap was built to sell into`);
    } else {
      vetoes.push(`FRESH_BEAR_TRAP: lows were raided and reclaimed ${trap.barsAgo} bar(s) ago — a short here is selling exactly what the trap was built to buy from`);
    }

    if (opposesTrend) {
      evidence.push(`Regime is a ${trendBias} trend, so this ${trap.direction} trap fade would be counter-trend. The trap is reported as a hazard but does not vote for a direction the trend is running against.`);
    }

    return {
      id: 'traps',
      name: 'Trap Forensics',
      read: opposesTrend ? 'NEUTRAL' : (trap.direction === 'LONG' ? 'BULLISH' : 'BEARISH'),
      conviction: opposesTrend ? 0 : trap.quality,
      evidence,
      levels: [{ price: trap.extreme, kind: `${trap.type}_EXTREME`, side: trap.direction === 'LONG' ? 'low' : 'high' }],
      vetoes,
      facts: { trap }
    };
  }

  return { analyse, detectTrap };
});
