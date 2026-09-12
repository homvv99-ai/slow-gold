import json, os, urllib.request, urllib.parse
from pathlib import Path

ROOT = Path(__file__).parent
D = ROOT / "site" / "data"

sig = json.loads((D / "signal.json").read_text(encoding="utf-8"))

date   = sig.get("date", "")
state  = sig.get("state", "FLAT")
price  = sig.get("price", "")
edate  = sig.get("entry_date", "")
eprice = sig.get("entry_price", "")
days   = sig.get("days_in_state", "")

if state == "LONG":
    pos = f"داخل السوق منذ {edate} ({days} يومًا) عند {eprice}$"
else:
    pos = f"خارج السوق منذ {edate} ({days} يومًا) — سيولةٌ تنتظر"

tweet = (f"🐢 Slow Gold — دفترٌ علنيّ\n"
         f"📅 {date}\n"
         f"الإشارة: {state} عند {price}$\n"
         f"{pos}\n"
         f"لا توقّع، لا توصيات — سطرٌ موثّق فقط.\n"
         f"📒 https://homvv99-ai.github.io/slow-gold/site/")

msg = "🐦 انسخ وانشر في 𝕏:\n\n" + tweet

tok = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
ch  = os.environ.get("TELEGRAM_CHANNEL", "").strip()
if "/" in ch:
    ch = ch.split("/")[-1]
if ch and not ch.startswith("@") and not ch.lstrip("-").isdigit():
    ch = "@" + ch
if tok and ch:
    data = urllib.parse.urlencode({"chat_id": ch, "text": msg}).encode()
    urllib.request.urlopen(urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage", data=data), timeout=30).read()
    print("tweet copy delivered")
else:
    print("secrets missing, skip")
