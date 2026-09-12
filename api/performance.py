import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_lib.bybit_client import get_client
from backend_lib.http_utils import JsonApiHandler
from backend_lib import trade_stats as trade_stats_mod


class handler(JsonApiHandler):
    def do_GET(self):
        client, err = get_client()
        if err:
            self._send_json(500, {"retCode": -1, "retMsg": err})
            return

        # Bybit's closed-PnL feed is the single source of truth for money.
        # The local records supply only the things Bybit cannot know: which
        # setup produced the trade and why it was exited. They are matched to
        # Bybit's rows by symbol, side and time rather than added to them --
        # doing both used to double-count every trade.
        bybit_pnl = client.get_closed_pnl(limit=100)
        closed_list = bybit_pnl.get("result", {}).get("list", []) or []

        stats = trade_stats_mod.load()
        local_history = list(stats.get("trade_history", []))

        def annotate(row):
            """Attaches the local reasoning snapshot to a Bybit closed-PnL row."""
            try:
                ts = int(row.get("updatedTime") or row.get("createdTime") or 0)
            except (TypeError, ValueError):
                ts = 0
            for loc in local_history:
                if loc.get("symbol") != row.get("symbol"):
                    continue
                if str(loc.get("side", "")).upper() != str(row.get("side", "")).upper():
                    continue
                if abs(int(loc.get("recorded_at", 0)) - ts) < 120000:
                    return loc
            return {}

        w_count = l_count = 0
        g_profit = g_loss = 0.0
        merged = []
        for row in closed_list:
            try:
                pnl = float(row.get("closedPnl", 0))
            except (TypeError, ValueError):
                continue
            if pnl > 0:
                w_count += 1
                g_profit += pnl
            elif pnl < 0:
                l_count += 1
                g_loss += abs(pnl)
            extra = annotate(row)
            merged.append({
                "id": row.get("orderId", "")[-8:] or "--",
                "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(
                    int(row.get("updatedTime") or row.get("createdTime") or 0) / 1000)),
                "symbol": row.get("symbol"),
                "side": str(row.get("side", "")).upper(),
                "entry": float(row.get("avgEntryPrice") or 0),
                "exit": float(row.get("avgExitPrice") or 0),
                "pnl": round(pnl, 4),
                "pnl_pct": round(float(row.get("closedPnl", 0)) / max(float(row.get("cumEntryValue") or 1), 1e-9) * 100, 3),
                "status": "WIN" if pnl > 0 else "LOSS",
                "setup_type": extra.get("setup_type", ""),
                "grade": extra.get("grade", ""),
                "r_multiple": extra.get("r_multiple"),
                "exit_reason": extra.get("exit_reason", ""),
                "reason": extra.get("reason", "")
            })

        tot_trades = w_count + l_count
        win_rate = round((w_count / tot_trades) * 100, 1) if tot_trades > 0 else 0.0
        profit_factor = round(g_profit / g_loss, 2) if g_loss > 0 else (0.0 if g_profit == 0 else 99.9)

        # Expectancy in R is the figure that says whether the system makes
        # money. A win rate without the average win and loss beside it says
        # nothing.
        r_values = [m["r_multiple"] for m in merged if isinstance(m.get("r_multiple"), (int, float))]
        r_wins = [r for r in r_values if r > 0]
        r_losses = [abs(r) for r in r_values if r <= 0]
        expectancy_r = round(sum(r_values) / len(r_values), 3) if r_values else None

        res = {
            "win_count": w_count,
            "loss_count": l_count,
            "total_trades": tot_trades,
            "win_rate": win_rate,
            "gross_profit": round(g_profit, 2),
            "gross_loss": round(g_loss, 2),
            "net_pnl": round(g_profit - g_loss, 2),
            "profit_factor": profit_factor,
            "expectancy_r": expectancy_r,
            "avg_win_r": round(sum(r_wins) / len(r_wins), 2) if r_wins else None,
            "avg_loss_r": round(sum(r_losses) / len(r_losses), 2) if r_losses else None,
            "r_sample_size": len(r_values),
            "trade_history": merged,
            "accounting_note": "Bybit closed-PnL is the single source of truth for money; local records supply setup and exit-reason metadata only."
        }
        self._send_json(200, res)
