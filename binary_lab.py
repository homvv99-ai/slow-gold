import json, os, sys, time, datetime, bisect
import websocket

MODE = open("lab_mode.txt", encoding="utf-8").read().strip().lower() or "demo"
STAMP = "[MODE: %s]" % MODE.upper()
CFG = json.load(open("lab_config.json", encoding="utf-8"))
TOKEN = os.environ.get("DERIV_TOKEN", "")
WS_URL = "wss://ws.derivws.com/websockets/v3?app_id=1089"
OUT = "site/data"
ACTION = sys.argv[1] if len(sys.argv) > 1 else "probe"

def log(*a):
    print(STAMP, *a, flush=True)

def connect():
    return websocket.create_connection(WS_URL, timeout=30)

def call(w, payload):
    w.send(json.dumps(payload))
    return json.loads(w.recv())

def authorize(w):
    r = call(w, {"authorize": TOKEN})
    if "error" in r:
        raise SystemExit(STAMP + " AUTH FAIL " + json.dumps(r["error"]))
    a = r["authorize"]
    return a["loginid"], float(a["balance"]), a["currency"]

def candles(w, sym, gran, need):
    out = {}
    end = "latest"
    while len(out) < need:
        r = call(w, {"ticks_history": sym, "adjust_start_time": 1, "count": 5000,
                     "end": end, "granularity": gran, "style": "candles"})
        if "error" in r:
            raise SystemExit(STAMP + " CANDLES FAIL " + json.dumps(r["error"]))
        cs = r.get("candles", [])
        if not cs:
            break
        for c in cs:
            out[c["epoch"]] = c
        end = str(cs[0]["epoch"] - 1)
        if len(cs) < 5:
            break
        time.sleep(0.25)
    return [out[k] for k in sorted(out)]

def ema(v, n):
    k = 2.0 / (n + 1)
    e = None
    o = []
    for x in v:
        e = x if e is None else x * k + e * (1 - k)
        o.append(e)
    return o

def atr(cs, n=14):
    t = []
    for i, c in enumerate(cs):
        if i == 0:
            t.append(c["high"] - c["low"])
        else:
            p = cs[i - 1]["close"]
            t.append(max(c["high"] - c["low"], abs(c["high"] - p), abs(c["low"] - p)))
    return ema(t, n)

def rsi(v, n=7):
    g = [0.0]
    l = [0.0]
    for i in range(1, len(v)):
        d = v[i] - v[i - 1]
        g.append(max(d, 0.0))
        l.append(max(-d, 0.0))
    ag = ema(g, n)
    al = ema(l, n)
    out = []
    for a, b in zip(ag, al):
        out.append(100.0 - 100.0 / (1.0 + (a / b if b > 1e-12 else 100.0)))
    return out

def swings(cs, w=2):
    hi = []
    lo = []
    for i in range(w, len(cs) - w):
        if all(cs[i]["high"] >= cs[j]["high"] for j in range(i - w, i + w + 1)):
            hi.append((cs[i]["epoch"] + 900, cs[i]["high"]))
        if all(cs[i]["low"] <= cs[j]["low"] for j in range(i - w, i + w + 1)):
            lo.append((cs[i]["epoch"] + 900, cs[i]["low"]))
    return hi, lo

def build(cs5, cs15, cs60):
    m = {}
    m["e20_5"] = ema([c["close"] for c in cs5], 20)
    m["e50_15"] = ema([c["close"] for c in cs15], 50)
    m["ep15"] = [c["epoch"] + 900 for c in cs15]
    m["e20_60"] = ema([c["close"] for c in cs60], 20)
    m["e50_60"] = ema([c["close"] for c in cs60], 50)
    m["atr60"] = atr(cs60)
    m["ep60c"] = [c["epoch"] + 3600 for c in cs60]
    m["atr5"] = atr(cs5)
    m["rsi5"] = rsi([c["close"] for c in cs5], 7)
    hi, lo = swings(cs15, 2)
    m["sw_hi"] = hi
    m["sw_lo"] = lo
    return m

def bias_at(m, t):
    j = bisect.bisect_right(m["ep60c"], t) - 1
    if j < 30:
        return 0, False
    seg = m["atr60"][max(0, j - 20):j]
    gate = m["atr60"][j] > (sum(seg) / len(seg)) if seg else True
    if not gate:
        return 0, False
    return (1 if m["e20_60"][j] > m["e50_60"][j] else -1), True

def level_touch(m, t, c, a5, side):
    src = m["sw_lo"] if side == 1 else m["sw_hi"]
    best = None
    for ep, px in src[-200:]:
        if ep > t:
            continue
        if side == 1 and (c["low"] <= px <= c["high"] or abs(c["low"] - px) <= 0.3 * a5):
            best = (ep, px)
        if side == -1 and (c["low"] <= px <= c["high"] or abs(c["high"] - px) <= 0.3 * a5):
            best = (ep, px)
    if best is None:
        j = bisect.bisect_right(m["ep15"], t) - 1
        if j >= 0:
            px = m["e50_15"][j]
            if side == 1 and (c["low"] <= px <= c["high"] or abs(c["low"] - px) <= 0.3 * a5):
                best = (m["ep15"][j], px)
            if side == -1 and (c["low"] <= px <= c["high"] or abs(c["high"] - px) <= 0.3 * a5):
                best = (m["ep15"][j], px)
    return best

def rej(cs, i, e20, side):
    c = cs[i]
    p = cs[i - 1]
    body = c["close"] - c["open"]
    pbody = p["close"] - p["open"]
    rng = c["high"] - c["low"]
    if side == 1:
        eng = body > 0 and pbody < 0 and c["close"] >= p["high"] and c["open"] <= p["close"]
        pin = rng > 0 and (min(c["open"], c["close"]) - c["low"]) >= 2 * abs(body) and (c["close"] - c["low"]) >= 0.66 * rng
        rec = c["close"] > e20[i] and cs[i - 1]["close"] < e20[i - 1] and cs[i - 2]["close"] < e20[i - 2]
        return eng or pin or rec
    eng = body < 0 and pbody > 0 and c["close"] <= p["low"] and c["open"] >= p["close"]
    pin = rng > 0 and (c["high"] - max(c["open"], c["close"])) >= 2 * abs(body) and (c["high"] - c["close"]) >= 0.66 * rng
    rec = c["close"] < e20[i] and cs[i - 1]["close"] > e20[i - 1] and cs[i - 2]["close"] > e20[i - 2]
    return eng or pin or rec

def signal(m, cs5, i, t, variant):
    c = cs5[i]
    a5 = m["atr5"][i]
    b, gate = bias_at(m, t)
    if variant == "V3":
        if not gate:
            return 0
        r = m["rsi5"][i]
        if r < 15:
            return 1
        if r > 85:
            return -1
        return 0
    if not gate or b == 0:
        return 0
    if variant == "V1":
        if b == 1 and level_touch(m, t, c, a5, 1) is not None and rej(cs5, i, m["e20_5"], 1):
            return 1
        if b == -1 and level_touch(m, t, c, a5, -1) is not None and rej(cs5, i, m["e20_5"], -1):
            return -1
        return 0
    if variant == "V2":
        if b == 1:
            sh = None
            for ep, px in m["sw_hi"][-200:]:
                if ep <= t:
                    sh = px
            if sh and cs5[i - 1]["close"] > sh and c["low"] <= sh + 0.3 * a5 and c["close"] > sh and rej(cs5, i, m["e20_5"], 1):
                return 1
        if b == -1:
            sl = None
            for ep, px in m["sw_lo"][-200:]:
                if ep <= t:
                    sl = px
            if sl and cs5[i - 1]["close"] < sl and c["high"] >= sl - 0.3 * a5 and c["close"] < sl and rej(cs5, i, m["e20_5"], -1):
                return -1
    return 0

def run_cell(cs1, cs5, m, variant, dur, payout):
    eq = 100.0
    wins = 0
    n = 0
    gw = 0.0
    gl = 0.0
    evs = []
    curve = []
    peak = eq
    maxdd = 0.0
    day = None
    day_n = 0
    cons = 0
    m1c = [c["epoch"] + 60 for c in cs1]
    for i in range(60, len(cs5) - 1):
        t = cs5[i]["epoch"] + 300
        d = datetime.datetime.utcfromtimestamp(t).date()
        if d != day:
            day = d
            day_n = 0
            cons = 0
        if day_n >= CFG["max_trades_per_day"] or cons >= CFG["daily_loss_stop"]:
            continue
        s = signal(m, cs5, i, t, variant)
        if s == 0:
            continue
        ep = t + dur
        j = bisect.bisect_left(m1c, ep)
        if j >= len(cs1):
            break
        exit_px = cs1[j]["close"]
        entry_px = cs5[i]["close"]
        win = (exit_px > entry_px) if s == 1 else (exit_px < entry_px)
        stake = eq * CFG["stake_pct"] / 100.0
        pnl = stake * payout if win else -stake
        eq += pnl
        n += 1
        day_n += 1
        if win:
            wins += 1
            gw += pnl
            cons = 0
        else:
            gl += -pnl
            cons += 1
        evs.append(pnl / stake)
        peak = max(peak, eq)
        maxdd = max(maxdd, (peak - eq) / peak * 100.0)
        if n % 10 == 0:
            curve.append([n, round(eq, 2)])
    win_pct = wins * 100.0 / n if n else 0.0
    ev_pct = sum(evs) * 100.0 / len(evs) if evs else 0.0
    pf = (gw / gl) if gl > 1e-9 else 999.0
    g = CFG["gate"]
    margin = win_pct - 100.0 / (1.0 + payout)
    if n < g["min_trades"]:
        verdict = "PENDING_DATA"
    elif ev_pct >= g["ev_pct"] and margin >= g["margin_pts"] and maxdd <= g["max_dd_pct"]:
        verdict = "PASS"
    else:
        verdict = "FAIL"
    return {"variant": variant, "expiry": dur, "n": n, "wins": wins,
            "win_pct": round(win_pct, 2), "ev_pct": round(ev_pct, 2),
            "pf": round(pf, 2), "max_dd_pct": round(maxdd, 2),
            "margin_pts": round(margin, 2), "verdict": verdict, "curve": curve}

def probe():
    w = connect()
    cs = candles(w, "R_75", 300, 10)
    log("candles", len(cs), "first", cs[0]["epoch"], "last", cs[-1]["epoch"], "close", cs[-1]["close"])
    if TOKEN:
        lid, bal, cur = authorize(w)
        log("auth ok", lid, bal, cur)
    else:
        log("no token in env")
    w.close()

def backtest():
    days = CFG.get("backtest_days", 90)
    cells = []
    w = connect()
    for sym in CFG["symbols"]:
        log("fetch", sym)
        cs1 = candles(w, sym, 60, days * 1440)
        cs5 = candles(w, sym, 300, days * 288)
        cs15 = candles(w, sym, 900, days * 96)
        cs60 = candles(w, sym, 3600, days * 24)
        m = build(cs5, cs15, cs60)
        for var in CFG["variants"]:
            for dur in CFG["expiries"]:
                cell = run_cell(cs1, cs5, m, var, dur, CFG["payout_assumed"])
                cell["symbol"] = sym
                cells.append(cell)
                log(sym, var, dur, "n=", cell["n"], "win%=", cell["win_pct"], "ev%=", cell["ev_pct"], cell["verdict"])
    w.close()
    json.dump(cells, open(OUT + "/lab_cells.json", "w", encoding="utf-8"), ensure_ascii=False)
    stats = {"generated_at": datetime.datetime.utcnow().isoformat() + "Z",
             "mode": MODE, "action": "backtest", "cells": len(cells),
             "trades": sum(c["n"] for c in cells),
             "pass_n": sum(1 for c in cells if c["verdict"] == "PASS"),
             "fail_n": sum(1 for c in cells if c["verdict"] == "FAIL"),
             "pending_n": sum(1 for c in cells if c["verdict"] == "PENDING_DATA")}
    json.dump(stats, open(OUT + "/lab_stats.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log("backtest done", stats)

def live():
    led = {"pending": [], "settled": []}
    try:
        led = json.load(open(OUT + "/lab_trades.json", encoding="utf-8"))
    except Exception:
        pass
    w = connect()
    lid, bal, cur = authorize(w)
    want = CFG.get("trade_loginid", "")
    if want and want != lid:
        r = call(w, {"login": want})
        if "error" in r:
            raise SystemExit(STAMP + " LOGIN SWITCH FAIL " + json.dumps(r["error"]))
        lid, bal, cur = authorize(w)
    if MODE == "demo" and not lid.startswith("VR"):
        raise SystemExit(STAMP + " REFUSED mode demo but account " + lid)
    if MODE == "real" and lid.startswith("VR"):
        raise SystemExit(STAMP + " REFUSED mode real but account " + lid)
    log("live on", lid, "balance", bal, cur)
    keep = []
    for p in led["pending"]:
        r = call(w, {"proposal_open_contract": 1, "contract_id": p["contract_id"]})
        poc = r.get("proposal_open_contract", {})
        if poc.get("is_sold"):
            p["profit"] = float(poc.get("profit", 0.0))
            p["settled_at"] = int(time.time())
            led["settled"].append(p)
            log("settled", p["contract_id"], p["profit"])
        else:
            keep.append(p)
    led["pending"] = keep
    day = datetime.datetime.utcnow().date()
    today_n = sum(1 for x in led["settled"] + led["pending"]
                  if datetime.datetime.utcfromtimestamp(x["entry_epoch"]).date() == day)
    for sym in CFG["symbols"]:
        cs5 = candles(w, sym, 300, 500)[:-1]
        cs15 = candles(w, sym, 900, 300)
        cs60 = candles(w, sym, 3600, 300)
        m = build(cs5, cs15, cs60)
        i = len(cs5) - 1
        t = cs5[i]["epoch"] + 300
        for var in CFG["variants"]:
            dur = CFG["expiries"][1]
            if any(p["symbol"] == sym and p["variant"] == var for p in led["pending"]):
                continue
            if today_n >= CFG["max_trades_per_day"]:
                continue
            s = signal(m, cs5, i, t, var)
            if s == 0:
                continue
            stake = round(bal * CFG["stake_pct"] / 100.0, 2)
            if stake < 0.35:
                stake = 0.35
            pr = call(w, {"proposal": 1, "amount": stake, "basis": "stake",
                          "contract_type": "CALL" if s == 1 else "PUT",
                          "currency": cur, "duration": dur, "duration_unit": "s",
                          "symbol": sym})
            if "error" in pr:
                log("proposal fail", sym, var, json.dumps(pr["error"]))
                continue
            p = pr["proposal"]
            br = call(w, {"buy": p["id"], "price": p["ask_price"]})
            if "error" in br:
                log("buy fail", sym, var, json.dumps(br["error"]))
                continue
            now_e = int(time.time())
            led["pending"].append({"contract_id": br["buy"]["contract_id"],
                                   "symbol": sym, "variant": var, "side": s,
                                   "entry_epoch": now_e, "expiry_epoch": now_e + dur,
                                   "stake": stake, "payout_pct": float(p["payout"]),
                                   "mode": MODE})
            today_n += 1
            log("bought", sym, var, s, dur, "payout", p["payout"])
    json.dump(led, open(OUT + "/lab_trades.json", "w", encoding="utf-8"), ensure_ascii=False)
    st = {"generated_at": datetime.datetime.utcnow().isoformat() + "Z",
          "mode": MODE, "action": "live", "loginid": lid,
          "pending": len(led["pending"]), "settled": len(led["settled"]),
          "wins": sum(1 for x in led["settled"] if x["profit"] > 0),
          "pnl": round(sum(x["profit"] for x in led["settled"]), 2)}
    json.dump(st, open(OUT + "/lab_live.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    w.close()
    log("live pass done", st)

if ACTION == "probe":
    probe()
elif ACTION == "backtest":
    backtest()
elif ACTION == "live":
    live()
else:
    log("unknown action", ACTION)
