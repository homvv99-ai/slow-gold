import json, glob, os, datetime

SITE="site"
BASE="https://homvv99-ai.github.io/slow-gold/site/"
sig=json.load(open(f"{SITE}/data/signal.json",encoding="utf-8"))
st=json.load(open(f"{SITE}/data/stats.json",encoding="utf-8"))
day=(sig.get("generated_at") or "")[:10] or datetime.date.today().isoformat()

state_ar = "🟢 داخل السوق (LONG)" if sig.get("state")=="LONG" else "🟡 خارج السوق — سيولة جاهزة (FLAT)"

CSS = "body{background:#14100b;color:#e8dcc0;font-family:Tahoma,Segoe UI,sans-serif;margin:0;padding:24px;line-height:1.8}h1{color:#d4af37}.card{background:#1f1913;border:1px solid #3a2f1d;border-radius:12px;padding:16px;margin:12px 0}.dim{color:#9a8b6a}a{color:#d4af37}"

page = f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>نشرة الفجر — {day} | Slow Gold</title>
<meta name="description" content="أرشيف دائم: نشرة فجر {day} لذهب PAXGUSDT — الحالة والسعر والأرقام المدققة كما نُشرت يومها.">
<meta property="og:title" content="نشرة الفجر — {day} | الذهب الصبور">
<meta property="og:image" content="{BASE}banner.png">
<link rel="icon" href="../turtle.png">
<link rel="canonical" href="{BASE}archive/{day}.html">
<style>{CSS}</style>
</head>
<body>
<h1>🐢 نشرة الفجر — {day}</h1>
<div class="dim">صفحة أرشيف دائمة لا تُعدّل — كما نُشرت فجر هذا اليوم</div>
<div class="card"><b>الحالة:</b> {state_ar}<br>
<b>سعر المرجع:</b> {sig.get('price')} $<br>
<b>دخول المركز:</b> {sig.get('entry_date') or '—'} | <b>اليوم رقم:</b> {sig.get('days_in_state')}</div>
<div class="card"><b>أرقام الدفتر المدققة حتى هذا اليوم:</b><br>
الرصيد: {st.get('final_eq')} | العائد: {st.get('total_ret_pct')}% | أقصى تراجع: {st.get('max_dd_pct')}%<br>
معامل الربح: {st.get('profit_factor')} | صفقات: {st.get('trades_n')} | أيام (لا): {st.get('no_pct')}%</div>
<div class="card dim">المنهج: تقاطع EMA20/50 على الشمعة اليومية لـ PAXGUSDT. ليست نصيحة استثمارية؛ التداول مخاطرة.<br>
<a href="../index.html">📒 الدفتر الحي</a> · <a href="../en.html">🌐 English</a> · <a href="../research.html">🔬 المختبر</a></div>
</body>
</html>"""

os.makedirs(f"{SITE}/archive", exist_ok=True)
open(f"{SITE}/archive/{day}.html","w",encoding="utf-8").write(page)

days = sorted({os.path.basename(p)[:-5] for p in glob.glob(f"{SITE}/archive/20*.html")}, reverse=True)
rows = "".join(f'<div><a href="{d}.html">📄 نشرة {d}</a></div>' for d in days)
idx = f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<title>أرشيف النشرات | Slow Gold</title>
<link rel="icon" href="../turtle.png">
<style>{CSS}</style>
</head>
<body>
<h1>🗄 أرشيف النشرات اليومية</h1>
<div class="dim">كل نشرةٍ صدرت منذ تشغيل الأرشيف — صفحةٌ دائمة لا تُعدّل</div>
<div class="card">{rows}</div>
<div class="card"><a href="../index.html">📒 العودة للدفتر</a></div>
</body>
</html>"""
open(f"{SITE}/archive/index.html","w",encoding="utf-8").write(idx)

static = [("", "daily", "1.0"), ("en.html", "weekly", "0.9"), ("research.html", "monthly", "0.8"),
          ("birthday.html", "weekly", "0.8"), ("mirror.html", "daily", "0.7"), ("archive/index.html", "daily", "0.7"),
          ("api.html", "weekly", "0.7")]
seen = set()
urls = ""
def add(loc, lastmod, freq, prio):
    global urls
    if loc in seen:
        return
    seen.add(loc)
    lm = f"<lastmod>{lastmod}</lastmod>" if lastmod else ""
    urls += f"<url><loc>{loc}</loc>{lm}<changefreq>{freq}</changefreq><priority>{prio}</priority></url>\n"

for path, freq, prio in static:
    add(BASE+path, "", freq, prio)
for d in days:
    add(BASE+f"archive/{d}.html", d, "yearly", "0.6")
sm = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + urls + "</urlset>\n"
open(f"{SITE}/sitemap.xml","w",encoding="utf-8").write(sm)
print("archive built:", day, "| pages:", len(days), "| urls:", len(seen))
