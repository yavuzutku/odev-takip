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

ACCESS_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = "1327842940416253"
RECIPIENT_PHONE = "905060308430"
VERIFY_TOKEN = os.environ.get("WEBHOOK_VERIFY_TOKEN", "odevtakip123")

if not ACCESS_TOKEN:
    raise RuntimeError("WHATSAPP_TOKEN environment variable eksik!")

ZAMAN_FORMAT = "%Y-%m-%dT%H:%M"
ZAMAN_FORMAT_SANIYE = "%Y-%m-%dT%H:%M:%S"


def send_whatsapp(message_text):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": RECIPIENT_PHONE,
        "type": "text",
        "text": {"body": message_text},
    }
    return requests.post(url, headers=headers, json=payload)


def send_whatsapp_interactive(message_text, doc_id):
    """Tamamlandı butonlu mesaj gönderir."""
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": RECIPIENT_PHONE,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": message_text},
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {"id": f"done_{doc_id}", "title": "✅ Tamamlandı"},
                    }
                ]
            },
        },
    }
    res = requests.post(url, headers=headers, json=payload)
    if res.status_code != 200:
        # Interactive başarısız olursa düz metin dene (ör. 24 saat penceresi dışıysa)
        print(f"Interactive gönderim hatası: {res.status_code} {res.text}")
        return send_whatsapp(message_text)
    return res


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
    send_whatsapp(liste_metni("📅 *Bugünkü Ödevler*", docs))


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
    send_whatsapp(liste_metni("🗓️ *Bu Haftaki Ödevler*", docs))


@app.route("/", methods=["GET"])
def home():
    return render_template("index.html")


@app.route("/webhook", methods=["GET"])
def webhook_verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200
    return "Forbidden", 403


@app.route("/webhook", methods=["POST"])
def webhook_receive():
    data = request.get_json(silent=True) or {}
    try:
        entry = data["entry"][0]["changes"][0]["value"]
        messages = entry.get("messages")
        if not messages:
            return jsonify({"status": "ignored"}), 200
        msg = messages[0]

        if msg.get("type") == "interactive":
            btn = msg.get("interactive", {}).get("button_reply", {})
            btn_id = btn.get("id", "")
            if btn_id.startswith("done_"):
                doc_id = btn_id[len("done_"):]
                db.collection("odevler").document(doc_id).update({"tamamlandi": True})
                send_whatsapp("✅ Ödev tamamlandı olarak işaretlendi, hatırlatmalar durduruldu.")

        elif msg.get("type") == "text":
            text = msg.get("text", {}).get("body", "").strip().lower()
            text = text.replace("ü", "u").replace("ğ", "g")
            if text in ("gun", "bugün", "bugun"):
                gunluk_liste_gonder()
            elif text == "hafta":
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
                        res = send_whatsapp_interactive(mesaj_olustur(data, dk), d.id)
                        if res.status_code == 200:
                            h = {"dk": dk, "gonderildi": True}
                            degisti = True
                            gonderilen_sayisi += 1
                            ilk_gonderim_bu_turda = True
                        else:
                            print(f"WhatsApp gönderim hatası ({d.id}, {dk} dk): {res.status_code} {res.text}")
                yeni_liste.append(h)
            if degisti:
                odevler_ref.document(d.id).update(
                    {"hatirlatmalar": yeni_liste, "son_hatirlatma": now.strftime(ZAMAN_FORMAT_SANIYE)}
                )
        else:
            if not data.get("gonderildi") and teslim_tarihi <= now_str:
                res = send_whatsapp_interactive(mesaj_olustur(data, 0), d.id)
                if res.status_code == 200:
                    odevler_ref.document(d.id).update(
                        {"gonderildi": True, "son_hatirlatma": now.strftime(ZAMAN_FORMAT_SANIYE)}
                    )
                    gonderilen_sayisi += 1
                    ilk_gonderim_bu_turda = True
                else:
                    print(f"WhatsApp gönderim hatası ({d.id}): {res.status_code} {res.text}")

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
                    res = send_whatsapp_interactive(mesaj_olustur(data, kalan_dk, tekrar=True), d.id)
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