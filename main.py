from datetime import datetime
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

# WhatsApp API Bilgilerin
ACCESS_TOKEN = "EAAiWcZCaZBZCSwBSegdkV9fwWPXA5caPZC9MTYdxZAgyuy8oniYBvTqOXvhtmDVD77eqZBcB7L0kbn214cRqHODQnFXG7ZBjFZABqQC9PwoHZAr9RAeRH81fMeQ0rBu5W07EztnHvMYzuqUFzSwGeOupZAuUouHEdisHU4nYti0oV3GXtfGVy5cePmhDeZAF5pqU47T8tFZCQYPaVVdtZChKvf1BHoffaI7RJxOmgYnx7jsBNxyvpZA6sHaNjwtEhYbNwT74ZB9WH2Avi9NZC8BKmZBUwhfsP6dYTRwZDZD"
PHONE_NUMBER_ID = "1327842940416253"
RECIPIENT_PHONE = "905060308430"


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


@app.route("/", methods=["GET"])
def home():
    # Ana sayfaya girildiğinde index.html arayüzünü gösterir
    return render_template("index.html")


@app.route("/check-assignments", methods=["GET"])
def check_assignments():
    # Şu anki tarihi ISO formatında al (Örn: 2026-09-16T18:30)
    now_str = datetime.now().strftime("%Y-%m-%dT%H:%M")
    odevler_ref = db.collection("odevler")

    # Henüz mesaj gönderilmemiş ödevleri filtrele
    query = odevler_ref.where("gonderildi", "==", False).stream()

    gonderilen_sayisi = 0

    for doc in query:
        data = doc.to_dict()
        teslim_tarihi = data.get("teslim_tarihi")

        # Zamanı gelmiş veya geçmişse WhatsApp mesajı at
        if teslim_tarihi and teslim_tarihi <= now_str:
            mesaj = (
                f"🚨 *ÖDEV HATIRLATMASI!*\n\n"
                f"📚 *Ödev:* {data.get('baslik')}\n"
                f"⏰ *Hatırlatma Zamanı:* {teslim_tarihi}\n\n"
                f"Lütfen ödevini kontrol etmeyi unutma!"
            )

            res = send_whatsapp(mesaj)
            if res.status_code == 200:
                # Tekrar tekrar mesaj atmaması için 'gonderildi' durumunu True yap
                odevler_ref.document(doc.id).update({"gonderildi": True})
                gonderilen_sayisi += 1

    return jsonify({"status": "ok", "gonderilen_bildirim": gonderilen_sayisi})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5001)))