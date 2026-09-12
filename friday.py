import json, os, urllib.request, urllib.parse, datetime as dt
from pathlib import Path

ROOT = Path(__file__).parent
D = ROOT / "site" / "data"

sig = json.loads((D / "signal.json").read_text(encoding="utf-8"))
tr  = json.loads((D / "ledger_trades.json").read_text(encoding="utf-8"))
eq  = json.loads((D / "equity.json").read_text(encoding="utf-8"))

today = dt.date.fromisoformat(sig["date"])
week = [today - dt.timedelta(days=i) for i in range(6, -1, -1)]
wstr = [d.isoformat() for d in week]

def in_market(d):
    ds = d.isoformat()
    for t in tr:
        if t["entry_date"] <= ds and (str(t["exit_date"]) == "OPEN" or t["exit_date"] >= ds):
            return True
    return False

days_in = sum(1 for d in week if in_market(d))
days_out = 7 - days_in

events = []
for t in tr:
    ex = str(t["exit_date"])
    if t["entry_date"] in wstr:
        events.append(f"• يوم {t['entry_date']}: دخلت الآلة عند {t['entry_price']}$")
    if ex != "OPEN" and ex in wstr:
        events.append(f"• يوم {ex}: ترجّلت الآلة بعد {t['days']} يومًا بنتيجة {t['ret_pct']}%")

def eq_on(d):
    last = None
    for date, v in eq:
        if date <= d:
            last = v
        else:
            break
    return last

w0 = eq_on((today - dt.timedelta(days=7)).isoformat())
w1 = eq_on(today.isoformat())
wk_chg = round((w1 / w0 - 1) * 100, 2) if w0 else 0.0

issue = (today - dt.date(2020, 1, 1)).days // 7
def AR(n): return "".join(chr(0x0660 + int(c)) for c in str(n))

POOL = [
    "السوق لا يكافئ الأسرع… بل الأبقى.",
    "خارج السوق… داخل الخطة.",
    "الدرع لا يلمع… لكنه يُبقيك حيًا.",
    "الصبر ليس بطيئًا؛ الصبر دقيق التصويب.",
]
line = POOL[issue % len(POOL)]

if events:
    head = "هذا الأسبوع حرّكت الآلة دفاتها:"
    body = "\n".join(events)
else:
    head = "هذا الأسبوع لم تتحوّل الآلة:"
    body = "سبعة أيامٍ من الصبر الموثق — لا دخولٌ اندفاعي، ولا خروجٌ مذعور."

summary = ("🟢" if days_in >= 4 else "🟡") + f" {AR(days_in)} من 7 أيامٍ داخل السوق، و{AR(days_out)} خارجًا — " + ("أسبوعُ ركوبٍ هادئ." if days_in > days_out else "أسبوعُ انتظارٍ مسلّح.")

txt = (f"🐢 حكاية الأسبوع — العدد {AR(issue)}\n"
       f"الجمعة {today.isoformat()} | نسخة الغروب\n\n"
       f"{summary}\n\n"
       f"{head}\n{body}\n\n"
       f"📈 الدفتر هذا الأسبوع: {wk_chg:+}%\n"
       f"⏳ أيام (لا): {AR(days_out)} من 7 — دروعٌ لا تفويت.\n\n"
       f"ترنيمة الأسبوع:\n«{line}»\n\n"
       "📒 الدفتر الكامل: https://homvv99-ai.github.io/slow-gold/site/\n"
       "🐢 دفترٌ علنيّ موقع — الصبر قرارٌ موثق\n\n"
       "⚖️ التداول ينطوي على مخاطر مالية — ليست نصيحة استثمارية.")

tok = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
ch  = os.environ.get("TELEGRAM_CHANNEL", "").strip()
if "/" in ch:
    ch = ch.split("/")[-1]
if ch and not ch.startswith("@") and not ch.lstrip("-").isdigit():
    ch = "@" + ch
if tok and ch:
    data = urllib.parse.urlencode({"chat_id": ch, "text": txt}).encode()
    urllib.request.urlopen(urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage", data=data), timeout=30).read()
    print("Friday dusk story delivered | issue", issue)
else:
    print("secrets missing, skip")
