# Geminiflow - Otomatik Video Üretim ve YouTube Yayınlama Paneli

Geminiflow; Google Flow AI altyapısını kullanarak otomatik video üreten, üretilen videoları kategorilere göre yöneten, çoklu Google/Flow hesap yönetimi ve rotasyonu sağlanan ve bağlı YouTube kanallarına otomatik Shorts taslağı olarak yükleyen gelişmiş bir web yönetim panelidir.

---

## 🚀 Mimari ve Çalışma Şeması

```text
  [ Kullanıcı / Otomatik Pilot ]
                │
                ▼
        [ Web Panel (Flask) ] ── (Kuyruk, Hesaplar & İş Takibi)
                │
         ┌──────┴─────────────────────────┐
         ▼                                ▼
[ Google Flow Botu ]             [ YouTube Yükleme Botu ]
 (Çoklu Hesap / Rotasyon)          (Playwright / Studio)
         │                                │
         ▼                                ▼
  static/videos/                     YouTube Shorts
   (.mp4 Dosyası)                     (Taslak / Yayın)
```

---

## 📋 Öne Çıkan Özellikler

1. **AI Video Üretimi:** Google Flow arayüzü üzerinden Playwright otomasyonu ile yüksek kalitede video üretimi.
2. **Çoklu Google / Flow Hesap Yönetimi:** Panel ayarlarından birden fazla Google hesabı ekleme, oturum açma ve hesaplar arası otomatik rotasyon (bakiye/kredi bitince yedek hesaba geçiş).
3. **Kategori ve YouTube Kanal Bağlantısı:** Her kategoriye özel YouTube Studio oturumu bağlama ve yönetme.
4. **Otomatik Shorts Taslak Yükleme:** Üretimi tamamlanan videolar, ilgili kategorinin bağlı YouTube kanalına sessizce (arka planda) taslak olarak iletilir.
5. **Arka Plan Modu (Headless):** Üretim ve yükleme işlemleri arka planda görünmeden çalışır.
6. **Güvenli Oturum Yapısı:** Kanal bağlantısı veya Google girişi yapılacağı zaman Chrome görünür modda açılır, oturum verileri güvenle yerel dizinde saklanır.

---

## 🛠️ Başka Bilgisayara Sıfırdan Kurulum Adımları

### 1. Ön Gereksinimler
- **Python 3.10+** (Kurulum yaparken *Add Python to PATH* kutucuğunu işaretleyin)
- **Google Chrome** (Bilgisayarınızda kurulu olmalıdır)
- **Git**

### 2. Projeyi İndirin
```bash
git clone https://github.com/mahmutcanh/geminifow.git
cd geminifow
```

### 3. Gerekli Kütüphaneleri Yükleyin
```bash
pip install -r requirements.txt
playwright install chromium
```

### 4. Google (Flow) Hesabınızı Bağlayın
Video üretimi yapacak Google hesabınızı sisteme tanıtmak için:
- `2_Google_Hesabina_Giris.bat` dosyasını çalıştırın  
  *(veya terminalde: `python flow_bot/login.py`)*
- Açılan Chrome penceresinde Google (labs.google/flow) hesabınıza giriş yapın.
- Flow sayfasını açıldıktan sonra Chrome penceresini kapatabilirsiniz.

### 5. Web Paneli Başlatın
- `1_Webpanel_Baslat.bat` dosyasını çalıştırın  
  *(veya terminalde: `python webpanel/app.py`)*
- Tarayıcınızdan `http://localhost:5050` adresine girin.

### 6. YouTube Kanallarını Bağlayın
- Web panelinde **Otomasyon** sekmesine geçin.
- Yeni bir kategori ekleyin (örneğin: *Örgü Dünyası*).
- **Kanal Bağla** butonuna tıklayın.
- Açılan Chrome penceresinde ilgili YouTube hesabınızla oturum açın ve pencereyi kapatın.
- Paneldeki **Girişi tamamladım** butonuna basın.

---

## ⚙️ Çevre Değişkenleri ve Konfigürasyon

- `GEMINIFLOW_PORT`: Web panelinin port numarası (varsayılan: `5050`).
- `GEMINIFLOW_HOST`: Ağ erişim adresi (varsayılan: `0.0.0.0`).
- `GEMINIFLOW_PANEL_TOKEN`: Dış ağ erişimi güvenlik tokeni.
- `GEMINIFLOW_AI_API_KEY`: AI destekli prompt önerileri için API key.

---

## 🔄 Güncelleme ve Deploy Adımları

Geliştirme yaptıktan sonra GitHub'a göndermek için:
```bash
git add .
git commit -m "feat: yeni güncelleme"
git push origin main
```

Farklı bir bilgisayarda güncellemeleri çekmek için:
```bash
git pull origin main
```
