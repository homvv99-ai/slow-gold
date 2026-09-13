import json, time, sys, os, urllib.request, urllib.parse
from pathlib import Path
import numpy as np
import pandas as pd
import requests

SYMBOL, INTERVAL, START = "PAXGUSDT", "1d", "2020-01-01"
COST = 0.0020
WARM = 60
HOSTS = ["https://data-api.binance.vision",
         "https://api.binance.com",
         "https://api1.binance.com",
         "https://api2.binance.com"]

def fetch():
    start_ms = int(pd.Timestamp(START, tz="UTC").timestamp() * 1000)
    rows, last_err = [], None
    for host in HOSTS:
        rows, cur, ok = [], start_ms, True
        while True:
            try:
                b = requests.get(host + "/api/v3/klines",
                                 params={"symbol": SYMBOL, "interval": INTERVAL,
                                         "startTime": cur, "limit": 1000}, timeout=20).json()
            except Exception as e:
                last_err = e; ok = False; break
            if isinstance(b, dict) or not b:
                last_err = str(b)[:200]; ok = False; break
            rows += b
            cur = b[-1][0] + 1
            if len(b) < 1000:
                break
            time.sleep(0.2)
        if ok and rows:
            print("data host:", host)
            break
    else:
        print("fetch failed:", last_err); sys.exit(1)
    df = pd.DataFrame(rows, columns=["ot","open","high","low","close","vol",
                                     "ct","a","b","c","d","e"])
    for col in ["open","high","low","close","vol"]:
        df[col] = df[col].astype(float)
    df["date"] = pd.to_datetime(df["ot"], unit="ms", utc=True)
    df = df[["date","open","high","low","close","vol"]].drop_duplicates("date").sort_values("date")
    today = pd.Timestamp.now(tz="UTC").date()
    return df[df["date"].dt.date < today].reset_index(drop=True)

def shift(a, n):
    out = np.zeros_like(a, dtype=bool)
    if n >= 0:
        out[n:] = a[:-n] if n > 0 else a
    return out

def backtest(o, h, l, c, dates, pos, act, atr, stop_mult):
    eq, units, in_mkt, entry_px, atr_entry, t_in = 100.0, 0.0, False, 0.0, 0.0, None
    stopped = False
    trades = []
    eq_curve = []
    for i in range(len(c)):
        if in_mkt and stop_mult is not None:
            stop_lvl = entry_px - stop_mult * atr_entry
            if l[i] <= stop_lvl:
                eq = units * stop_lvl * (1 - COST)
                trades.append({"entry": str(t_in)[:10], "exit": str(dates[i])[:10],
                               "ret": round((stop_lvl/entry_px - 1)*100 - 2*COST*100, 2)})
                units, in_mkt = 0.0, False
                stopped = True
        if stopped and not pos[i]:
            stopped = False
        can_enter = act[i] and (not stopped)
        if can_enter and not in_mkt:
            entry_px, in_mkt, t_in = o[i], True, dates[i]
            atr_entry = atr[i]
            units = eq * (1 - COST) / entry_px
        elif not act[i] and in_mkt:
            eq = units * o[i] * (1 - COST)
            trades.append({"entry": str(t_in)[:10], "exit": str(dates[i])[:10],
                           "ret": round((o[i]/entry_px - 1)*100 - 2*COST*100, 2)})
            units, in_mkt = 0.0, False
        eq_curve.append(units * c[i] if in_mkt else eq)
    if in_mkt:
        trades.append({"entry": str(t_in)[:10], "exit": "OPEN",
                       "ret": round((c[-1]/entry_px - 1)*100 - COST*100, 2)})
    eqs = pd.Series(eq_curve, index=pd.DatetimeIndex(dates))
    dd = ((eqs - eqs.cummax()) / eqs.cummax()).min() * 100
    rets = [t["ret"] for t in trades if t["exit"] != "OPEN"]
    wins = [r for r in rets if r > 0]; losses = [r for r in rets if r <= 0]
    pf = round(sum(wins)/abs(sum(losses)), 2) if losses and sum(losses) else 99.0
    return {"final_eq": round(float(eqs.iloc[-1]), 2),
            "max_dd": round(float(dd), 2),
            "trades": len(trades),
            "win_pct": round(len(wins)/len(rets)*100, 1) if rets else 0.0,
            "pf": pf}

def main():
    df = fetch()
    o = df["open"].values; h = df["high"].values; l = df["low"].values; c = df["close"].values
    dates = df["date"].values
    e20 = pd.Series(c).ewm(span=20, adjust=False).mean().values
    e50 = pd.Series(c).ewm(span=50, adjust=False).mean().values
    pos = e20 > e50
    pos[:WARM] = False
    act = shift(pos, 1)
    prev_c = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev_c), np.abs(l - prev_c)))
    atr = pd.Series(tr).ewm(alpha=1/14, adjust=False).mean().values

    res = {}
    for mult, name in [(None, "current"), (2.0, "stop2"), (3.0, "stop3")]:
        res[name] = backtest(o, h, l, c, dates, pos, act, atr, mult)
        print(name, res[name])

    tok = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    ch  = os.environ.get("TELEGRAM_CHANNEL", "").strip()
    if "/" in ch:
        ch = ch.split("/")[-1]
    if ch and not ch.startswith("@") and not ch.lstrip("-").isdigit():
        ch = "@" + ch
    if tok and ch:
        txt = (f"🔬 بحث v2 — الوقف اللحظي (2020←{str(dates[-1])[:10]})\n\n"
               f"أ) المنهج الحالي:\nرصيد {res['current']['final_eq']} | تراجع {res['current']['max_dd']}% | صفقات {res['current']['trades']} | فوز {res['current']['win_pct']}% | معامل {res['current']['pf']}\n\n"
               f"ب) وقف 2×ATR داخل اليوم:\nرصيد {res['stop2']['final_eq']} | تراجع {res['stop2']['max_dd']}% | صفقات {res['stop2']['trades']} | فوز {res['stop2']['win_pct']}% | معامل {res['stop2']['pf']}\n\n"
               f"ج) وقف 3×ATR داخل اليوم:\nرصيد {res['stop3']['final_eq']} | تراجع {res['stop3']['max_dd']}% | صفقات {res['stop3']['trades']} | فوز {res['stop3']['win_pct']}% | معامل {res['stop3']['pf']}\n\n"
               f"⚖️ القرار بشريٌّ علني: لا تبنّي قبل تحسّنٍ واضح — وفق فصل v2 في PLAYBOOK")
        data = urllib.parse.urlencode({"chat_id": ch, "text": txt}).encode()
        urllib.request.urlopen(urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage", data=data), timeout=30).read()
        print("research delivered")
    else:
        print("secrets missing, skip")

main()
