"""Telegram bot for the setup tracker (backend_lib/setup_tracker.py).

The bot token is read from the TELEGRAM_BOT_TOKEN environment variable (never stored in the code or the database). The first person to send
/start to the bot becomes the receiver (TELEGRAM_CHAT_ID overrides that). Every 5 minutes the pasted setups are judged and a message goes out
when a setup changes status (entered, TP1, TP2, stopped, expired); nothing is sent when nothing changed. Commands: see COMMANDS below (/help in the chat). The receiver can also paste the setups CSV into the chat (long lists arrive as several messages: they
are joined) or send it as a .csv / .txt file: it replaces the tracked list, like the dashboard's Track button. Only the pasted setups are looked at: no orders, no other markets."""
import html
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request

from . import sbgz, setup_orders, setup_tracker, sbgz_auto

CHAT_KEY = "telegram_chat"
SENT_KEY = "setup_notified"
CHECK_EVERY = 300.0
POLL_EVERY = 20.0
FINAL = ("TP2 HIT", "STOPPED", "STOPPED AFTER TP1", "EXPIRED", "INVALIDATED", "NO DATA")
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


def _chunks(text, limit=3800):
    """Split on blank lines (every block is balanced HTML) into pieces under Telegram's 4096-character limit."""
    out, cur = [], ""
    for blk in text.split("\n\n"):
        if cur and len(cur) + len(blk) + 2 > limit: out.append(cur); cur = ""
        cur = (cur + "\n\n" + blk) if cur else blk
    if cur: out.append(cur)
    return out


def send(chat_id, text):
    """Send HTML text (<b>, <pre>, <i>). If Telegram rejects the markup the same text goes out as plain text."""
    ok = True
    for piece in _chunks(text):
        r = _api("sendMessage", dict(chat_id=chat_id, text=piece[:4000], parse_mode="HTML", disable_web_page_preview="true"))
        if not r: r = _api("sendMessage", dict(chat_id=chat_id, text=re.sub(r"<[^>]+>", "", html.unescape(piece))[:4000], disable_web_page_preview="true"))
        ok = bool(r) and ok
    return ok


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


def _order_report(res):
    if not res.get("ok"): return "🧾 <b>Orders not placed</b>\n" + esc(res.get("error"))
    out = [f"🧾 <b>Demo orders</b> (risk ${res['risk']:g} per trade, half exits at TP1, half at TP2)",
           f"Placed <b>{res['placed_orders']}</b> orders for <b>{res['placed_setups']}</b> setups. A coin's long and short are either-or: when one triggers, the other is cancelled."]
    if res["skipped"]:
        out.append(f"<b>Skipped ({len(res['skipped'])})</b>\n" + pre([f"{c:<6}{sd:<6}{why[:60]}" for c, sd, why in res["skipped"][:25]]))
    return "\n\n".join(out)


def _save_pieces(cid, reply, doc=None, client_fn=None):
    text = "\n".join(_pieces); _pieces.clear()
    r = setup_tracker.save(text)
    if r.get("ok"):
        msg = f"✅ Tracking <b>{r['saved']}</b> setups (the old list is replaced). I will message you when one enters, hits a target, is stopped or expires. /status shows them all."
        if r.get("skipped"): msg += "\n<b>Skipped:</b>\n" + pre(r["skipped"][:10])
        reply(cid, msg)
        if not (doc or {}).get("orders_on", True):
            reply(cid, "🧾 Orders are off. /orders on places demo orders for the next list you paste.")
        elif client_fn:
            try:
                res = setup_orders.place_all(client_fn(), r["created"], r["rows"], (doc or {}).get("risk_usd") or setup_orders.DEFAULT_RISK)
            except Exception as e:
                print(f"[telegram] placing orders failed: {type(e).__name__}: {e}")
                res = dict(ok=False, error="placing the orders failed: " + type(e).__name__)
            reply(cid, _order_report(res))
    else:
        reply(cid, "I could not read that list: " + esc(r.get("error")))


def _chat():
    """The registered chat (stored under CHAT_KEY; TELEGRAM_CHAT_ID overrides who the receiver is), or None."""
    doc, ok = sbgz_auto._read_key(CHAT_KEY)
    doc = dict(doc) if ok and doc else None
    env = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if env: doc = dict(doc or {}, id=env)
    return doc


def _save_chat(doc):
    return sbgz_auto._write_key(CHAT_KEY, doc)


def esc(x):
    return html.escape(str(x), quote=False)


def pre(lines):
    return "<pre>" + "\n".join(esc(l) for l in lines) + "</pre>"


SEP = "━━━━━━━━━━━━━━"


def _fmt(x):
    return f"{x:,.6g}" if x < 1 else f"{x:,.2f}" if x < 1000 else f"{x:,.1f}"


def r1(x):
    return "--" if x is None else f"{x:+.1f}R"


def usd(r, risk):
    if not risk: return ""
    t = f"{abs(r) * risk:,.2f}".rstrip("0").rstrip(".")
    return f"  ≈ {'+' if r >= 0 else '-'}${t}"


def breakdown(rows, key):
    """The decided trades of one plan grouped by result: '3 × +2.0R, 1 × -1.0R'."""
    c = {}
    for x in rows:
        if x.get(key) is not None: c[round(x[key], 2)] = c.get(round(x[key], 2), 0) + 1
    return ", ".join(f"{n} × {v:+.1f}R" for v, n in sorted(c.items(), reverse=True)) or "none yet"


def cmd_net(d, risk=None):
    """Net result of both plans, trade by trade grouped: e.g. 3 × +2.0R, 1 × -1.0R = +5.0R ≈ +$10 at $2 risk per trade."""
    s = d["summary"]; un = s["unrealized_r"]; rows = d["rows"]
    blk = []
    for name, key, tot, n in (("Exit all at TP2", "R2", s["r_tp2_plan"], s["decided2"]), ("Exit all at TP1", "R1", s["r_tp1_plan"], s["decided1"])):
        net = s["r_tp2_net"] if key == "R2" else s["r_tp1_net"]; fee = s["fees_tp2_r"] if key == "R2" else s["fees_tp1_r"]
        blk.append(f"<b>{name}</b> ({n} decided)\n" + pre([breakdown(rows, key), f"= {tot:+.1f}R{usd(tot, risk)}", f"fees  -{fee:.1f}R{usd(-fee, risk)}", f"AFTER FEES {net:+.1f}R{usd(net, risk)}",
                                                          f"with open trades now {net + un:+.1f}R{usd(net + un, risk)}"]))
    return (f"<b>💰 Net profit / loss</b>" + (f"   (risk ${risk:g} per trade)" if risk else "") + "\n" + "\n\n".join(blk) +
            f"\n\n<i>Fees: taker {setup_tracker.TAKER * 100:.3f}% on the entry and a stop, maker {setup_tracker.MAKER * 100:.3f}% on a take-profit. Open trades now: {s['open']} ({un:+.1f}R{esc(usd(un, risk))})" + ("" if risk else " · try /net 2 for $2 per trade") + "</i>")


ICON1 = {"ENTERED": "🟡", "TP1 HIT": "🟢", "TP2 HIT": "✅", "STOPPED": "🔴", "STOPPED AFTER TP1": "🟠", "EXPIRED": "⚪", "INVALIDATED": "🚫", "WAITING": "⏳"}


def row(x):
    """One setup as a fixed-width line: icon, coin, side, then now-R (open) or the R of the two plans (finished: exit at TP1 / exit at TP2)."""
    head = f"{ICON1.get(x['status'], '•')} {x['coin']:<6}{x['side']:<6}"
    if x["status"] in ("ENTERED", "TP1 HIT"): return head + f"now {r1(x.get('now_r'))}" + (" TP1✓" if x["status"] == "TP1 HIT" else "")
    if x["status"] in ("TP2 HIT", "STOPPED", "STOPPED AFTER TP1"): return head + f"{r1(x['R1'])} / {r1(x['R2'])}"
    return head + x["status"].lower()


def line(x):
    return row(x)


def _section(title, items):
    return f"<b>{esc(title)} ({len(items)})</b>\n" + pre([row(x) for x in items]) if items else ""


def summary(d, rows=True, risk=None):
    s = d["summary"]; un = s["unrealized_r"]
    head = (f"<b>📊 Setup report</b>\n{SEP}\n"
            f"<b>Trades</b>  {s['trades']} entered of {s['n']} setups\n"
            f"✅ Wins (reached TP1): <b>{s['wins']}</b>\n🔴 Losses (stopped): <b>{s['losses']}</b>\n🟡 Open: <b>{s['open']}</b>   ⏳ Waiting: {s['waiting']}   ⚪ Expired: {s['expired']}" + (f"   🚫 Invalidated: {s['invalidated']}" if s.get("invalidated") else "") + "\n"
            f"🎯 TP1 hit trades: <b>{s['tp1_hit']}</b>   TP2 hits: {s['tp2']}" +
            (f"\n⚠️ <b>{s['no_data']} setup(s) have no price data right now</b> (Bybit did not answer): the numbers below leave them out. Send /status again in a minute." if s.get("no_data") else ""))
    net = ("<b>💰 Net result</b>\n" + pre([f"Exit all at TP1  {s['r_tp1_plan']:+.1f}R{usd(s['r_tp1_plan'], risk)}", f"  {breakdown(d['rows'], 'R1')}",
                                           f"Exit all at TP2  {s['r_tp2_plan']:+.1f}R{usd(s['r_tp2_plan'], risk)}", f"  {breakdown(d['rows'], 'R2')}",
                                           f"After fees TP1 {s['r_tp1_net']:+.1f}R{usd(s['r_tp1_net'], risk)}", f"After fees TP2 {s['r_tp2_net']:+.1f}R{usd(s['r_tp2_net'], risk)}",
                                           f"Unrealized open {un:+.1f}R{usd(un, risk)}"]) +
           f"\n<i>{s['decided1']} trades decided for the TP1 plan, {s['decided2']} for the TP2 plan" + (f" · risk ${risk:g} per trade" if risk else " · /risk 10 shows $") + "</i>")
    out = [head, net]
    if rows:
        out += [_section("✅ Winners (reached TP1)  TP1 plan / TP2 plan", [x for x in d["rows"] if x["status"] in ("TP2 HIT", "STOPPED AFTER TP1")]),
                _section("🔴 Stopped", [x for x in d["rows"] if x["status"] == "STOPPED"]),
                _section("🟡 Open now", [x for x in d["rows"] if x["status"] in ("ENTERED", "TP1 HIT")])]
    return "\n\n".join(b for b in out if b)


def _price(coin):
    cs = sbgz.fetch_candles(coin + "USDT", "1", 2, timeout=8)
    return cs[-1]["close"] if cs else None


def _away(x):
    return abs(x["dist_pct"]) if x.get("dist_pct") is not None else 9e9


def cmd_open(d, risk=None):
    rows = sorted((x for x in d["rows"] if x["status"] in ("ENTERED", "TP1 HIT")), key=lambda x: -(x.get("now_r") or 0))
    if not rows: return "<b>🟡 Open trades</b>\nNone right now."
    lines = []
    for x in rows:
        p = x.get("price"); tgt = x["tp1"] if x["status"] == "ENTERED" else (x.get("tp2") or x["tp1"]); nm = "TP1" if x["status"] == "ENTERED" else "TP2"
        lines.append(row(x) + (f"  SL {abs(p - x['stop']) / p * 100:.1f}% {nm} {abs(tgt - p) / p * 100:.1f}%" if p else ""))
    un = d["summary"]["unrealized_r"]
    return f"<b>🟡 Open trades ({len(rows)})</b>   unrealized <b>{un:+.1f}R</b>{esc(usd(un, risk))}\n" + pre(lines) + "\n<i>SL / TP = distance from the price now</i>"


def cmd_waiting(d, limit=None):
    rows = sorted((x for x in d["rows"] if x["status"] == "WAITING"), key=_away)
    if limit is not None: rows = [x for x in rows if _away(x) <= limit]
    title = f"⏳ Waiting within {limit}% of the entry" if limit is not None else "⏳ Waiting for entry, nearest first"
    if not rows: return f"<b>{title}</b>\nNone."
    return f"<b>{title} ({len(rows)})</b>\n" + pre([f"{x['coin']:<6}{x['side']:<6}entry {_fmt(x['entry'])}" + (f"  {_away(x):.2f}% away" if x.get("price") else "") for x in rows])


def cmd_coin(d, coin):
    coin = (coin or "").upper().replace("USDT", "")
    rows = [x for x in d["rows"] if x["coin"] == coin]
    if not coin: return "Use /coin BTC"
    if not rows: return f"{esc(coin)} is not in the tracked list."
    out = [f"<b>🪙 {esc(coin)}</b>" + (f"   price {_fmt(rows[0]['price'])}" if rows[0].get("price") else "")]
    for x in rows:
        lines = [f"Status  {x['status']}", f"Entry   {_fmt(x['entry'])}", f"Stop    {_fmt(x['stop'])}", f"TP1     {_fmt(x['tp1'])}" + (" ✓" if x.get("tp1_t") else "")]
        if x.get("tp2"): lines.append(f"TP2     {_fmt(x['tp2'])}" + (" ✓" if x.get("tp2_t") else ""))
        if x.get("now_r") is not None: lines.append(f"Now     {r1(x['now_r'])}")
        if x.get("best_r") is not None: lines.append(f"Best    {r1(x['best_r'])}")
        out.append(f"{ICON1.get(x['status'], '•')} <b>{x['side']}</b>\n" + pre(lines))
    return "\n\n".join(out)


def cmd_results(d, risk=None):
    s = d["summary"]
    done = [x for x in d["rows"] if x["status"] in ("TP2 HIT", "STOPPED", "STOPPED AFTER TP1")]
    head = (f"<b>🏁 Results</b>\n{SEP}\nFinished <b>{len(done)}</b> · wins <b>{sum(1 for x in done if x['tp1_t'])}</b> · stopped <b>{sum(1 for x in done if x['status'] == 'STOPPED')}</b> · expired {s['expired']}\n" +
            pre([f"Exit all at TP1  {s['r_tp1_plan']:+.1f}R{usd(s['r_tp1_plan'], risk)}", f"Exit all at TP2  {s['r_tp2_plan']:+.1f}R{usd(s['r_tp2_plan'], risk)}",
                 f"Still open      {s['open']} ({s['unrealized_r']:+.1f}R)"]))
    if not done: return head + "\n\nNothing has finished yet."
    return head + "\n\n" + "\n\n".join(b for b in (_section("✅ Winners  TP1 plan / TP2 plan", [x for x in done if x["tp1_t"]]), _section("🔴 Stopped", [x for x in done if x["status"] == "STOPPED"])) if b)


def cmd_price(d, coin):
    coin = (coin or "").upper().replace("USDT", "")
    if not coin: return "Use /price BTC"
    p = _price(coin)
    if p is None: return f"No price for {esc(coin)}USDT."
    lines = [f"{coin} {_fmt(p)}"]
    for x in (d["rows"] if d else []):
        if x["coin"] == coin and x["status"] in ("WAITING", "ENTERED", "TP1 HIT"):
            lines.append(f"{x['side']:<6}{x['status'].lower():<9}entry {_fmt(x['entry'])} ({abs(p - x['entry']) / p * 100:.2f}%)")
    return f"<b>💲 {esc(coin)}</b>\n" + pre(lines)


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
        lines = []
        for t in rows:
            c = t["symbol"].replace("USDT", "")
            if t["status"] == "resting": lines.append(f"⏳ {c:<6}{t['side']:<6}waiting " + (f"{t['dist_pct']:.2f}% away" if t.get("dist_pct") is not None else f"@ {_fmt(t['entry'])}"))
            else: lines.append(f"🟡 {c:<6}{t['side']:<6}open " + (r1(t["r_now"]) if t.get("r_now") is not None else "") + (f" ({t['pnl_usd']:+.2f} USDT)" if t.get("pnl_usd") is not None else ""))
        out.append(f"<b>{name}</b> ({esc((d.get('settings') or {}).get('mode', '?'))}) · {len(rows)} active" + ("\n" + pre(lines) if lines else ""))
    return "<b>🤖 Demo auto-orders</b>\n" + "\n\n".join(out) if out else "The auto-order status is not available right now."


def cmd_pnl(client_fn, now=None):
    now = now or int(time.time() * 1000); day0 = now // 86_400_000 * 86_400_000; out = []; st = _auto_rows(client_fn)
    for name, d in st.items():
        done = [t for t in d.get("tracked") or [] if not t.get("virtual") and t["status"] == "done" and t.get("R") is not None and t.get("done")]
        def agg(sel): return f"{len(sel)} closed  {sum(1 for t in sel if t['R'] > 0)}W {sum(1 for t in sel if t['R'] <= 0)}L  {sum(t['R'] for t in sel):+.2f}R"
        out.append(f"<b>{name}</b>\n" + pre([f"Today    {agg([t for t in done if t['done'] >= day0])}", f"7 days   {agg([t for t in done if t['done'] >= now - 7 * 86_400_000])}"]))
    return "<b>💵 Auto-orders result</b>\n" + "\n\n".join(out) if out else "The auto-order status is not available right now."


COMMANDS = [("status", "Report: trades, wins, losses, net R, unrealized R, TP1 hits"), ("open", "Open trades with live R"), ("waiting", "Setups waiting for entry, nearest first"),
            ("near", "Setups within 0.3% of their entry"), ("coin", "One coin: /coin BTC"), ("results", "Finished setups and R"), ("net", "Net R and $ per plan: /net 2 = $2 risk per trade"), ("price", "Price + your levels: /price BTC"),
            ("trades", "Demo auto-orders now (SBGZ, AMD)"), ("pnl", "Auto-orders result today and 7 days"), ("quiet", "Mute updates for N hours: /quiet 3"),
            ("digest", "Daily summary: /digest 20 or /digest off"), ("tz", "Your UTC offset: /tz +5.5"), ("risk", "Risk per trade in $ (orders + net $): /risk 2"),
            ("orders", "Demo orders for pasted lists: /orders on | off | cancel"), ("expiry", "Hours a setup may wait: /expiry 12"),
            ("clear", "Clear the tracked list"), ("stop", "Mute all updates"), ("help", "All commands")]


def help_text():
    return ("<b>🤖 Commands</b>\n" + "\n".join(f"/{c} – {esc(t)}" for c, t in COMMANDS) +
            "\n\n<i>Paste the setups CSV (text or a .csv file) to start tracking a new list.</i>")


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
        if p["act"] == "clear":
            setup_tracker.clear(); sbgz_auto._write_key(SENT_KEY, None)
            n = setup_orders.cancel_all(client_fn(), "the list was cleared") if client_fn else 0
            reply(cid, "The tracked list is cleared." + (f" {n} waiting order(s) cancelled." if n else ""))
        elif p["act"] == "expiry":
            r = setup_tracker.set_expiry(p["val"]); reply(cid, f"A setup now waits up to {p['val']:g} hours for its entry." if r.get("ok") else "Could not change it: " + str(r.get("error")))
        return
    if cmd in ("price", "help", "start", "trades", "pnl", "stop", "quiet", "digest", "tz", "clear", "expiry", "risk", "orders"):
        pass
    elif cmd in ("status", "open", "waiting", "near", "coin", "results", "net"):
        d = tracked()
        if d is None: reply(cid, "No setups are being tracked. Paste a list here or on the dashboard."); return
        risk = doc.get("risk_usd")
        reply(cid, {"status": lambda: summary(d, risk=risk), "open": lambda: cmd_open(d, risk), "waiting": lambda: cmd_waiting(d), "near": lambda: cmd_waiting(d, 0.3),
                    "coin": lambda: cmd_coin(d, args[0] if args else ""), "results": lambda: cmd_results(d, risk), "net": lambda: cmd_net(d, _num(args[0], 0.01, 1_000_000) if args and _num(args[0], 0.01, 1_000_000) else risk)}[cmd]()); return
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
    if cmd == "orders":
        a = (args[0].lower() if args else "")
        if a in ("on", "off"):
            doc["orders_on"] = a == "on"
            reply(cid, "🧾 Orders are <b>on</b>: the next list you paste is placed as demo orders (risk $%g per trade)." % (doc.get("risk_usd") or setup_orders.DEFAULT_RISK) if a == "on"
                  else "🧾 Orders are <b>off</b>: pasted lists are only tracked. Orders already placed stay."); return
        if a == "cancel":
            n = setup_orders.cancel_all(client_fn()) if client_fn else 0
            reply(cid, f"{n} waiting order(s) cancelled. Open trades keep their stop and targets."); return
        s_ = setup_orders.summary()
        if not s_.get("ok") or not s_.get("orders"):
            reply(cid, f"🧾 Orders: <b>{'on' if doc.get('orders_on', True) else 'off'}</b>. None placed yet. Paste a list to place them."); return
        lines = [f"Waiting   {s_['waiting']}", f"Filled    {s_['filled']}", f"Finished  {s_['done']}", f"Cancelled {s_['cancelled']}"]
        reply(cid, f"🧾 <b>Demo orders</b> ({'on' if doc.get('orders_on', True) else 'off'}, ${s_['risk']:g} per trade)\n" + pre(lines) +
              (f"\nSkipped at placement: {len(s_['skipped'])}" if s_["skipped"] else "")); return
    if cmd == "risk":
        if args and args[0].lower() == "off": doc.pop("risk_usd", None); reply(cid, "Dollar amounts are off."); return
        v = _num(args[0] if args else None, 0.01, 1_000_000)
        if v is None: reply(cid, "Use /risk 10 (dollars risked per trade, so 1R = $10) or /risk off"); return
        doc["risk_usd"] = v; reply(cid, f"1R = ${v:g}. Net results now also show dollars (/status, /open, /results)."); return
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
    if not (send_fn or send)(chat["id"], "<b>🗓 Daily summary</b>\n" + summary(d, risk=chat.get("risk_usd"))): return 0
    chat["digest"]["last"] = today; _save_chat(chat)
    return 1


def check(send_fn=None, now=None, client_fn=None):
    """Judge the pasted setups and send what changed. Returns the number of messages sent (0 when nothing changed, nobody is registered, or it is muted / quiet)."""
    chat = _chat(); now_ms = now or int(time.time() * 1000)
    if not chat or chat.get("muted") or (chat.get("quiet_until") or 0) > now_ms: return 0
    d = setup_tracker.status(now)
    if not d.get("ok") or d.get("empty"): return 0
    sent, ok = sbgz_auto._read_key(SENT_KEY)
    if not ok: return 0
    if not sent or sent.get("created") != d["created"]: sent = dict(created=d["created"], sent={})
    changed = [x for x in d["rows"] if x["status"] not in ("WAITING", "NO DATA") and sent["sent"].get(f"{x['coin']}|{x['side']}") != x["status"]]
    if not changed: return 0
    if client_fn:                                                       # an invalidated setup: its orders are no longer wanted
        for x in changed:
            if x["status"] == "INVALIDATED":
                try: setup_orders.cancel_setup(client_fn(), x["coin"], x["side"], "invalidated")
                except Exception as e: print(f"[telegram] cancelling the orders of an invalidated setup failed: {type(e).__name__}")
    by = {}
    for x in changed: by.setdefault(x["status"], []).append(x)
    names = [("ENTERED", "🟡 Entered"), ("TP1 HIT", "🟢 TP1 reached"), ("TP2 HIT", "✅ TP2 reached"), ("STOPPED", "🔴 Stopped"), ("STOPPED AFTER TP1", "🟠 Stopped after TP1"), ("EXPIRED", "⚪ Expired"), ("INVALIDATED", "🚫 Invalidated (price went past the invalidation level before the entry)")]
    left = sum(1 for x in d["rows"] if x["status"] not in FINAL); sm = d["summary"]
    blocks = ["<b>🔔 Setup update</b>"] + [_section(t, by[k]) for k, t in names if k in by]
    blocks.append(f"⏳ {left} still waiting or open · net so far: TP1 plan <b>{sm['r_tp1_plan']:+.1f}R</b> · TP2 plan <b>{sm['r_tp2_plan']:+.1f}R</b>")
    if not left: blocks.append(summary(d, rows=False, risk=chat.get("risk_usd")))
    text = "\n\n".join(b for b in blocks if b)
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
    if _pieces and not got_csv and doc is not None: _save_pieces(doc["id"], reply, doc, client_fn)           # nothing more arrived since the last poll: the list is complete
    if doc is not None and doc != chat: _save_chat(doc)
    return n


def manage_orders(get_client):
    """About once a minute: the order partner / expiry pass of backend_lib/setup_orders.py; what happened goes to the chat."""
    try:
        ev = setup_orders.manage(get_client())
    except Exception as e:
        print(f"[telegram] order management failed: {type(e).__name__}: {e}"); return
    chat = _chat()
    if ev and chat and not chat.get("muted") and (chat.get("quiet_until") or 0) <= time.time() * 1000: send(chat["id"], "<b>🧾 Order update</b>\n" + "\n".join(esc(e) for e in ev))


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
                    if time.time() - last >= CHECK_EVERY: last = time.time(); check(client_fn=get_client); digest()
                    if get_client: manage_orders(get_client)
            except Exception as e:
                print(f"[telegram] loop: {type(e).__name__}: {e}")
            _stop.wait(POLL_EVERY)

    _thread = threading.Thread(target=loop, name="telegram-notify", daemon=True)
    _thread.start()
    return _thread
