from datetime import datetime, timedelta
import json
import os
import firebase_admin
from firebase_admin import credentials, firestore
from flask import Flask, jsonify, render_template, request
import requests

app = Flask(__name__, template_folder=".")

if os.environ.get("FIREBASE_KEY"):
    cred_json = json.loads(os.environ.get("FIREBASE_KEY"))
    cred = credentials.Certificate(cred_json)
else:
    cred = credentials.Certificate("firebase_key.json")

firebase_admin.initialize_app(cred)
db = firestore.client()

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_VERIFY_TOKEN", "odevtakip123")

if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable eksik!")
if not CHAT_ID:
    raise RuntimeError("TELEGRAM_CHAT_ID environment variable eksik!")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

ZAMAN_FORMAT = "%Y-%m-%dT%H:%M"
ZAMAN_FORMAT_SANIYE = "%Y-%m-%dT%H:%M:%S"


def send_telegram(message_text):
    payload = {
        "chat_id": CHAT_ID,
        "text": message_text,
        "parse_mode": "Markdown",
    }
    return requests.post(f"{TELEGRAM_API}/sendMessage", json=payload)


def send_telegram_interactive(message_text, doc_id):
    """Tamamlandı butonlu mesaj gönderir."""
    payload = {
        "chat_id": CHAT_ID,
        "text": message_text,
        "parse_mode": "Markdown",
        "reply_markup": {
            "inline_keyboard": [[
                {"text": "✅ Tamamlandı", "callback_data": f"done_{doc_id}"}
            ]]
        },
    }
    res = requests.post(f"{TELEGRAM_API}/sendMessage", json=payload)
    if res.status_code != 200:
        print(f"Interactive gönderim hatası: {res.status_code} {res.text}")
        return send_telegram(message_text)
    return res


def answer_callback_query(callback_query_id, text=None):
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    requests.post(f"{TELEGRAM_API}/answerCallbackQuery", json=payload)


def sure_metni(dakika):
    if dakika <= 0:
        return "Teslim zamanı geldi!"
    if dakika < 60:
        return f"{dakika} dakika kaldı"
    if dakika < 1440:
        saat = dakika // 60
        return f"{saat} saat kaldı"
    gun = dakika // 1440
    return f"{gun} gün kaldı"


def mesaj_olustur(data, dakika, tekrar=False):
    baslik = "🔁 *HATIRLATMA (tekrar)*" if tekrar else "🚨 *ÖDEV HATIRLATMASI!*"
    satirlar = [
        baslik,
        "",
        f"📚 *Ödev:* {data.get('baslik')}",
        f"📖 *Ders:* {data.get('ders') or '-'}",
    ]
    if data.get("notlar"):
        satirlar.append(f"📝 *Not:* {data.get('notlar')}")
    satirlar.append(f"⏰ *{sure_metni(dakika)}*")
    satirlar.append(f"🗓️ *Teslim:* {data.get('teslim_tarihi')}")
    satirlar.append("")
    satirlar.append("Tamamladıysan aşağıdaki butona bas 👇")
    return "\n".join(satirlar)


def liste_metni(baslik, docs):
    docs = list(docs)
    if not docs:
        return f"{baslik}\n\nÖdev bulunamadı 🎉"
    satirlar = [baslik, ""]
    for d in docs:
        v = d.to_dict()
        durum = "✅" if v.get("tamamlandi") else "🔲"
        satirlar.append(f"{durum} *{v.get('baslik')}* ({v.get('ders') or '-'})")
        if v.get("notlar"):
            satirlar.append(f"   📝 {v.get('notlar')}")
        satirlar.append(f"   ⏰ {v.get('teslim_tarihi')}")
        satirlar.append("")
    return "\n".join(satirlar).strip()


def gunluk_liste_gonder():
    now = datetime.utcnow()
    start = now.strftime("%Y-%m-%dT00:00")
    end = now.strftime("%Y-%m-%dT23:59")
    docs = (
        db.collection("odevler")
        .where("teslim_tarihi", ">=", start)
        .where("teslim_tarihi", "<=", end)
        .stream()
    )
    send_telegram(liste_metni("📅 *Bugünkü Ödevler*", docs))


def haftalik_liste_gonder():
    now = datetime.utcnow()
    end_dt = now + timedelta(days=7)
    start = now.strftime(ZAMAN_FORMAT)
    end = end_dt.strftime(ZAMAN_FORMAT)
    docs = (
        db.collection("odevler")
        .where("teslim_tarihi", ">=", start)
        .where("teslim_tarihi", "<=", end)
        .stream()
    )
    send_telegram(liste_metni("🗓️ *Bu Haftaki Ödevler*", docs))


@app.route("/", methods=["GET"])
def home():
    return render_template("index.html")


@app.route("/webhook", methods=["POST"])
def webhook_receive():
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if secret != WEBHOOK_SECRET:
        return "Forbidden", 403

    data = request.get_json(silent=True) or {}
    print(f"[DEBUG] Gelen webhook verisi: {json.dumps(data, ensure_ascii=False)}")
    try:
        if "callback_query" in data:
            cq = data["callback_query"]
            cq_data = cq.get("data", "")
            if cq_data.startswith("done_"):
                doc_id = cq_data[len("done_"):]
                db.collection("odevler").document(doc_id).update({"tamamlandi": True})
                answer_callback_query(cq["id"], "Tamamlandı olarak işaretlendi ✅")
                send_telegram("✅ Ödev tamamlandı olarak işaretlendi, hatırlatmalar durduruldu.")

        elif "message" in data:
            text = data["message"].get("text", "").strip().lower()
            text = text.replace("ü", "u").replace("ğ", "g")
            if text in ("gun", "bugün", "bugun", "/gun"):
                gunluk_liste_gonder()
            elif text in ("hafta", "/hafta"):
                haftalik_liste_gonder()

    except Exception as e:
        print(f"Webhook işleme hatası: {e}")

    return jsonify({"status": "ok"}), 200


@app.route("/check-assignments", methods=["GET"])
def check_assignments():
    now = datetime.utcnow()
    now_str = now.strftime(ZAMAN_FORMAT)
    odevler_ref = db.collection("odevler")
    docs = odevler_ref.stream()

    gonderilen_sayisi = 0

    for d in docs:
        data = d.to_dict()

        if data.get("tamamlandi"):
            continue

        teslim_tarihi = data.get("teslim_tarihi")
        if not teslim_tarihi:
            continue

        try:
            teslim_dt = datetime.strptime(teslim_tarihi, ZAMAN_FORMAT)
        except ValueError:
            continue

        hatirlatmalar = data.get("hatirlatmalar")
        ilk_gonderim_bu_turda = False

        if hatirlatmalar:
            yeni_liste = []
            degisti = False
            for h in hatirlatmalar:
                dk = h.get("dk", 0)
                if not h.get("gonderildi"):
                    tetik_zamani = teslim_dt - timedelta(minutes=dk)
                    if tetik_zamani <= now:
                        res = send_telegram_interactive(mesaj_olustur(data, dk), d.id)
                        if res.status_code == 200:
                            h = {"dk": dk, "gonderildi": True}
                            degisti = True
                            gonderilen_sayisi += 1
                            ilk_gonderim_bu_turda = True
                        else:
                            print(f"Telegram gönderim hatası ({d.id}, {dk} dk): {res.status_code} {res.text}")
                yeni_liste.append(h)
            if degisti:
                odevler_ref.document(d.id).update(
                    {"hatirlatmalar": yeni_liste, "son_hatirlatma": now.strftime(ZAMAN_FORMAT_SANIYE)}
                )
        else:
            if not data.get("gonderildi") and teslim_tarihi <= now_str:
                res = send_telegram_interactive(mesaj_olustur(data, 0), d.id)
                if res.status_code == 200:
                    odevler_ref.document(d.id).update(
                        {"gonderildi": True, "son_hatirlatma": now.strftime(ZAMAN_FORMAT_SANIYE)}
                    )
                    gonderilen_sayisi += 1
                    ilk_gonderim_bu_turda = True
                else:
                    print(f"Telegram gönderim hatası ({d.id}): {res.status_code} {res.text}")

        # 30 dakikada bir tekrar hatırlatma (ilk mesaj daha önce gitmiş ve tamamlanmamışsa)
        if not ilk_gonderim_bu_turda:
            daha_once_gonderildi = data.get("gonderildi") or any(
                h.get("gonderildi") for h in (hatirlatmalar or [])
            )
            if daha_once_gonderildi:
                son = data.get("son_hatirlatma")
                tekrar_gerekli = True
                if son:
                    try:
                        son_dt = datetime.strptime(son, ZAMAN_FORMAT_SANIYE)
                        if now - son_dt < timedelta(minutes=30):
                            tekrar_gerekli = False
                    except ValueError:
                        pass
                if tekrar_gerekli:
                    kalan_dk = int((teslim_dt - now).total_seconds() // 60)
                    res = send_telegram_interactive(mesaj_olustur(data, kalan_dk, tekrar=True), d.id)
                    if res.status_code == 200:
                        odevler_ref.document(d.id).update(
                            {"son_hatirlatma": now.strftime(ZAMAN_FORMAT_SANIYE)}
                        )
                        gonderilen_sayisi += 1
                    else:
                        print(f"Tekrar hatırlatma hatası ({d.id}): {res.status_code} {res.text}")

    return jsonify({"status": "ok", "gonderilen_bildirim": gonderilen_sayisi})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5001)))