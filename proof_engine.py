import json, time, sys
from pathlib import Path
import numpy as np
import pandas as pd
import requests

SYMBOL, INTERVAL, START = "PAXGUSDT", "1d", "2020-01-01"
COST = 0.0020
WARM = 60
OUT = Path("out"); OUT.mkdir(exist_ok=True)
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

def main():
    print("جاري جلب البيانات (2020 ← اليوم)...")
    df = fetch()
    print(f"تم جلب {len(df)} شمعة يومية.")
    c = df["close"].values
    e20 = pd.Series(c).ewm(span=20, adjust=False).mean().values
    e50 = pd.Series(c).ewm(span=50, adjust=False).mean().values
    pos = e20 > e50
    pos[:WARM] = False
    act = shift(pos, 1)
    o = df["open"].values
    dates = df["date"].values

    eq, units, in_mkt, entry_px = 100.0, 0.0, False, 0.0
    trades, waits = [], []
    eq_curve, w_start, w_beg = [], None, None
    for i in range(len(df)):
        if act[i] and not in_mkt:
            entry_px, in_mkt, t_in = o[i], True, dates[i]
            units = eq * (1 - COST) / entry_px
        elif not act[i] and in_mkt:
            eq = units * o[i] * (1 - COST)
            trades.append({"entry_date": str(t_in)[:10], "entry_price": round(entry_px, 2),
                           "exit_date": str(dates[i])[:10], "exit_price": round(o[i], 2),
                           "days": int((pd.Timestamp(dates[i]) - pd.Timestamp(t_in)).days),
                           "ret_pct": round((o[i]/entry_px - 1)*100 - 2*COST*100, 2)})
            units, in_mkt = 0.0, False
        if not pos[i] and w_start is None:
            w_start, w_beg = dates[i], c[i]
        elif pos[i] and w_start is not None:
            waits.append({"start": str(w_start)[:10], "end": str(dates[i])[:10],
                          "days": int((pd.Timestamp(dates[i]) - pd.Timestamp(w_start)).days),
                          "mkt_chg_pct": round((c[i]/w_beg - 1)*100, 2)})
            w_start = None
        eq_curve.append(units * c[i] if in_mkt else eq)

    if in_mkt:
        trades.append({"entry_date": str(t_in)[:10], "entry_price": round(entry_px, 2),
                       "exit_date": "OPEN", "exit_price": round(c[-1], 2),
                       "days": int((pd.Timestamp(dates[-1]) - pd.Timestamp(t_in)).days),
                       "ret_pct": round((c[-1]/entry_px - 1)*100 - COST*100, 2)})
    if w_start is not None:
        waits.append({"start": str(w_start)[:10], "end": "NOW",
                      "days": int((pd.Timestamp(dates[-1]) - pd.Timestamp(w_start)).days),
                      "mkt_chg_pct": round((c[-1]/w_beg - 1)*100, 2)})

    eqs = pd.Series(eq_curve, index=pd.DatetimeIndex(dates))
    dd = ((eqs - eqs.cummax()) / eqs.cummax()).min() * 100
    rets = [t["ret_pct"] for t in trades if t["exit_date"] != "OPEN"]
    wins = [r for r in rets if r > 0]; losses = [r for r in rets if r <= 0]
    pf = sum(wins) / abs(sum(losses)) if losses and sum(losses) else 99.0

    yearly = []
    for yr, g in eqs.groupby(eqs.index.year):
        yearly.append({"year": int(yr),
                       "ret_pct": round((g.iloc[-1]/g.iloc[0]-1)*100, 2),
                       "maxdd_pct": round(((g - g.cummax())/g.cummax()).min()*100, 2)})

    hold = 100 * (1 - COST) * c[-1] / c[WARM]
    dca_u = dca_inv = 0.0
    nerv_eq, nerv_u, nerv_in, nerv_next = 100.0, 0.0, False, WARM + 7
    last_m = None
    for i in range(WARM, len(df)):
        m = pd.Timestamp(dates[i]).month
        if m != last_m:
            add = 100.0 / ((len(df) - WARM) / 30.4)
            dca_u += add * (1 - COST) / o[i]
            dca_inv += add
            last_m = m
        if i >= nerv_next:
            if not nerv_in:
                nerv_u = nerv_eq * (1 - COST) / o[i]; nerv_in = True
            else:
                nerv_eq = nerv_u * o[i] * (1 - COST)
                nerv_u, nerv_in = 0.0, False
            nerv_next = i + 7
    dca_final = dca_u * c[-1] / dca_inv * 100 if dca_inv else 100
    nerv_final = nerv_u * c[-1] if nerv_in else nerv_eq

    # ===== new: decision line + current performance =====
    alpha20 = 2.0 / 21.0
    alpha50 = 2.0 / 51.0
    decision_price = round(((1 - alpha50) * e50[-1] - (1 - alpha20) * e20[-1]) / (alpha20 - alpha50), 2)
    current_perf = None
    if in_mkt and entry_px > 0:
        current_perf = round((c[-1] / entry_px - 1) * 100 - 2 * COST * 100, 2)
    closed = [t for t in trades if t["exit_date"] != "OPEN"]
    last_closed_trade = closed[-1] if closed else None

    stats = {"generated_at": str(pd.Timestamp.now(tz="UTC"))[:19],
             "period": [START, str(dates[-1])[:10]],
             "last_date": str(dates[-1])[:10],
             "last_close": round(float(c[-1]), 2),
             "decision_price": float(decision_price),
             "current_perf_pct": current_perf,
             "last_closed_trade": last_closed_trade,
             "days_total": int(len(df)), "no_pct": round((~pos).mean()*100, 1),
             "final_eq": round(eqs.iloc[-1], 2),
             "total_ret_pct": round(eqs.iloc[-1]-100, 2),
             "max_dd_pct": round(dd, 2), "trades_n": len(trades),
             "win_pct": round(len(wins)/len(rets)*100, 1) if rets else 0,
             "profit_factor": round(pf, 2),
             "avg_hold_days": int(pd.Series([t["days"] for t in trades]).mean()) if trades else 0,
             "yearly": yearly,
             "ghosts": {"buy_hold": round(hold, 2), "dca": round(dca_final, 2),
                        "nervous": round(nerv_final, 2)},
             "waits_n": len(waits),
             "waits_saved_top": sorted([w for w in waits if w["mkt_chg_pct"]<0],
                                       key=lambda w: w["mkt_chg_pct"])[:5]}

    pd.DataFrame(trades).to_csv(OUT/"ledger_trades.csv", index=False)
    pd.DataFrame(waits).to_csv(OUT/"ledger_waits.csv", index=False)
    eqs.to_csv(OUT/"daily_equity.csv", header=["equity"])
    (OUT/"stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    print("="*58)
    print(f"الرصيد النهائي: {stats['final_eq']}  ({stats['total_ret_pct']:+.1f}%)")
    print(f"أكبر تراجع: {stats['max_dd_pct']}% | صفقات: {stats['trades_n']} | فوز: {stats['win_pct']}%")
    print(f"خط القرار: {decision_price}$")
    print("="*58)

main()
