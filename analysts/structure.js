/**
 * ANALYST — Market Structure.
 *
 * Reads the tape the way a discretionary trader reads a chart: not "is the EMA
 * above the other EMA", but where did price leave unfinished business, which
 * candle actually caused the last displacement, and is the current price
 * expensive or cheap relative to the leg it is retracing.
 *
 * Concepts implemented, each of which is a thing professionals genuinely use:
 *
 *  - BOS (break of structure): a confirmed CLOSE beyond the last protected
 *    swing. Continuation.
 *  - CHoCH (change of character): the first break the OTHER way after a
 *    sequence. This is the earliest honest reversal signal available, and it is
 *    the difference between calling a reversal and chasing one.
 *  - Order blocks: the last opposing candle before the move that broke
 *    structure. It marks where the size that caused the displacement was
 *    filled, which is why price so often returns to it and reacts.
 *  - Fair value gaps: a three-bar imbalance where the middle bar moved so fast
 *    that bar 1's wick and bar 3's wick do not overlap. Price returns to fill
 *    these more often than not, because the auction was skipped.
 *  - Premium / discount: where price sits within the current leg. Buying in
 *    premium and selling in discount is how an otherwise correct directional
 *    call still loses.
 */
(function (root, factory) {
  const api = factory(typeof require === 'function' ? require('../agents/indicators.js') : root.MasisIndicators);
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.AnalystStructure = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function (I) {

  /** Three-bar imbalance. Returns unfilled gaps, newest first. */
  function fairValueGaps(candles, atr, maxAge = 60) {
    const gaps = [];
    const from = Math.max(2, candles.length - maxAge);
    for (let i = from; i < candles.length; i++) {
      const a = candles[i - 2], c = candles[i];
      // Bullish FVG: bar 3's low is above bar 1's high — the gap between them
      // never traded.
      if (c.low > a.high) {
        const size = c.low - a.high;
        if (size > atr * 0.18) gaps.push({ type: 'BULLISH', top: c.low, bottom: a.high, index: i, size });
      }
      if (c.high < a.low) {
        const size = a.low - c.high;
        if (size > atr * 0.18) gaps.push({ type: 'BEARISH', top: a.low, bottom: c.high, index: i, size });
      }
    }
    // Drop gaps price has already traded back through — the imbalance is gone.
    return gaps.filter(g => {
      for (let k = g.index + 1; k < candles.length; k++) {
        if (g.type === 'BULLISH' && candles[k].low <= g.bottom) return false;
        if (g.type === 'BEARISH' && candles[k].high >= g.top) return false;
      }
      return true;
    }).reverse();
  }

  /** The last opposing candle before the displacement that broke structure. */
  function orderBlock(candles, breakIndex, direction) {
    if (breakIndex == null || breakIndex < 3) return null;
    for (let i = breakIndex - 1; i >= Math.max(0, breakIndex - 12); i--) {
      const c = candles[i];
      const isDown = c.close < c.open;
      const isUp = c.close > c.open;
      if (direction === 'BULLISH' && isDown) {
        return { type: 'BULLISH', top: Math.max(c.open, c.close), bottom: c.low, index: i };
      }
      if (direction === 'BEARISH' && isUp) {
        return { type: 'BEARISH', top: c.high, bottom: Math.min(c.open, c.close), index: i };
      }
    }
    return null;
  }

  /** BOS / CHoCH from confirmed pivots. */
  function structureEvents(candles) {
    const st = I.marketStructure(candles, 2);
    const n = candles.length;
    let lastEvent = null;

    const refHigh = st.priorSwingHigh || st.lastSwingHigh;
    const refLow = st.priorSwingLow || st.lastSwingLow;

    for (let i = Math.max(5, n - 25); i < n; i++) {
      const c = candles[i];
      if (refHigh && c.close > refHigh.price && i > refHigh.index) {
        lastEvent = { type: st.structure === 'DOWNTREND' ? 'CHOCH' : 'BOS', direction: 'BULLISH', level: refHigh.price, index: i };
      }
      if (refLow && c.close < refLow.price && i > refLow.index) {
        lastEvent = { type: st.structure === 'UPTREND' ? 'CHOCH' : 'BOS', direction: 'BEARISH', level: refLow.price, index: i };
      }
    }
    return { structure: st, lastEvent };
  }

  function analyse(ctx) {
    const { mtf, atrMtf, price } = ctx;
    const none = { id: 'structure', name: 'Market Structure', read: 'NEUTRAL', conviction: 0,
                   evidence: ['Insufficient history for structural reading'], levels: [], vetoes: [], facts: {} };
    if (!mtf || mtf.length < 50 || !atrMtf) return none;

    const { structure: st, lastEvent } = structureEvents(mtf);
    const gaps = fairValueGaps(mtf, atrMtf);
    const ob = lastEvent ? orderBlock(mtf, lastEvent.index, lastEvent.direction) : null;

    // Premium / discount within the active leg.
    const legHigh = st.lastSwingHigh ? st.lastSwingHigh.price : Math.max(...mtf.slice(-40).map(c => c.high));
    const legLow = st.lastSwingLow ? st.lastSwingLow.price : Math.min(...mtf.slice(-40).map(c => c.low));
    const legRange = legHigh - legLow;
    const position = legRange > 0 ? (price - legLow) / legRange : 0.5;
    const zone = position > 0.66 ? 'PREMIUM' : position < 0.34 ? 'DISCOUNT' : 'EQUILIBRIUM';

    const evidence = [];
    const vetoes = [];
    let read = 'NEUTRAL';
    let conviction = 0;

    evidence.push(`Swing structure: ${st.structure}`);
    if (lastEvent) {
      const label = lastEvent.type === 'CHOCH' ? 'Change of character' : 'Break of structure';
      evidence.push(`${label} ${lastEvent.direction} — a confirmed close through ${lastEvent.level.toFixed(4)}${lastEvent.type === 'CHOCH' ? '. This is the first break against the prior sequence, which is the earliest honest reversal evidence rather than a guess at one.' : '. Continuation of the existing sequence.'}`);
      // MEASURED: over ~8,000 observations a raw break of structure on this
      // timeframe pointed the right way 45.8% of the time, with an average
      // forward move of -0.04 ATR in its own direction. Most breaks of
      // structure fail, which is a well-known property of the pattern and not
      // a quirk of this sample.
      //
      // So this analyst does not vote on direction. It is not inverted to
      // profit from the negative reading either — that would be fitting a sign
      // to one dataset, and a signal that is reliably wrong is rarer than a
      // signal that is merely noisy. It contributes what it genuinely measures
      // well: levels, order blocks, imbalances, and where price sits in its leg.
      read = 'NEUTRAL';
      conviction = 0;
      evidence.push('Structure contributes levels and location here, not a directional vote: break-of-structure direction measured no better than chance over ~8,000 observations on this timeframe.');
    }

    evidence.push(`Price sits in ${zone} (${(position * 100).toFixed(0)}% of the current leg, ${legLow.toFixed(4)}–${legHigh.toFixed(4)})`);

    // Location vetoes. These are the ones that stop a correct direction being
    // traded from the wrong price.
    // Location is a matter of degree, not a switch. Buying at 70% of a leg is
    // mildly expensive; buying at 95% is standing on the edge. The first cut of
    // this vetoed everything above 66%, which meant roughly half of all bars
    // carried a fatal objection and nothing could ever be traded.
    const concerns = [];
    if (position > 0.85) {
      vetoes.push(`BUYING_PREMIUM: price is at ${(position * 100).toFixed(0)}% of its leg — a long here pays the extreme of the range and puts the stop beneath the entire move`);
    } else if (position > 0.62) {
      concerns.push({ side: 'LONG', weight: (position - 0.62) / 0.23 * 0.5,
        note: `Price is at ${(position * 100).toFixed(0)}% of its leg — a long is paying up rather than buying value` });
    }
    if (position < 0.15) {
      vetoes.push(`SELLING_DISCOUNT: price is at ${(position * 100).toFixed(0)}% of its leg — a short here sells the extreme of the range with the stop above the entire move`);
    } else if (position < 0.38) {
      concerns.push({ side: 'SHORT', weight: (0.38 - position) / 0.23 * 0.5,
        note: `Price is at ${(position * 100).toFixed(0)}% of its leg — a short is selling into value rather than into strength` });
    }

    const levels = [];
    if (ob) {
      levels.push({ price: (ob.top + ob.bottom) / 2, kind: `${ob.type}_ORDER_BLOCK`, top: ob.top, bottom: ob.bottom });
      evidence.push(`${ob.type} order block at ${ob.bottom.toFixed(4)}–${ob.top.toFixed(4)} — the last opposing candle before the displacement, where the size that caused the move was filled`);
    }
    for (const g of gaps.slice(0, 3)) {
      levels.push({ price: (g.top + g.bottom) / 2, kind: `${g.type}_FVG`, top: g.top, bottom: g.bottom });
    }
    if (gaps.length) {
      const g = gaps[0];
      evidence.push(`Nearest unfilled imbalance: ${g.type} fair value gap ${g.bottom.toFixed(4)}–${g.top.toFixed(4)} — price skipped the auction there and tends to return`);
    }

    return {
      id: 'structure', name: 'Market Structure', read, conviction,
      evidence, levels, vetoes, concerns,
      facts: {
        structure: st.structure, lastEvent, zone,
        legPosition: +position.toFixed(3), legHigh, legLow,
        orderBlock: ob, unfilledGaps: gaps.length
      }
    };
  }

  return { analyse, fairValueGaps, orderBlock, structureEvents };
});
