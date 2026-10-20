"""Telegram bot for the setup tracker (backend_lib/setup_tracker.py).

The bot token is read from the TELEGRAM_BOT_TOKEN environment variable (never stored in the code or the database). The first person to send
/start to the bot becomes the receiver (TELEGRAM_CHAT_ID overrides that). Every 5 minutes the pasted setups are judged and a message goes out
when a setup changes status (entered, TP1, TP2, stopped, expired); nothing is sent when nothing changed. Commands: see COMMANDS below (/help in the chat). The receiver can also paste the setups CSV into the chat (long lists arrive as several messages: they
are joined) or send it as a .csv / .txt file: it replaces the tracked list, like the dashboard's Track button. Only the pasted setups are looked at: no orders, no other markets."""
import json
import os
import threading
import time
import urllib.parse
import urllib.request

from . import sbgz, setup_tracker, sbgz_auto

CHAT_KEY = "telegram_chat"
SENT_KEY = "setup_notified"
CHECK_EVERY = 300.0
POLL_EVERY = 20.0
FINAL = ("TP2 HIT", "STOPPED", "STOPPED AFTER TP1", "EXPIRED", "NO DATA")
ICON = {"ENTERED": "🟡 ENTERED", "TP1 HIT": "🟢 TP1 HIT", "TP2 HIT": "✅ TP2 HIT", "STOPPED": "🔴 STOPPED", "STOPPED AFTER TP1": "🟠 STOPPED AFTER TP1", "EXPIRED": "⚪ EXPIRED"}
_pieces = []                                                                         # CSV text received in the last poll(s), saved when no more arrives
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


def _csv_like(text):
    lines = [l for l in (text or "").splitlines() if l.strip()]
    return bool(lines) and not text.lstrip().startswith("/") and sum(1 for l in lines if l.count(",") >= 5 or l.count("\t") >= 5) >= max(1, len(lines) // 2)


def _download(doc):
    """Text of a Telegram document (.csv / .txt up to 200 KB), or None."""
    name = (doc.get("file_name") or "").lower()
    if not name.endswith((".csv", ".txt", ".tsv")) or int(doc.get("file_size") or 0) > 200_000: return None
    r = _api("getFile", dict(file_id=doc.get("file_id")))
    path = ((r or {}).get("result") or {}).get("file_path")
    if not path: return None
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/file/bot{_token()}/{path}", timeout=20) as f:
            return f.read().decode("utf-8", "replace")
    except Exception as e:
        print(f"[telegram] file download failed: {type(e).__name__}")
        return None


def _save_pieces(cid, reply):
    text = "\n".join(_pieces); _pieces.clear()
    r = setup_tracker.save(text)
    if r.get("ok"):
        msg = f"Tracking {r['saved']} setups (the old list is replaced). I will message you when one enters, hits a target, is stopped or expires. /status shows them all."
        if r.get("skipped"): msg += "\nSkipped:\n" + "\n".join(r["skipped"][:10])
    else:
        msg = "I could not read that list: " + str(r.get("error"))
    reply(cid, msg)


def _chat():
    """The registered chat (stored under CHAT_KEY; TELEGRAM_CHAT_ID overrides who the receiver is), or None."""
    doc, ok = sbgz_auto._read_key(CHAT_KEY)
    doc = dict(doc) if ok and doc else None
    env = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if env: doc = dict(doc or {}, id=env)
    return doc


def _save_chat(doc):
    return sbgz_auto._write_key(CHAT_KEY, doc)


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


def summary(d, rows=True):
    s = d["summary"]
    head = (f"Total trades (entered): {s['trades']} of {s['n']} setups\n"
            f"Wins (reached TP1): {s['wins']} · Losses (stopped before TP1): {s['losses']} · Open now: {s['open']}\n"
            f"TP1 hit trades: {s['tp1_hit']} · TP2 hit: {s['tp2']}\n"
            f"Unrealized R (open trades): {s['unrealized_r']:+.2f}R\n"
            f"Realized R: all at TP1 {s['r_tp1_plan']:+.2f}R ({s['decided1']} decided) · all at TP2 {s['r_tp2_plan']:+.2f}R ({s['decided2']} decided)\n"
            f"Waiting for entry: {s['waiting']} · Expired: {s['expired']}")
    if not rows: return head
    moved = [x for x in d["rows"] if x["status"] != "WAITING"]
    return head + ("\n\n" + "\n".join(line(x) for x in moved) if moved else "\n\nNo setup has entered yet.")


def _price(coin):
    cs = sbgz.fetch_candles(coin + "USDT", "1", 2, timeout=8)
    return cs[-1]["close"] if cs else None


def _away(x):
    return abs(x["dist_pct"]) if x.get("dist_pct") is not None else 9e9


def cmd_open(d):
    rows = sorted((x for x in d["rows"] if x["status"] in ("ENTERED", "TP1 HIT")), key=lambda x: -(x.get("now_r") or 0))
    if not rows: return "No open trades."
    out = []
    for x in rows:
        p = x.get("price"); tgt = x["tp1"] if x["status"] == "ENTERED" else (x.get("tp2") or x["tp1"]); nm = "TP1" if x["status"] == "ENTERED" else "TP2"
        extra = f" · stop {abs(p - x['stop']) / p * 100:.2f}% away · {nm} {abs(tgt - p) / p * 100:.2f}% away" if p else ""
        out.append(f"{'🟢' if x['status'] == 'TP1 HIT' else '🟡'} {x['coin']} {x['side']} {x['status']} now {x['now_r']:+.2f}R · price {_fmt(p)}{extra}" if x.get("now_r") is not None and p else line(x))
    return f"Open trades ({len(rows)}) · unrealized {d['summary']['unrealized_r']:+.2f}R\n" + "\n".join(out)


def cmd_waiting(d, limit=None):
    rows = sorted((x for x in d["rows"] if x["status"] == "WAITING"), key=_away)
    if limit is not None: rows = [x for x in rows if _away(x) <= limit]
    if not rows: return "No waiting setups" + (f" within {limit}% of their entry." if limit is not None else ".")
    return (f"Waiting setups within {limit}% of the entry ({len(rows)})\n" if limit is not None else f"Waiting for entry ({len(rows)}), nearest first\n") + \
        "\n".join(f"⏳ {x['coin']} {x['side']} entry {_fmt(x['entry'])} · price {_fmt(x['price'])} · {_away(x):.2f}% away" if x.get("price") else f"⏳ {x['coin']} {x['side']} entry {_fmt(x['entry'])}" for x in rows)


def cmd_coin(d, coin):
    coin = (coin or "").upper().replace("USDT", "")
    rows = [x for x in d["rows"] if x["coin"] == coin]
    if not rows: return f"{coin or 'That coin'} is not in the tracked list." if coin else "Use /coin BTC"
    out = [f"{coin}" + (f" · price {_fmt(rows[0]['price'])}" if rows[0].get("price") else "")]
    for x in rows:
        out.append(f"{x['side']}: {x['status']}\n  entry {_fmt(x['entry'])} · stop {_fmt(x['stop'])} · TP1 {_fmt(x['tp1'])}" + (f" · TP2 {_fmt(x['tp2'])}" if x.get("tp2") else "") +
                   (f"\n  now {x['now_r']:+.2f}R" if x.get("now_r") is not None else "") + (f"\n  best so far {x['best_r']:+.2f}R" if x.get("best_r") is not None else ""))
    return "\n".join(out)


def cmd_results(d):
    done = [x for x in d["rows"] if x["status"] in ("TP2 HIT", "STOPPED", "STOPPED AFTER TP1")]
    s = d["summary"]
    head = (f"Finished: {len(done)} · wins (reached TP1) {sum(1 for x in done if x['tp1_t'])} · stopped before TP1 {sum(1 for x in done if x['status'] == 'STOPPED')} · expired {s['expired']}\n"
            f"All at TP1: {s['r_tp1_plan']:+.2f}R · all at TP2: {s['r_tp2_plan']:+.2f}R · still open {s['open']} ({s['unrealized_r']:+.2f}R)")
    return head + ("\n\n" + "\n".join(line(x) for x in done) if done else "\n\nNothing has finished yet.")


def cmd_price(d, coin):
    coin = (coin or "").upper().replace("USDT", "")
    if not coin: return "Use /price BTC"
    p = _price(coin)
    if p is None: return f"No price for {coin}USDT."
    out = [f"{coin} {_fmt(p)}"]
    for x in (d["rows"] if d else []):
        if x["coin"] == coin and x["status"] in ("WAITING", "ENTERED", "TP1 HIT"):
            out.append(f"  {x['side']} {x['status']}: entry {_fmt(x['entry'])} ({abs(p - x['entry']) / p * 100:.2f}% away) · stop {_fmt(x['stop'])}")
    return "\n".join(out)


def _auto_rows(client_fn):
    out = {}
    try:
        from . import amd_auto
        out["SBGZ"] = sbgz_auto.status(client_fn() if client_fn else None)
        out["AMD"] = amd_auto.status(client_fn() if client_fn else None)
    except Exception as e:
        print(f"[telegram] auto status failed: {type(e).__name__}")
    return out


def cmd_trades(client_fn, now=None):
    out = []; st = _auto_rows(client_fn)
    for name, d in st.items():
        rows = [t for t in d.get("tracked") or [] if not t.get("virtual") and t["status"] in ("resting", "open", "closing")]
        out.append(f"{name} ({(d.get('settings') or {}).get('mode', '?')}): {len(rows)} active")
        for t in rows:
            c = t["symbol"].replace("USDT", "")
            if t["status"] == "resting": out.append(f"  ⏳ {c} {t['side']} waiting, entry {_fmt(t['entry'])}" + (f" · {t['dist_pct']:.2f}% away" if t.get("dist_pct") is not None else ""))
            else: out.append(f"  🟡 {c} {t['side']} open" + (f" · {t['r_now']:+.2f}R" if t.get("r_now") is not None else "") + (f" ({t['pnl_usd']:+.2f} USDT)" if t.get("pnl_usd") is not None else ""))
    return "\n".join(out) if out else "The auto-order status is not available right now."


def cmd_pnl(client_fn, now=None):
    now = now or int(time.time() * 1000); day0 = now // 86_400_000 * 86_400_000; out = []; st = _auto_rows(client_fn)
    for name, d in st.items():
        done = [t for t in d.get("tracked") or [] if not t.get("virtual") and t["status"] == "done" and t.get("R") is not None and t.get("done")]
        def agg(sel): return f"{len(sel)} closed · {sum(1 for t in sel if t['R'] > 0)} wins · {sum(1 for t in sel if t['R'] <= 0)} losses · {sum(t['R'] for t in sel):+.2f}R"
        out.append(f"{name}: today {agg([t for t in done if t['done'] >= day0])}\n  last 7 days {agg([t for t in done if t['done'] >= now - 7 * 86_400_000])}")
    return "\n".join(out) if out else "The auto-order status is not available right now."


COMMANDS = [("status", "Totals: trades, wins, losses, unrealized R, TP1 hits"), ("open", "Open trades with live R"), ("waiting", "Setups waiting for entry, nearest first"),
            ("near", "Setups within 0.3% of their entry"), ("coin", "One coin: /coin BTC"), ("results", "Finished setups and R"), ("price", "Price + your levels: /price BTC"),
            ("trades", "Demo auto-orders now (SBGZ, AMD)"), ("pnl", "Auto-orders result today and 7 days"), ("quiet", "Mute updates for N hours: /quiet 3"),
            ("digest", "Daily summary: /digest 20 or /digest off"), ("tz", "Your UTC offset: /tz +5.5"), ("expiry", "Hours a setup may wait: /expiry 12"),
            ("clear", "Clear the tracked list"), ("stop", "Mute all updates"), ("help", "All commands")]


def help_text():
    return "Commands\n" + "\n".join(f"/{c} - {t}" for c, t in COMMANDS) + "\n\nPaste the setups CSV (text or a .csv file) to start tracking a new list."


def _num(a, lo, hi):
    try:
        v = float(a)
        return v if lo <= v <= hi else None
    except (TypeError, ValueError):
        return None


def handle(cmd, args, cid, doc, reply, client_fn=None, now=None):
    """One command from the registered chat. Changes to doc (mute, quiet, digest, tz, pending confirmation) are saved by the caller."""
    now = now or int(time.time() * 1000)
    def tracked():
        d = setup_tracker.status()
        return d if d.get("ok") and not d.get("empty") else None
    if cmd in ("yes", "no"):
        p = doc.pop("pending", None)
        if not p or p["until"] < now: reply(cid, "Nothing to confirm."); return
        if cmd == "no": reply(cid, "Cancelled."); return
        if p["act"] == "clear": setup_tracker.clear(); sbgz_auto._write_key(SENT_KEY, None); reply(cid, "The tracked list is cleared.")
        elif p["act"] == "expiry":
            r = setup_tracker.set_expiry(p["val"]); reply(cid, f"A setup now waits up to {p['val']:g} hours for its entry." if r.get("ok") else "Could not change it: " + str(r.get("error")))
        return
    if cmd in ("price", "help", "start", "trades", "pnl", "stop", "quiet", "digest", "tz", "clear", "expiry"):
        pass
    elif cmd in ("status", "open", "waiting", "near", "coin", "results"):
        d = tracked()
        if d is None: reply(cid, "No setups are being tracked. Paste a list here or on the dashboard."); return
        reply(cid, {"status": lambda: summary(d), "open": lambda: cmd_open(d), "waiting": lambda: cmd_waiting(d), "near": lambda: cmd_waiting(d, 0.3),
                    "coin": lambda: cmd_coin(d, args[0] if args else ""), "results": lambda: cmd_results(d)}[cmd]()); return
    if cmd == "price": reply(cid, cmd_price(tracked(), args[0] if args else "")); return
    if cmd == "trades": reply(cid, cmd_trades(client_fn)); return
    if cmd == "pnl": reply(cid, cmd_pnl(client_fn, now)); return
    if cmd in ("help", "start"): reply(cid, help_text()); return
    if cmd == "stop": doc["muted"] = True; reply(cid, "Messages stopped. Send /start to turn them on again."); return
    if cmd == "quiet":
        if args and args[0].lower() == "off": doc.pop("quiet_until", None); reply(cid, "Updates are on again."); return
        h = _num(args[0] if args else None, 0.1, 168)
        if h is None: reply(cid, "Use /quiet 3 (hours) or /quiet off"); return
        doc["quiet_until"] = now + int(h * 3_600_000); reply(cid, f"Quiet for {h:g} hour(s). Changes that happen meanwhile are sent when it ends."); return
    if cmd == "tz":
        v = _num(args[0] if args else None, -12, 14)
        if v is None: reply(cid, f"Your UTC offset is {doc.get('tz', 0):+g} h. Change it with /tz +5.5"); return
        doc["tz"] = v; reply(cid, f"UTC offset set to {v:+g} h."); return
    if cmd == "digest":
        a = (args[0].lower() if args else "")
        if a == "off": doc.pop("digest", None); reply(cid, "The daily summary is off."); return
        h = _num(a.split(":")[0], 0, 23)
        if h is None: reply(cid, "Use /digest 20 (hour of the day in your time) or /digest off. Set your offset with /tz first."); return
        doc["digest"] = dict(hour=int(h), last=None); reply(cid, f"Daily summary at {int(h):02d}:00 (UTC {doc.get('tz', 0):+g} h)."); return
    if cmd == "expiry":
        v = _num(args[0] if args else None, 1, 720)
        if v is None: reply(cid, "Use /expiry 12 (hours a setup may wait for its entry)"); return
        doc["pending"] = dict(act="expiry", val=v, until=now + 120_000); reply(cid, f"Change the waiting time to {v:g} hours for the tracked list? Send /yes within 2 minutes, or /no."); return
    if cmd == "clear":
        doc["pending"] = dict(act="clear", until=now + 120_000); reply(cid, "Clear the whole tracked list? Send /yes within 2 minutes, or /no."); return
    reply(cid, "Unknown command. /help lists them.")


def digest(send_fn=None, now=None):
    """The daily summary, once a day at the hour chosen with /digest (in the /tz offset). Returns 1 when sent."""
    chat = _chat(); now = now or int(time.time() * 1000)
    if not chat or chat.get("muted") or not chat.get("digest"): return 0
    local = time.gmtime(now / 1000 + chat.get("tz", 0) * 3600); today = time.strftime("%Y-%m-%d", local)
    if local.tm_hour < chat["digest"]["hour"] or chat["digest"].get("last") == today: return 0
    d = setup_tracker.status(now)
    if not d.get("ok") or d.get("empty"): return 0
    if not (send_fn or send)(chat["id"], "Daily summary\n" + summary(d)): return 0
    chat["digest"]["last"] = today; _save_chat(chat)
    return 1


def check(send_fn=None, now=None):
    """Judge the pasted setups and send what changed. Returns the number of messages sent (0 when nothing changed, nobody is registered, or it is muted / quiet)."""
    chat = _chat(); now_ms = now or int(time.time() * 1000)
    if not chat or chat.get("muted") or (chat.get("quiet_until") or 0) > now_ms: return 0
    d = setup_tracker.status(now)
    if not d.get("ok") or d.get("empty"): return 0
    sent, ok = sbgz_auto._read_key(SENT_KEY)
    if not ok: return 0
    if not sent or sent.get("created") != d["created"]: sent = dict(created=d["created"], sent={})
    changed = [x for x in d["rows"] if x["status"] != "WAITING" and sent["sent"].get(f"{x['coin']}|{x['side']}") != x["status"]]
    if not changed: return 0
    text = "Setup update\n" + "\n".join(line(x) for x in changed)
    left = sum(1 for x in d["rows"] if x["status"] not in FINAL)
    text += f"\n\n{left} setup(s) still waiting or open." + ("" if left else " All done.\n\n" + summary(d, rows=False))
    if not (send_fn or send)(chat["id"], text): return 0
    for x in changed: sent["sent"][f"{x['coin']}|{x['side']}"] = x["status"]
    sbgz_auto._write_key(SENT_KEY, sent)
    return 1


def poll(send_fn=None, client_fn=None):
    """Read the bot's new messages: /start registers the sender; the registered chat can use the commands, paste the CSV or send it as a file.
    Returns how many messages were handled."""
    r = _api("getUpdates", dict(offset=(_chat() or {}).get("offset", 0), timeout=0, allowed_updates=json.dumps(["message"])))
    if not r or not r.get("ok"): return 0
    reply = send_fn or send; n = 0
    chat = _chat(); doc = dict(chat) if chat else None; got_csv = False
    for u in r.get("result") or []:
        msg = u.get("message") or {}; cid = (msg.get("chat") or {}).get("id"); raw = (msg.get("text") or "").strip()
        off = u["update_id"] + 1
        if doc is not None: doc["offset"] = off
        if cid is None: continue
        n += 1
        parts = raw.split(); cmd = parts[0].lstrip("/").split("@")[0].lower() if parts and raw.startswith("/") else ""; args = parts[1:]
        if cmd == "start":
            if doc is None or str(doc.get("id")) == str(cid):
                keep = {k: v for k, v in (doc or {}).items() if k in ("tz", "digest")}
                doc = dict(keep, id=cid, name=(msg.get("from") or {}).get("first_name") or "", offset=off, muted=False)
                reply(cid, "Connected. I will message you when a pasted setup enters, reaches TP1 / TP2, is stopped or expires.\n\n" + help_text())
            else:
                reply(cid, "This bot already belongs to someone else.")
        elif doc is None or str(doc.get("id")) != str(cid):
            continue
        elif msg.get("document") and not raw:
            body = _download(msg["document"])
            if body and _csv_like(body): _pieces.append(body); got_csv = True
            else: reply(cid, "Send the setups as text or as a .csv / .txt file with Coin,Side,Reference USD,Entry,Stop,TP1,TP2,...")
        elif _csv_like(raw):
            _pieces.append(raw); got_csv = True
        elif cmd:
            try:
                handle(cmd, args, cid, doc, reply, client_fn)
            except Exception as e:
                print(f"[telegram] /{cmd} failed: {type(e).__name__}: {e}"); reply(cid, f"/{cmd} failed, try again in a minute.")
        else:
            reply(cid, "I did not understand that. /help lists the commands.")
    if _pieces and not got_csv and doc is not None: _save_pieces(doc["id"], reply)           # nothing more arrived since the last poll: the list is complete
    if doc is not None and doc != chat: _save_chat(doc)
    return n


def register_menu():
    """The / menu in Telegram (setMyCommands)."""
    return _api("setMyCommands", dict(commands=json.dumps([dict(command=c, description=t[:250]) for c, t in COMMANDS])))


def start_thread(get_client=None, delay=60.0):
    global _thread
    if not _token(): return None
    if _thread and _thread.is_alive(): return _thread
    _stop.clear()

    def loop():
        _stop.wait(delay); last = 0.0
        register_menu()
        while not _stop.is_set():
            try:
                with _lock:
                    poll(client_fn=get_client)
                    if time.time() - last >= CHECK_EVERY: last = time.time(); check(); digest()
            except Exception as e:
                print(f"[telegram] loop: {type(e).__name__}: {e}")
            _stop.wait(POLL_EVERY)

    _thread = threading.Thread(target=loop, name="telegram-notify", daemon=True)
    _thread.start()
    return _thread
