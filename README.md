# 🏠 Daft.ie Dublin Monitor

Dublin 6/6W bölgesinde €1,500–€1,800 arası studio/daire ilanlarını otomatik takip eden sistem.

## Mimari

```
┌─────────────────────────────────────────────────────┐
│  GitHub Actions (her 30 dk — ücretsiz)              │
│  scanner.py → Daft.ie API v2 → Telegram bildirim    │
│  seen_ids.json → repo'ya commit (tekrar bildirmez)  │
└─────────────────────────────────────────────────────┘
            +
┌─────────────────────────────────────────────────────┐
│  Render.com (ücretsiz web service — 7/24)           │
│  bot.py → Telegram webhook (Render) / polling (local)│
│  Flask /ping → UptimeRobot her 5 dk ping atar       │
│  (Render'ın 15 dk uyuma sorununu çözer)             │
└─────────────────────────────────────────────────────┘
```

## Dosya Yapısı

```
daft-monitor/
├── bot.py                          # Render'da çalışan Telegram bot
├── scanner.py                      # GitHub Actions'da çalışan tarayıcı
├── requirements.txt                # Python bağımlılıkları
├── render.yaml                     # Render deploy config
├── state.json                      # Bot ayarları (fiyat, bölge, favoriler)
├── seen_ids.json                   # Görülen ilan ID'leri
├── recent_listings.json            # Son taranan ilanlar (/list komutu)
├── .gitignore
└── .github/
    └── workflows/
        └── scan.yml                # GitHub Actions cron job
```

---

## KURULUM — ADIM ADIM

### ADIM 1 — Telegram Bot Oluştur

1. Telegram'da `@BotFather`'a git
2. `/newbot` yaz
3. İsim ver: `DaftDublinMonitor`
4. Kullanıcı adı ver: `daft_dublin_monitor_bot` (benzersiz olmalı)
5. Verilen **token**'ı kopyala → `7123456789:AAFxxx...`
6. Bota herhangi bir mesaj at (örn. "merhaba")
7. Tarayıcıda aç:
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
8. JSON'da `"chat":{"id":` değerini bul → bu senin **Chat ID**'n

---

### ADIM 2 — GitHub Repo Oluştur

```bash
# GitHub'da "daft-monitor" adında yeni repo oluştur (public veya private)
# Sonra:
git clone https://github.com/<KULLANICI_ADIN>/daft-monitor.git
cd daft-monitor

# Dosyaları kopyala (hepsini bu dizine koy)
# Klasör yapısına dikkat et: .github/workflows/scan.yml

git add .
git commit -m "feat: initial daft monitor setup"
git push origin main
```

> **Not:** `seen_ids.json` mevcut ilan ID'leriyle önceden doldurulmuştur. İlk Actions çalışmasında tüm mevcut ilanlar için spam bildirim gelmez.

---

### ADIM 3 — GitHub Secrets Ekle

GitHub repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

| Secret Adı         | Değer                          |
|--------------------|--------------------------------|
| `TELEGRAM_TOKEN`   | BotFather'dan aldığın token    |
| `TELEGRAM_CHAT_ID` | getUpdates'ten aldığın chat ID |

GitHub Actions, `seen_ids.json` ve `recent_listings.json` commit'leri için otomatik olarak `GITHUB_TOKEN` sağlar — ayrıca secret eklemen gerekmez.

---

### ADIM 4 — Render Deploy

1. [render.com](https://render.com) → **New** → **Web Service**
2. GitHub repo'nu bağla
3. Ayarlar:
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python bot.py`
4. **Environment Variables** ekle:

| Değişken             | Değer                                              |
|----------------------|----------------------------------------------------|
| `TELEGRAM_TOKEN`     | Bot token                                          |
| `TELEGRAM_CHAT_ID`   | Chat ID                                            |
| `GITHUB_TOKEN`       | GitHub Personal Access Token (`repo` scope)          |
| `GITHUB_REPOSITORY`  | `kullanici-adin/daft-monitor` formatında repo adı  |

> **Webhook:** Render otomatik olarak `RENDER_EXTERNAL_URL` ayarlar; bot bu URL'ye webhook kaydeder ve **polling kullanmaz** (409 Conflict önlenir). İsteğe bağlı: `WEBHOOK_URL` ile override, `WEBHOOK_SECRET` ile `/webhook/<secret>` güvenliği.

5. **Deploy** et

> **Önemli:** Render deploy edildikten sonra yerelde `python bot.py` çalıştırmayın — aynı token ile iki instance 409 hatasına yol açar. Yerel geliştirme için Render servisini durdurun veya farklı bir test bot token'ı kullanın.

> Bot, `state.json` ve `recent_listings.json` dosyalarını GitHub ile senkronize eder. `/scan` komutu GitHub Actions workflow'unu tetikler. Bu nedenle Render'da `GITHUB_TOKEN` ve `GITHUB_REPOSITORY` zorunludur.

---

### ADIM 5 — UptimeRobot Kur (Render'ı Uyutmama)

1. [uptimerobot.com](https://uptimerobot.com) → ücretsiz hesap aç
2. **Add New Monitor**:
   - Monitor Type: **HTTP(s)**
   - URL: `https://daft-monitor-bot.onrender.com/ping`
   - Monitoring Interval: **5 minutes**
3. Kaydet → Render artık uyumaz

---

### ADIM 6 — GitHub Actions Test Et

1. GitHub → **Actions** sekmesi → `Daft.ie Monitor — Scheduled Scan`
2. **Run workflow** → **Run workflow** butonuna bas
3. Log'ları kontrol et — hata yoksa her 30 dk otomatik çalışır

---

### ADIM 7 — Telegram'dan Test Et

Botuna mesaj gönder:

- `/start` → ana menüyü görmeli
- `/scan` → manuel tarama başlatmalı
- `/help` → tüm komutlar

---

## Telegram Komutları

| Komut          | Açıklama                                    |
|----------------|---------------------------------------------|
| `/start`       | Ana menü — inline butonlarla tam kontrol    |
| `/scan`        | Daft.ie'yi hemen tara, yeni ilanları gönder |
| `/list`        | Son taranan ilanları listele (max 10)       |
| `/setprice`    | Fiyat aralığını değiştir                    |
| `/setlocation` | Takip edilecek bölgeleri seç/kaldır         |
| `/toggle`      | Otomatik bildirimleri aç/kapat              |
| `/fav`         | Favori ilanlarımı göster                    |
| `/help`        | Tüm komutlar                                |

---

## Bildirim Formatı

Her yeni ilan şu şekilde gelir:

```
🏠 YENİ İLAN — Dublin 6/6W
━━━━━━━━━━━━━━━━━━━━
📍 Studio Apartment, Rathmines
💶 €1,650 per month
🏢 Studio
🛏 Studio
📅 Yayın: 25 May 2026, 09:14

🔗 Daft.ie'de Gör   [tıklanabilir link]

[⭐ Favori Ekle/Çıkar]     ← inline buton
```

Fotoğraf varsa → fotoğraf + caption olarak gelir.

---

## Sorun Giderme

**Bot yanıt vermiyor:**

- Render loglarını kontrol et → `render.com` → servis → Logs
- `TELEGRAM_TOKEN` doğru mu?
- `/ping` yanıtında `mode=webhook` görünmeli (Render'da)
- Yerelde `bot.py` çalışıyorsa kapatın — Render webhook ile çakışır

**Telegram 409 Conflict:**

- Render'da webhook modu kullanılır; eski polling deploy'ları bu hatayı üretirdi
- Aynı anda yalnızca **bir** bot instance'ı çalışmalı (Render **veya** local, ikisi birden değil)

**GitHub Actions çalışmıyor:**

- Actions sekmesinde workflow enable edildi mi?
- `TELEGRAM_TOKEN` ve `TELEGRAM_CHAT_ID` secrets doğru eklendi mi?

**Aynı ilan tekrar geliyor:**

- `seen_ids.json` repo'ya commit edilmiş mi? Actions log'una bak.

**Daft API 403 veya 0 ilan hatası:**

- Daft.ie v2 API kullanılıyor: `POST https://gateway.daft.ie/api/v2/ads/listings`
- Eski `old/v1/listings` endpoint'i artık çalışmıyor (403 Cloudflare).
- Gerekli header'lar: `brand: daft`, `platform: web`
- Bölge filtresi v2'de `geoFilter.storedShapeIds` ile yapılır — `locations` filtresi çalışmaz.
- `scanner.py` içindeki `LOCATION_GEO_IDS` eşleşmelerini kontrol et (Dublin 6 → `70`, Dublin 6W → `71`).
- İstekler `curl_cffi` ile Chrome impersonation kullanır.

**Bot `/scan` çalışmıyor veya ayarlar senkronize olmuyor:**

- Render'da `GITHUB_TOKEN` (PAT, `repo` scope) ve `GITHUB_REPOSITORY` tanımlı mı?

---

## Özelleştirme

`state.json` dosyasını düzenleyerek başlangıç değerlerini değiştirebilirsin.
Telegram'dan `/setprice` ve `/setlocation` ile çalışma sırasında da değiştirebilirsin — ayarlar `state.json`'a kaydedilir.

Yeni bölge eklemek için:

1. `bot.py` içindeki `LOCATION_OPTIONS` dict'ine yeni satır ekle
2. `scanner.py` içindeki `LOCATION_GEO_IDS` ve `LOCATION_NAMES` dict'lerine karşılık gelen geo ID'yi ekle

Geo ID'leri bulmak için Daft.ie'nin bölge arama API'sinden veya [daftlistings](https://github.com/AnthonyBloomer/daftlistings) kütüphanesinden yararlanabilirsin.
