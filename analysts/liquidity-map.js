/**
 * ANALYST — Liquidity Cartographer.
 *
 * This is the question a professional asks before any other one: *where is the
 * money that has to trade?* Not "is this bullish", but "where are the stops,
 * who is offside, and which direction does the book have to travel to reach
 * them". Price is drawn to resting liquidity because resting liquidity is the
 * only place large size can be filled.
 *
 * Nothing in V2 or V3 asked this. Both read indicators, which describe where
 * price HAS been. This describes where it is likely to be TAKEN.
 *
 * Pools mapped here:
 *  - Equal highs / equal lows (EQH/EQL). Two or more swings at the same price
 *    leave a shelf of stop orders directly beyond it. The tighter the equality
 *    and the more touches, the denser the pool.
 *  - Session extremes. Asia high/low, London high/low, previous day high/low.
 *    These are the reference points most desks and most retail stops key off.
 *  - Round numbers. Humans cluster stops at psychological levels; in crypto
 *    this is very strong at thousands and hundreds.
 *  - Swing clusters. Several pivots within a narrow band compound into one pool.
 *
 * Each pool gets a magnet score: how much stop liquidity is likely resting
 * there, how untested it is, and how close price is to it. A pool that has
 * already been swept is worth far less — the fuel is spent.
 */
(function (root, factory) {
  const api = factory(typeof require === 'function' ? require('../agents/indicators.js') : root.MasisIndicators);
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.AnalystLiquidityMap = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function (I) {

  /** Round-number ladder appropriate to the instrument's price scale. */
  function roundLevels(price, atr) {
    if (!price) return [];
    const mag = Math.pow(10, Math.floor(Math.log10(price)));
    const steps = [mag, mag / 2, mag / 4, mag / 10];
    const out = [];
    for (const step of steps) {
      if (step <= 0) continue;
      // Only levels within a few ATR matter; a round number 20 ATR away is not
      // a magnet, it is trivia.
      for (let k = -4; k <= 4; k++) {
        const lvl = Math.round(price / step) * step + k * step;
        if (lvl <= 0) continue;
        const dist = Math.abs(lvl - price);
        if (dist > atr * 6 || dist === 0) continue;
        out.push({ price: lvl, kind: 'ROUND', weight: step >= mag ? 1.0 : step >= mag / 2 ? 0.7 : 0.45 });
      }
    }
    return out;
  }

  /**
   * Equal highs / equal lows. Tolerance scales with ATR so "equal" means the
   * same thing on BTC and on DOGE.
   */
  function equalLevels(pivots, atr, side) {
    const tol = atr * 0.12;
    const used = new Array(pivots.length).fill(false);
    const pools = [];
    for (let i = 0; i < pivots.length; i++) {
      if (used[i]) continue;
      const group = [pivots[i]];
      for (let j = i + 1; j < pivots.length; j++) {
        if (used[j]) continue;
        if (Math.abs(pivots[j].price - pivots[i].price) <= tol) {
          group.push(pivots[j]);
          used[j] = true;
        }
      }
      if (group.length >= 2) {
        const price = group.reduce((a, p) => a + p.price, 0) / group.length;
        const spread = Math.max(...group.map(p => p.price)) - Math.min(...group.map(p => p.price));
        // Tighter equality = more convincing shelf = denser stops behind it.
        const tightness = tol > 0 ? 1 - Math.min(1, spread / tol) : 1;
        pools.push({
          price,
          kind: side === 'high' ? 'EQH' : 'EQL',
          touches: group.length,
          weight: Math.min(1, 0.55 + group.length * 0.18 + tightness * 0.2),
          lastTouchIndex: Math.max(...group.map(p => p.index))
        });
      }
    }
    return pools;
  }

  /** UTC session windows. Crypto trades continuously, but the desks that move
   * it do not, and session extremes are the levels they reference. */
  function sessionExtremes(candles) {
    if (!candles.length) return [];
    const now = candles[candles.length - 1].start;
    const day = 24 * 3600 * 1000;
    const dayStart = Math.floor(now / day) * day;

    const windows = [
      { name: 'ASIA', from: dayStart, to: dayStart + 8 * 3600 * 1000 },
      { name: 'LONDON', from: dayStart + 7 * 3600 * 1000, to: dayStart + 16 * 3600 * 1000 },
      { name: 'NY', from: dayStart + 13 * 3600 * 1000, to: dayStart + 21 * 3600 * 1000 },
      { name: 'PREV_DAY', from: dayStart - day, to: dayStart }
    ];

    const out = [];
    for (const w of windows) {
      const inWindow = candles.filter(c => c.start >= w.from && c.start < w.to);
      if (inWindow.length < 3) continue;
      const hi = Math.max(...inWindow.map(c => c.high));
      const lo = Math.min(...inWindow.map(c => c.low));
      // Previous-day and Asia extremes are the most-referenced of these.
      const weight = w.name === 'PREV_DAY' ? 0.95 : w.name === 'ASIA' ? 0.85 : 0.7;
      out.push({ price: hi, kind: `${w.name}_HIGH`, weight, side: 'high' });
      out.push({ price: lo, kind: `${w.name}_LOW`, weight, side: 'low' });
    }
    return out;
  }

  /** Has price already traded through this level since it formed? A swept pool
   * has had its stops triggered — the fuel is gone and it is no longer a target. */
  function isSwept(level, candles, side, fromIndex) {
    const start = Math.max(0, fromIndex == null ? candles.length - 40 : fromIndex + 1);
    for (let i = start; i < candles.length; i++) {
      if (side === 'high' && candles[i].high > level) return true;
      if (side === 'low' && candles[i].low < level) return true;
    }
    return false;
  }

  /**
   * @returns standard analyst read.
   */
  function analyse(ctx) {
    const { mtf, htf, price, atrMtf } = ctx;
    if (!mtf || mtf.length < 50 || !price || !atrMtf) {
      return { id: 'liquidity', name: 'Liquidity Cartographer', read: 'NEUTRAL', conviction: 0,
               evidence: ['Insufficient history to map resting liquidity'], levels: [], vetoes: [], facts: {} };
    }

    const struct = I.marketStructure(mtf, 2);
    const eqh = equalLevels(struct.swingHighs.slice(-14), atrMtf, 'high');
    const eql = equalLevels(struct.swingLows.slice(-14), atrMtf, 'low');
    const sessions = sessionExtremes(htf && htf.length > 24 ? htf : mtf);
    const rounds = roundLevels(price, atrMtf);

    const pools = [];

    for (const p of eqh) {
      if (isSwept(p.price, mtf, 'high', p.lastTouchIndex)) continue;
      pools.push({ price: p.price, side: 'high', kind: p.kind, touches: p.touches, weight: p.weight });
    }
    for (const p of eql) {
      if (isSwept(p.price, mtf, 'low', p.lastTouchIndex)) continue;
      pools.push({ price: p.price, side: 'low', kind: p.kind, touches: p.touches, weight: p.weight });
    }
    for (const s of sessions) {
      const swept = isSwept(s.price, mtf, s.side, null);
      if (swept) continue;
      pools.push({ price: s.price, side: s.side, kind: s.kind, touches: 1, weight: s.weight });
    }
    for (const r of rounds) {
      pools.push({ price: r.price, side: r.price > price ? 'high' : 'low', kind: r.kind, touches: 1, weight: r.weight });
    }

    // Merge pools sitting within a fraction of ATR — they are one shelf, and
    // counting them separately overstates how many targets exist.
    pools.sort((a, b) => a.price - b.price);
    const merged = [];
    for (const p of pools) {
      const last = merged[merged.length - 1];
      if (last && Math.abs(last.price - p.price) < atrMtf * 0.15 && last.side === p.side) {
        last.weight = Math.min(1.4, last.weight + p.weight * 0.5);
        last.kind = `${last.kind}+${p.kind}`;
        last.touches += p.touches;
      } else {
        merged.push(Object.assign({}, p));
      }
    }

    // Magnet score: density of likely stops, discounted by distance. Close,
    // heavy, untested pools are what price reaches for.
    for (const p of merged) {
      const distAtr = Math.abs(p.price - price) / atrMtf;
      p.distanceAtr = +distAtr.toFixed(2);
      p.magnet = +(p.weight * Math.exp(-distAtr / 3.5)).toFixed(3);
    }
    merged.sort((a, b) => b.magnet - a.magnet);

    const above = merged.filter(p => p.price > price);
    const below = merged.filter(p => p.price < price);
    const magnetAbove = above.reduce((a, p) => a + p.magnet, 0);
    const magnetBelow = below.reduce((a, p) => a + p.magnet, 0);
    const total = magnetAbove + magnetBelow;
    const imbalance = total > 0 ? (magnetAbove - magnetBelow) / total : 0;

    const nearestAbove = above.length ? above.reduce((a, b) => (a.distanceAtr <= b.distanceAtr ? a : b)) : null;
    const nearestBelow = below.length ? below.reduce((a, b) => (a.distanceAtr <= b.distanceAtr ? a : b)) : null;

    const evidence = [];
    if (nearestAbove) evidence.push(`Nearest untapped pool above: ${nearestAbove.kind} at ${nearestAbove.price.toFixed(4)} (${nearestAbove.distanceAtr} ATR away, magnet ${nearestAbove.magnet})`);
    if (nearestBelow) evidence.push(`Nearest untapped pool below: ${nearestBelow.kind} at ${nearestBelow.price.toFixed(4)} (${nearestBelow.distanceAtr} ATR away, magnet ${nearestBelow.magnet})`);
    evidence.push(`Resting-liquidity balance: ${(magnetAbove).toFixed(2)} above vs ${(magnetBelow).toFixed(2)} below`);

    // The read here is NOT a directional opinion in the usual sense. It says
    // which way the book has unfinished business. Price reaching for liquidity
    // above is a bullish DRAW, even in a downtrend — which is precisely why
    // shorting into an untouched shelf of stops above is how traders get run.
    let read = 'NEUTRAL';
    let conviction = 0;
    if (Math.abs(imbalance) > 0.22 && total > 0.5) {
      read = imbalance > 0 ? 'DRAW_UP' : 'DRAW_DOWN';
      conviction = Math.min(1, Math.abs(imbalance) * 1.4);
      evidence.push(`Unfinished business ${imbalance > 0 ? 'above' : 'below'} — untested stop shelves are the likelier magnet from here`);
    }

    const vetoes = [];
    // Entering with a dense pool immediately in the way is how a technically
    // clean setup gets stopped for no reason other than where it was entered.
    if (nearestAbove && nearestAbove.distanceAtr < 0.6 && nearestAbove.magnet > 0.55) {
      vetoes.push(`LONG_INTO_LIQUIDITY: a ${nearestAbove.kind} shelf sits only ${nearestAbove.distanceAtr} ATR above — longs here are providing the exit liquidity for whoever is filled into that sweep`);
    }
    if (nearestBelow && nearestBelow.distanceAtr < 0.6 && nearestBelow.magnet > 0.55) {
      vetoes.push(`SHORT_INTO_LIQUIDITY: a ${nearestBelow.kind} shelf sits only ${nearestBelow.distanceAtr} ATR below — shorts here are the fuel for the sweep that follows`);
    }

    return {
      id: 'liquidity',
      name: 'Liquidity Cartographer',
      read, conviction,
      evidence,
      levels: merged.slice(0, 12),
      vetoes,
      facts: {
        magnetAbove: +magnetAbove.toFixed(3),
        magnetBelow: +magnetBelow.toFixed(3),
        imbalance: +imbalance.toFixed(3),
        nearestAbove, nearestBelow,
        poolCount: merged.length
      }
    };
  }

  return { analyse, roundLevels, equalLevels, sessionExtremes };
});
