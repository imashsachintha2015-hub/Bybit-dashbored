/**
 * Reconstruction of the ORIGINAL (V2) decision and exit logic, so the new
 * engine can be measured against what it replaced on identical data, identical
 * cost assumptions and an identical execution simulator.
 *
 * This is a faithful reconstruction of the shipped behaviour, not a strawman.
 * Specifically it reproduces:
 *   - a single 1-minute series, indicators computed including the forming bar;
 *   - the bullet-counting direction rule (longCase >= 3 && shortCase <= 1);
 *   - swing high/low taken as the rolling max/min of the last 20 bars,
 *     including the current bar, and break-of-structure tested against it;
 *   - the adversarial VETO checks;
 *   - stop = max(1.2 ATR, 0.3% of price), first target at 0.5 x (2.2 x stop),
 *     i.e. 1.1R, attached broker-side at full size — so the realised winner is
 *     capped at 1.1R while the loser is a full 1R;
 *   - the Position Guardian closing the whole position at market as soon as the
 *     live decision flips against it, regardless of P&L or how long the trade
 *     has been open.
 *
 * The one thing it cannot reproduce is the guardian's 3-second cadence: the
 * backtest can only re-evaluate once per bar. That makes this reconstruction
 * MORE forgiving than the real thing, since the live version had roughly twenty
 * chances per bar to find a flip and cut.
 */
const I = require('../agents/indicators.js');
const { barTakerRatio } = require('./flow-proxy.js');

function calcEmaV2(vals, p) {
  // Reproduces the original implementation, including seeding from vals[0].
  if (!vals.length) return 0;
  if (vals.length < p) return vals.reduce((a, b) => a + b, 0) / vals.length;
  const k = 2 / (p + 1);
  let ema = vals[0];
  for (let i = 1; i < vals.length; i++) ema = vals[i] * k + ema * (1 - k);
  return ema;
}

function calcAtrV2(candles, p = 14) {
  if (candles.length < 2) return candles.length ? candles[0].close * 0.003 : 0;
  const trs = [];
  for (let i = 1; i < candles.length; i++) {
    const c = candles[i], pr = candles[i - 1];
    trs.push(Math.max(c.high - c.low, Math.abs(c.high - pr.close), Math.abs(c.low - pr.close)));
  }
  const slice = trs.slice(-p);
  return slice.reduce((a, b) => a + b, 0) / slice.length;
}

function extractFeatures(candles) {
  const closes = candles.map(c => c.close);
  const highs = candles.map(c => c.high);
  const lows = candles.map(c => c.low);
  const lastPrice = closes[closes.length - 1];

  const ema9 = calcEmaV2(closes, 9);
  const ema21 = calcEmaV2(closes, 21);
  const ema50 = calcEmaV2(closes, Math.min(50, closes.length));
  const atr14 = calcAtrV2(candles, 14) || lastPrice * 0.003;
  const vwap = I.vwap(candles) || lastPrice;

  const swingHigh = Math.max(...highs.slice(-20));
  const swingLow = Math.min(...lows.slice(-20));
  const bos = lastPrice > swingHigh || lastPrice < swingLow;

  let choch = false;
  if (closes.length >= 6) {
    const recentHigh = Math.max(...highs.slice(-6, -1));
    const recentLow = Math.min(...lows.slice(-6, -1));
    if (lastPrice > recentHigh && closes[closes.length - 2] <= recentHigh) choch = true;
    else if (lastPrice < recentLow && closes[closes.length - 2] >= recentLow) choch = true;
  }

  // Delta stands in for the live tape's CVD, using the same bar-shape proxy the
  // new engine's backtest uses, so neither system gets an information advantage.
  let delta = 0;
  for (const c of candles.slice(-80)) delta += (barTakerRatio(c) - 0.5) * 2 * c.volume * c.close;

  const obImbalance = 0.5;   // no historical book; V2's default balanced read
  const takerRatio = barTakerRatio(candles[candles.length - 1]);

  return { lastPrice, ema9, ema21, ema50, atr14, vwap, swingHigh, swingLow, bos, choch, delta, obImbalance, takerRatio, spreadPct: 0.02 };
}

function analyseLiquidity(f, candles) {
  const curr = candles[candles.length - 1];
  if (curr.high > f.swingHigh && curr.close < f.swingHigh) return 'SWEEP_AND_REJECTION_BEARISH';
  if (curr.low < f.swingLow && curr.close > f.swingLow) return 'SWEEP_AND_REJECTION_BULLISH';
  if (curr.close > f.swingHigh && f.delta > 0) return 'BREAK_AND_ACCEPTANCE_BULLISH';
  if (curr.close < f.swingLow && f.delta < 0) return 'BREAK_AND_ACCEPTANCE_BEARISH';
  return 'IN_RANGE_AUCTION';
}

function analyseAbsorption(f, candles) {
  const last = candles[candles.length - 1];
  const body = Math.abs(last.close - last.open);
  if ((f.takerRatio < 0.35 || f.delta < -1000) && body < f.atr14 * 0.3 && f.obImbalance > 0.52) return 'BULLISH_PASSIVE_ABSORPTION';
  if ((f.takerRatio > 0.65 || f.delta > 1000) && body < f.atr14 * 0.3 && f.obImbalance < 0.48) return 'BEARISH_PASSIVE_ABSORPTION';
  return 'NONE';
}

function prosecute(dir, f, absorption, liquidity) {
  let verdict = 'PASS';
  if (dir === 'LONG') {
    if (absorption === 'BEARISH_PASSIVE_ABSORPTION') verdict = 'VETO';
    if (liquidity === 'SWEEP_AND_REJECTION_BEARISH') verdict = 'VETO';
  } else {
    if (absorption === 'BULLISH_PASSIVE_ABSORPTION') verdict = 'VETO';
    if (liquidity === 'SWEEP_AND_REJECTION_BULLISH') verdict = 'VETO';
  }
  if (f.spreadPct > 0.2) verdict = 'VETO';
  return verdict;
}

/** Returns { decision, stopLoss, takeProfit, riskDist } matching V2's shape. */
function decide(candles) {
  if (candles.length < 5) return { decision: 'WAIT' };
  const f = extractFeatures(candles);
  const liquidity = analyseLiquidity(f, candles);
  const absorption = analyseAbsorption(f, candles);
  const candidateDir = f.lastPrice >= f.vwap ? 'LONG' : 'SHORT';
  const verdict = prosecute(candidateDir, f, absorption, liquidity);

  const longCase = [], shortCase = [];
  if (f.lastPrice > f.vwap) longCase.push(1);
  if (f.ema9 > f.ema21) longCase.push(1);
  if (f.delta > 0) longCase.push(1);
  if (f.obImbalance > 0.53) longCase.push(1);
  if (liquidity === 'SWEEP_AND_REJECTION_BULLISH') longCase.push(1);
  if (absorption === 'BULLISH_PASSIVE_ABSORPTION') longCase.push(1);

  if (f.lastPrice < f.vwap) shortCase.push(1);
  if (f.ema9 < f.ema21) shortCase.push(1);
  if (f.delta < 0) shortCase.push(1);
  if (f.obImbalance < 0.47) shortCase.push(1);
  if (liquidity === 'SWEEP_AND_REJECTION_BEARISH') shortCase.push(1);
  if (absorption === 'BEARISH_PASSIVE_ABSORPTION') shortCase.push(1);

  let dominant = 'NO_TRADE';
  if (verdict !== 'VETO') {
    if (longCase.length >= 3 && shortCase.length <= 1) dominant = 'LONG';
    else if (shortCase.length >= 3 && longCase.length <= 1) dominant = 'SHORT';
  }
  if (dominant === 'NO_TRADE') return { decision: 'WAIT' };

  const price = f.lastPrice;
  const stopDist = Math.max(f.atr14 * 1.2, price * 0.003);
  const targetDist = stopDist * 2.2;
  const decision = dominant === 'LONG' ? 'BUY' : 'SELL';
  const stopLoss = decision === 'BUY' ? price - stopDist : price + stopDist;
  // V2 attached only takeProfit[0] broker-side, at FULL size: the realised
  // winner is therefore 1.1R against a 1.0R loser.
  const tp1 = decision === 'BUY' ? price + targetDist * 0.5 : price - targetDist * 0.5;

  return {
    decision, direction: dominant,
    entry: price, stopLoss, takeProfit: [tp1],
    riskDist: stopDist,
    // V2's trust score was assembled from four constants and always landed here.
    trustScore: Math.round(85 * 0.35 + 80 * 0.25 + 85 * 0.25 + 70 * 0.15) + 8
  };
}

module.exports = { decide };
