import json, os, sys, time, datetime, bisect, math, hashlib, base64
import websocket
import requests

MODE = open("lab_mode.txt", encoding="utf-8").read().strip().lower() or "demo"
STAMP = "[MODE: %s]" % MODE.upper()
CFG = json.load(open("lab_config.json", encoding="utf-8"))
TOKEN = os.environ.get("DERIV_TOKEN", "")
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT", "")
ENC_KEY = os.environ.get("LAB_ENC_KEY", "")
OUT = "site/data"
ACTION = sys.argv[1] if len(sys.argv) > 1 else "probe"
APP_ID = CFG.get("app_id", "1089")
API_BASE = "https://api.derivws.com"

TABLES = [
    {"id": "ST3D", "sym": "STPRNG", "rule": "after3down", "dir": "CALL", "dur": 180, "min_pay": 93, "exam": "A", "kind": "rev"},
    {"id": "STUW", "sym": "STPRNG", "rule": "upperwick", "dir": "CALL", "dur": 180, "min_pay": 93, "exam": "A", "kind": "rev"},
    {"id": "STBR", "sym": "STPRNG", "rule": "below_round", "dir": "CALL", "dur": 180, "min_pay": 93, "exam": "B", "kind": "round"},
    {"id": "JDAR", "sym": "JD25", "rule": "above_round", "dir": "CALL", "dur": 180, "min_pay": 89, "exam": "B", "kind": "round"},
    {"id": "PT3D", "sym": "STPRNG", "rule": "after3down", "dir": "PUT", "dur": 180, "min_pay": 93, "exam": "A", "kind": "trend"},
    {"id": "CT3U", "sym": "STPRNG", "rule": "after3up", "dir": "CALL", "dur": 180, "min_pay": 93, "exam": "A", "kind": "trend"},
]

def log(*a):
    print(STAMP, *a, flush=True)

def _keystream(key, n, salt):
    out = b""
    ctr = 0
    while len(out) < n:
        out += hashlib.sha256((key + "|" + salt + "|" + str(ctr)).encode()).digest()
        ctr += 1
    return out[:n]

def enc_text(s):
    if not ENC_KEY:
        return s
    salt = base64.b64encode(os.urandom(8)).decode()
    raw = s.encode("utf-8")
    ks = _keystream(ENC_KEY, len(raw), salt)
    x = bytes(a ^ b for a, b in zip(raw, ks))
    return "ENC1:" + salt + ":" + base64.b64encode(x).decode()

def dec_text(s):
    if not ENC_KEY or not s.startswith("ENC1:"):
        return s
    try:
        _, salt, b64 = s.split(":", 2)
        x = base64.b64decode(b64)
        ks = _keystream(ENC_KEY, len(x), salt)
        return bytes(a ^ b for a, b in zip(x, ks)).decode("utf-8")
    except Exception:
        return s

def tg(text):
    if TG_TOKEN and TG_CHAT:
        try:
            rr = requests.post("https://api.telegram.org/bot" + TG_TOKEN + "/sendMessage",
                               data={"chat_id": TG_CHAT, "text": text}, timeout=15)
            if rr.status_code == 200:
                return
            log("tg http", rr.status_code, rr.text[:100])
        except Exception as e:
            log("tg fail", str(e)[:60])
    log("CARD", text.replace("\n", " | ")[:300])

def connect():
    return websocket.create_connection("wss://ws.derivws.com/websockets/v3?app_id=" + str(CFG.get("ws_app_id", "1089")), timeout=30)

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
        try:
            self.w = connect()
        except Exception:
            self.w = None
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

def otp_door():
    r = rest("GET", "/trading/v1/options/accounts")
    if r.status_code != 200:
        log("door http", r.status_code)
        return None
    j = r.json()
    rows = j.get("data") if isinstance(j, dict) else j
    if not isinstance(rows, list):
        rows = []
    ids = [a.get("account_id") for a in rows if isinstance(a, dict) and a.get("account_type") == "demo"]
    if not ids:
        ids = [a.get("account_id") for a in rows if isinstance(a, dict)]
    if not ids:
        log("door no accounts")
        return None
    r2 = rest("POST", "/trading/v1/options/accounts/%s/otp" % ids[0])
    if r2.status_code != 200:
        log("door otp http", r2.status_code)
        return None
    return find_ws_url(r2.json())

def candles(link, sym, gran, need):
    out = {}
    end = "latest"
    pages = 0
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
        pages += 1
        if pages > CFG.get("max_pages", 200):
            break
        time.sleep(0.25)
    return [out[k] for k in sorted(out)]

def live_tick(link, sym):
    try:
        w = link.get()
        w.send(json.dumps({"ticks": sym, "subscribe": False}))
        r = json.loads(w.recv())
        if isinstance(r, dict) and "error" in r:
            log("tick err", r["error"].get("code"), str(r)[:80])
            return None
        q = r.get("tick", {}).get("quote")
        if q is not None:
            return float(q)
    except Exception as e:
        log("tick fail", str(e)[:60])
    return None

def regime_z(cs, i):
    if i < 31:
        return 0.0
    closes = [cs[j]["close"] for j in range(i - 30, i + 1)]
    drift = closes[-1] - closes[0]
    rngs = [cs[j]["high"] - cs[j]["low"] for j in range(i - 30, i + 1)]
    a = sum(rngs) / len(rngs) if rngs else 0.0
    return drift / a if a > 0 else 0.0

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
        eng = body > 0 and pbody < 0 and c["close"] >= p["high"] and c["open"] <= p["close"] and c["close"] > e20[i]
        pin = rng > 0 and (min(c["open"], c["close"]) - c["low"]) >= 2 * abs(body) and (c["close"] - c["low"]) >= 0.66 * rng
        rec = c["close"] > e20[i] and cs[i - 1]["close"] < e20[i - 1] and cs[i - 2]["close"] < e20[i - 2]
        return eng or pin or rec
    eng = body < 0 and pbody > 0 and c["close"] <= p["low"] and c["open"] >= p["close"] and c["close"] < e20[i]
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
    cls = classify_asset(sym)
    gran = 300 if cls == "market" else 60
    per_day = 288 if gran == 300 else 1440
    try:
        cs = candles(link, sym, gran, CFG.get("explore_days", 365) * per_day)
    except SystemExit:
        log("SKIP", sym, "fetch error")
        return None
    n = len(cs) if cs else 0
    if n < 5000:
        log("SKIP", sym, "short depth", n)
        return None
    depth = round((cs[-1]["epoch"] - cs[0]["epoch"]) / 86400.0, 1)
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
    if not TOKEN:
        log("payouts no token")
        return
    u = otp_door()
    if not u:
        log("payouts no door")
        return
    log("payouts door ok", True)
    w = websocket.create_connection(u, timeout=30)
    field = None
    for f in ["symbol", "underlying", "underlying_symbol", "asset", "instrument"]:
        payload = {"proposal": 1, "amount": 1, "basis": "stake", "contract_type": "CALL",
                   "currency": "USD", "duration": 120, "duration_unit": "s"}
        payload[f] = "R_10"
        try:
            w.send(json.dumps(payload))
            rr = json.loads(w.recv())
        except Exception as e:
            log("payouts ws dead", str(e)[:80])
            return
        code = rr.get("error", {}).get("code", "") if isinstance(rr, dict) else ""
        msg = rr.get("error", {}).get("message", "") if isinstance(rr, dict) else ""
        log("schema try", f, code if code else "OK", msg[:80])
        if isinstance(rr, dict) and "error" not in rr:
            field = f
            p = rr.get("proposal", {})
            log("PAYOUT", "R_10", "CALL", 120, "s", "payout=", p.get("payout"), "ask=", p.get("ask_price"))
            break
        if code == "InputValidationFailed" and ("not allowed" in msg or "required" in msg.lower()):
            continue
        field = f
        break
    if not field:
        log("payouts schema unknown")
        try:
            w.close()
        except Exception:
            pass
        return
    log("payouts field", field)
    grid = []
    for sym in ["R_75", "1HZ25V", "1HZ50V", "JD25", "JD10", "STPRNG",
                "frxEURUSD", "frxXAUUSD"]:
        for typ in ["CALL", "PUT"]:
            for dur in [120, 180, 300]:
                grid.append((sym, typ, dur, "s"))
    for sym in ["BOOM300N", "BOOM600", "CRASH600", "CRASH1000"]:
        for typ in ["CALL", "PUT"]:
            for dur in [5, 10]:
                grid.append((sym, typ, dur, "t"))
    for sym in ["BOOM300N", "CRASH1000"]:
        for typ in ["ONETOUCH", "NOTOUCH"]:
            for dur in [5, 10]:
                grid.append((sym, typ, dur, "m"))
    for sym, typ, dur, unit in grid:
        payload = {"proposal": 1, "amount": 1, "basis": "stake", "contract_type": typ,
                   "currency": "USD", "duration": dur, "duration_unit": unit}
        payload[field] = sym
        if typ in ("ONETOUCH", "NOTOUCH"):
            payload["barrier"] = "+1%"
        ok = False
        rr = None
        for attempt in range(3):
            try:
                w.send(json.dumps(payload))
                rr = json.loads(w.recv())
                ok = True
                break
            except Exception as e:
                log("payouts ws retry", attempt, str(e)[:80])
                time.sleep(2)
        if not ok:
            log("PAYOUT", sym, typ, dur, unit, "ERR", "ws-dead")
            break
        if isinstance(rr, dict) and rr.get("error", {}).get("code", "") == "RateLimit":
            time.sleep(20)
            try:
                w.send(json.dumps(payload))
                rr = json.loads(w.recv())
            except Exception:
                log("PAYOUT", sym, typ, dur, unit, "ERR", "ws-dead")
                break
        if isinstance(rr, dict) and "error" in rr:
            log("PAYOUT", sym, typ, dur, unit, "ERR", rr["error"].get("code", "?"), rr["error"].get("message", "")[:100])
        else:
            p = rr.get("proposal", {}) if isinstance(rr, dict) else {}
            payout = float(p.get("payout", 0))
            ask = float(p.get("ask_price", 1))
            ratio = round((payout / ask - 1) * 100, 2) if ask > 0 else 0
            log("PAYOUT", sym, typ, dur, unit, "payout=", payout, "ask=", ask, "return%=", ratio)
        time.sleep(1.2)
    try:
        w.close()
    except Exception:
        pass
    log("payouts done")

def probe():
    link = Link()
    cs = candles(link, "R_75", 300, 10)
    log("candles", len(cs), "first", cs[0]["epoch"], "last", cs[-1]["epoch"], "close", cs[-1]["close"])
    link.close()
    if not TOKEN:
        log("no token in env")
        return
    u = otp_door()
    log("door found", bool(u))
    if u:
        w2 = websocket.create_connection(u, timeout=30)
        w2.send(json.dumps({"ping": 1}))
        log("new ws first msg", w2.recv()[:120])
        w2.close()
        log("NEW API OK")

def buyschema():
    if not TOKEN:
        log("buy no token")
        return
    u = otp_door()
    if not u:
        log("buy no door")
        return
    w = websocket.create_connection(u, timeout=30)
    payload = {"proposal": 1, "amount": 1, "basis": "stake", "contract_type": "CALL",
               "currency": "USD", "duration": 180, "duration_unit": "s",
               "underlying_symbol": "R_10"}
    w.send(json.dumps(payload))
    rr = json.loads(w.recv())
    if isinstance(rr, dict) and "error" in rr:
        log("buy proposal err", rr["error"].get("code"), rr["error"].get("message", "")[:120])
        return
    p = rr.get("proposal", {})
    pid = p.get("id")
    log("buy proposal ok id=", bool(pid), "payout=", p.get("payout"), "ask=", p.get("ask_price"))
    cands = [
        ("ws-buy-id", {"buy": pid, "price": 1}),
        ("ws-buy-contract", {"contract": {"buy": 1, "proposal_id": pid, "amount": 1, "basis": "stake"}}),
        ("ws-purchase", {"purchase": pid, "amount": 1}),
    ]
    for name, msg in cands:
        try:
            w.send(json.dumps(msg))
            r2 = json.loads(w.recv())
            code = r2.get("error", {}).get("code", "") if isinstance(r2, dict) else ""
            log("buy try", name, code if code else "OK", json.dumps(r2)[:200])
            if isinstance(r2, dict) and "error" not in r2:
                log("BUY SCHEMA FOUND", name)
                break
        except Exception as e:
            log("buy try", name, "EXC", str(e)[:80])
    try:
        w.close()
    except Exception:
        pass
    log("buyschema done")

def rule_signal(rule, cs, i, ctx):
    c = cs[i]
    if rule == "after3down":
        if i < 3:
            return 0
        return 1 if (cs[i]["close"] < cs[i]["open"] and cs[i - 1]["close"] < cs[i - 1]["open"] and cs[i - 2]["close"] < cs[i - 2]["open"]) else 0
    if rule == "after3up":
        if i < 3:
            return 0
        return 1 if (cs[i]["close"] > cs[i]["open"] and cs[i - 1]["close"] > cs[i - 1]["open"] and cs[i - 2]["close"] > cs[i - 2]["open"]) else 0
    if rule == "upperwick":
        rng = c["high"] - c["low"]
        body = abs(c["close"] - c["open"])
        uw = c["high"] - max(c["open"], c["close"])
        return 1 if (rng > 0 and uw >= 2 * body and uw >= 0.5 * rng) else 0
    if rule in ("below_round", "above_round"):
        med = ctx.get("med")
        step = ctx.get("step")
        a5 = ctx.get("a5")
        if not med or not step or not a5:
            return 0
        bestd = 1e18
        bestL = c["close"]
        for s in (step / 10.0, step):
            L = round(c["close"] / s) * s
            d = abs(c["close"] - L)
            if d < bestd:
                bestd = d
                bestL = L
        if bestd > 0.1 * a5:
            return 0
        if rule == "below_round":
            return 1 if c["close"] < bestL else 0
        return 1 if c["close"] >= bestL else 0
    if rule == "prevlow_touch":
        pl = ctx.get("prev_low")
        return 1 if (pl and c["low"] <= pl <= c["high"]) else 0
    if rule == "prevhigh_touch":
        ph = ctx.get("prev_high")
        return 1 if (ph and c["low"] <= ph <= c["high"]) else 0
    return 0

def load_state():
    try:
        raw = open(OUT + "/lab_state.json", encoding="utf-8").read()
        st = json.loads(dec_text(raw))
    except Exception:
        st = {"cells": {}, "ledger": [], "waits": [], "events": [], "peak": 0.0,
              "tg_offset": 0, "day": "", "cache": {}, "halt": False, "paper": True,
              "paper_until": 0, "buy_schema": "", "shadow": [], "shadow_stats": {},
              "filter_off": False, "fw_alerted": False}
    st.setdefault("shadow", [])
    st.setdefault("shadow_stats", {})
    st.setdefault("filter_off", False)
    st.setdefault("fw_alerted", False)
        return st


def save_state(st):
    st["ledger"] = st["ledger"][-2000:]
    st["waits"] = st["waits"][-500:]
    st["events"] = st["events"][-500:]
    st["shadow"] = st.get("shadow", [])[-500:]
    txt = json.dumps(st, ensure_ascii=False)
    open(OUT + "/lab_state.json", "w", encoding="utf-8").write(enc_text(txt))

def push_repo(msg):
    ref = os.environ.get("GITHUB_REF_NAME", "main")
    os.system('git add -f site/data/lab_state.json 2>/dev/null; git commit -m "' + msg + '" && git push origin HEAD:' + ref)

def tg_safe(st, text):
    try:
        tg(text)
    except Exception:
        pass
    st["events"].append([int(time.time()), "tg", text[:80]])

def settle_shadows(st, link, now):
    due = [s for s in st.get("shadow", []) if now >= s[5] + 60]
    if not due:
        return
    for s in due:
        t0, cid, sym, d, entry, expiry = s
        try:
            w = link.get()
            w.send(json.dumps({"ticks_history": sym, "adjust_start_time": 1,
                               "count": 2, "end": str(expiry + 59),
                               "granularity": 60, "style": "candles"}))
            r = json.loads(w.recv())
            cs = r.get("candles", [])
        except Exception:
            cs = []
        if not cs:
            continue
        exit_px = cs[-1]["close"]
        win = (exit_px > entry) if d == "CALL" else (exit_px < entry)
        a = st["shadow_stats"].get(cid, [0, 0])
        st["shadow_stats"][cid] = [a[0] + 1, a[1] + (1 if win else 0)]
        log("SHADOW-SET", cid, d, "win=", 1 if win else 0, "n=", st["shadow_stats"][cid][0])
    st["shadow"] = [s for s in st.get("shadow", []) if now < s[5] + 60]

def live():
    st = load_state()
    now = int(time.time())
    today = datetime.datetime.utcfromtimestamp(now).date().isoformat()
    if MODE == "demo":
        st["paper"] = False
    if not st.get("buy_schema"):
        st["buy_schema"] = "ws-buy-id"
    if st.get("halt"):
        log("live halted by guard")
        tg_safe(st, "🛑 البوت موقوف — قرار بشري مطلوب")
        save_state(st)
        return
    link = Link()
    settle_shadows(st, link, now)
    for cid, a in list(st.get("shadow_stats", {}).items()):
        cell = st["cells"].get(cid)
        if a[0] >= 30 and cell and cell.get("trades", 0) >= 20:
            sh_pct = a[1] * 100.0 / a[0]
            lv_pct = cell["wins"] * 100.0 / cell["trades"]
            if sh_pct >= lv_pct and not st.get("filter_off") and not st.get("fw_alerted"):
                st["filter_off"] = True
                st["fw_alerted"] = True
                st["events"].append([now, cid, "tripwire shadow>=live"])
                tg_safe(st, "⚠️ القاطع: ظل " + cid + " أفضل من حيّه — الفلتر أُطفئ ذاتيًا")
    door = None
    for row in TABLES:
        cid = row["id"]
        cell = st["cells"].setdefault(cid, {"status": "awake", "sleep_until": 0, "fails": 0,
                                            "last20": [], "trades": 0, "wins": 0, "day_trades": 0,
                                            "day": today, "open": None, "last_sig": 0})
        if cell["day"] != today:
            cell["day"] = today
            cell["day_trades"] = 0
        if cell["status"] == "buried":
            continue
        if cell["status"] == "asleep":
            if now < cell["sleep_until"]:
                continue
            win7 = remeasure(link, row)
            need = 100.0 / (1.0 + row["min_pay"] / 100.0) + 1.0
            if win7 is not None and win7 >= need:
                cell["status"] = "awake"
                cell["last20"] = []
                st["events"].append([now, cid, "wake win7=" + str(win7)])
                tg_safe(st, "😴→ " + cid + " استيقظت فوز7=" + str(win7))
            else:
                cell["fails"] += 1
                if cell["fails"] >= 2:
                    cell["status"] = "buried"
                    st["events"].append([now, cid, "bury win7=" + str(win7)])
                    tg_safe(st, "⚰️ " + cid + " دُفنت (فشلان)")
                else:
                    cell["sleep_until"] = now + 86400
                    st["events"].append([now, cid, "sleep2 win7=" + str(win7)])
            continue
        try:
            cs = candles(link, row["sym"], 60, 5000)
        except SystemExit:
            log("live fetch fail", row["sym"])
            continue
        if len(cs) < 100:
            continue
        i = len(cs) - 2
        c = cs[i]
        t = c["epoch"] + 60
        z = regime_z(cs, i)
        regime = "down" if z < -1.0 else ("up" if z > 1.0 else "flat")
        if cell["open"] and now >= cell["open"]["expiry"]:
            settle(link, st, row, cell, now)
        if cell["open"]:
            continue
        cap = 50 if cell["trades"] < 500 else CFG.get("cap_real", 6)
        if cell["day_trades"] >= cap:
            continue
        ctx = build_ctx(link, st, row, cs, today)
        s = rule_signal(row["rule"], cs, i, ctx)
        if s == 0 or cell["last_sig"] == c["epoch"]:
            continue
        cell["last_sig"] = c["epoch"]
        log("SIG-RAW", cid, s, c["epoch"], "regime=", regime, "z=", round(z, 2))
        delta = now - t
        gate_s = 180 if row["exam"] == "A" else 360
        if delta > gate_s:
            st["waits"].append([now, cid, "stale", delta])
            log("PATH", cid, "stale delta=", delta)
            continue
        kind = row.get("kind", "rev")
        blocked = (regime == "down" and kind == "rev" and row["dir"] == "CALL") or \
                  (regime == "up" and kind == "trend" and row["dir"] == "PUT")
        if blocked and not st.get("filter_off"):
            st["shadow"].append([now, cid, row["sym"], row["dir"], c["close"], now + row["dur"]])
            log("SHADOW", cid, row["dir"], "regime=", regime)
            continue
        if door is None:
            door = otp_door()
            if not door:
                time.sleep(3)
                door = otp_door()
        log("PATH", cid, "delta=", delta, "door=", bool(door), "regime=", regime)
        if not door:
            st["waits"].append([now, cid, "door", None])
            st["events"].append([now, cid, "door dead"])
            tg_safe(st, "🚪 الباب المالي ميت — فحص التوكن")
            continue
        pay = live_payout(door, row, row["dur"])
        if pay is None:
            door = otp_door()
            pay = live_payout(door, row, row["dur"])
        log("PATH", cid, "pay=", pay)
        if pay is None or pay < row["min_pay"]:
            st["waits"].append([now, cid, "pay", pay])
            continue
        entry_px = live_tick(link, row["sym"])
        if entry_px is None:
            entry_px = cs[i]["close"]
            log("tick fallback", cid, entry_px)
        log("PATH", cid, "tick=", entry_px)
        stake = 1.0
        if cell["trades"] >= 100:
            stake = CFG.get("stake2", 5.0)
        cell["open"] = {"entry": entry_px, "epoch": c["epoch"], "expiry": now + row["dur"],
                        "pay": pay, "stake": stake, "dir": row["dir"], "paper": False,
                        "remaining": row["dur"]}
        cell["day_trades"] += 1
        log("SIGNAL", cid, row["sym"], row["dir"], "pay=", pay, "delta=", delta, "stake=", stake, "regime=", regime)
        exec_buy(st, door, row, cell)
    if st.get("day", "") != today:
        st["day"] = today
        daily_guardian(st, link, today)
    handle_tg(st)
    if int(datetime.datetime.utcfromtimestamp(now).hour) == CFG.get("card_hour", 8) and st.get("card_day", "") != today:
        st["card_day"] = today
        send_card(st)
    link.close()
    save_state(st)
    push_repo("lab: state pulse")
    log("live pulse done")

def build_ctx(link, st, row, cs, today):
    ctx = {}
    sym = row["sym"]
    cache = st["cache"].get(sym)
    if not cache or cache.get("day") != today:
        try:
            big = candles(link, sym, 60, 43200)
        except SystemExit:
            big = cs
        closes = sorted(c["close"] for c in big)
        med = closes[len(closes) // 2] if closes else 0
        m = 10.0 ** math.floor(math.log10(med)) if med > 0 else 1.0
        rngs = [c["high"] - c["low"] for c in big[-2000:]]
        cache = {"day": today, "med": med, "step": m,
                 "a5": sum(rngs) / len(rngs) if rngs else 0}
        st["cache"][sym] = cache
    ctx["med"] = cache.get("med")
    ctx["step"] = cache.get("step")
    ctx["a5"] = cache.get("a5")
    day_agg = {}
    for c in cs:
        dk = c["epoch"] // 86400
        a = day_agg.get(dk)
        if a is None:
            day_agg[dk] = [c["high"], c["low"]]
        else:
            if c["high"] > a[0]:
                a[0] = c["high"]
            if c["low"] < a[1]:
                a[1] = c["low"]
    keys = sorted(day_agg)
    nowk = int(time.time()) // 86400
    prev = None
    for k in keys:
        if k < nowk:
            prev = day_agg[k]
    if prev:
        ctx["prev_high"] = prev[0]
        ctx["prev_low"] = prev[1]
    return ctx

def live_payout(door, row, dur):
    try:
        w = websocket.create_connection(door, timeout=20)
        w.send(json.dumps({"proposal": 1, "amount": 1, "basis": "stake",
                           "contract_type": row["dir"], "currency": "USD",
                           "duration": dur, "duration_unit": "s",
                           "underlying_symbol": row["sym"]}))
        rr = json.loads(w.recv())
        w.close()
        if isinstance(rr, dict) and "error" not in rr:
            return (float(rr.get("proposal", {}).get("payout", 0)) - 1.0) * 100.0
        log("pay err", rr.get("error", {}).get("code", "?") if isinstance(rr, dict) else "?", str(rr)[:80])
    except Exception as e:
        log("payout check fail", str(e)[:60])
    return None

def exec_buy(st, door, row, cell):
    try:
        w = websocket.create_connection(door, timeout=20)
        w.send(json.dumps({"proposal": 1, "amount": cell["open"]["stake"], "basis": "stake",
                           "contract_type": row["dir"], "currency": "USD",
                           "duration": cell["open"]["remaining"], "duration_unit": "s",
                           "underlying_symbol": row["sym"]}))
        rr = json.loads(w.recv())
        pid = rr.get("proposal", {}).get("id") if isinstance(rr, dict) else None
        if not pid:
            log("buy no proposal", row["id"], str(rr)[:100])
            w.close()
            cell["open"] = None
            return
        msg = {"buy": pid, "price": cell["open"]["stake"]} if st["buy_schema"] == "ws-buy-id" else \
              {"contract": {"buy": 1, "proposal_id": pid, "amount": cell["open"]["stake"], "basis": "stake"}} if st["buy_schema"] == "ws-buy-contract" else \
              {"purchase": pid, "amount": cell["open"]["stake"]}
        w.send(json.dumps(msg))
        r2 = json.loads(w.recv())
        w.close()
        if isinstance(r2, dict) and "error" in r2:
            log("buy exec err", r2["error"].get("code"), str(r2)[:120])
            cell["open"] = None
        else:
            b = r2.get("buy", {})
            log("BUY OK", row["id"], "contract=", b.get("contract_id"), "balance=", b.get("balance_after"))
    except Exception as e:
        log("buy exec exc", str(e)[:80])

def settle(link, st, row, cell, now):
    o = cell["open"]
    try:
        w = link.get()
        w.send(json.dumps({"ticks_history": row["sym"], "adjust_start_time": 1,
                           "count": 2, "end": str(o["expiry"] + 59),
                           "granularity": 60, "style": "candles"}))
        r = json.loads(w.recv())
        cs = r.get("candles", [])
    except Exception:
        cs = []
    if not cs:
        return
    exit_px = cs[-1]["close"]
    win = (exit_px > o["entry"]) if o["dir"] == "CALL" else (exit_px < o["entry"])
    pnl = o["stake"] * (o["pay"] / 100.0) if win else -o["stake"]
    cell["open"] = None
    cell["trades"] += 1
    cell["wins"] += 1 if win else 0
    cell["last20"].append(1 if win else 0)
    cell["last20"] = cell["last20"][-20:]
    st["ledger"].append([o["epoch"], row["id"], o["dir"], o["entry"], exit_px, o["stake"], o["pay"], 1 if win else 0, round(pnl, 4), 0])
    eq = 100.0 + sum(x[8] for x in st["ledger"])
    st["peak"] = max(st.get("peak", 0.0), eq)
    if len(cell["last20"]) >= 20 and sum(cell["last20"]) <= 8:
        cell["status"] = "asleep"
        cell["sleep_until"] = now + 86400
        st["events"].append([now, row["id"], "sleep 12/20"])
        tg_safe(st, "😴 " + row["id"] + " نامت (12 خسارة/20)")
    if st["peak"] - eq >= CFG.get("circuit", 10.0):
        st["halt"] = True
        st["events"].append([now, "BOT", "circuit 10%"])
        tg_safe(st, "🛑 دائرة 10% — توقف كامل")
    asleep_today = sum(1 for c in st["cells"].values() if c.get("status") == "asleep" and c.get("sleep_until", 0) > now - 86400)
    if asleep_today >= 3:
        st["halt"] = True
        st["events"].append([now, "BOT", "3 cells asleep"])
        tg_safe(st, "🛑 ثلاث خلايا نائمة — قرار بشري")

def remeasure(link, row):
    try:
        cs = candles(link, row["sym"], 60, 10080)
    except SystemExit:
        return None
    if len(cs) < 500:
        return None
    closes = sorted(c["close"] for c in cs)
    med = closes[len(closes) // 2]
    m = 10.0 ** math.floor(math.log10(med)) if med > 0 else 1.0
    rngs = [c["high"] - c["low"] for c in cs[-2000:]]
    ctx = {"med": med, "step": m, "a5": sum(rngs) / len(rngs) if rngs else 0}
    day_agg = {}
    for c in cs:
        dk = c["epoch"] // 86400
        a = day_agg.get(dk)
        if a is None:
            day_agg[dk] = [c["high"], c["low"]]
        else:
            if c["high"] > a[0]:
                a[0] = c["high"]
            if c["low"] < a[1]:
                a[1] = c["low"]
    keys = sorted(day_agg)
    wins = 0
    n = 0
    for i in range(3, len(cs) - 4):
        dk = cs[i]["epoch"] // 86400
        prev = None
        for k in keys:
            if k < dk:
                prev = day_agg[k]
        if prev:
            ctx["prev_high"] = prev[0]
            ctx["prev_low"] = prev[1]
        if rule_signal(row["rule"], cs, i, ctx) == 1:
            exit_px = cs[i + 3]["close"]
            win = (exit_px > cs[i]["close"]) if row["dir"] == "CALL" else (exit_px < cs[i]["close"])
            wins += 1 if win else 0
            n += 1
    return round(wins * 100.0 / n, 2) if n >= 30 else None

def daily_guardian(st, link, today):
    lines = ["📅 بطاقة " + today]
    eq = sum(x[8] for x in st["ledger"])
    lines.append("الرأس: " + str(round(100 + eq, 2)) + "$")
    for row in TABLES:
        cell = st["cells"].get(row["id"], {})
        tr = cell.get("trades", 0)
        w = cell.get("wins", 0)
        wp = round(w * 100.0 / tr, 2) if tr else 0
        lines.append(row["id"] + ": " + str(tr) + " صفقة فوز " + str(wp) + "% حالة " + cell.get("status", "awake"))
    lines.append("فرص فائتة: " + str(len(st["waits"])))
    sh = st.get("shadow_stats", {})
    if sh:
        lines.append("ظل: " + ", ".join(k + "=" + str(v[1]) + "/" + str(v[0]) for k, v in sh.items()))
    tg_safe(st, "\n".join(lines))

def send_card(st):
    daily_guardian(st, None, st.get("day", ""))

def handle_tg(st):
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        r = requests.get("https://api.telegram.org/bot" + TG_TOKEN + "/getUpdates",
                         params={"offset": st.get("tg_offset", 0), "timeout": 5}, timeout=15).json()
        if not r.get("ok", True):
            log("tg poll err", str(r.get("description"))[:100])
            return
        for up in r.get("result", []):
            st["tg_offset"] = up["update_id"] + 1
            txt = up.get("message", {}).get("text", "")
            if txt.startswith("/card"):
                send_card(st)
            elif txt.startswith("/ledger"):
                last = st["ledger"][-15:]
                tg("\n".join(str(x) for x in last) if last else "لا صفقات بعد")
            elif txt.startswith("/sleeps"):
                tg("\n".join(str(x) for x in st["events"][-15:]) if st["events"] else "لا أحداث")
            elif txt.startswith("/waits"):
                tg("\n".join(str(x) for x in st["waits"][-15:]) if st["waits"] else "لا فوائت")
    except Exception as e:
        log("tg poll fail", str(e)[:60])

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

if ACTION == "payouts":
    payouts()
elif ACTION == "probe":
    probe()
elif ACTION == "buyschema":
    buyschema()
elif ACTION == "backtest":
    backtest()
elif ACTION == "explore":
    explore()
elif ACTION == "live":
    live()
else:
    log("unknown action", ACTION)
