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
APP_API_TOKEN = os.environ.get("APP_API_TOKEN", WEBHOOK_SECRET)
DAILY_SUMMARY_HOUR_UTC = int(os.environ.get("DAILY_SUMMARY_HOUR_UTC", "5"))  # 05:00 UTC = 08:00 Türkiye
# Veritabanındaki tarihler UTC saklanır; müsaitlik hesabı yerel saatle yapılır (Türkiye = UTC+3, yaz saati yok).
LOCAL_TZ_OFFSET_HOURS = int(os.environ.get("LOCAL_TZ_OFFSET_HOURS", "3"))

# Varsayılan haftalık müsaitlik programı (yerel saat, gece yarısından itibaren dakika).
# Bu aralıkların DIŞINDA kalan zaman meşgul sayılır (okul, sabah meşguliyeti, uyku).
# Yine de bu saatlere etkinlik/görev eklenebilir; sadece "boş zaman" hesabı bunları baz alır.
MUSAITLIK = {
    "hafta_ici":  (15 * 60 + 30, 24 * 60),  # okul 08:00-15:30 -> müsait 15:30-00:00
    "hafta_sonu": (13 * 60,      24 * 60),  # 13:00'e kadar meşgul -> müsait 13:00-00:00
}
MIN_BOSLUK_DK = 20  # bundan kısa boşluklar listelenmez

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


def parse_dt(s, fmt=ZAMAN_FORMAT):
    try:
        return datetime.strptime(s, fmt)
    except (ValueError, TypeError):
        return None


def yerel_simdi():
    return datetime.utcnow() + timedelta(hours=LOCAL_TZ_OFFSET_HOURS)


def utc_to_yerel(dt):
    return dt + timedelta(hours=LOCAL_TZ_OFFSET_HOURS)


def musaitlik_penceresi(yerel_gun):
    """Günün varsayılan müsait aralığı (yerel saat): hafta içi okul sonrası, hafta sonu öğleden sonra."""
    anahtar = "hafta_sonu" if yerel_gun.weekday() >= 5 else "hafta_ici"
    bas_dk, bit_dk = MUSAITLIK[anahtar]
    gun0 = yerel_gun.replace(hour=0, minute=0, second=0, microsecond=0)
    return gun0 + timedelta(minutes=bas_dk), gun0 + timedelta(minutes=bit_dk)


def tekrar_zamanlari(baslangic, tekrar, aralik_bas, aralik_son):
    """İstemcideki ruleOccurrences ile aynı mantık. Hepsi yerel saat (naive datetime)."""
    tip = (tekrar or {}).get("tip") or "yok"
    if tip == "yok":
        return [baslangic] if aralik_bas <= baslangic < aralik_son else []

    limit = aralik_son
    bitis_str = (tekrar or {}).get("bitis")
    if bitis_str:
        try:
            limit = datetime.strptime(bitis_str + "T23:59", ZAMAN_FORMAT)
        except ValueError:
            pass

    out = []
    cur = baslangic
    guard = 0
    while cur <= limit and cur < aralik_son and guard < 2000:
        guard += 1
        if cur >= aralik_bas and (tip != "hafta_ici" or cur.weekday() < 5):
            out.append(cur)
        if tip == "haftalik":
            cur += timedelta(days=7)
        elif tip == "hafta_ici":
            cur += timedelta(days=1)
        elif tip == "aylik_ilk_gun":
            yil = cur.year + (1 if cur.month == 12 else 0)
            ay = cur.month % 12 + 1
            cur = cur.replace(year=yil, month=ay, day=1)
        else:
            break
    return out


def gunun_bosluklarini_hesapla(yerel_gun):
    """yerel_gun: hesaplanacak günün (yerel saat) herhangi bir datetime'ı.
    Varsayılan müsaitlik penceresinden, o güne denk gelen dolu aralıkları çıkarıp
    boşlukları (yerel saat) döndürür."""
    pencere_bas, pencere_son = musaitlik_penceresi(yerel_gun)
    gun0 = yerel_gun.replace(hour=0, minute=0, second=0, microsecond=0)
    # Önceki günlerde başlayıp bugüne uzanan çok günlü etkinlikler de yakalansın
    aralik_bas = gun0 - timedelta(days=7)
    aralik_son = gun0 + timedelta(days=1)

    busy = []
    for d in db.collection("odevler").stream():
        data = d.to_dict()
        if data.get("tamamlandi"):
            continue
        start_utc = parse_dt(data.get("teslim_tarihi"))
        if not start_utc:
            continue
        start = utc_to_yerel(start_utc)

        end_utc = parse_dt(data.get("bitis_tarihi")) if data.get("bitis_tarihi") else None
        if end_utc and end_utc > start_utc:
            sure = end_utc - start_utc
        else:
            sure = timedelta(minutes=data.get("sure_dk") or 30)

        for occ in tekrar_zamanlari(start, data.get("tekrar"), aralik_bas, aralik_son):
            occ_end = occ + sure
            if occ_end <= pencere_bas or occ >= pencere_son:
                continue
            busy.append((max(occ, pencere_bas), min(occ_end, pencere_son)))

    busy.sort(key=lambda x: x[0])
    merged = []
    for b in busy:
        if merged and b[0] <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b[1]))
        else:
            merged.append(b)

    free = []
    cur = pencere_bas
    for b in merged:
        if b[0] > cur:
            free.append((cur, b[0]))
        cur = max(cur, b[1])
    if cur < pencere_son:
        free.append((cur, pencere_son))

    return [(s, e) for s, e in free if (e - s).total_seconds() >= MIN_BOSLUK_DK * 60]


def gunluk_ozet_kontrol_et():
    """check-assignments her çağrıldığında bir kez, günde tek sefer boşluk özeti gönderir."""
    now = datetime.utcnow()
    if now.hour != DAILY_SUMMARY_HOUR_UTC:
        return
    ayarlar_ref = db.collection("ayarlar").document("gunluk_ozet")
    snap = ayarlar_ref.get()
    bugun_str = now.strftime("%Y-%m-%d")
    if snap.exists and snap.to_dict().get("son_gonderim") == bugun_str:
        return

    bosluklar = gunun_bosluklarini_hesapla(yerel_simdi())

    if bosluklar:
        satirlar = ["☀️ *Günaydın! Bugünkü boş zamanların:*", ""]
        for s, e in bosluklar:
            satirlar.append(f"🕐 {s.strftime('%H:%M')} – {e.strftime('%H:%M')}")
        satirlar.append("")
        satirlar.append("Bu aralıklarda ödev/görev planlayabilirsin 👍")
        send_telegram("\n".join(satirlar))

    ayarlar_ref.set({"son_gonderim": bugun_str})


@app.route("/api/notify", methods=["POST"])
def api_notify():
    token = request.headers.get("X-App-Token")
    if token != APP_API_TOKEN:
        return "Forbidden", 403
    data = request.get_json(silent=True) or {}
    text = (data.get("message") or "").strip()
    if not text:
        return jsonify({"status": "error", "detay": "message boş"}), 400
    res = send_telegram(text)
    ok = res.status_code == 200
    return jsonify({"status": "ok" if ok else "error"}), (200 if ok else 502)


@app.route("/api/free-slots", methods=["GET"])
def api_free_slots():
    gun_str = request.args.get("gun")  # YYYY-MM-DD (yerel gün), opsiyonel
    if gun_str:
        try:
            gun = datetime.strptime(gun_str, "%Y-%m-%d")
        except ValueError:
            return jsonify({"status": "error"}), 400
    else:
        gun = yerel_simdi()
    bosluklar = gunun_bosluklarini_hesapla(gun)
    return jsonify({
        "status": "ok",
        "saat_dilimi": f"UTC{LOCAL_TZ_OFFSET_HOURS:+d}",
        "bosluklar": [{"baslangic": s.strftime(ZAMAN_FORMAT), "bitis": e.strftime(ZAMAN_FORMAT)} for s, e in bosluklar]
    })


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

        if data.get("bildirim_kapali"):
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

    try:
        gunluk_ozet_kontrol_et()
    except Exception as e:
        print(f"Günlük özet hatası: {e}")

    return jsonify({"status": "ok", "gonderilen_bildirim": gonderilen_sayisi})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5001)))