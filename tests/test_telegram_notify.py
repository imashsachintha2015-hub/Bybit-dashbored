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
    return dict(ok=True, created=created, rows=list(rows), summary=dict(n=n, waiting=0, open=0, stopped=0, tp2=0, expired=0, r_tp1_plan=0.0, r_tp2_plan=0.0, decided1=0, decided2=0))


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
            assert N.check() == 1 and "ENTERED  BTC LONG" in out[-1][1] and out[-1][0] == "111", out[-1]
            assert N.check() == 0, "the same status is not sent twice"
            S.status = lambda now=None: status(dict(ROW, status="TP1 HIT", R1=1.5), created=5)
            assert N.check() == 1 and "TP1 HIT" in out[-1][1], out[-1]
            S.status = lambda now=None: status(dict(ROW, status="TP2 HIT", R1=1.5, R2=2.0), created=5)
            assert N.check() == 1 and "All done" in out[-1][1] and "TP2 HIT" in out[-1][1], out[-1]
            S.status = lambda now=None: status(dict(ROW, status="ENTERED", now_r=0.1), created=6)
            assert N.check() == 1, "a new paste starts fresh"
            updates.append(dict(update_id=13, message=dict(chat=dict(id=111), text="/status"))); N.poll(); assert "Setups: 1" in out[-1][1], out[-1]
            updates.append(dict(update_id=14, message=dict(chat=dict(id=111), text="/stop"))); N.poll()
            S.status = lambda now=None: status(dict(ROW, status="TP1 HIT", R1=1.5), created=6)
            n = len(out); assert N.check() == 0 and len(out) == n, "muted"
            assert "TESTTOKEN" not in repr(out)
        finally:
            A.ROOT, N._api, S.status = old; os.environ.pop("SBGZ_AUTO_BACKEND", None); os.environ.pop("TELEGRAM_BOT_TOKEN", None)
    print("  /start registers the first sender only; messages only on a status change; /status, /stop; a new paste starts fresh")


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
