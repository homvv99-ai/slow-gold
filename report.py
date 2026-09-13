import json, os, urllib.request, urllib.parse
from pathlib import Path

ROOT = Path(__file__).parent
D = ROOT / "site" / "data"

sig = json.loads((D / "signal.json").read_text(encoding="utf-8"))
state = sig.get("state", "FLAT")
days  = sig.get("days_in_state", "")
edate = sig.get("entry_date", "")

week_txt = "غير متوفر"
try:
    url = "https://api.binance.com/api/v3/klines?symbol=PAXGUSDT&interval=1d&limit=8"
    kl = json.loads(urllib.request.urlopen(url, timeout=30).read())
    closes = [float(k[4]) for k in kl]
    week_chg = (closes[-2] / closes[0] - 1) * 100
    week_txt = f"{week_chg:+.2f}%"
except Exception:
    pass

trades = json.loads((D / "ledger_trades.json").read_text(encoding="utf-8"))
last = trades[-1] if trades else None
last_txt = f"{last['entry_date']} ← {last['exit_date']}: {last['ret_pct']:+.2f}%" if last else "لا صفقات بعد"

if state == "LONG":
    pos = f"🟢 داخل السوق منذ {edate} ({days} يومًا)"
else:
    pos = f"🟡 خارج السوق — درعٌ مرفوع ({days} يومًا)"

msg = (f"📊 تقرير الأسبوع — Slow Gold\n"
       f"تغيّر سعر الذهب هذا الأسبوع: {week_txt}\n"
       f"{pos}\n"
       f"آخر صفقة موثقة: {last_txt}\n"
       f"لا وعود، لا توصيات — أرقامٌ موثقة فقط 🐢\n"
       f"📒 https://homvv99-ai.github.io/slow-gold/site/")

out = "📊 انسخ وانشر في 𝕏:\n\n" + msg

tok = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
ch  = os.environ.get("TELEGRAM_CHANNEL", "").strip()
if "/" in ch:
    ch = ch.split("/")[-1]
if ch and not ch.startswith("@") and not ch.lstrip("-").isdigit():
    ch = "@" + ch
if tok and ch:
    data = urllib.parse.urlencode({"chat_id": ch, "text": out}).encode()
    urllib.request.urlopen(urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage", data=data), timeout=30).read()
    print("weekly report delivered")
else:
    print("secrets missing, skip")
