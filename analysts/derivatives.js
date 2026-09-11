/**
 * ANALYST — Positioning & Derivatives.
 *
 * The single largest edge available in crypto that does not exist in equities:
 * the market publishes, in near real time, how the crowd is positioned and how
 * much it is paying to stay there. Open interest says how much leveraged
 * exposure is open, funding says which side is paying, and the long/short
 * account ratio says which way retail is leaning.
 *
 * Neither V2 nor V3 read any of it. V2 fetched `fundingRate` off the ticker and
 * used it for two cosmetic warning strings. That is the difference between
 * having the data and reading it.
 *
 * What a professional actually extracts:
 *
 * 1. THE OI/PRICE QUADRANT. Price direction alone is ambiguous; paired with
 *    open interest it is not:
 *      price up   + OI up   → new longs. Real money committing. Trend supported,
 *                             but becomes fuel for a flush once crowded.
 *      price up   + OI down → short covering. A rally with nobody buying it;
 *                             it ends when the last trapped short is out.
 *      price down + OI up   → new shorts. Supported downtrend, and equally the
 *                             raw material of a squeeze.
 *      price down + OI down → longs being liquidated. Capitulation; the leverage
 *                             is being cleaned out, which is how bottoms form.
 *
 * 2. SQUEEZE FUEL. Crowded positioning plus expensive funding plus price that
 *    refuses to go the crowd's way is the setup for a violent move against them.
 *    This is the mechanism behind most large, fast crypto candles, and it is
 *    readable in advance.
 *
 * 3. COILED POSITIONING. Open interest climbing hard while price goes nowhere
 *    means leverage is stacking inside a range. The resolution is usually
 *    disproportionate, because one side gets liquidated into the other's entry.
 */
(function (root, factory) {
  const api = factory(typeof require === 'function' ? require('../agents/indicators.js') : root.MasisIndicators);
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.AnalystDerivatives = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function (I) {

  function pctChange(series, key, bars) {
    if (!series || series.length < bars + 1) return null;
    const now = series[series.length - 1][key];
    const then = series[series.length - 1 - bars][key];
    if (now == null || then == null || !then) return null;
    return (now - then) / Math.abs(then);
  }

  function zScore(series, key, lookback = 72) {
    const vals = (series || []).slice(-lookback).map(r => r[key]).filter(v => v != null);
    if (vals.length < 12) return null;
    const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
    const sd = I.stdev(vals);
    if (!sd) return null;
    return (vals[vals.length - 1] - mean) / sd;
  }

  function analyse(ctx) {
    const { derivs, htf } = ctx;
    const none = {
      id: 'derivatives', name: 'Positioning & Derivatives', read: 'NEUTRAL', conviction: 0,
      evidence: ['No derivatives context available — positioning is unread, not neutral'],
      levels: [], vetoes: [], concerns: [], facts: { available: false }
    };
    if (!derivs || derivs.length < 24 || !htf || htf.length < 12) return none;

    const oiChange6 = pctChange(derivs, 'oiUsd', 6);
    const oiChange24 = pctChange(derivs, 'oiUsd', 24);
    const last = derivs[derivs.length - 1];
    const funding = last.fundingRate;
    const lsRatio = last.longShortRatio;

    const priceNow = htf[htf.length - 1].close;
    const price6 = htf.length > 6 ? htf[htf.length - 7].close : priceNow;
    const priceChange6 = price6 ? (priceNow - price6) / price6 : 0;

    const fundingZ = zScore(derivs, 'fundingRate', 120);
    const lsZ = zScore(derivs, 'longShortRatio', 120);
    const oiZ = zScore(derivs, 'oiUsd', 120);

    const evidence = [];
    const vetoes = [];
    const concerns = [];
    let read = 'NEUTRAL';
    let conviction = 0;

    // ── 1. Quadrant ──
    let quadrant = 'UNCLEAR';
    if (oiChange6 != null && Math.abs(priceChange6) > 0.002 && Math.abs(oiChange6) > 0.004) {
      if (priceChange6 > 0 && oiChange6 > 0) quadrant = 'NEW_LONGS';
      else if (priceChange6 > 0 && oiChange6 < 0) quadrant = 'SHORT_COVERING';
      else if (priceChange6 < 0 && oiChange6 > 0) quadrant = 'NEW_SHORTS';
      else quadrant = 'LONG_LIQUIDATION';
    }

    const quadrantNote = {
      NEW_LONGS: 'Price up on rising open interest — fresh leveraged longs are committing. Supports continuation, and builds the inventory that a flush would liquidate.',
      SHORT_COVERING: 'Price up on FALLING open interest — this is shorts closing, not buyers arriving. Rallies built on covering run out of fuel when the last trapped short is out.',
      NEW_SHORTS: 'Price down on rising open interest — fresh shorts are committing. Supports continuation, and stacks the fuel for a squeeze.',
      LONG_LIQUIDATION: 'Price down on FALLING open interest — leveraged longs are being liquidated out. This is how leverage gets cleaned out and how durable lows form.',
      UNCLEAR: 'Open interest and price are not moving decisively enough to classify positioning.'
    }[quadrant];
    evidence.push(quadrantNote);

    if (quadrant === 'NEW_LONGS') { read = 'BULLISH'; conviction = 0.45; }
    else if (quadrant === 'NEW_SHORTS') { read = 'BEARISH'; conviction = 0.45; }
    else if (quadrant === 'SHORT_COVERING') {
      read = 'BULLISH'; conviction = 0.2;
      concerns.push({ side: 'LONG', weight: 0.4,
        note: 'This advance is short covering on falling open interest, not new buying — chasing it means buying the last of the fuel' });
    } else if (quadrant === 'LONG_LIQUIDATION') {
      read = 'BEARISH'; conviction = 0.2;
      evidence.push('Forced selling tends to overshoot, so the low it makes is often worth more as a reversal level than as a short entry.');
    }

    // ── 2. Crowding and squeeze fuel ──
    // Crowding is only fatal when BOTH the price of leverage and the crowd's
    // positioning say the same thing. Either alone is common enough that
    // treating it as fatal (the first cut did) vetoes most of the tape.
    const crowdedLong = (fundingZ != null && fundingZ > 1.4) && (lsZ != null && lsZ > 1.0);
    const crowdedShort = (fundingZ != null && fundingZ < -1.4) && (lsZ != null && lsZ < -1.0);
    const leaningLong = !crowdedLong && ((fundingZ != null && fundingZ > 1.2) || (lsZ != null && lsZ > 1.4));
    const leaningShort = !crowdedShort && ((fundingZ != null && fundingZ < -1.2) || (lsZ != null && lsZ < -1.4));

    if (funding != null) {
      const annualised = funding * 3 * 365 * 100;
      evidence.push(`Funding ${(funding * 100).toFixed(4)}% per period (~${annualised.toFixed(1)}% annualised)${fundingZ != null ? `, ${fundingZ.toFixed(1)} SD from its own recent norm` : ''} — ${funding > 0 ? 'longs are paying shorts to hold' : 'shorts are paying longs to hold'}`);
    }
    if (lsRatio != null) {
      evidence.push(`Crowd long/short account ratio ${lsRatio.toFixed(2)}${lsZ != null ? ` (${lsZ.toFixed(1)} SD)` : ''} — ${lsRatio > 1.5 ? 'retail is heavily long' : lsRatio < 0.9 ? 'retail is heavily short' : 'retail positioning is balanced'}`);
    }

    if (crowdedLong && priceChange6 <= 0.001) {
      read = 'BEARISH';
      conviction = Math.max(conviction, 0.7);
      evidence.push('SQUEEZE FUEL: longs are crowded and paying to stay, and price is not rewarding them. That combination resolves downward far more often than it resolves by grinding up.');
      vetoes.push('CROWDED_LONG: joining a crowded, well-paid long that price is refusing to reward is taking the worst side of a squeeze setup');
    }
    if (crowdedShort && priceChange6 >= -0.001) {
      read = 'BULLISH';
      conviction = Math.max(conviction, 0.7);
      evidence.push('SQUEEZE FUEL: shorts are crowded and paying, and price is not falling for them. This is the raw material of a short squeeze.');
      vetoes.push('CROWDED_SHORT: joining a crowded short that price is refusing to reward is standing in front of the squeeze');
    }

    if (leaningLong) {
      concerns.push({ side: 'LONG', weight: 0.35,
        note: 'Positioning is already leaning long — the marginal buyer is scarcer and the downside air pocket is larger' });
    }
    if (leaningShort) {
      concerns.push({ side: 'SHORT', weight: 0.35,
        note: 'Positioning is already leaning short — squeeze risk is elevated against a new short' });
    }

    // ── 3. Coiled leverage ──
    let coiled = false;
    if (oiChange24 != null && oiChange24 > 0.06 && Math.abs(priceChange6) < 0.004) {
      coiled = true;
      evidence.push(`COILED: open interest up ${(oiChange24 * 100).toFixed(1)}% over 24h while price went nowhere — leverage is stacking inside the range. The resolution is usually disproportionate because one side liquidates into the other's entry.`);
    }

    // ── 4. Measured aggression (real taker volume, not a bar-shape proxy) ──
    let takerRatio = null;
    const withTaker = derivs.slice(-6).filter(r => r.takerBuy != null && r.takerSell != null);
    if (withTaker.length >= 3) {
      const buy = withTaker.reduce((a, r) => a + r.takerBuy, 0);
      const sell = withTaker.reduce((a, r) => a + r.takerSell, 0);
      takerRatio = (buy + sell) > 0 ? buy / (buy + sell) : 0.5;
      evidence.push(`Measured taker aggression over 6h: ${(takerRatio * 100).toFixed(1)}% of notional lifted the offer${takerRatio > 0.55 ? ' — buyers are the aggressors' : takerRatio < 0.45 ? ' — sellers are the aggressors' : ''}`);
    }

    return {
      id: 'derivatives',
      name: 'Positioning & Derivatives',
      read, conviction,
      evidence, levels: [], vetoes, concerns,
      facts: {
        available: true, quadrant, coiled, leaningLong, leaningShort,
        oiChange6h: oiChange6 != null ? +(oiChange6 * 100).toFixed(2) : null,
        oiChange24h: oiChange24 != null ? +(oiChange24 * 100).toFixed(2) : null,
        oiZ: oiZ != null ? +oiZ.toFixed(2) : null,
        fundingRate: funding, fundingZ: fundingZ != null ? +fundingZ.toFixed(2) : null,
        longShortRatio: lsRatio, lsZ: lsZ != null ? +lsZ.toFixed(2) : null,
        crowdedLong, crowdedShort, takerRatio
      }
    };
  }

  return { analyse };
});
