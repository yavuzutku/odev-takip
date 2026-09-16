import requests

ACCESS_TOKEN = "EAAiWcZCaZBZCSwBSegdkV9fwWPXA5caPZC9MTYdxZAgyuy8oniYBvTqOXvhtmDVD77eqZBcB7L0kbn214cRqHODQnFXG7ZBjFZABqQC9PwoHZAr9RAeRH81fMeQ0rBu5W07EztnHvMYzuqUFzSwGeOupZAuUouHEdisHU4nYti0oV3GXtfGVy5cePmhDeZAF5pqU47T8tFZCQYPaVVdtZChKvf1BHoffaI7RJxOmgYnx7jsBNxyvpZA6sHaNjwtEhYbNwT74ZB9WH2Avi9NZC8BKmZBUwhfsP6dYTRwZDZD"
PHONE_NUMBER_ID = "1327842940416253"
RECIPIENT_PHONE = "905060308430"


def send_whatsapp_message(phone, message_text):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

    # Özel serbest metin payload yapısı
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": message_text},
    }

    response = requests.post(url, headers=headers, json=payload)
    return response


# Göndermek istediğin özel mesaj içeriği
ozel_mesaj = (
    "🚨 *ÖDEV HATIRLATMASI*\n\n"
    "📚 *Ödev:* IB English Essay\n"
    "⏳ *Kalan Süre:* 3 Saat\n\n"
    "Bilgisayarından otomatik olarak gönderildi."
)

# ÖNCE TELEFONUNDAN TEST NUMARASINA MESAJ ATTIĞINDAN EMİN OL!
print("Özel mesaj gönderiliyor...")
res = send_whatsapp_message(RECIPIENT_PHONE, ozel_mesaj)

if res.status_code == 200:
    print("✅ Özel mesaj başarıyla teslim edildi!")
else:
    print(f"❌ Gönderim başarısız: {res.text}")