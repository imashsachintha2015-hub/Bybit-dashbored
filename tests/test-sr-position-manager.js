const assert = require('assert');
const { PositionManager, EXIT } = require('../agents/position-manager.js');
const Playbooks = require('../agents/playbooks.js');

console.log('=== RUNNING S/R & PROFIT COVERAGE TESTS ===\n');

// ── Test 1: Milestone Break-Even Ratchet (+12% ROI / +1.0R) ──
{
  const pm = new PositionManager({ gracePeriodMs: 0 });
  const trade = pm.open({
    symbol: 'ETHUSDT',
    side: 'Buy',
    entryPrice: 2000,
    stopLoss: 1980, // riskDist = 20
    targets: [2040, 2070],
    riskDist: 20,
    qty: 1.0,
    originalQty: 1.0,
    leverage: 10
  });

  // Small move: +0.5R (price = 2010, ROI = +5%) -> Should HOLD
  let res = pm.evaluate(trade, { price: 2010, atr: 10 });
  assert.strictEqual(res.action, 'HOLD', 'Should hold when under break-even milestone');

  // Milestone move: +1.0R (price = 2020, ROI = +10%) -> Should trigger MOVE_STOP to break-even
  res = pm.evaluate(trade, { price: 2020, atr: 10 });
  assert.strictEqual(res.action, 'MOVE_STOP', 'Should move stop when reaching +1.0R');
  assert.strictEqual(res.newStop, 2000, 'New stop should be entry price');
  assert.strictEqual(res.reason, 'BREAK_EVEN', 'Reason should be BREAK_EVEN');
  console.log('✔ Test 1: +1.0R / ROI Break-Even ratchet passed.');
}

// ── Test 2: S/R Wall Collision Profit Coverage ──
{
  const pm = new PositionManager({ gracePeriodMs: 0 });
  const trade = pm.open({
    symbol: 'LINKUSDT',
    side: 'Buy',
    entryPrice: 10.0,
    stopLoss: 9.7, // riskDist = 0.3
    targets: [10.6, 11.0], // TP1 is at 10.6
    riskDist: 0.3,
    qty: 100,
    originalQty: 100,
    leverage: 10
  });

  // Price moves to 10.28 (below TP1 at 10.6, but resistance sits at 10.30, atr = 0.20)
  // Distance to resistance = 10.30 - 10.28 = 0.02 <= 0.20 * 0.20 (0.04)
  // r = (10.28 - 10.0) / 0.3 = 0.93R >= 0.70R
  const res = pm.evaluate(trade, {
    price: 10.28,
    atr: 0.20,
    nextResistance: 10.30
  });

  assert.strictEqual(res.action, 'SCALE_OUT', 'Should scale out on S/R resistance test');
  assert.strictEqual(res.reason, EXIT.SR_COLLISION, 'Reason should be SR_COLLISION');
  assert.strictEqual(res.fraction, 0.50, 'Should bank 50% profit coverage');
  console.log('✔ Test 2: S/R Resistance collision profit coverage passed.');
}

// ── Test 3: Playbooks S/R Anchored Target Geometry ──
{
  // Test candidate risk geometry when resistance sits at 1.4R
  const entry = 100;
  const invalidation = 96;
  const atr = 2.0;
  // stop = 96 - (2.0 * 1.0) = 94. riskDist = 100 - 94 = 6.0
  const structure = {
    nextResistance: 108.0, // distance = 8.0, headroomR = 8.0 / 6.0 = 1.33R
    nextSupport: 90.0
  };

  // Previously, headroomR = 1.33R would have been REJECTED with "need at least 2.0R".
  // Now, it should anchor TP1 just shy of resistance (108 - 0.15 * 2.0 = 107.7) and be VIABLE!
  const geom = Playbooks.buildRiskGeometry('LONG', entry, invalidation, atr, structure);
  assert(geom !== null, 'Geometry should not be null');
  assert.strictEqual(geom.viable, true, 'Setup with 1.33R headroom to resistance should be viable');
  assert(geom.targets[0] < 108.0, 'TP1 should sit below resistance wall');
  assert.strictEqual(geom.cappedByStructure, true, 'cappedByStructure should be true');
  console.log(`✔ Test 3: Playbook anchored TP1 to ${geom.targets[0]} (headroom ${geom.headroomR}R), viable = ${geom.viable}.`);
}

console.log('\nALL S/R & PROFIT COVERAGE TESTS PASSED SUCCESSFULLY! 🎉');
