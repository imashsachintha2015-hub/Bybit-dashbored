"""Telegram bot for the setup tracker (backend_lib/setup_tracker.py).

The bot token is read from the TELEGRAM_BOT_TOKEN environment variable (never stored in the code or the database). The first person to send
/start to the bot becomes the receiver (TELEGRAM_CHAT_ID overrides that). Every 5 minutes the pasted setups are judged and a message goes out
when a setup changes status (entered, TP1, TP2, stopped, expired); nothing is sent when nothing changed. Commands: /start, /status (every
setup right now), /stop (stop the messages). Only the pasted setups are looked at: no orders, no other markets."""
import json
import os
import threading
import time
import urllib.parse
import urllib.request

from . import setup_tracker, sbgz_auto

CHAT_KEY = "telegram_chat"
SENT_KEY = "setup_notified"
CHECK_EVERY = 300.0
POLL_EVERY = 20.0
FINAL = ("TP2 HIT", "STOPPED", "STOPPED AFTER TP1", "EXPIRED", "NO DATA")
ICON = {"ENTERED": "🟡 ENTERED", "TP1 HIT": "🟢 TP1 HIT", "TP2 HIT": "✅ TP2 HIT", "STOPPED": "🔴 STOPPED", "STOPPED AFTER TP1": "🟠 STOPPED AFTER TP1", "EXPIRED": "⚪ EXPIRED"}
_thread = None
_stop = threading.Event()
_lock = threading.Lock()


def _token():
    return (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()


def _api(method, params=None, timeout=15):
    tok = _token()
    if not tok: return None
    try:
        data = urllib.parse.urlencode(params or {}).encode()
        with urllib.request.urlopen(urllib.request.Request(f"https://api.telegram.org/bot{tok}/{method}", data=data), timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"[telegram] {method} failed: {type(e).__name__}")                     # never print the URL: it holds the token
        return None


def send(chat_id, text):
    for i in range(0, len(text), 3800):                                              # Telegram's limit is 4096 characters
        if not _api("sendMessage", dict(chat_id=chat_id, text=text[i:i + 3800], disable_web_page_preview="true")): return False
    return True


def _chat():
    env = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if env: return dict(id=env, offset=(sbgz_auto._read_key(CHAT_KEY)[0] or {}).get("offset", 0))
    doc, ok = sbgz_auto._read_key(CHAT_KEY)
    return doc if ok and doc else None


def _fmt(x):
    return f"{x:,.6g}" if x < 1 else f"{x:,.2f}" if x < 1000 else f"{x:,.1f}"


def line(x):
    st = ICON.get(x["status"], x["status"])
    base = f"{st}  {x['coin']} {x['side']}  entry {_fmt(x['entry'])}"
    if x["status"] == "ENTERED": return base + f"  now {x['now_r']:+.2f}R" if x.get("now_r") is not None else base
    if x["status"] == "TP1 HIT": return base + f"  (+{x['R1']:.1f}R at TP1; TP2 {_fmt(x['tp2'])} next)" if x.get("tp2") else base + f"  (+{x['R1']:.1f}R)"
    if x["status"] == "TP2 HIT": return base + f"  (+{x['R2']:.1f}R at TP2)"
    if x["status"] == "STOPPED": return base + "  (-1R)"
    if x["status"] == "STOPPED AFTER TP1": return base + f"  (+{x['R1']:.1f}R if you exited at TP1, -1R if you held for TP2)"
    return base


def summary(d):
    s = d["summary"]
    head = f"Setups: {s['n']} · waiting {s['waiting']} · open {s['open']} · stopped {s['stopped']} · TP2 {s['tp2']} · expired {s['expired']}\nAll at TP1: {s['r_tp1_plan']:+.2f}R ({s['decided1']} decided) · all at TP2: {s['r_tp2_plan']:+.2f}R ({s['decided2']} decided)"
    rows = [x for x in d["rows"] if x["status"] != "WAITING"]
    return head + ("\n\n" + "\n".join(line(x) for x in rows) if rows else "\n\nNo setup has entered yet.")


def check(send_fn=None, now=None):
    """Judge the pasted setups and send what changed. Returns the number of messages sent (0 when nothing changed or nobody is registered)."""
    chat = _chat()
    if not chat or chat.get("muted"): return 0
    d = setup_tracker.status(now)
    if not d.get("ok") or d.get("empty"): return 0
    sent, ok = sbgz_auto._read_key(SENT_KEY)
    if not ok: return 0
    if not sent or sent.get("created") != d["created"]: sent = dict(created=d["created"], sent={})
    changed = [x for x in d["rows"] if x["status"] != "WAITING" and sent["sent"].get(f"{x['coin']}|{x['side']}") != x["status"]]
    if not changed: return 0
    text = "Setup update\n" + "\n".join(line(x) for x in changed)
    left = sum(1 for x in d["rows"] if x["status"] not in FINAL)
    text += f"\n\n{left} setup(s) still waiting or open." + ("" if left else " All done.\n\n" + summary(d))
    if not (send_fn or send)(chat["id"], text): return 0
    for x in changed: sent["sent"][f"{x['coin']}|{x['side']}"] = x["status"]
    sbgz_auto._write_key(SENT_KEY, sent)
    return 1


def poll(send_fn=None):
    """Read the bot's new messages: /start registers the sender, /status answers, /stop mutes. Returns how many messages were handled."""
    r = _api("getUpdates", dict(offset=(_chat() or {}).get("offset", 0), timeout=0, allowed_updates=json.dumps(["message"])))
    if not r or not r.get("ok"): return 0
    reply = send_fn or send; n = 0
    chat = _chat(); doc = dict(chat) if chat else None
    for u in r.get("result") or []:
        msg = u.get("message") or {}; cid = (msg.get("chat") or {}).get("id"); text = (msg.get("text") or "").strip().lower().split("@")[0]
        off = u["update_id"] + 1
        if doc is not None: doc["offset"] = off
        if cid is None: continue
        n += 1
        if text == "/start":
            if doc is None or str(doc.get("id")) == str(cid):
                doc = dict(id=cid, name=(msg.get("from") or {}).get("first_name") or "", offset=off, muted=False)
                reply(cid, "Connected. I will message you when a pasted setup enters, reaches TP1 / TP2, is stopped or expires.\n/status = every setup now · /stop = stop the messages")
            else:
                reply(cid, "This bot already belongs to someone else.")
        elif doc is None or str(doc.get("id")) != str(cid):
            continue
        elif text == "/status":
            d = setup_tracker.status()
            reply(cid, summary(d) if d.get("ok") and not d.get("empty") else "No setups are being tracked. Paste a list on the dashboard.")
        elif text == "/stop":
            doc["muted"] = True; reply(cid, "Messages stopped. Send /start to turn them on again.")
        else:
            reply(cid, "Commands: /status · /stop · /start")
    if doc is not None and doc != chat and not (os.environ.get("TELEGRAM_CHAT_ID") or "").strip():
        sbgz_auto._write_key(CHAT_KEY, doc)
    return n


def start_thread(delay=60.0):
    global _thread
    if not _token(): return None
    if _thread and _thread.is_alive(): return _thread
    _stop.clear()

    def loop():
        _stop.wait(delay); last = 0.0
        while not _stop.is_set():
            try:
                with _lock:
                    poll()
                    if time.time() - last >= CHECK_EVERY: last = time.time(); check()
            except Exception as e:
                print(f"[telegram] loop: {type(e).__name__}: {e}")
            _stop.wait(POLL_EVERY)

    _thread = threading.Thread(target=loop, name="telegram-notify", daemon=True)
    _thread.start()
    return _thread
