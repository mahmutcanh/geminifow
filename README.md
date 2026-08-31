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

## 🛠️ Başka Bilgisayara Sıfırdan Kurulum Adımları

### 1. Ön Gereksinimler
- **Python 3.10+** (Kurulumda *Add Python to PATH* seçeneğini işaretleyin)
- **Google Chrome** (Bilgisayarda kurulu olmalıdır)
- **Git**

### 2. Repoyu İndirin
```bash
git clone https://github.com/mahmutcanh/geminifow.git
cd geminifow
```

### 3. Kütüphaneleri ve Playwright'ı Yükleyin
```bash
pip install -r requirements.txt
playwright install chromium
```

### 4. İlk Kez Google (Flow) Hesabına Giriş Yapın
Video üretecek Google hesabınızı bağlamak için:
- `2_Google_Hesabina_Giris.bat` dosyasını çalıştırın  
  *(veya terminalde: `python flow_bot/login.py`)*
- Açılan Chrome penceresinde Google (labs.google/flow) hesabınıza giriş yapın.
- Flow arayüzünü gördükten sonra Chrome penceresini kapatabilirsiniz.

### 5. Web Paneli Başlatın
- `1_Webpanel_Baslat.bat` dosyasını çalıştırın  
  *(veya terminalde: `python webpanel/app.py`)*
- Tarayıcınızdan `http://localhost:5050` adresine girin.

### 6. YouTube Kanalını Bağlayın
- Web panelinde **Otomasyon** sekmesine geçin.
- Kanalınıza uygun bir kategori adı yazıp ekleyin.
- **Kanal Bağla** butonuna basın.
- Açılan Chrome penceresinde ilgili YouTube kanalına giriş yapın ve pencereyi kapatın.
- Panelde **Girişi tamamladım** butonuna basın.

Artık video oluşturduğunuzda sistem otomatik olarak arka planda videoyu üretip bağlı kanala Shorts taslağı olarak yükleyecektir.

---

## ⚙️ Çevre Değişkenleri ve Konfigürasyon

- `GEMINIFLOW_PORT`: Web panelinin çalışacağı port (varsayılan: `5050`).
- `GEMINIFLOW_HOST`: Ağ adresi (varsayılan: `0.0.0.0`).
- `GEMINIFLOW_PANEL_TOKEN`: Ağ erişimi aktif edildiğinde güvenlik belirteci.
- `GEMINIFLOW_AI_API_KEY`: AI destekli prompt önerileri için API anahtarı.

---

## 🔄 Deploy ve Güncelleme Adımları

Yeni bir geliştirme yaptıktan sonra repoyu güncellemek için:
```bash
git add .
git commit -m "feat: güncelleme açıklaması"
git push origin main
```

Başka bir bilgisayarda/sunucuda güncellemeleri çekmek için:
```bash
git pull origin main
```
