# Geminiflow - Otomatik Video Üretim ve YouTube Yayınlama Paneli

Geminiflow; Google Flow AI altyapısını kullanarak otomatik video üreten, üretilen videoları kategorilere göre yöneten ve bağlı YouTube kanallarına otomatik/manuel Shorts taslağı olarak yükleyen bir Flask tabanlı web yönetim panelidir.

---

## 🚀 Mimari ve Çalışma Şeması

```text
  [ Kullanıcı / Otomatik Pilot ]
                │
                ▼
        [ Web Panel (Flask) ] ── (Kuyruk & İş Takibi)
                │
         ┌──────┴─────────────────────────┐
         ▼                                ▼
[ Video Üretim Botu ]            [ YouTube Yükleme Botu ]
  (Playwright / Flow)              (Playwright / Studio)
         │                                │
         ▼                                ▼
  static/videos/                     YouTube Shorts
   (.mp4 Dosyası)                     (Taslak/Yayın)
```

---

## 📋 Özellikler

1. **AI Video Üretimi:** Google Flow arayüzü üzerinden Playwright otomasyonu ile video üretimi.
2. **Kategori ve Kanal Bağlantısı:** Her kategori için bağımsız Chrome profili ile YouTube Studio oturum yönetimi.
3. **Otomatik Taslak Yükleme:** Üretimi tamamlanan videolar bağlı YouTube kanalına otomatik olarak Shorts taslağı olarak iletilir.
4. **Arka Plan Modu (Headless):** Video üretimi ve YouTube yükleme süreçleri tamamen arka planda sessiz çalışır.
5. **Giriş ve Oturum Güvenliği:** YouTube kanal bağlantısı sırasında görünür Chrome penceresi açılır, oturum bilgileri `data/youtube_profiles` dizininde saklanır.

---

## 🛠️ Kurulum Adımları

### 1. Gereksinimler
- Python 3.10+
- Google Chrome (Sistemde kurulu olmalıdır)
- Git

### 2. Bağımlılıkların Yüklenmesi
```bash
pip install -r requirements.txt
playwright install chromium
```

### 3. Uygulamanın Başlatılması
```bash
# Windows Başlatma Komutu
python webpanel/app.py
```
Panel varsayılan olarak `http://localhost:5050` (veya konfigüre edilen port) üzerinden erişilebilir.

---

## ⚙️ Çevre Değişkenleri ve Konfigürasyon

- `GEMINIFLOW_PORT`: Web panelinin çalışacağı port (varsayılan: `5050`).
- `GEMINIFLOW_HOST`: Ağ adresi (varsayılan: `0.0.0.0`).
- `GEMINIFLOW_PANEL_TOKEN`: Ağ erişimi aktif edildiğinde güvenlik belirteci.
- `GEMINIFLOW_AI_API_KEY`: AI destekli prompt önerileri için API anahtarı.

---

## 🔄 Deploy ve Güncelleme Adımları

Yeni bir geliştirme veya değişiklik yaptıktan sonra repoyu güncellemek için:

```bash
git add .
git commit -m "feat: güncelleme açıklaması"
git push origin main
```

Sunucu tarafında güncellemeleri çekmek için:
```bash
git pull origin main
python -m py_compile webpanel/app.py
```
