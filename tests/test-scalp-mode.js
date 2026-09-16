const assert = require('assert');
const { PositionManager, DEFAULTS, SCALP_DEFAULTS, EXIT } = require('../agents/position-manager.js');
const Playbooks = require('../agents/playbooks.js');
const { MasisEngine } = require('../masis-engine.js');
const I = require('../agents/indicators.js');

console.log('=== RUNNING SCALPING MODE TEST SUITE (5m–10m Targets) ===\n');

// ── Test 1: Scalp Risk Geometry ──
{
  const structure = { nextResistance: 2015, nextSupport: 1985, swingHighs: [], swingLows: [] };
  const entry = 2000;
  const invalidation = 1996; // tight 5m swing low
  const atrLtf = 4.0; // 5m ATR

  const geom = Playbooks.buildScalpRiskGeometry('LONG', entry, invalidation, atrLtf, structure);
  assert(geom, 'Scalp risk geometry should build successfully');
  assert.strictEqual(geom.viable, true, 'Scalp geometry should be viable');
  assert.strictEqual(geom.stop, 1994, 'Stop should be invalidation (1996) - 0.5 * ATR (2.0) = 1994');
  assert.strictEqual(geom.riskDist, 6, 'Risk dist should be 2000 - 1994 = 6');
  assert(geom.targets.length >= 2, 'Should provide at least 2 scalp targets');
  assert(geom.rrToTp1 >= 0.5 && geom.rrToTp1 <= 1.0, `TP1 RR should be in 0.5R–1.0R range, got ${geom.rrToTp1}R`);
  console.log(`✔ Test 1: Scalp Risk Geometry passed (Entry: ${geom.entry}, SL: ${geom.stop} [0.5x ATR buffer], TP1: ${geom.targets[0]}, TP2: ${geom.targets[1]}).`);
}

// ── Test 2: Scalp Playbooks (Burst, Fade, Orderflow) ──
{
  // Build synthetic 5m candles
  const ltf = [];
  const basePrice = 100;
  for (let i = 0; i < 25; i++) {
    ltf.push({
      start: 1700000000000 + i * 300000,
      open: basePrice + i * 0.1,
      high: basePrice + i * 0.1 + 0.3,
      low: basePrice + i * 0.1 - 0.2,
      close: basePrice + i * 0.1 + 0.1,
      volume: 1000,
      confirm: true
    });
  }
  // Add a breakout burst candle at the end
  const lastOpen = 102.5;
  const lastClose = 104.0; // strong +1.5 body
  ltf.push({
    start: 1700000000000 + 25 * 300000,
    open: lastOpen,
    high: 104.1, // closes within top 10%
    low: 102.4,
    close: lastClose,
    volume: 3500, // 3.5x volume burst
    confirm: true
  });

  const ctx = {
    ltf,
    mtf: ltf,
    htf: ltf,
    price: lastClose,
    symbol: 'SOLUSDT',
    atrLtf: 0.8,
    flow: {
      available: true,
      flow5m: { ratio: 0.75 },
      book: { available: true, spreadPct: 0.0002, imbalance: 0.4 },
      spread: 0.02
    },
    scalpMode: true
  };

  const candidates = Playbooks.evaluateScalp(ctx);
  assert(candidates.length > 0, 'evaluateScalp should find at least one scalp candidate');
  const burst = candidates.find(c => c.name === 'SCALP_MOMENTUM_BURST');
  assert(burst, 'Should identify SCALP_MOMENTUM_BURST setup');
  assert.strictEqual(burst.direction, 'LONG');
  assert.strictEqual(burst.isScalp, true, 'Candidate must have isScalp: true');
  assert.strictEqual(burst.horizon, '5m-10m', 'Horizon should be 5m-10m');
  console.log(`✔ Test 2: Scalp Playbook evaluation passed (${burst.name} grade ${burst.grade}, score ${burst.score}, horizon ${burst.horizon}).`);
}

// ── Test 3: Scalp Position Management (Fast BE Ratchet & 60% Bank) ──
{
  const now = Date.now();
  let fakeTime = now;
  const pm = new PositionManager({ now: () => fakeTime });

  const trade = pm.open({
    symbol: 'BTCUSDT',
    side: 'Buy',
    entryPrice: 60000,
    stopLoss: 59700, // riskDist = 300 (0.5%)
    targets: [60200, 60400], // TP1 @ +0.67R, TP2 @ +1.33R
    riskDist: 300,
    qty: 0.1,
    originalQty: 0.1,
    leverage: 10,
    isScalp: true // tagged as scalp
  });

  // Fast forward past 60s scalp grace period (70 seconds)
  fakeTime += 70 * 1000;

  // Move 1: +0.45R gain (price = 60135, ROI = +2.25% at price, +22.5% at 10x)
  // Scalp threshold is +0.40R -> Must immediately ratchet stop to BREAK-EVEN!
  let res = pm.evaluate(trade, { price: 60135, atr: 150 });
  assert.strictEqual(res.action, 'MOVE_STOP', 'Scalp should ratchet stop to BE at +0.45R');
  assert.strictEqual(res.newStop, 60000, 'New stop should be entry price 60000');
  pm.markStopMoved(trade, res.newStop, res.reason);

  // Move 2: Hits TP1 @ 60200 (+0.67R)
  res = pm.evaluate(trade, { price: 60200, atr: 150 });
  assert.strictEqual(res.action, 'SCALE_OUT', 'Should trigger scale-out at TP1');
  assert.strictEqual(res.tpIndex, 0);
  assert.strictEqual(res.fraction, 0.60, 'Scalp must bank 60% at TP1 (not standard 25%)');
  pm.markTpFilled(trade, 0, 60200);

  // Move 3: Check remaining fraction after 60% bank
  assert.strictEqual(+trade.remainingFraction.toFixed(2), 0.40, 'Remaining runner should be 40%');
  console.log('✔ Test 3: Scalp Position Management passed (rapid BE ratchet @ +0.4R, 60% profit banked at TP1).');
}

// ── Test 4: Scalp Time Stop (3 Bars / 12-Minute Max Hold) ──
{
  const now = Date.now();
  let fakeTime = now;
  const pm = new PositionManager({ now: () => fakeTime });

  const trade = pm.open({
    symbol: 'DOGEUSDT',
    side: 'Buy',
    entryPrice: 0.10,
    stopLoss: 0.098,
    targets: [0.102, 0.104],
    riskDist: 0.002,
    qty: 1000,
    originalQty: 1000,
    leverage: 10,
    isScalp: true
  });

  // Fast forward 13 minutes (dead scalp that went nowhere, price 0.1001, +0.05R)
  fakeTime += 13 * 60 * 1000;
  const res = pm.evaluate(trade, { price: 0.1001, atr: 0.001 });
  assert.strictEqual(res.action, 'CLOSE_ALL', 'Stalled scalp should trigger TIME_STOP after 12m limit');
  assert.strictEqual(res.reason, EXIT.TIME_STOP, 'Reason should be TIME_STOP');
  console.log('✔ Test 4: Scalp 12-minute time stop passed (freed margin from dead trade).');
}

// ── Test 5: MasisEngine Scalp Mode Integration ──
{
  const engine = new MasisEngine({ symbol: 'ETHUSDT', scalpMode: true, swarmMode: 'off' });
  assert.strictEqual(engine.scalpMode, true, 'Engine should initialize in scalpMode: true');
  engine.setScalpMode(false);
  assert.strictEqual(engine.scalpMode, false, 'setScalpMode(false) should work');
  engine.setScalpMode(true);
  assert.strictEqual(engine.scalpMode, true, 'setScalpMode(true) should work');
  console.log('✔ Test 5: MasisEngine Scalp Mode toggle and configuration passed.');
}

console.log('\nALL SCALPING MODE TESTS PASSED PERFECTLY! ⚡🎯');
