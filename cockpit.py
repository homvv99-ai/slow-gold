import json, time, os, requests

TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT", "")
OUT = "site/data"
CELL_IDS = ["ST3D", "STUW", "STBR", "JDAR", "PT3D", "CT3U"]

def log(*a):
    print("[COCKPIT]", *a, flush=True)

def tg(text):
    if TG_TOKEN and TG_CHAT:
        try:
            requests.post("https://api.telegram.org/bot" + TG_TOKEN + "/sendMessage",
                          data={"chat_id": TG_CHAT, "text": text}, timeout=15)
        except Exception as e:
            log("tg fail", str(e)[:60])

def tg_safe(st, text):
    try:
        tg(text)
    except Exception:
        pass
    st["events"].append([int(time.time()), "tg", text[:80]])

def card_text(st):
    lines = ["📅 بطاقة " + st.get("day", "")]
    eq = sum(x[8] for x in st["ledger"])
    lines.append("الرأس: " + str(round(100 + eq, 2)) + "$")
    for cid in CELL_IDS:
        cell = st["cells"].get(cid, {})
        tr = cell.get("trades", 0)
        w = cell.get("wins", 0)
        wp = round(w * 100.0 / tr, 2) if tr else 0
        lines.append(cid + ": " + str(tr) + " صفقة فوز " + str(wp) + "% حالة " + cell.get("status", "awake"))
    lines.append("فرص فائتة: " + str(len(st["waits"])))
    sh = st.get("shadow_stats", {})
    if sh:
        lines.append("ظل: " + ", ".join(k + "=" + str(v[1]) + "/" + str(v[0]) for k, v in sh.items()))
    return "\n".join(lines)

def write_view(st, now):
    try:
        st["view_ts"] = now
        eq = 100.0 + sum(x[8] for x in st["ledger"])
        cells = []
        for cid in CELL_IDS:
            c = st["cells"].get(cid, {})
            cells.append({"id": cid, "trades": c.get("trades", 0),
                          "wins": c.get("wins", 0), "status": c.get("status", "awake")})
        trades = [{"t": x[0], "id": x[1], "dir": x[2], "in": x[3], "out": x[4],
                   "win": x[7], "pnl": x[8]} for x in st["ledger"][-40:]]
        view = {"ts": now, "eq": round(eq, 2), "halt": bool(st.get("halt")),
                "filter_off": bool(st.get("filter_off")), "cells": cells,
                "trades": trades, "shadow": st.get("shadow_stats", {}),
                "waits": len(st.get("waits", []))}
        open(OUT + "/lab_view.json", "w", encoding="utf-8").write(json.dumps(view, ensure_ascii=False))
    except Exception as e:
        log("view write fail", str(e)[:60])

def kb():
    rows = [
        [{"text": "▶️ استئناف", "callback_data": "resume"}, {"text": "⏸ إيقاف", "callback_data": "halt"}],
        [{"text": "📊 البطاقة", "callback_data": "card"}, {"text": "📒 الكشف", "callback_data": "ledger"}],
        [{"text": "😴 الخلايا", "callback_data": "cells"}, {"text": "🔍 ظل وفوائت", "callback_data": "shadow"}],
        [{"text": "🧭 عكس الفلتر", "callback_data": "filt"}, {"text": "🔄 المحرك", "callback_data": "engine"}],
    ]
    return {"reply_markup": json.dumps({"inline_keyboard": rows})}

def panel_text(st):
    eq = 100.0 + sum(x[8] for x in st["ledger"])
    return ("🎛 <b>كابينة القيادة</b>\nالرأس: " + str(round(eq, 2)) + "$\nالحالة: " +
            ("موقوف 🛑" if st.get("halt") else "يعمل ✅") + "\nالفلتر: " +
            ("مطفأ" if st.get("filter_off") else "يعمل") + "\nاضغط زرًا:")

def tg_panel(st):
    try:
        d = {"chat_id": TG_CHAT, "text": panel_text(st), "parse_mode": "HTML"}
        d.update(kb())
        requests.post("https://api.telegram.org/bot" + TG_TOKEN + "/sendMessage", data=d, timeout=15)
    except Exception:
        pass

def tg_cells(st):
    lines = []
    rows = []
    for cid in CELL_IDS:
        c = st["cells"].get(cid, {})
        lines.append(cid + ": " + c.get("status", "awake"))
        rows.append([{"text": cid + " 😴 نوم", "callback_data": "cs:" + cid},
                     {"text": cid + " ▶️ إيقاظ", "callback_data": "cw:" + cid}])
    tg("حالة الخلايا:\n" + "\n".join(lines))
    try:
        requests.post("https://api.telegram.org/bot" + TG_TOKEN + "/sendMessage",
                      data={"chat_id": TG_CHAT, "text": "تحكم بالخلايا:",
                            "reply_markup": json.dumps({"inline_keyboard": rows})}, timeout=15)
    except Exception:
        pass

def act(st, data):
    now = int(time.time())
    if data == "resume":
        st["halt"] = False
        st["halt_told"] = False
        st["events"].append([now, "BOT", "human resume"])
        tg_safe(st, "▶️ تم الاستئناف بقرار بشري")
    elif data == "halt":
        st["halt"] = True
        st["halt_told"] = False
        st["events"].append([now, "BOT", "human halt"])
        tg_safe(st, "⏸ أوقفه القائد يدويًا")
    elif data == "card":
        tg(card_text(st))
    elif data == "ledger":
        last = st["ledger"][-15:]
        tg("\n".join(str(x) for x in last) if last else "لا صفقات بعد")
    elif data == "shadow":
        sh = st.get("shadow_stats", {})
        tg("ظل: " + (", ".join(k + "=" + str(v[1]) + "/" + str(v[0]) for k, v in sh.items()) if sh else "لا شيء") +
           "\nفوائت: " + str(len(st.get("waits", []))))
    elif data == "filt":
        st["filter_off"] = not st.get("filter_off", False)
        st["events"].append([now, "BOT", "filter_off=" + str(st["filter_off"])])
        tg_safe(st, "🧭 الفلتر الآن: " + ("مطفأ" if st.get("filter_off") else "يعمل"))
    elif data == "engine":
        tg("آخر نبضة قبل " + str(now - st.get("view_ts", now)) + " ث\nhalt=" + str(st.get("halt")) +
           " · filter_off=" + str(st.get("filter_off")) + " · schema=" + st.get("buy_schema", ""))
    elif data == "cells":
        tg_cells(st)
    elif data.startswith("cs:"):
        c = st["cells"].get(data[3:])
        if c:
            c["status"] = "asleep"
            c["sleep_until"] = now + 86400
            st["events"].append([now, data[3:], "human sleep"])
            tg_safe(st, "😴 " + data[3:] + " نامت بأمر القائد")
    elif data.startswith("cw:"):
        c = st["cells"].get(data[3:])
        if c:
            c["status"] = "awake"
            c["last20"] = []
            st["events"].append([now, data[3:], "human wake"])
            tg_safe(st, "▶️ " + data[3:] + " استيقظت بأمر القائد")

def handle_tg(st):
    write_view(st, int(time.time()))
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        r = requests.get("https://api.telegram.org/bot" + TG_TOKEN + "/getUpdates",
                         params={"offset": st.get("tg_offset", 0), "timeout": 5}, timeout=15).json()
        if not r.get("ok", True):
            log("tg poll err", str(r.get("description"))[:100])
            return
        for up in r.get("result", []):
            st["tg_offset"] = up["update_id"] + 1
            cb = up.get("callback_query")
            if cb:
                try:
                    requests.post("https://api.telegram.org/bot" + TG_TOKEN + "/answerCallbackQuery",
                                  data={"callback_query_id": cb.get("id")}, timeout=10)
                except Exception:
                    pass
                act(st, cb.get("data", ""))
                try:
                    m = cb.get("message", {})
                    d = {"chat_id": m.get("chat", {}).get("id", TG_CHAT),
                         "message_id": m.get("message_id"),
                         "text": panel_text(st), "parse_mode": "HTML"}
                    d.update(kb())
                    requests.post("https://api.telegram.org/bot" + TG_TOKEN + "/editMessageText", data=d, timeout=15)
                except Exception:
                    pass
                continue
            txt = up.get("message", {}).get("text", "")
            if txt.startswith("/menu"):
                tg_panel(st)
            elif txt.startswith("/resume"):
                act(st, "resume")
            elif txt.startswith("/halt"):
                act(st, "halt")
            elif txt.startswith("/card"):
                tg(card_text(st))
            elif txt.startswith("/ledger"):
                last = st["ledger"][-15:]
                tg("\n".join(str(x) for x in last) if last else "لا صفقات بعد")
            elif txt.startswith("/sleeps"):
                tg("\n".join(str(x) for x in st["events"][-15:]) if st["events"] else "لا أحداث")
            elif txt.startswith("/waits"):
                tg("\n".join(str(x) for x in st["waits"][-15:]) if st["waits"] else "لا فوائت")
    except Exception as e:
        log("tg poll fail", str(e)[:60])
