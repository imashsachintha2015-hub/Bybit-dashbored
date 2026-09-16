/**
 * Shared indicator math — pure functions, no state, no DOM.
 * Loaded both in the browser (window.MasisIndicators) and in Node by the
 * backtest harness, so the numbers the backtest reports are produced by exactly
 * the same code path the live engine uses.
 *
 * Convention used everywhere below: `candles` are chronological and every
 * candle passed in is CONFIRMED (closed). The forming candle is deliberately
 * excluded by the caller — an indicator computed on a half-formed bar flickers
 * and was the single largest source of false signals in the previous engine.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.MasisIndicators = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {

  /** Standard EMA series. Seeded with an SMA of the first `period` values so the
   * head of the series isn't dominated by whatever the first tick happened to be. */
  function emaSeries(values, period) {
    if (!values.length) return [];
    if (values.length < period) {
      const mean = values.reduce((a, b) => a + b, 0) / values.length;
      return values.map(() => mean);
    }
    const k = 2 / (period + 1);
    const out = new Array(values.length);
    let seed = 0;
    for (let i = 0; i < period; i++) seed += values[i];
    seed /= period;
    for (let i = 0; i < period; i++) out[i] = seed;
    let prev = seed;
    for (let i = period; i < values.length; i++) {
      prev = values[i] * k + prev * (1 - k);
      out[i] = prev;
    }
    return out;
  }

  function ema(values, period) {
    const s = emaSeries(values, period);
    return s.length ? s[s.length - 1] : 0;
  }

  function sma(values, period) {
    if (!values.length) return 0;
    const slice = values.slice(-period);
    return slice.reduce((a, b) => a + b, 0) / slice.length;
  }

  function stdev(values) {
    if (values.length < 2) return 0;
    const mean = values.reduce((a, b) => a + b, 0) / values.length;
    const variance = values.reduce((a, b) => a + (b - mean) * (b - mean), 0) / (values.length - 1);
    return Math.sqrt(variance);
  }

  function trueRanges(candles) {
    const trs = [];
    for (let i = 1; i < candles.length; i++) {
      const c = candles[i], p = candles[i - 1];
      trs.push(Math.max(c.high - c.low, Math.abs(c.high - p.close), Math.abs(c.low - p.close)));
    }
    return trs;
  }

  /** Wilder's ATR (smoothed), not the flat mean the previous engine used.
   * A flat mean over-reacts to a single spike bar and then under-reacts for 14
   * bars afterwards, which mis-sizes every stop placed off it. */
  function atr(candles, period = 14) {
    const trs = trueRanges(candles);
    if (!trs.length) return 0;
    if (trs.length < period) return trs.reduce((a, b) => a + b, 0) / trs.length;
    let val = trs.slice(0, period).reduce((a, b) => a + b, 0) / period;
    for (let i = period; i < trs.length; i++) val = (val * (period - 1) + trs[i]) / period;
    return val;
  }

  /** ATR as a fraction of price — the comparable-across-symbols volatility unit.
   * BTC at 0.05% ATR and DOGE at 0.4% ATR need completely different stop distances. */
  function atrPct(candles, period = 14) {
    const a = atr(candles, period);
    const last = candles[candles.length - 1];
    return last && last.close ? a / last.close : 0;
  }

  /** Rolling VWAP over the supplied window (anchored VWAP if the caller slices
   * from a session/swing origin). */
  function vwap(candles) {
    let cumVP = 0, cumV = 0;
    for (const c of candles) {
      const typical = (c.high + c.low + c.close) / 3;
      cumVP += typical * c.volume;
      cumV += c.volume;
    }
    if (cumV > 0) return cumVP / cumV;
    return candles.length ? candles[candles.length - 1].close : 0;
  }

  /** Kaufman Efficiency Ratio: net directional travel / total path travelled.
   * ~1.0 = clean one-way trend, ~0.0 = pure chop. This is the primary regime
   * discriminator — trend-following setups taken in a low-ER tape are the
   * classic way an otherwise sound strategy bleeds out. */
  function efficiencyRatio(closes, period = 20) {
    if (closes.length < period + 1) return 0;
    const slice = closes.slice(-(period + 1));
    const net = Math.abs(slice[slice.length - 1] - slice[0]);
    let path = 0;
    for (let i = 1; i < slice.length; i++) path += Math.abs(slice[i] - slice[i - 1]);
    return path > 0 ? net / path : 0;
  }

  /** Wilder ADX — trend *strength* (direction-agnostic), paired with ER so a
   * strong move isn't confused with a wide-but-directionless one. */
  function adx(candles, period = 14) {
    if (candles.length < period * 2 + 1) return 0;
    const plusDM = [], minusDM = [], trs = [];
    for (let i = 1; i < candles.length; i++) {
      const c = candles[i], p = candles[i - 1];
      const up = c.high - p.high;
      const down = p.low - c.low;
      plusDM.push(up > down && up > 0 ? up : 0);
      minusDM.push(down > up && down > 0 ? down : 0);
      trs.push(Math.max(c.high - c.low, Math.abs(c.high - p.close), Math.abs(c.low - p.close)));
    }
    const wilder = (arr) => {
      let v = arr.slice(0, period).reduce((a, b) => a + b, 0);
      const out = [v];
      for (let i = period; i < arr.length; i++) {
        v = v - v / period + arr[i];
        out.push(v);
      }
      return out;
    };
    const trS = wilder(trs), pS = wilder(plusDM), mS = wilder(minusDM);
    const dx = [];
    for (let i = 0; i < trS.length; i++) {
      if (!trS[i]) { dx.push(0); continue; }
      const pDI = 100 * pS[i] / trS[i];
      const mDI = 100 * mS[i] / trS[i];
      const sum = pDI + mDI;
      dx.push(sum > 0 ? 100 * Math.abs(pDI - mDI) / sum : 0);
    }
    if (dx.length < period) return dx.length ? dx[dx.length - 1] : 0;
    let a = dx.slice(0, period).reduce((x, y) => x + y, 0) / period;
    for (let i = period; i < dx.length; i++) a = (a * (period - 1) + dx[i]) / period;
    return a;
  }

  function rsi(closes, period = 14) {
    if (closes.length < period + 1) return 50;
    let gain = 0, loss = 0;
    for (let i = 1; i <= period; i++) {
      const d = closes[i] - closes[i - 1];
      if (d >= 0) gain += d; else loss -= d;
    }
    let avgGain = gain / period, avgLoss = loss / period;
    for (let i = period + 1; i < closes.length; i++) {
      const d = closes[i] - closes[i - 1];
      avgGain = (avgGain * (period - 1) + (d > 0 ? d : 0)) / period;
      avgLoss = (avgLoss * (period - 1) + (d < 0 ? -d : 0)) / period;
    }
    if (avgLoss === 0) return 100;
    return 100 - 100 / (1 + avgGain / avgLoss);
  }

  /** Normalised slope of a least-squares fit, expressed in ATR units per bar,
   * so "how hard is this trending" is comparable across symbols and timeframes. */
  function slopeInAtr(closes, atrValue, period = 20) {
    if (closes.length < period || !atrValue) return 0;
    const y = closes.slice(-period);
    const n = y.length;
    let sumX = 0, sumY = 0, sumXY = 0, sumXX = 0;
    for (let i = 0; i < n; i++) {
      sumX += i; sumY += y[i]; sumXY += i * y[i]; sumXX += i * i;
    }
    const denom = n * sumXX - sumX * sumX;
    if (!denom) return 0;
    return ((n * sumXY - sumX * sumY) / denom) / atrValue;
  }

  /** Fractal swing points: a pivot needs `lookback` lower highs on each side.
   * Structure derived from real pivots — not from `Math.max` over a rolling
   * window that includes the bar currently being tested, which is what made the
   * old break-of-structure check fire on essentially every new high tick. */
  function swingPoints(candles, lookback = 2) {
    const highs = [], lows = [];
    for (let i = lookback; i < candles.length - lookback; i++) {
      let isHigh = true, isLow = true;
      for (let j = i - lookback; j <= i + lookback; j++) {
        if (j === i) continue;
        if (candles[j].high >= candles[i].high) isHigh = false;
        if (candles[j].low <= candles[i].low) isLow = false;
      }
      if (isHigh) highs.push({ index: i, price: candles[i].high, time: candles[i].start });
      if (isLow) lows.push({ index: i, price: candles[i].low, time: candles[i].start });
    }
    return { highs, lows };
  }

  /** Market structure from confirmed pivots: higher-high/higher-low sequencing,
   * plus the most recent *unbroken* pivot levels that act as real invalidation. */
  function marketStructure(candles, lookback = 2) {
    const { highs, lows } = swingPoints(candles, lookback);
    const lastHighs = highs.slice(-3);
    const lastLows = lows.slice(-3);

    let structure = 'UNDEFINED';
    if (lastHighs.length >= 2 && lastLows.length >= 2) {
      const hh = lastHighs[lastHighs.length - 1].price > lastHighs[lastHighs.length - 2].price;
      const hl = lastLows[lastLows.length - 1].price > lastLows[lastLows.length - 2].price;
      const lh = lastHighs[lastHighs.length - 1].price < lastHighs[lastHighs.length - 2].price;
      const ll = lastLows[lastLows.length - 1].price < lastLows[lastLows.length - 2].price;
      if (hh && hl) structure = 'UPTREND';
      else if (lh && ll) structure = 'DOWNTREND';
      else structure = 'RANGE';
    }

    return {
      structure,
      swingHighs: highs,
      swingLows: lows,
      lastSwingHigh: highs.length ? highs[highs.length - 1] : null,
      lastSwingLow: lows.length ? lows[lows.length - 1] : null,
      priorSwingHigh: highs.length > 1 ? highs[highs.length - 2] : null,
      priorSwingLow: lows.length > 1 ? lows[lows.length - 2] : null
    };
  }

  /** Percentile rank of `value` within `arr` (0..1). Used to ask "is current
   * volatility/volume unusual *for this symbol*" rather than against a constant. */
  function percentileRank(arr, value) {
    if (!arr.length) return 0.5;
    let below = 0;
    for (const v of arr) if (v <= value) below++;
    return below / arr.length;
  }

  /** Full RSI series, needed for divergence analysis and momentum exit. */
  function rsiSeries(closes, period = 14) {
    if (closes.length < period + 1) return [];
    let gain = 0, loss = 0;
    for (let i = 1; i <= period; i++) {
      const d = closes[i] - closes[i - 1];
      if (d >= 0) gain += d; else loss -= d;
    }
    let avgGain = gain / period, avgLoss = loss / period;
    const out = new Array(period).fill(50);
    out.push(avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss));
    for (let i = period + 1; i < closes.length; i++) {
      const d = closes[i] - closes[i - 1];
      avgGain = (avgGain * (period - 1) + (d > 0 ? d : 0)) / period;
      avgLoss = (avgLoss * (period - 1) + (d < 0 ? -d : 0)) / period;
      out.push(avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss));
    }
    return out;
  }

  /**
   * RSI Divergence Scanner.
   *
   * Regular divergence: price makes a new extreme, RSI does not → reversal.
   * Hidden divergence: RSI makes a new extreme, price does not → continuation.
   *
   * Both types use only confirmed swing points, never the forming bar, so the
   * detection is stable and does not flicker.
   *
   * @returns {{ type: 'REGULAR'|'HIDDEN', side: 'BULLISH'|'BEARISH',
   *            priceSwings: [{index, price}], rsiSwings: [{index, value}] } | null }
   */
  function rsiDivergence(candles, period = 14, lookback = 2) {
    if (candles.length < period + lookback * 3 + 5) return null;
    const closes = candles.map(c => c.close);
    const rs = rsiSeries(closes, period);
    if (rs.length < candles.length) return null;

    const { highs, lows } = swingPoints(candles, lookback);
    if (highs.length < 2 && lows.length < 2) return null;

    // --- Bearish divergence: price highs ---
    if (highs.length >= 2) {
      const h1 = highs[highs.length - 2], h2 = highs[highs.length - 1];
      const r1 = rs[h1.index], r2 = rs[h2.index];
      // Regular bearish: price higher high, RSI lower high → reversal
      if (h2.price > h1.price && r2 < r1 - 1.5) {
        return { type: 'REGULAR', side: 'BEARISH',
          priceSwings: [h1, h2], rsiSwings: [{ index: h1.index, value: r1 }, { index: h2.index, value: r2 }] };
      }
      // Hidden bearish: price lower high, RSI higher high → continuation down
      if (h2.price < h1.price && r2 > r1 + 1.5) {
        return { type: 'HIDDEN', side: 'BEARISH',
          priceSwings: [h1, h2], rsiSwings: [{ index: h1.index, value: r1 }, { index: h2.index, value: r2 }] };
      }
    }

    // --- Bullish divergence: price lows ---
    if (lows.length >= 2) {
      const l1 = lows[lows.length - 2], l2 = lows[lows.length - 1];
      const r1 = rs[l1.index], r2 = rs[l2.index];
      // Regular bullish: price lower low, RSI higher low → reversal
      if (l2.price < l1.price && r2 > r1 + 1.5) {
        return { type: 'REGULAR', side: 'BULLISH',
          priceSwings: [l1, l2], rsiSwings: [{ index: l1.index, value: r1 }, { index: l2.index, value: r2 }] };
      }
      // Hidden bullish: price higher low, RSI lower low → continuation up
      if (l2.price > l1.price && r2 < r1 - 1.5) {
        return { type: 'HIDDEN', side: 'BULLISH',
          priceSwings: [l1, l2], rsiSwings: [{ index: l1.index, value: r1 }, { index: l2.index, value: r2 }] };
      }
    }

    return null;
  }

  /**
   * Fibonacci retracement levels between a swing high and swing low.
   * Returns levels as { ratio, price } objects.
   */
  function fibLevels(swingHigh, swingLow) {
    const range = swingHigh - swingLow;
    if (range <= 0) return [];
    const RATIOS = [0.236, 0.382, 0.5, 0.618, 0.786];
    return RATIOS.map(r => ({
      ratio: r,
      // Retracement from the high: price = high - range * ratio
      priceFromHigh: +(swingHigh - range * r).toFixed(8),
      // Retracement from the low: price = low + range * ratio
      priceFromLow: +(swingLow + range * r).toFixed(8)
    }));
  }

  /**
   * Fibonacci extension levels from a swing (A→B→C pattern).
   * Used for profit targets on continuation moves.
   */
  function fibExtensions(swingA, swingB, swingC) {
    const leg = Math.abs(swingB - swingA);
    if (leg <= 0) return [];
    const RATIOS = [1.0, 1.272, 1.618, 2.0, 2.618];
    const isUp = swingB > swingA;
    return RATIOS.map(r => ({
      ratio: r,
      price: +(isUp ? swingC + leg * r : swingC - leg * r).toFixed(8)
    }));
  }

  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

  return {
    emaSeries, ema, sma, stdev, trueRanges, atr, atrPct, vwap,
    efficiencyRatio, adx, rsi, rsiSeries, rsiDivergence, fibLevels, fibExtensions,
    slopeInAtr, swingPoints, marketStructure,
    percentileRank, clamp
  };
});
