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
# ===== Telegram dawn voice (v3.5 — final signed bulletin) =====
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
        gen     = sig.get("generated_at", "")
        dpart   = gen[:10] or "2020-01-01"
        tpart   = gen[11:16] or "00:00"
        d0      = _dt.date.fromisoformat(dpart)
        issue   = (d0 - _dt.date(2020, 1, 1)).days
        issue_ar = str(issue).translate(str.maketrans("0123456789", "٠١٢٣٤٥٧٨٩"))
        wd = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"][d0.weekday()]
        mantra = ("نحن لا نخمّن القمة؛ نحن نقيس الاتجاه:\n"
                  "حيٌّ نركبه، ومنكسرٌ نترجله —\n"
                  "والدفتر يوقّع كل ترجّلٍ بتاريخه.\n")
        header = f"🐢 نشرة الفجر — العدد {issue_ar}\n{wd} {dpart} | {tpart} UTC\n\n"
        if st == "LONG":
            state_block = f"🟢 المركز الحالي: داخل السوق (LONG) — اليوم {days}\nدخول: {entry} | السعر لحظة النشر: {price}$\n\n"
            if entry != "—" and str(entry) == dpart:
                decision = ("قرار اليوم: دخولٌ مُعلن.\n"
                            "كل متبعي الإشارة يدخلون من هنا — بسعرٍ واحد، في اللحظة نفسها.\n"
                            + mantra)
            else:
                decision = ("قرار اليوم: انتظار.\n"
                            "نقاط الدخول تُعلن في نشرات التحوّل فقط،\n"
                            "وعندها يدخل القديم والجديد بسعرٍ واحد.\n"
                            + mantra)
        else:
            state_block = f"🟡 المركز الحالي: خارج السوق — سيولة جاهزة (FLAT) — اليوم {days}\nالسعر لحظة النشر: {price}$\n\n"
            decision = ("قرار اليوم: انتظارٌ في السيولة.\n"
                        "النشرة القادمة قد تكون نشرة دخول —\n"
                        "وعندها يدخل القديم والجديد بسعرٍ واحد.\n"
                        + mantra)
        txt = (header + state_block + decision +
               "\n📒 الدفتر الكامل: https://homvv99-ai.github.io/slow-gold/site/"
               "\n🐢 دفترٌ علنيّ موقع — الصبر قرارٌ موثق"
               + "\n\n⚖️ التداول ينطوي على مخاطر مالية — ليست نصيحة استثمارية.")
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
