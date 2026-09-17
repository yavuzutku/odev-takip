from datetime import datetime, timedelta
import json
import os
import firebase_admin
from firebase_admin import credentials, firestore
from flask import Flask, jsonify, render_template  # render_template eklendi
import requests

# template_folder="." ile index.html dosyasını doğrudan proje kök dizininden okur
app = Flask(__name__, template_folder=".")

# Firebase Baglantisi (Bulut ve Yerel Uyumlu)
if os.environ.get("FIREBASE_KEY"):
    cred_json = json.loads(os.environ.get("FIREBASE_KEY"))
    cred = credentials.Certificate(cred_json)
else:
    cred = credentials.Certificate("firebase_key.json")

firebase_admin.initialize_app(cred)
db = firestore.client()

# WhatsApp API Bilgilerin (token Render env variable'ından okunuyor)
ACCESS_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = "1327842940416253"
RECIPIENT_PHONE = "905060308430"

if not ACCESS_TOKEN:
    raise RuntimeError("WHATSAPP_TOKEN environment variable eksik!")


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


def sure_metni(dakika):
    """Kalan süreyi okunabilir Türkçe metne çevirir."""
    if dakika <= 0:
        return "Teslim zamanı geldi!"
    if dakika < 60:
        return f"{dakika} dakika kaldı"
    if dakika < 1440:
        saat = dakika // 60
        return f"{saat} saat kaldı"
    gun = dakika // 1440
    return f"{gun} gün kaldı"


def mesaj_olustur(data, dakika):
    return (
        f"🚨 *ÖDEV HATIRLATMASI!*\n\n"
        f"📚 *Ödev:* {data.get('baslik')}\n"
        f"📖 *Ders:* {data.get('ders') or '-'}\n"
        f"⏰ *{sure_metni(dakika)}*\n"
        f"🗓️ *Teslim:* {data.get('teslim_tarihi')}\n\n"
        f"Lütfen ödevini kontrol etmeyi unutma!"
    )


@app.route("/", methods=["GET"])
def home():
    # Ana sayfaya girildiğinde index.html arayüzünü gösterir
    return render_template("index.html")


@app.route("/check-assignments", methods=["GET"])
def check_assignments():
    now = datetime.utcnow()
    now_str = now.strftime("%Y-%m-%dT%H:%M")
    odevler_ref = db.collection("odevler")

    # Artık her ödevin birden çok hatırlatma zamanı olabildiği için
    # tamamlanmamış tüm ödevleri çekip her birinin hatırlatma listesini
    # kendimiz kontrol ediyoruz.
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
            teslim_dt = datetime.strptime(teslim_tarihi, "%Y-%m-%dT%H:%M")
        except ValueError:
            continue

        hatirlatmalar = data.get("hatirlatmalar")

        if hatirlatmalar:
            # Yeni format: her biri {"dk": <teslimden kaç dk önce>, "gonderildi": bool}
            yeni_liste = []
            degisti = False
            for h in hatirlatmalar:
                dk = h.get("dk", 0)
                if not h.get("gonderildi"):
                    tetik_zamani = teslim_dt - timedelta(minutes=dk)
                    if tetik_zamani <= now:
                        res = send_whatsapp(mesaj_olustur(data, dk))
                        if res.status_code == 200:
                            h = {"dk": dk, "gonderildi": True}
                            degisti = True
                            gonderilen_sayisi += 1
                        else:
                            print(f"WhatsApp gönderim hatası ({d.id}, {dk} dk): {res.status_code} {res.text}")
                yeni_liste.append(h)
            if degisti:
                odevler_ref.document(d.id).update({"hatirlatmalar": yeni_liste})
        else:
            # Eski kayıtlar (hatirlatmalar alanı yok): geriye dönük tek seferlik hatırlatma
            if not data.get("gonderildi") and teslim_tarihi <= now_str:
                res = send_whatsapp(mesaj_olustur(data, 0))
                if res.status_code == 200:
                    odevler_ref.document(d.id).update({"gonderildi": True})
                    gonderilen_sayisi += 1
                else:
                    print(f"WhatsApp gönderim hatası ({d.id}): {res.status_code} {res.text}")

    return jsonify({"status": "ok", "gonderilen_bildirim": gonderilen_sayisi})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5001)))