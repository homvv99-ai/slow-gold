import json, hashlib, subprocess, sys
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).parent
SITE = ROOT / "site" / "data"
SITE.mkdir(parents=True, exist_ok=True)

subprocess.run([sys.executable, str(ROOT / "proof_engine.py")], check=True)

stats = json.loads((ROOT / "out" / "stats.json").read_text(encoding="utf-8"))
trades = pd.read_csv(ROOT / "out" / "ledger_trades.csv")
waits = pd.read_csv(ROOT / "out" / "ledger_waits.csv")
eq = pd.read_csv(ROOT / "out" / "daily_equity.csv", index_col=0)

last_date = stats["last_date"]
last_close = stats["last_close"]

last = trades.iloc[-1]
in_mkt = str(last["exit_date"]) == "OPEN"
if in_mkt:
    days_state = (pd.Timestamp(last_date) - pd.Timestamp(last["entry_date"])).days
else:
    w = waits.iloc[-1]
    days_state = int(w["days"]) if str(w["end"]) == "NOW" else 0

signal = {"date": last_date, "state": "LONG" if in_mkt else "FLAT",
          "price": last_close,
          "decision_price": stats.get("decision_price"),
          "current_perf_pct": stats.get("current_perf_pct"),
          "last_closed_trade": stats.get("last_closed_trade"),
          "entry_date": str(last["entry_date"]) if in_mkt else None,
          "entry_price": float(last["entry_price"]) if in_mkt else None,
          "days_in_state": int(days_state),
          "generated_at": stats["generated_at"]}

chain_f = SITE / "hashes.json"
chain = json.loads(chain_f.read_text(encoding="utf-8")) if chain_f.exists() else []
prev = chain[-1]["hash"] if chain else "GENESIS"
payload = (ROOT / "out" / "ledger_trades.csv").read_bytes() + prev.encode()
h = hashlib.sha256(payload).hexdigest()
today = stats["generated_at"][:10]
if not chain or chain[-1]["date"] != today:
    chain.append({"date": today, "hash": h, "rows": int(len(trades))})
chain_f.write_text(json.dumps(chain, indent=1), encoding="utf-8")

(SITE / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
(SITE / "signal.json").write_text(json.dumps(signal, ensure_ascii=False, indent=1), encoding="utf-8")
(SITE / "ledger_trades.json").write_text(json.dumps(trades.to_dict(orient="records"), ensure_ascii=False), encoding="utf-8")
(SITE / "ledger_waits.json").write_text(json.dumps(waits.to_dict(orient="records"), ensure_ascii=False), encoding="utf-8")
(SITE / "equity.json").write_text(json.dumps([[str(i)[:10], round(v, 2)] for i, v in eq["equity"].items()]), encoding="utf-8")

print(f"PUBLISHED {today} | state={signal['state']} | chain={len(chain)} | rows={len(trades)}")

# ===== Telegram dawn bulletin — new professional format =====
import os, urllib.request, urllib.parse, json as _j, datetime as _dt
_tok = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
_ch  = os.environ.get("TELEGRAM_CHANNEL", "").strip()
if "/" in _ch:
    _ch = _ch.split("/")[-1]
if _ch and not _ch.startswith("@") and not _ch.lstrip("-").isdigit():
    _ch = "@" + _ch
if _tok and _ch:
    try:
        _p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site", "data", "signal.json")
        sig = _j.load(open(_p, encoding="utf-8"))
        st      = sig.get("state", "FLAT")
        price   = sig.get("price", 0)
        days    = sig.get("days_in_state", 0)
        entry   = sig.get("entry_date") or "—"
        dprice  = sig.get("decision_price")
        cperf   = sig.get("current_perf_pct")
        lclosed = sig.get("last_closed_trade")
        gen     = sig.get("generated_at", "")
        dpart   = gen[:10] or "2020-01-01"
        tpart   = gen[11:16] or "00:00"
        d0      = _dt.date.fromisoformat(dpart)
        issue   = (d0 - _dt.date(2020, 1, 1)).days
        issue_ar = "".join(chr(0x0660 + int(c)) for c in str(issue))
        wd = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"][d0.weekday()]
        pf_val = stats.get("profit_factor", 3.86)
        fp = str((chain[-1]["hash"]) if chain else "—")[:8]

        header = (f"🐢 إشارة الفجر | ذهب PAXGUSDT\n"
                  f"📅 {dpart} | {wd} | {tpart} UTC\n"
                  f"العدد: {issue_ar}\n\n")

        if st == "LONG":
            perf_line = f"📈 أداء المركز منذ الدخول ({entry}): {cperf:+.2f}%" if cperf is not None else "📈 المركز حديثٌ جدًا"
            dec_line = f"🛑 خط القرار: إغلاقٌ يومي أدنى {dprice:.2f}$ ← تنقلب الإشارة 🟡 غدًا"
            reason = "📝 السبب: EMA20 فوق EMA50 على الإغلاق اليومي"
            block = (f"🟢 الحالة: LONG — داخل السوق (اليوم {days})\n"
                     f"📍 سعر المرجع: {price:.2f}$\n"
                     f"{perf_line}\n"
                     f"{dec_line}\n"
                     f"🎯 الهدف: لا هدف ثابت — الربح يجري ما دامت 🟢\n"
                     f"{reason}\n")
        else:
            if lclosed:
                perf_line = f"📈 آخر صفقة مغلقة: {lclosed['entry_date']} ← {lclosed['exit_date']}: {lclosed['ret_pct']:+.2f}%"
            else:
                perf_line = "📈 لا صفقات مغلقة بعد"
            dec_line = f"🛎️ خط العودة: إغلاقٌ يومي أعلى {dprice:.2f}$ ← تنقلب الإشارة 🟢 غدًا"
            reason = "📝 السبب: EMA20 تحت EMA50 على الإغلاق اليومي"
            block = (f"🟡 الحالة: FLAT — خارج السوق، الدرع مرفوع (اليوم {days})\n"
                     f"📍 سعر المرجع: {price:.2f}$\n"
                     f"{perf_line}\n"
                     f"{dec_line}\n"
                     f"{reason}\n")

        footer = (f"\n⚙️ قاعدة المنهج: دخولٌ كامل على 🟢، خروجٌ كامل على 🟡 — بلا رافعة ولا أوامر جزئية\n"
                  f"📊 معامل الربح منذ 2020: {pf_val} — إصاباتٌ قليلة بأرباحٍ كبيرة\n"
                  f"🔒 البصمة: {fp}\n"
                  f"\n📒 الدفتر كاملًا: https://homvv99-ai.github.io/slow-gold/site/\n"
                  f"\n⚖️ التداول ينطوي على مخاطر مالية — ليست نصيحة استثمارية\n"
                  f"🐢 دفترٌ علنيّ موقع — الصبر قرارٌ موثق")

        txt = header + block + footer
        _url = f"https://api.telegram.org/bot{_tok}/sendMessage"
        _data = urllib.parse.urlencode({"chat_id": _ch, "text": txt}).encode()
        urllib.request.urlopen(urllib.request.Request(_url, data=_data), timeout=30).read()
        print("Telegram: dawn bulletin delivered to", _ch, "| issue", issue)
    except Exception as e:
        _b = ""
        try: _b = e.read().decode()
        except Exception: _b = str(e)
        print("Telegram send failed:", _b)
else:
    print("Telegram: secrets missing, skip")
