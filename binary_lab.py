import json, os, sys, time, datetime, bisect, math
import websocket
import requests

MODE = open("lab_mode.txt", encoding="utf-8").read().strip().lower() or "demo"
STAMP = "[MODE: %s]" % MODE.upper()
CFG = json.load(open("lab_config.json", encoding="utf-8"))
TOKEN = os.environ.get("DERIV_TOKEN", "")
WS_URL = "wss://ws.derivws.com/websockets/v3?app_id=1089"
OUT = "site/data"
ACTION = sys.argv[1] if len(sys.argv) > 1 else "probe"
APP_ID = CFG.get("app_id", "1089")
API_BASE = "https://api.derivws.com"

def log(*a):
    print(STAMP, *a, flush=True)

def connect():
    return websocket.create_connection(WS_URL, timeout=30)

class Link:
    def __init__(self):
        self.w = None
    def get(self):
        if self.w is None:
            self.w = connect()
        return self.w
    def reset(self):
        try:
            if self.w is not None:
                self.w.close()
        except Exception:
            pass
        self.w = connect()
    def close(self):
        try:
            if self.w is not None:
                self.w.close()
        except Exception:
            pass
        self.w = None
    def call(self, payload):
        last = None
        r = None
        for attempt in range(3):
            try:
                w = self.get()
                w.send(json.dumps(payload))
                r = json.loads(w.recv())
            except Exception as e:
                last = e
                self.reset()
                time.sleep(2)
                continue
            if isinstance(r, dict) and r.get("error", {}).get("code", "") == "RateLimit":
                log("rate-wait", attempt)
                time.sleep(20)
                continue
            return r
        if last is not None:
            raise last
        return r

def rest(method, path):
    h = {"Authorization": "Bearer " + TOKEN, "Deriv-App-ID": APP_ID}
    return requests.request(method, API_BASE + path, headers=h, timeout=30)

def find_ws_url(j):
    if isinstance(j, dict):
        for k, v in j.items():
            if isinstance(v, str) and v.startswith("wss://"):
                return v
            u = find_ws_url(v)
            if u:
                return u
    if isinstance(j, list):
        for v in j:
            u = find_ws_url(v)
            if u:
                return u
    return None

def authorize(link):
    r = link.call({"authorize": TOKEN})
    if "error" in r:
        raise SystemExit(STAMP + " AUTH FAIL " + json.dumps(r["error"]))
    a = r["authorize"]
    return a["loginid"], float(a["balance"]), a["currency"]

def candles(link, sym, gran, need):
    out = {}
    end = "latest"
    while len(out) < need:
        r = link.call({"ticks_history": sym, "adjust_start_time": 1, "count": 5000,
                       "end": end, "granularity": gran, "style": "candles"})
        if "error" in r:
            raise SystemExit(STAMP + " CANDLES FAIL " + sym + " " + json.dumps(r["error"]))
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

def signal_v4(cs1, i):
    if i < 5:
        return 0
    closes = [cs1[j]["close"] for j in range(i - 5, i)]
    dirs = []
    for k in range(1, 5):
        dirs.append(1 if closes[k] > closes[k - 1] else -1)
    if dirs == [-1, -1, -1, -1] and cs1[i]["close"] > cs1[i - 1]["close"]:
        return 1
    if dirs == [1, 1, 1, 1] and cs1[i]["close"] < cs1[i - 1]["close"]:
        return -1
    return 0

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

def run_cell_m5(cs1, cs5, m, variant, dur, payout):
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
    skipped = 0
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
        if ep < m1c[0]:
            skipped += 1
            continue
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
    return eq, wins, n, gw, gl, evs, curve, peak, maxdd, skipped

def run_cell_m1(cs1, variant, dur, payout):
    eq = 100.0
    wins = 0
    n = 0
    gw = 0.0
    gl = 0.0
    evs = []
    curve = []
    peak = eq
    maxdd = 0.0
    for i in range(5, len(cs1) - 5):
        s = signal_v4(cs1, i)
        if s == 0:
            continue
        entry_px = cs1[i]["close"]
        offset = max(1, dur // 60)
        if i + offset >= len(cs1):
            break
        exit_px = cs1[i + offset]["close"]
        win = (exit_px > entry_px) if s == 1 else (exit_px < entry_px)
        stake = eq * CFG["stake_pct"] / 100.0
        pnl = stake * payout if win else -stake
        eq += pnl
        n += 1
        if win:
            wins += 1
            gw += pnl
        else:
            gl += -pnl
        evs.append(pnl / stake)
        peak = max(peak, eq)
        maxdd = max(maxdd, (peak - eq) / peak * 100.0)
        if n % 50 == 0:
            curve.append([n, round(eq, 2)])
    return eq, wins, n, gw, gl, evs, curve, peak, maxdd, 0

def summarize(eq, wins, n, gw, gl, evs, curve, peak, maxdd, skipped, variant, dur, payout):
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
            "margin_pts": round(margin, 2), "verdict": verdict,
            "skipped": skipped, "curve": curve}

def classify_asset(sym):
    if sym.startswith(("BOOM", "CRASH")):
        return "spike"
    if sym.startswith("JD"):
        return "jump"
    if sym.startswith(("frx", "cry")):
        return "market"
    return "synth"

def add_hint(res, probe, key, tot, hit, base_pct, min_n, min_delta):
    if tot >= min_n:
        p = hit * 100.0 / tot
        d = p - base_pct
        if abs(d) >= min_delta:
            res["hints"].append({"probe": probe, "key": key, "n": tot,
                                  "p": round(p, 2), "base": round(base_pct, 2),
                                  "delta": round(d, 2)})
        return round(p, 2)
    return None

def explore_asset(link, sym):
    try:
        cs = candles(link, sym, 60, CFG.get("explore_days", 365) * 1440)
    except SystemExit:
        log("SKIP", sym, "fetch error")
        return None
    n = len(cs) if cs else 0
    if n < 5000:
        log("SKIP", sym, "short depth", n)
        return None
    depth = round((cs[-1]["epoch"] - cs[0]["epoch"]) / 86400.0, 1)
    cls = classify_asset(sym)
    closes = [c["close"] for c in cs]
    ranges = [c["high"] - c["low"] for c in cs]
    min_n = CFG.get("hint_min_n", 300)
    min_delta = CFG.get("hint_min_delta", 2.0)
    res = {"symbol": sym, "class": cls, "depth_days": depth, "n_candles": n, "hints": []}
    ema_arr = [0.0] * n
    is_jump = [False] * n
    ema_r = ranges[0]
    ema_arr[0] = ema_r
    for i in range(1, n):
        if ema_r > 0 and ranges[i] > 8.0 * ema_r:
            is_jump[i] = True
        else:
            ema_r = ranges[i] * 0.02 + ema_r * 0.98
        ema_arr[i] = ema_r
    up3 = sum(1 for i in range(n - 3) if closes[i + 3] > closes[i])
    base3 = up3 * 100.0 / (n - 3)
    down3 = 100.0 - base3
    res["base_p_up3"] = round(base3, 2)
    if cls in ("spike", "jump"):
        nj = sum(is_jump)
        res["n_jumps"] = nj
        res["jump_pct"] = round(nj * 100.0 / n, 2)
        runs = []
        q = 0
        for i in range(n):
            if is_jump[i]:
                runs.append(q)
                q = 0
            else:
                q += 1
        runs.sort()
        if runs:
            res["quiet_p50"] = runs[int(0.5 * (len(runs) - 1))]
            res["quiet_p90"] = runs[int(0.9 * (len(runs) - 1))]
        K = 5
        buckets = {"0-4": [0, 0], "5-9": [0, 0], "10-14": [0, 0], "15-19": [0, 0], "20+": [0, 0]}
        q = 0
        for i in range(n):
            if is_jump[i]:
                q = 0
                continue
            q += 1
            if i + K < n:
                key = "0-4" if q < 5 else "5-9" if q < 10 else "10-14" if q < 15 else "15-19" if q < 20 else "20+"
                buckets[key][0] += 1
                if any(is_jump[j] for j in range(i + 1, i + 1 + K)):
                    buckets[key][1] += 1
        base = nj * 1.0 / n
        baseK = (1 - (1 - base) ** K) * 100.0
        res["base_p_jump_in5"] = round(baseK, 2)
        cond = {}
        for k in ("0-4", "5-9", "10-14", "15-19", "20+"):
            tot, hit = buckets[k]
            p = add_hint(res, "P1", "quiet" + k, tot, hit, baseK, min_n, min_delta)
            if p is not None:
                cond[k] = [tot, p]
        res["conditional"] = cond
        if cls == "jump":
            dirs = []
            for i in range(1, n):
                if is_jump[i]:
                    dirs.append(1 if cs[i]["close"] >= cs[i]["open"] else -1)
            if len(dirs) > 1:
                same = sum(1 for a, b in zip(dirs, dirs[1:]) if a == b)
                res["jump_dir_same_pct"] = round(same * 100.0 / (len(dirs) - 1), 2)
    else:
        out = {}
        for h in (2, 3, 5):
            up = sum(1 for i in range(n - h) if closes[i + h] > closes[i])
            out["h%d_p_up" % h] = round(up * 100.0 / (n - h), 2)
        def p_after(pattern):
            tot = 0
            hit = 0
            for i in range(5, n - 2):
                ok = True
                for k in range(1, 5):
                    d = closes[i - k] > closes[i - k - 1]
                    if pattern == "down4" and d:
                        ok = False
                        break
                    if pattern == "up4" and not d:
                        ok = False
                        break
                if not ok:
                    continue
                if pattern == "down4":
                    if not closes[i] > closes[i - 1]:
                        continue
                    tot += 1
                    if closes[i + 2] > closes[i]:
                        hit += 1
                else:
                    if not closes[i] < closes[i - 1]:
                        continue
                    tot += 1
                    if closes[i + 2] < closes[i]:
                        hit += 1
            return [tot, round(hit * 100.0 / tot, 2)] if tot >= 100 else [tot, None]
        out["after_down4_rev_p_up2"] = p_after("down4")
        out["after_up4_rev_p_down2"] = p_after("up4")
        hours = {}
        for i in range(n - 3):
            hb = (datetime.datetime.utcfromtimestamp(cs[i]["epoch"]).hour // 4) * 4
            a, b = hours.get(hb, [0, 0])
            hours[hb] = [a + 1, b + (1 if closes[i + 3] > closes[i] else 0)]
        out["hour_p_up_h3"] = {str(k): round(v[1] * 100.0 / v[0], 2) for k, v in sorted(hours.items()) if v[0] >= 500}
        res["continuous"] = out
        for k, v in hours.items():
            if v[0] >= 500:
                add_hint(res, "P4", "hour%02d" % k, v[0], v[1], base3, min_n, min_delta)
    med = sorted(closes)[n // 2]
    m = 10.0 ** math.floor(math.log10(med)) if med > 0 else 1.0
    steps = (m / 10.0, m)
    rnd = {"above": [0, 0], "below": [0, 0]}
    for i in range(n - 3):
        c = closes[i]
        if ema_arr[i] <= 0:
            continue
        bestd = 1e18
        bestL = c
        for s in steps:
            L = round(c / s) * s
            d = abs(c - L)
            if d < bestd:
                bestd = d
                bestL = L
        if bestd <= 0.1 * ema_arr[i]:
            if c >= bestL:
                rnd["above"][0] += 1
                if closes[i + 3] > c:
                    rnd["above"][1] += 1
            else:
                rnd["below"][0] += 1
                if closes[i + 3] < c:
                    rnd["below"][1] += 1
    pa = add_hint(res, "P5", "above_round", rnd["above"][0], rnd["above"][1], base3, min_n, min_delta)
    pb = add_hint(res, "P5", "below_round", rnd["below"][0], rnd["below"][1], down3, min_n, min_delta)
    res["round"] = {"above_tot": rnd["above"][0], "above_p": pa,
                    "below_tot": rnd["below"][0], "below_p": pb}
    an = {"up3": [0, 0], "down3": [0, 0], "lwick": [0, 0], "uwick": [0, 0]}
    cons = 1
    for i in range(1, n - 3):
        upc = cs[i]["close"] > cs[i]["open"]
        pup = cs[i - 1]["close"] > cs[i - 1]["open"]
        cons = cons + 1 if upc == pup else 1
        rng = ranges[i]
        body = abs(cs[i]["close"] - cs[i]["open"])
        if cons >= 3 and upc:
            an["up3"][0] += 1
            if closes[i + 3] > closes[i]:
                an["up3"][1] += 1
        if cons >= 3 and not upc:
            an["down3"][0] += 1
            if closes[i + 3] < closes[i]:
                an["down3"][1] += 1
        if rng > 0:
            lw = min(cs[i]["open"], cs[i]["close"]) - cs[i]["low"]
            uw = cs[i]["high"] - max(cs[i]["open"], cs[i]["close"])
            if uw >= 2 * body and uw >= 0.5 * rng:
                an["uwick"][0] += 1
                if closes[i + 3] < closes[i]:
                    an["uwick"][1] += 1
            if lw >= 2 * body and lw >= 0.5 * rng:
                an["lwick"][0] += 1
                if closes[i + 3] > closes[i]:
                    an["lwick"][1] += 1
    add_hint(res, "P6", "after_3up", an["up3"][0], an["up3"][1], base3, min_n, min_delta)
    add_hint(res, "P6", "after_3down", an["down3"][0], an["down3"][1], down3, min_n, min_delta)
    add_hint(res, "P6", "long_lower_wick", an["lwick"][0], an["lwick"][1], base3, min_n, min_delta)
    add_hint(res, "P6", "long_upper_wick", an["uwick"][0], an["uwick"][1], down3, min_n, min_delta)
    res["anatomy"] = an
    day_agg = {}
    for i in range(n):
        dk = cs[i]["epoch"] // 86400
        a = day_agg.get(dk)
        if a is None:
            day_agg[dk] = [cs[i]["high"], cs[i]["low"]]
        else:
            if cs[i]["high"] > a[0]:
                a[0] = cs[i]["high"]
            if cs[i]["low"] < a[1]:
                a[1] = cs[i]["low"]
    keys = sorted(day_agg)
    prevmap = {}
    for idx in range(1, len(keys)):
        prevmap[keys[idx]] = day_agg[keys[idx - 1]]
    pd = {"ph": [0, 0], "pl": [0, 0]}
    tph = set()
    tpl = set()
    for i in range(n - 3):
        dk = cs[i]["epoch"] // 86400
        pv = prevmap.get(dk)
        if not pv:
            continue
        if dk not in tph and cs[i]["high"] >= pv[0]:
            tph.add(dk)
            pd["ph"][0] += 1
            if closes[i + 3] > closes[i]:
                pd["ph"][1] += 1
        if dk not in tpl and cs[i]["low"] <= pv[1]:
            tpl.add(dk)
            pd["pl"][0] += 1
            if closes[i + 3] < closes[i]:
                pd["pl"][1] += 1
    add_hint(res, "P7", "touch_prev_high", pd["ph"][0], pd["ph"][1], base3, 30, min_delta)
    add_hint(res, "P7", "touch_prev_low", pd["pl"][0], pd["pl"][1], down3, 30, min_delta)
    res["prevday"] = pd
    return res

def explore():
    report = []
    done = set()
    try:
        old = json.load(open(OUT + "/lab_explore.json", encoding="utf-8"))
        if isinstance(old, list):
            report = old
            done = set(r.get("symbol") for r in old)
    except Exception:
        pass
    os.system('git config user.name "slow-gold lab" && git config user.email "lab@slowgold.local"')
    ref = os.environ.get("GITHUB_REF_NAME", "main")
    link = Link()
    since = 0
    for sym in CFG.get("explore_assets", []):
        if sym in done:
            log("explore skip done", sym)
            continue
        log("explore fetch", sym)
        r = None
        for attempt in range(3):
            try:
                link.reset()
                r = explore_asset(link, sym)
                break
            except Exception as e:
                log("retry", sym, attempt, str(e)[:120])
                time.sleep(3)
        if r:
            report.append(r)
            log("explore done", sym, r["class"], "depth", r["depth_days"], "hints", len(r["hints"]))
            for h in r["hints"]:
                log("HINT", sym, h["probe"], h["key"], "n=", h["n"], "p=", h["p"], "base=", h["base"], "delta=", h["delta"])
        json.dump(report, open(OUT + "/lab_explore.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        since += 1
        if since >= 6:
            os.system('git add -f site/data/lab_explore.json && git commit -m "lab: explore partial" && git push origin HEAD:' + ref)
            since = 0
    link.close()
    json.dump(report, open(OUT + "/lab_explore.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.system('git add -f site/data/lab_explore.json && git commit -m "lab: explore final" && git push origin HEAD:' + ref)
    log("explore complete assets=", len(report), "total_hints=", sum(len(r["hints"]) for r in report))

def payouts():
    link = Link()
    grid = []
    for sym in ["R_10", "R_75", "1HZ25V", "1HZ50V", "JD25", "JD10", "STPRNG",
                "BOOM300N", "BOOM600", "CRASH600", "CRASH1000", "frxEURUSD", "frxXAUUSD"]:
        for typ in ["CALL", "PUT"]:
            for dur in [120, 180, 300]:
                grid.append((sym, typ, dur, "s"))
    for sym in ["BOOM300N", "BOOM600", "CRASH600", "CRASH1000"]:
        for typ in ["ONETOUCH", "NOTOUCH"]:
            for dur in [5, 10, 15]:
                grid.append((sym, typ, dur, "m"))
    for sym, typ, dur, unit in grid:
        r = link.call({"proposal": 1, "amount": 1, "basis": "stake", "contract_type": typ,
                       "currency": "USD", "duration": dur, "duration_unit": unit, "symbol": sym})
        if "error" in r:
            log("PAYOUT", sym, typ, dur, unit, "ERR", r["error"].get("code", "?"))
        else:
            p = r["proposal"]
            payout = float(p.get("payout", 0))
            ask = float(p.get("ask_price", 1))
            ratio = round((payout / ask - 1) * 100, 2) if ask > 0 else 0
            log("PAYOUT", sym, typ, dur, unit, "payout=", payout, "ask=", ask, "return%=", ratio)
        time.sleep(1.2)
    link.close()
    log("payouts done")

def probe():
    link = Link()
    cs = candles(link, "R_75", 300, 10)
    log("candles", len(cs), "first", cs[0]["epoch"], "last", cs[-1]["epoch"], "close", cs[-1]["close"])
    link.close()
    if not TOKEN:
        log("no token in env")
        return
    r = rest("GET", "/trading/v1/options/accounts")
    log("accounts status", r.status_code, r.text[:300])
    if r.status_code != 200:
        return
    j = r.json()
    rows = j.get("data") if isinstance(j, dict) else j
    if not isinstance(rows, list):
        rows = []
    ids = [a.get("account_id") for a in rows if isinstance(a, dict) and a.get("account_type") == "demo"]
    if not ids:
        ids = [a.get("account_id") for a in rows if isinstance(a, dict)]
    log("account ids", ids)
    if not ids:
        return
    r2 = rest("POST", "/trading/v1/options/accounts/%s/otp" % ids[0])
    log("otp status", r2.status_code, r2.text[:300])
    if r2.status_code != 200:
        return
    u = find_ws_url(r2.json())
    log("ws url found", bool(u))
    if u:
        w2 = websocket.create_connection(u, timeout=30)
        w2.send(json.dumps({"ping": 1}))
        log("new ws first msg", w2.recv()[:200])
        w2.close()
        log("NEW API OK")

def backtest():
    days = CFG.get("backtest_days", 90)
    days_v4 = CFG.get("backtest_days_v4", 30)
    cells = []
    link = Link()
    for sym in CFG["symbols"]:
        log("fetch", sym)
        cs1_full = candles(link, sym, 60, max(days, days_v4) * 1440)
        if not cs1_full:
            raise SystemExit(STAMP + " NO M1 DATA " + sym)
        cs5 = candles(link, sym, 300, days * 288)
        cs15 = candles(link, sym, 900, days * 96)
        cs60 = candles(link, sym, 3600, days * 24)
        log("depth", sym, "m1_days=", round((cs1_full[-1]["epoch"] - cs1_full[0]["epoch"]) / 86400.0, 1))
        m = build(cs5, cs15, cs60)
        for var in ["V1", "V2", "V3"]:
            for dur in CFG["expiries"]:
                res = run_cell_m5(cs1_full, cs5, m, var, dur, CFG["payout_assumed"])
                cell = summarize(*res, var, dur, CFG["payout_assumed"])
                cell["symbol"] = sym
                cells.append(cell)
                log(sym, var, dur, "n=", cell["n"], "win%=", cell["win_pct"], "ev%=", cell["ev_pct"], cell["verdict"])
        cs1_v4 = cs1_full[-days_v4 * 1440:] if len(cs1_full) > days_v4 * 1440 else cs1_full
        for dur in CFG.get("expiries_v4", [120]):
            res = run_cell_m1(cs1_v4, "V4", dur, CFG["payout_assumed"])
            cell = summarize(*res, "V4", dur, CFG["payout_assumed"])
            cell["symbol"] = sym
            cells.append(cell)
            log(sym, "V4", dur, "n=", cell["n"], "win%=", cell["win_pct"], "ev%=", cell["ev_pct"], cell["verdict"])
    link.close()
    json.dump(cells, open(OUT + "/lab_cells.json", "w", encoding="utf-8"), ensure_ascii=False)
    stats = {"generated_at": datetime.datetime.utcnow().isoformat() + "Z",
             "mode": MODE, "action": "backtest", "cells": len(cells),
             "trades": sum(c["n"] for c in cells),
             "pass_n": sum(1 for c in cells if c["verdict"] == "PASS"),
             "fail_n": sum(1 for c in cells if c["verdict"] == "FAIL"),
             "pending_n": sum(1 for c in cells if c["verdict"] == "PENDING_DATA"),
             "v4_cells": sum(1 for c in cells if c["variant"] == "V4")}
    json.dump(stats, open(OUT + "/lab_stats.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log("backtest done", stats)

def live():
    log("live mode not implemented in this build")

if ACTION == "payouts":
    payouts()
elif ACTION == "probe":
    probe()
elif ACTION == "backtest":
    backtest()
elif ACTION == "explore":
    explore()
elif ACTION == "live":
    live()
else:
    log("unknown action", ACTION)
