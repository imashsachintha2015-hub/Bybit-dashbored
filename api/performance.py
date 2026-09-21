import os
import sys
import time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_lib.bybit_client import get_client
from backend_lib.http_utils import JsonApiHandler
from backend_lib import trade_stats as trade_stats_mod

SL_TZ = timezone(timedelta(hours=5, minutes=30))

class handler(JsonApiHandler):
    def do_GET(self):
        client, err = get_client()
        if err:
            self._send_json(500, {"retCode": -1, "retMsg": err})
            return

        # Fetch Bybit closed trades (two pages of 100 to get full 200 history)
        closed_list = []
        try:
            p1 = client.get_closed_pnl(limit=100)
            res1 = p1.get("result", {})
            list1 = res1.get("list", []) or []
            closed_list.extend(list1)
            cursor = res1.get("nextPageCursor")
            if cursor:
                try:
                    p2 = client._signed_request("GET", "/v5/position/closed-pnl", {"category": "linear", "limit": 100, "cursor": cursor})
                    list2 = p2.get("result", {}).get("list", []) or []
                    closed_list.extend(list2)
                except Exception:
                    pass
        except Exception as e:
            print(f"[api/performance] Bybit pnl fetch error: {e}")

        stats = trade_stats_mod.load()
        local_history = list(stats.get("trade_history", []))

        def annotate(row):
            try:
                ts = int(row.get("updatedTime") or row.get("createdTime") or 0)
            except (TypeError, ValueError):
                ts = 0
            for loc in local_history:
                if loc.get("symbol") != row.get("symbol"):
                    continue
                if abs(int(loc.get("recorded_at", 0)) - ts) < 120000:
                    return loc
            return {}

        now_sl = datetime.now(tz=SL_TZ)
        today_sl_str = now_sl.strftime("%Y-%m-%d")
        yesterday_sl_str = (now_sl - timedelta(days=1)).strftime("%Y-%m-%d")

        w_count = l_count = 0
        g_profit = g_loss = 0.0
        today_w = today_l = 0
        today_gp = today_gl = 0.0
        yest_w = yest_l = 0
        yest_gp = yest_gl = 0.0

        merged = []
        for row in closed_list:
            try:
                pnl = float(row.get("closedPnl", 0))
            except (TypeError, ValueError):
                continue

            ts_ms = int(row.get("updatedTime") or row.get("createdTime") or 0)
            dt_sl = datetime.fromtimestamp(ts_ms / 1000, tz=SL_TZ) if ts_ms else datetime.now(tz=SL_TZ)
            d_str = dt_sl.strftime("%Y-%m-%d")

            if pnl > 0:
                w_count += 1
                g_profit += pnl
                if d_str == today_sl_str:
                    today_w += 1
                    today_gp += pnl
                elif d_str == yesterday_sl_str:
                    yest_w += 1
                    yest_gp += pnl
            elif pnl < 0:
                l_count += 1
                g_loss += abs(pnl)
                if d_str == today_sl_str:
                    today_l += 1
                    today_gl += abs(pnl)
                elif d_str == yesterday_sl_str:
                    yest_l += 1
                    yest_gl += abs(pnl)

            extra = annotate(row)
            entry_p = float(row.get("avgEntryPrice") or 0)
            exit_p = float(row.get("avgExitPrice") or 0)

            # In Bybit V5 closed-pnl, row.get("side") is the CLOSING order's side.
            raw_side = str(row.get("side", "")).upper()
            pos_side = extra.get("side")
            if not pos_side:
                pos_side = "BUY" if raw_side == "SELL" else "SELL"

            is_short = pos_side.upper() in ("SELL", "SHORT")
            stop_val = float(extra.get("stop", 0) or extra.get("stopLoss", 0) or 0)
            target_list = extra.get("targets") or ([] if not extra.get("target") else [extra.get("target")])
            if not stop_val:
                stop_val = exit_p if (pnl < 0 and exit_p) else round(entry_p * (1.018 if is_short else 0.982), 4)

            if not target_list:
                if pnl > 0 and exit_p:
                    target_list = [exit_p]
                else:
                    risk_dist = abs(entry_p - stop_val) if stop_val else (entry_p * 0.015)
                    target_list = [round(entry_p - risk_dist * 2.0 if is_short else entry_p + risk_dist * 2.0, 4)]

            merged.append({
                "id": row.get("orderId", "")[-8:] or "--",
                "time": dt_sl.strftime("%Y-%m-%d %H:%M"),
                "ts": ts_ms,
                "symbol": row.get("symbol"),
                "side": pos_side,
                "entry": entry_p,
                "exit": exit_p,
                "stop": stop_val,
                "targets": target_list,
                "nextSupport": extra.get("nextSupport"),
                "nextResistance": extra.get("nextResistance"),
                "isScalp": bool(extra.get("isScalp")),
                "pnl": round(pnl, 4),
                "pnl_pct": round(float(row.get("closedPnl", 0)) / max(float(row.get("cumEntryValue") or 1), 1e-9) * 100, 3),
                "status": "WIN" if pnl > 0 else "LOSS",
                "setup_type": extra.get("setup_type", "HTF_SWING_RUNNER"),
                "grade": extra.get("grade", "A+"),
                "r_multiple": extra.get("r_multiple"),
                "exit_reason": extra.get("exit_reason", ""),
                "reason": extra.get("reason", "")
            })

        merged.sort(key=lambda x: x["ts"], reverse=True)

        tot_trades = w_count + l_count
        win_rate = round((w_count / tot_trades) * 100, 1) if tot_trades > 0 else 0.0
        profit_factor = round(g_profit / g_loss, 2) if g_loss > 0 else (0.0 if g_profit == 0 else 99.9)

        r_values = [m["r_multiple"] for m in merged if isinstance(m.get("r_multiple"), (int, float))]
        r_wins = [r for r in r_values if r > 0]
        r_losses = [abs(r) for r in r_values if r <= 0]
        expectancy_r = round(sum(r_values) / len(r_values), 3) if r_values else None

        tot_today = today_w + today_l
        tot_yest = yest_w + yest_l

        res = {
            "win_count": w_count,
            "loss_count": l_count,
            "total_trades": tot_trades,
            "win_rate": win_rate,
            "gross_profit": round(g_profit, 2),
            "gross_loss": round(g_loss, 2),
            "net_pnl": round(g_profit - g_loss, 2),
            "profit_factor": profit_factor,
            "today": {
                "date": today_sl_str,
                "total_trades": tot_today,
                "win_count": today_w,
                "loss_count": today_l,
                "win_rate": round((today_w / tot_today * 100), 1) if tot_today else 0.0,
                "gross_profit": round(today_gp, 2),
                "gross_loss": round(today_gl, 2),
                "net_pnl": round(today_gp - today_gl, 2),
                "profit_factor": round(today_gp / today_gl, 2) if today_gl > 0 else (0.0 if today_gp == 0 else 99.9)
            },
            "yesterday": {
                "date": yesterday_sl_str,
                "total_trades": tot_yest,
                "win_count": yest_w,
                "loss_count": yest_l,
                "win_rate": round((yest_w / tot_yest * 100), 1) if tot_yest else 0.0,
                "gross_profit": round(yest_gp, 2),
                "gross_loss": round(yest_gl, 2),
                "net_pnl": round(yest_gp - yest_gl, 2),
                "profit_factor": round(yest_gp / yest_gl, 2) if yest_gl > 0 else (0.0 if yest_gp == 0 else 99.9)
            },
            "expectancy_r": expectancy_r,
            "avg_win_r": round(sum(r_wins) / len(r_wins), 2) if r_wins else None,
            "avg_loss_r": round(sum(r_losses) / len(r_losses), 2) if r_losses else None,
            "r_sample_size": len(r_values),
            "trade_history": merged,
            "timezone": "Asia/Colombo (UTC+05:30)",
            "accounting_note": "Bybit closed-PnL is the single source of truth for money; local records supply setup and exit-reason metadata only."
        }
        self._send_json(200, res)
