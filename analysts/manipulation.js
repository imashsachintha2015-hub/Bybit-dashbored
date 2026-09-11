/**
 * ANALYST — Manipulation Forensics.
 *
 * A deliberate note on epistemics, because this is the area where trading
 * systems most often lie to their operators: this analyst never claims proven
 * manipulation. Proving intent from public market data is not possible, and any
 * system that tells you "a whale is spoofing" is overstating what it knows.
 * What it can do is recognise PATTERNS that are characteristic of manipulative
 * behaviour, and treat them as reasons for caution rather than as facts.
 *
 * Patterns detected:
 *
 * LIVE (needs the order book):
 *  - Spoofing: a wall many multiples of the recent median appears at the top of
 *    book and disappears without being traded through. Leaning on a level that
 *    was never real is a reliable way to be stopped out.
 *  - Layering: several large orders stacked on one side that vanish together.
 *  - Iceberg: repeated fills at one price with the displayed size barely moving —
 *    a large hidden resting order. Unlike a spoof, this is REAL size and is a
 *    reason to respect the level, not to distrust it. Distinguishing the two is
 *    the whole point.
 *
 * CANDLE-BASED (works in backtest too):
 *  - Momentum ignition: an abrupt large bar on heavy volume that immediately
 *    fully retraces. The classic "push it, trigger the algos, sell into them".
 *  - Wash-trade signature: sustained high volume with almost no price range,
 *    which is what volume without genuine transfer of risk looks like.
 *  - Liquidity vacuum: a large move on BELOW-average volume, meaning price
 *    travelled because nobody was there, not because anyone wanted it there.
 *    These retrace far more often than volume-backed moves.
 */
(function (root, factory) {
  const api = factory(typeof require === 'function' ? require('../agents/indicators.js') : root.MasisIndicators);
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.AnalystManipulation = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function (I) {

  function detectMomentumIgnition(candles, atr) {
    const n = candles.length;
    if (n < 6 || !atr) return null;
    for (let back = 1; back <= 3; back++) {
      const i = n - back - 1;
      if (i < 3) continue;
      const push = candles[i];
      const body = Math.abs(push.close - push.open);
      if (body < atr * 1.1) continue;

      const avgVol = I.sma(candles.slice(Math.max(0, i - 20), i).map(c => c.volume), 20);
      if (!avgVol || push.volume < avgVol * 1.8) continue;

      // Did the move get given straight back?
      const after = candles.slice(i + 1, i + 4);
      if (!after.length) continue;
      const pushUp = push.close > push.open;
      const retraced = pushUp
        ? Math.min(...after.map(c => c.low)) <= push.open + body * 0.2
        : Math.max(...after.map(c => c.high)) >= push.open - body * 0.2;
      if (!retraced) continue;

      return {
        direction: pushUp ? 'UP' : 'DOWN',
        barsAgo: back + 1,
        bodyAtr: +(body / atr).toFixed(2),
        volumeRatio: +(push.volume / avgVol).toFixed(2)
      };
    }
    return null;
  }

  function detectWashSignature(candles, atr) {
    if (candles.length < 25 || !atr) return null;
    const recent = candles.slice(-6);
    const avgVol = I.sma(candles.slice(-30, -6).map(c => c.volume), 24);
    if (!avgVol) return null;
    const recentVol = I.sma(recent.map(c => c.volume), 6);
    const span = Math.max(...recent.map(c => c.high)) - Math.min(...recent.map(c => c.low));
    if (recentVol > avgVol * 1.7 && span < atr * 0.9) {
      return { volumeRatio: +(recentVol / avgVol).toFixed(2), spanAtr: +(span / atr).toFixed(2) };
    }
    return null;
  }

  function detectLiquidityVacuum(candles, atr) {
    if (candles.length < 25 || !atr) return null;
    const last = candles[candles.length - 1];
    const body = Math.abs(last.close - last.open);
    if (body < atr * 0.9) return null;
    const avgVol = I.sma(candles.slice(-25, -1).map(c => c.volume), 24);
    if (!avgVol || last.volume > avgVol * 0.75) return null;
    return {
      direction: last.close > last.open ? 'UP' : 'DOWN',
      bodyAtr: +(body / atr).toFixed(2),
      volumeRatio: +(last.volume / avgVol).toFixed(2)
    };
  }

  /** Iceberg: many fills at one price while displayed size stays flat. Requires
   * live trades + book, so it is simply unavailable in replay. */
  function detectIceberg(flowAgent) {
    if (!flowAgent || !flowAgent.trades || flowAgent.trades.length < 60) return null;
    const recent = flowAgent.trades.slice(-140);
    const byPrice = {};
    for (const t of recent) {
      const k = t.price.toFixed(8);
      byPrice[k] = byPrice[k] || { count: 0, notional: 0, buys: 0 };
      byPrice[k].count++;
      byPrice[k].notional += t.notional;
      if (t.isBuy) byPrice[k].buys++;
    }
    let best = null;
    for (const [price, d] of Object.entries(byPrice)) {
      if (d.count >= 22 && (!best || d.count > best.count)) {
        best = { price: parseFloat(price), count: d.count, notional: d.notional, buyShare: d.buys / d.count };
      }
    }
    if (!best) return null;
    // Absorbed side: if buyers keep lifting at one price and it holds, a hidden
    // seller is there; and vice versa.
    return Object.assign(best, { hiddenSide: best.buyShare > 0.6 ? 'SELLER' : best.buyShare < 0.4 ? 'BUYER' : 'UNCLEAR' });
  }

  function analyse(ctx) {
    const { mtf, atrMtf, flowAgent } = ctx;
    const evidence = [];
    const vetoes = [];
    const concerns = [];
    const levels = [];
    const facts = { liveBookAvailable: false };
    let read = 'NEUTRAL';
    let conviction = 0;

    if (!mtf || mtf.length < 25 || !atrMtf) {
      return { id: 'manipulation', name: 'Manipulation Forensics', read, conviction,
               evidence: ['Insufficient history'], levels, vetoes, concerns, facts };
    }

    const ignition = detectMomentumIgnition(mtf, atrMtf);
    if (ignition) {
      facts.momentumIgnition = ignition;
      evidence.push(`Momentum-ignition pattern ${ignition.barsAgo} bars ago: a ${ignition.bodyAtr} ATR push ${ignition.direction} on ${ignition.volumeRatio}x volume that was immediately given back. Consistent with pushing price to trigger reactive flow and selling into it — not proof of intent, but a reason to distrust the direction of that push.`);
      concerns.push({ side: ignition.direction === 'UP' ? 'LONG' : 'SHORT', weight: 0.45,
        note: `The last decisive move in this direction fully retraced, so the displacement it created is not reliable evidence` });
      conviction = 0.4;
      read = 'DANGER';
    }

    const wash = detectWashSignature(mtf, atrMtf);
    if (wash) {
      facts.washSignature = wash;
      evidence.push(`Volume/range anomaly: ${wash.volumeRatio}x normal volume across the last six bars while price covered only ${wash.spanAtr} ATR. Heavy trading with no transfer of price is characteristic of wash or cross activity, and makes volume-based confirmation unreliable here.`);
      concerns.push({ side: null, weight: 0.4,
        note: 'The volume/range relationship is anomalous, so any signal leaning on volume confirmation should be discounted here' });
      conviction = Math.max(conviction, 0.35);
      read = 'DANGER';
    }

    const vacuum = detectLiquidityVacuum(mtf, atrMtf);
    if (vacuum) {
      facts.liquidityVacuum = vacuum;
      evidence.push(`Last bar moved ${vacuum.bodyAtr} ATR ${vacuum.direction} on only ${vacuum.volumeRatio}x average volume — price travelled because the book was thin, not because anyone wanted it there. Moves made in a vacuum retrace far more often than volume-backed ones.`);
      concerns.push({ side: vacuum.direction === 'UP' ? 'LONG' : 'SHORT', weight: 0.35,
        note: 'That displacement was made on absent liquidity, so it should not be read as conviction' });
      conviction = Math.max(conviction, 0.3);
    }

    if (flowAgent && typeof flowAgent.detectSpoof === 'function') {
      const spoof = flowAgent.detectSpoof();
      facts.liveBookAvailable = !!(flowAgent.bookHistory && flowAgent.bookHistory.length > 6);
      if (spoof && spoof.detected) {
        facts.spoof = spoof;
        evidence.push(`A large ${spoof.side} wall appeared at the top of book and vanished without trading through. This is the signature of spoofed liquidity — the level it advertised was never real support or resistance.`);
        vetoes.push(`SPOOFED_${spoof.side}: any thesis leaning on that level is leaning on liquidity that has already been pulled`);
        conviction = Math.max(conviction, 0.5);
        read = 'DANGER';
      }
      const ice = detectIceberg(flowAgent);
      if (ice && ice.hiddenSide !== 'UNCLEAR') {
        facts.iceberg = ice;
        levels.push({ price: ice.price, kind: `ICEBERG_${ice.hiddenSide}` });
        // An iceberg is the opposite of a spoof: it is real, concealed size.
        evidence.push(`Iceberg signature at ${ice.price}: ${ice.count} fills at a single price with the displayed size barely moving, implying a large hidden ${ice.hiddenSide.toLowerCase()}. Unlike a spoof this is genuine size, so the level deserves MORE respect, not less.`);
        conviction = Math.max(conviction, 0.45);
      }
    } else {
      evidence.push('No live order book in this context — spoofing, layering and iceberg detection are unavailable, not clear. Treat book-based manipulation as unknown.');
    }

    if (!evidence.length) evidence.push('No manipulation-characteristic patterns in the recent tape');
    return { id: 'manipulation', name: 'Manipulation Forensics', read, conviction, evidence, levels, vetoes, concerns, facts };
  }

  return { analyse, detectMomentumIgnition, detectWashSignature, detectLiquidityVacuum, detectIceberg };
});
