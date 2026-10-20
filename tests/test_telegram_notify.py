"""Tests for backend_lib/telegram_notify.py. Run:  python tests/test_telegram_notify.py   (Telegram and Bybit are stubbed; nothing leaves this machine)"""
import os
import sys
import tempfile
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backend_lib import telegram_notify as N, setup_tracker as S, sbgz_auto as A  # noqa: E402

ROW = dict(coin="BTC", side="LONG", entry=81629.0, stop=81222.0, tp1=82239.0, tp2=82443.0, status="WAITING", R1=None, R2=None, now_r=None)


def status(*rows, created=1):
    n = len(rows)
    return dict(ok=True, created=created, rows=list(rows), summary=dict(n=n, waiting=0, open=0, stopped=0, tp2=0, expired=0, trades=0, wins=0, losses=0, tp1_hit=0, unrealized_r=0.0, r_tp1_plan=0.0, r_tp2_plan=0.0, decided1=0, decided2=0))


def test_bot():
    os.environ["SBGZ_AUTO_BACKEND"] = "file"; os.environ["TELEGRAM_BOT_TOKEN"] = "TESTTOKEN"; os.environ.pop("TELEGRAM_CHAT_ID", None)
    old = (A.ROOT, N._api, S.status)
    out = []; updates = []
    with tempfile.TemporaryDirectory() as d:
        A.ROOT = d
        try:
            def api(method, params=None, timeout=15):
                if method == "getUpdates":
                    off = int(params["offset"]); return dict(ok=True, result=[u for u in updates if u["update_id"] >= off])
                if method == "sendMessage": out.append((str(params["chat_id"]), params["text"])); return dict(ok=True)
            N._api = api
            S.status = lambda now=None: status(dict(ROW), created=5)
            assert N.check() == 0 and not out, "nobody registered: nothing is sent"
            updates.append(dict(update_id=10, message=dict(chat=dict(id=111), text="/start", **{"from": dict(first_name="Sahan")})))
            assert N.poll() == 1 and out[-1][0] == "111" and "Connected" in out[-1][1], out
            updates.append(dict(update_id=11, message=dict(chat=dict(id=222), text="/start")))
            N.poll(); assert out[-1] == ("222", "This bot already belongs to someone else."), out[-1]
            n = len(out); updates.append(dict(update_id=12, message=dict(chat=dict(id=222), text="/status"))); N.poll(); assert len(out) == n, "a stranger gets no status"
            assert N.check() == 0, "all WAITING: nothing to say"
            S.status = lambda now=None: status(dict(ROW, status="ENTERED", now_r=0.4), created=5)
            assert N.check() == 1 and "Entered (1)" in out[-1][1] and "BTC" in out[-1][1] and out[-1][0] == "111", out[-1]
            assert N.check() == 0, "the same status is not sent twice"
            S.status = lambda now=None: status(dict(ROW, status="TP1 HIT", R1=1.5), created=5)
            assert N.check() == 1 and "TP1 reached" in out[-1][1], out[-1]
            S.status = lambda now=None: status(dict(ROW, status="TP2 HIT", R1=1.5, R2=2.0), created=5)
            assert N.check() == 1 and "Setup report" in out[-1][1] and "TP2 reached" in out[-1][1] and "<pre>" in out[-1][1], out[-1]
            S.status = lambda now=None: status(dict(ROW, status="ENTERED", now_r=0.1), created=6)
            assert N.check() == 1, "a new paste starts fresh"
            updates.append(dict(update_id=13, message=dict(chat=dict(id=111), text="/status"))); N.poll(); assert "Setup report" in out[-1][1], out[-1]
            updates.append(dict(update_id=14, message=dict(chat=dict(id=111), text="/stop"))); N.poll()
            S.status = lambda now=None: status(dict(ROW, status="TP1 HIT", R1=1.5), created=6)
            n = len(out); assert N.check() == 0 and len(out) == n, "muted"
            assert "TESTTOKEN" not in repr(out)
            # pasting the list into the chat: long lists arrive as several messages and are joined; a stranger's paste is ignored
            saved = []
            real_save = S.save
            S.save = lambda text: saved.append(text) or dict(ok=True, saved=text.count("\n") + 1, skipped=[])
            csv1 = "Coin,Side,Reference USD,Entry,Stop,TP1,TP2,RR TP1,RR TP2,Status\nBTC,long,1,2,0.5,3,4,1.5,2,Unconfirmed"
            csv2 = "ETH,short,1,2,3,1,0.5,1.5,2,Unconfirmed"
            n = len(out)
            updates.append(dict(update_id=20, message=dict(chat=dict(id=222), text=csv1))); N.poll(); N.poll()
            assert not saved and len(out) == n, "a stranger's paste is ignored"
            updates.append(dict(update_id=21, message=dict(chat=dict(id=111), text=csv1)))
            updates.append(dict(update_id=22, message=dict(chat=dict(id=111), text=csv2)))
            N.poll(); assert not saved, "wait: more pieces may follow"
            N.poll(); assert len(saved) == 1 and "BTC,long" in saved[0] and "ETH,short" in saved[0], saved
            assert "Tracking 3 setups" in out[-1][1] and out[-1][0] == "111", out[-1]
            N.poll(); assert len(saved) == 1, "saved once"
            updates.append(dict(update_id=23, message=dict(chat=dict(id=111), text="hello"))); N.poll(); assert "did not understand" in out[-1][1]
            S.save = real_save
        finally:
            A.ROOT, N._api, S.status = old; os.environ.pop("SBGZ_AUTO_BACKEND", None); os.environ.pop("TELEGRAM_BOT_TOKEN", None)
    print("  /start registers the first sender only; messages only on a status change; /status, /stop; a new paste starts fresh")


def rows_fixture():
    b = dict(ROW, price=81800.0, dist_pct=0.2, tp1_t=None, tp2_t=None, best_r=None)
    return [dict(b, coin="BTC", status="ENTERED", now_r=0.4, best_r=0.8),
            dict(b, coin="ETH", side="SHORT", entry=2380.0, stop=2392.0, tp1=2363.0, tp2=2357.0, price=2390.0, dist_pct=0.42, status="WAITING"),
            dict(b, coin="SOL", entry=104.5, stop=104.0, tp1=105.3, tp2=105.6, price=104.45, dist_pct=-0.05, status="WAITING"),
            dict(b, coin="XRP", status="TP1 HIT", R1=1.5, tp1_t=1, now_r=1.7),
            dict(b, coin="ADA", status="TP2 HIT", R1=1.5, R2=2.0, tp1_t=1, tp2_t=2),
            dict(b, coin="LTC", status="STOPPED", R1=-1.0, R2=-1.0),
            dict(b, coin="UNI", status="STOPPED AFTER TP1", R1=1.5, R2=-1.0, tp1_t=1),
            dict(b, coin="HBAR", status="EXPIRED")]


def full_status():
    d = status(*rows_fixture(), created=9)
    d["summary"].update(n=8, waiting=2, open=2, stopped=2, tp2=1, expired=1, trades=6, wins=4, losses=1, tp1_hit=4, unrealized_r=2.1, r_tp1_plan=3.5, r_tp2_plan=0.0, decided1=5, decided2=3)
    return d


def test_commands():
    os.environ["SBGZ_AUTO_BACKEND"] = "file"
    old = (A.ROOT, S.status, N._price, S.clear, S.set_expiry)
    out = []; reply = lambda cid, t: out.append(t)
    try:
        with tempfile.TemporaryDirectory() as dd:
            A.ROOT = dd
            S.status = lambda now=None: full_status(); N._price = lambda coin: 81900.0
            calls = []; S.clear = lambda: calls.append("clear") or dict(ok=True); S.set_expiry = lambda h: calls.append(("exp", h)) or dict(ok=True)
            doc = dict(id=1); h = lambda cmd, *a, now=1_000_000: N.handle(cmd, list(a), 1, doc, reply, None, now)
            h("status"); t = out[-1]
            assert "6 entered of 8 setups" in t and "Wins (reached TP1): <b>4</b>" in t and "Losses (stopped): <b>1</b>" in t and "TP1 hit trades: <b>4</b>" in t, t
            assert "Exit all at TP1  +3.5R" in t and "Unrealized open +2.1R" in t and "/risk 10" in t and "≈" not in t, t
            assert "✅ ADA   LONG  +1.5R / +2.0R" in t and "🟠 UNI   LONG  +1.5R / -1.0R" in t and "🔴 LTC   LONG  -1.0R / -1.0R" in t, t
            doc["risk_usd"] = 10; h("status"); assert "+3.5R  ≈ +$35" in out[-1] and "Unrealized open +2.1R  ≈ +$21" in out[-1] and "risk $10 per trade" in out[-1], out[-1]
            h("risk", "off"); assert "risk_usd" not in doc; h("risk", "25"); assert doc["risk_usd"] == 25.0; h("risk", "x"); assert "Use /risk" in out[-1]; doc.pop("risk_usd")
            h("open"); assert "Open trades (2)" in out[-1] and "XRP" in out[-1] and "BTC" in out[-1] and "ETH" not in out[-1], out[-1]
            h("waiting"); assert out[-1].index("SOL") < out[-1].index("ETH") and "ETH" in out[-1], out[-1]
            h("near"); assert "SOL" in out[-1] and "ETH" not in out[-1], out[-1]
            h("coin", "eth"); assert "SHORT" in out[-1] and "WAITING" in out[-1] and "TP2" in out[-1] and "<pre>" in out[-1], out[-1]
            h("coin", "doge"); assert "not in the tracked list" in out[-1]
            h("results"); assert "Finished <b>3</b>" in out[-1] and "wins <b>2</b>" in out[-1] and "expired 1" in out[-1], out[-1]
            h("price", "btc"); assert "BTC 81,900" in out[-1] and "entered" in out[-1], out[-1]
            h("quiet", "3"); assert doc["quiet_until"] == 1_000_000 + 3 * 3_600_000
            h("quiet", "off"); assert "quiet_until" not in doc
            h("tz", "+5.5"); assert doc["tz"] == 5.5
            h("digest", "20"); assert doc["digest"]["hour"] == 20; h("digest", "off"); assert "digest" not in doc
            h("digest", "99"); assert "Use /digest" in out[-1]
            h("clear"); assert doc["pending"]["act"] == "clear" and not calls, "asks first"
            h("no"); assert not calls and "pending" not in doc
            h("clear"); h("yes"); assert calls == ["clear"], calls
            h("expiry", "12"); h("yes"); assert calls[-1] == ("exp", 12.0), calls
            h("clear"); h("yes", now=1_000_000 + 200_000); assert len(calls) == 2 and "Nothing to confirm" in out[-1], "a stale confirmation does nothing"
            h("help"); assert "/open" in out[-1] and "/digest" in out[-1]
            h("nonsense"); assert "Unknown command" in out[-1]
    finally:
        A.ROOT, S.status, N._price, S.clear, S.set_expiry = old; os.environ.pop("SBGZ_AUTO_BACKEND", None)
    print("  /status totals, /open /waiting /near /coin /results /price, /quiet /tz /digest, confirm-first /clear and /expiry")


def test_quiet_digest_trades_pnl():
    os.environ["SBGZ_AUTO_BACKEND"] = "file"; os.environ["TELEGRAM_BOT_TOKEN"] = "T"
    old = (A.ROOT, S.status, N._auto_rows)
    out = []; send = lambda cid, t: out.append(t) or True
    try:
        with tempfile.TemporaryDirectory() as dd:
            A.ROOT = dd
            N._save_chat(dict(id=5, quiet_until=10**13, tz=5.5, digest=dict(hour=20, last=None)))
            S.status = lambda now=None: status(dict(ROW, status="ENTERED", now_r=0.1), created=1)
            assert N.check(send, now=1_000) == 0 and not out, "quiet: nothing is sent, nothing is marked sent"
            c = N._chat(); c.pop("quiet_until"); N._save_chat(c)
            assert N.check(send) == 1, "after the quiet time the change is sent"
            S.status = lambda now=None: full_status()
            noon = 1_800_000_000_000 // 86_400_000 * 86_400_000 + 12 * 3_600_000          # 12:00 UTC = 17:30 local
            assert N.digest(send, now=noon) == 0, "before 20:00 local"
            late = noon + 5 * 3_600_000                                                    # 17:00 UTC = 22:30 local
            assert N.digest(send, now=late) == 1 and "Daily summary" in out[-1] and "Setup report" in out[-1]
            assert N.digest(send, now=late + 600_000) == 0, "once a day"
            day = 1_800_000_000_000
            auto = {"SBGZ": dict(settings=dict(mode="live"), tracked=[
                        dict(symbol="APTUSDT", side="SHORT", status="resting", entry=0.811, dist_pct=1.2, virtual=False),
                        dict(symbol="BTCUSDT", side="LONG", status="open", r_now=0.6, pnl_usd=0.25, virtual=False, entry=1),
                        dict(symbol="AAAUSDT", side="LONG", status="done", R=1.5, done=day - 1000, virtual=False),
                        dict(symbol="BBBUSDT", side="LONG", status="done", R=-1.0, done=day - 3 * 86_400_000, virtual=False),
                        dict(symbol="CCCUSDT", side="LONG", status="done", R=-1.0, done=day - 9 * 86_400_000, virtual=False)]),
                    "AMD": dict(settings=dict(mode="live"), tracked=[])}
            N._auto_rows = lambda fn: auto
            t = N.cmd_trades(None); assert "SBGZ</b> (live) · 2 active" in t and "APT   SHORT waiting 1.20% away" in t and "BTC   LONG  open +0.6R" in t and "AMD</b> (live) · 0 active" in t, t
            t = N.cmd_pnl(None, now=day); assert "Today    1 closed  1W 0L  +1.50R" in t and "7 days   2 closed  1W 1L  +0.50R" in t, t
    finally:
        A.ROOT, S.status, N._auto_rows = old; os.environ.pop("SBGZ_AUTO_BACKEND", None); os.environ.pop("TELEGRAM_BOT_TOKEN", None)
    print("  /quiet holds the updates back, the daily digest goes once at the chosen local hour, /trades and /pnl read the auto-orders")


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception:
                fails += 1
                print(f"FAIL {name}"); traceback.print_exc()
    print("all telegram_notify tests passed" if not fails else f"{fails} telegram_notify test(s) failed")
    sys.exit(1 if fails else 0)
