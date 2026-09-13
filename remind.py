import os, urllib.request, urllib.parse, datetime

tok = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
ch  = os.environ.get("TELEGRAM_CHANNEL", "").strip()
if "/" in ch:
    ch = ch.split("/")[-1]
if ch and not ch.startswith("@") and not ch.lstrip("-").isdigit():
    ch = "@" + ch

hour = datetime.datetime.now(datetime.timezone.utc).hour
if hour < 12:
    txt = ("☕ موعد روتين الألسنة الثلاثة (دقيقتان):\n"
           "1) انسخ بطاقة الفجر أعلاه كما هي → قناة واتساب\n"
           "2) انسخ تغريدة 𝕏 من رسالة (انسخ وانشر) → انشرها في 𝕏\n"
           "3) إن وُجدت حلقة خيط اليوم: انشرها في 𝕏 كبداية خيط\n"
           "🐢 الصوت واحد: النص المولَّد هو المنشور، حرفًا بحرف")
else:
    txt = ("🌙 سؤال الراعي المسائي:\n"
           "هل نُشر اليوم على 𝕏 وواتساب؟\n"
           "إن لا — النسخ ما زالت أعلى القناة، دقيقتان وتنام مرتاحًا.\n"
           "وإن نعم — فالسلحفاة تفخر بك 🐢")

if tok and ch:
    data = urllib.parse.urlencode({"chat_id": ch, "text": txt}).encode()
    urllib.request.urlopen(urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage", data=data), timeout=30).read()
    print("shepherd spoke")
else:
    print("secrets missing, skip")
