import json, os, urllib.request, urllib.parse
from pathlib import Path

ROOT = Path(__file__).parent
D = ROOT / "site" / "data"

sig = json.loads((D / "signal.json").read_text(encoding="utf-8"))

date   = sig.get("date", "")
state  = sig.get("state", "FLAT")
price  = sig.get("price", "")
days   = sig.get("days_in_state", "")
dprice = sig.get("decision_price")
cperf  = sig.get("current_perf_pct")
lclosed= sig.get("last_closed_trade")

lines = ["🐢 إشارة الفجر | ذهب PAXGUSDT", f"📅 {date}"]
if state == "LONG":
    lines.append(f"🟢 LONG — داخل السوق (اليوم {days})")
    lines.append(f"📍 المرجع: {price}$")
    if cperf is not None:
        lines.append(f"📈 منذ الدخول: {cperf:+.2f}%")
    if dprice:
        lines.append(f"🛑 خط القرار: إغلاق أدنى {dprice:.2f}$ ← تنقلب 🟡 غدًا")
else:
    lines.append(f"🟡 FLAT — خارج السوق، درع مرفوع (اليوم {days})")
    lines.append(f"📍 المرجع: {price}$")
    if lclosed:
        lines.append(f"📈 آخر صفقة: {lclosed['entry_date'][5:]} ← {lclosed['exit_date'][5:]}: {lclosed['ret_pct']:+.2f}%")
    if dprice:
        lines.append(f"🛎️ خط العودة: إغلاق أعلى {dprice:.2f}$ ← تنقلب 🟢 غدًا")
lines.append("⚙️ بلا رافعة، دخول/خروج كامل")
try:
    st = json.loads((D / "stats.json").read_text(encoding="utf-8"))
    lines.append(f"📊 معامل ربح 2020←: {st.get('profit_factor')}")
except Exception:
    pass
lines.append("📒 الدفتر: https://homvv99-ai.github.io/slow-gold/site/")

tweet = "\n".join(lines)

msg = "🐦 انسخ وانشر في 𝕏:\n\n" + tweet

ep = ""
tf = D / "thread.json"
if tf.exists():
    try:
        for item in json.loads(tf.read_text(encoding="utf-8")):
            if item.get("date") == date:
                ep = item.get("text", "")
    except Exception:
        ep = ""
if ep:
    msg += "\n\n🧵 حلقة الخيط اليوم (انسخها وانشرها):\n\n" + ep

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
