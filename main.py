from datetime import datetime, timedelta
import json
import os
import firebase_admin
from firebase_admin import credentials, firestore
from flask import Flask, jsonify, render_template, request, send_from_directory
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
    """Tamamlandı / Ertele butonlu mesaj gönderir."""
    payload = {
        "chat_id": CHAT_ID,
        "text": message_text,
        "parse_mode": "Markdown",
        "reply_markup": {
            "inline_keyboard": [[
                {"text": "✅ Tamamlandı", "callback_data": f"done_{doc_id}"},
                {"text": "⏰ Ertele", "callback_data": f"snooze_{doc_id}"},
            ]]
        },
    }
    res = requests.post(f"{TELEGRAM_API}/sendMessage", json=payload)
    if res.status_code != 200:
        print(f"Interactive gönderim hatası: {res.status_code} {res.text}")
        return send_telegram(message_text)
    return res


def send_snooze_options(doc_id):
    payload = {
        "chat_id": CHAT_ID,
        "text": "⏰ Ne kadar ertelensin?",
        "reply_markup": {
            "inline_keyboard": [[
                {"text": "30 dk", "callback_data": f"snooze30_{doc_id}"},
                {"text": "1 saat", "callback_data": f"snooze60_{doc_id}"},
                {"text": "Yarın", "callback_data": f"snoozetom_{doc_id}"},
            ]]
        },
    }
    requests.post(f"{TELEGRAM_API}/sendMessage", json=payload)


def answer_callback_query(callback_query_id, text=None):
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    requests.post(f"{TELEGRAM_API}/answerCallbackQuery", json=payload)


def sure_metni(dakika):
    if dakika < 0:
        gecikme = abs(dakika)
        if gecikme < 60:
            return f"{gecikme} dakika gecikti!"
        if gecikme < 1440:
            return f"{gecikme // 60} saat gecikti!"
        return f"{gecikme // 1440} gün gecikti!"
    if dakika == 0:
        return "Tam zamanı!"
    if dakika < 60:
        return f"{dakika} dakika kaldı"
    if dakika < 1440:
        saat = dakika // 60
        return f"{saat} saat kaldı"
    gun = dakika // 1440
    return f"{gun} gün kaldı"


def tekrar_araligi_dakika(teslim_dt, now):
    """Teslime kalan süreye göre gitgide sıklaşan hatırlatma aralığı (dk)."""
    kalan_dk = (teslim_dt - now).total_seconds() / 60
    if kalan_dk < 0:
        return 15    # süresi geçmiş -> tamamlanana kadar sık sık rahatsız et
    if kalan_dk <= 60:
        return 10    # son 1 saat -> çok sık
    if kalan_dk <= 360:
        return 30    # son 6 saat
    if kalan_dk <= 1440:
        return 120   # son 1 gün
    return 240       # daha uzun vadeli -> 4 saatte bir


def mesaj_olustur(data, dakika, tekrar=False):
    if not tekrar:
        baslik = "🚨 *ÖDEV HATIRLATMASI!*"
    elif dakika < 0:
        baslik = "🔴 *HÂLÂ TAMAMLANMADI!*"
    elif dakika <= 60:
        baslik = "🟠 *SON DAKİKA HATIRLATMASI!*"
    else:
        baslik = "🔁 *HATIRLATMA (tekrar)*"
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


def ertele_odev(doc_id, dakika=None, yarina=False):
    ref = db.collection("odevler").document(doc_id)
    snap = ref.get()
    if not snap.exists:
        return None
    data = snap.to_dict()
    eski = datetime.strptime(data["teslim_tarihi"], ZAMAN_FORMAT)
    now = datetime.utcnow()
    baslangic = max(eski, now)

    if yarina:
        yeni = now + timedelta(days=1)
        yeni = yeni.replace(hour=eski.hour, minute=eski.minute, second=0, microsecond=0)
    else:
        yeni = baslangic + timedelta(minutes=dakika)

    ref.update({
        "teslim_tarihi": yeni.strftime(ZAMAN_FORMAT),
        "gonderildi": False,
        "son_hatirlatma": None,
    })
    return data, yeni


def yardim_metni():
    return (
        "🤖 *Komutlar*\n\n"
        "📅 *gun* – bugünkü ödevler\n"
        "🗓️ *hafta* – bu haftaki ödevler\n"
        "📋 *hepsi* – tüm bekleyen ödevler\n"
        "❓ *yardim* – bu mesaj\n\n"
        "Hatırlatma mesajındaki *Ertele* butonuyla bir ödevi 30 dk, 1 saat ya da yarına erteleyebilirsin."
    )


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


def tum_bekleyenler_gonder():
    docs = [d for d in db.collection("odevler").stream() if not d.to_dict().get("tamamlandi")]
    docs.sort(key=lambda d: d.to_dict().get("teslim_tarihi") or "")
    send_telegram(liste_metni("📋 *Tüm Bekleyen Ödevler*", docs))


@app.route("/", methods=["GET"])
def home():
    return render_template("index.html")


@app.route("/manifest.json", methods=["GET"])
def manifest():
    return send_from_directory(".", "manifest.json", mimetype="application/manifest+json")


@app.route("/sw.js", methods=["GET"])
def service_worker():
    return send_from_directory(".", "sw.js", mimetype="application/javascript")


@app.route("/icon-192.png", methods=["GET"])
def icon_192():
    return send_from_directory(".", "icon-192.png", mimetype="image/png")


@app.route("/icon-512.png", methods=["GET"])
def icon_512():
    return send_from_directory(".", "icon-512.png", mimetype="image/png")


@app.route("/icon-512-maskable.png", methods=["GET"])
def icon_512_maskable():
    return send_from_directory(".", "icon-512-maskable.png", mimetype="image/png")


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

            elif cq_data.startswith("snooze30_"):
                doc_id = cq_data[len("snooze30_"):]
                sonuc = ertele_odev(doc_id, dakika=30)
                answer_callback_query(cq["id"], "30 dakika ertelendi")
                if sonuc:
                    eski_data, yeni = sonuc
                    send_telegram(f"⏰ *{eski_data.get('baslik')}* 30 dakika ertelendi.\n🗓️ Yeni zaman: {yeni.strftime(ZAMAN_FORMAT)}")

            elif cq_data.startswith("snooze60_"):
                doc_id = cq_data[len("snooze60_"):]
                sonuc = ertele_odev(doc_id, dakika=60)
                answer_callback_query(cq["id"], "1 saat ertelendi")
                if sonuc:
                    eski_data, yeni = sonuc
                    send_telegram(f"⏰ *{eski_data.get('baslik')}* 1 saat ertelendi.\n🗓️ Yeni zaman: {yeni.strftime(ZAMAN_FORMAT)}")

            elif cq_data.startswith("snoozetom_"):
                doc_id = cq_data[len("snoozetom_"):]
                sonuc = ertele_odev(doc_id, yarina=True)
                answer_callback_query(cq["id"], "Yarına ertelendi")
                if sonuc:
                    eski_data, yeni = sonuc
                    send_telegram(f"⏰ *{eski_data.get('baslik')}* yarına ertelendi.\n🗓️ Yeni zaman: {yeni.strftime(ZAMAN_FORMAT)}")

            elif cq_data.startswith("snooze_"):
                doc_id = cq_data[len("snooze_"):]
                answer_callback_query(cq["id"])
                send_snooze_options(doc_id)

        elif "message" in data:
            text = data["message"].get("text", "").strip().lower()
            text = text.replace("ü", "u").replace("ğ", "g").replace("ı", "i")
            if text in ("gun", "bugün", "bugun", "/gun"):
                gunluk_liste_gonder()
            elif text in ("hafta", "/hafta"):
                haftalik_liste_gonder()
            elif text in ("hepsi", "tumu", "/hepsi"):
                tum_bekleyenler_gonder()
            elif text in ("yardim", "/yardim", "/help", "yardım"):
                send_telegram(yardim_metni())

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

        # Tamamlanana kadar sıklığı gitgide artan tekrar hatırlatmaları
        if not ilk_gonderim_bu_turda:
            daha_once_gonderildi = data.get("gonderildi") or any(
                h.get("gonderildi") for h in (hatirlatmalar or [])
            )
            if daha_once_gonderildi:
                son = data.get("son_hatirlatma")
                aralik_dk = tekrar_araligi_dakika(teslim_dt, now)
                tekrar_gerekli = True
                if son:
                    try:
                        son_dt = datetime.strptime(son, ZAMAN_FORMAT_SANIYE)
                        if now - son_dt < timedelta(minutes=aralik_dk):
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