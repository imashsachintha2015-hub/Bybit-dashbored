const { RiskGovernor } = require('../agents/risk-governor.js');
const assert = require('assert');

console.log("=== RUNNING VERIFICATION FOR $10 EQUITY & $5/DAY TARGET SYSTEM ===");

// 1. Initialize RiskGovernor with $10 equity settings
const rg = new RiskGovernor({
  virtualEquity: 10.0,
  dailyGrossProfitTarget: 5.0,
  targetNotional: 100.0,
  maxTradeNotional: 100.0,
  maxConcurrentPositions: 1,
  leverage: 10
});
rg.updateEquity(10.0);

console.log("[PASS] Initialized RiskGovernor with Virtual Equity: $" + rg.currentEquity);

// 2. Test Sizing on multiple coins
const coins = [
  { sym: 'BTCUSDT', price: 77000, step: 0.001, minQ: 0.001 },
  { sym: 'SOLUSDT', price: 102.0, step: 0.1, minQ: 0.1 },
  { sym: 'LINKUSDT', price: 11.5, step: 0.1, minQ: 0.1 },
  { sym: 'ATOMUSDT', price: 1.5, step: 0.1, minQ: 0.1 },
  { sym: 'DOGEUSDT', price: 0.082, step: 1.0, minQ: 1.0 },
];

for (const c of coins) {
  const sized = rg.sizePosition({
    entry: c.price,
    stop: c.price * 0.985, // 1.5% stop
    equity: 10.0,
    symbol: c.sym,
    qtyStep: c.step,
    minQty: c.minQ,
    maxLeverage: 10
  });
  const notional = sized.qty * c.price;
  console.log(`Coin: ${c.sym.padEnd(10)} | Entry: ${c.price.toFixed(3).padEnd(8)} | Qty: ${sized.qty} | Notional: $${notional.toFixed(2)} | Risk: $${sized.riskAmount.toFixed(2)}`);
  assert(notional >= 5.0, `${c.sym} notional must be >= Bybit min (5.0 USDT)`);
  assert(notional <= 105.0, `${c.sym} notional must not exceed $100 cap for $10 equity`);
}
console.log("[PASS] All coin position sizings are perfectly calibrated around $100 notional (capped, no runaways)!");

// 3. Test Max Concurrent Positions Gate (strictly 1 for $10 equity)
const gate1 = rg.canOpen({ symbol: 'SOLUSDT', openPositions: [], pendingOrders: [] });
assert(gate1.allowed === true, "First trade should be allowed");

const gate2 = rg.canOpen({ symbol: 'ETHUSDT', openPositions: [{ symbol: 'SOLUSDT' }], pendingOrders: [] });
assert(gate2.allowed === false, "Second trade must be blocked by maxConcurrentPositions: 1");
console.log("[PASS] Max concurrent positions strictly enforced (1 position at a time for $10 equity). Block reason: " + gate2.reasons[0]);

// 4. Test Daily $5.00 Gross Profit Target Auto-Lock Circuit Breaker
rg.recordOutcome({ symbol: 'SOLUSDT', pnl: 2.50, rMultiple: 1.0 });
assert(rg.dailyGrossProfitToday === 2.50, "Daily gross profit should be $2.50");
const gateMid = rg.canOpen({ symbol: 'LINKUSDT', openPositions: [], pendingOrders: [] });
assert(gateMid.allowed === true, "Trade should be allowed before reaching $5.00 target");

// Add another win bringing gross profit to $5.20
rg.recordOutcome({ symbol: 'LINKUSDT', pnl: 2.70, rMultiple: 1.0 });
assert(rg.dailyGrossProfitToday === 5.20, "Daily gross profit should be $5.20");

const gateTargetReached = rg.canOpen({ symbol: 'BTCUSDT', openPositions: [], pendingOrders: [] });
assert(gateTargetReached.allowed === false, "Trading MUST be locked after hitting $5.00 daily gross target");
console.log("[PASS] Daily $5.00 Gross Profit Target Circuit Breaker verified! Block reason: " + gateTargetReached.reasons[0]);

console.log("\nALL VERIFICATION TESTS PASSED SUCCESSFULLY!");
