"""
Flow Bot - Chrome ile labs.google/flow'u otomatik acar, video uretir, indirir.
gemini_bot/bot.py ile ayni mimari: kalici Chrome profili, tek seferlik
--generate-video modu, webpanel bunu her istekte subprocess olarak calistirir.
"""

import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

BASE_DIR = Path(__file__).parent
BOT_DIR = BASE_DIR
PROFILE_DIR = BASE_DIR / "chrome_profile"
LOG_FILE = BASE_DIR / "steps.log"
STOP_FLAG = BASE_DIR / "stop.flag"
RESULT_FILE = BASE_DIR / "result.json"
VIDEOS_DIR = BASE_DIR.parent / "webpanel" / "static" / "videos"
FLOW_URL = "https://labs.google/fx/tr/tools/flow"

PROFILE_DIR.mkdir(exist_ok=True)
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:
        pass

log = logging.getLogger("flow_bot")
log.setLevel(logging.INFO)
if not any(isinstance(h, logging.FileHandler) for h in log.handlers):
    _fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(_fh)
if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in log.handlers):
    _sh = logging.StreamHandler(sys.stdout)
    _sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(_sh)


def cleanup_orphan_chrome(target_dir: Path = None):
    """Ayni profil dizinini kullanan asili Chrome islemlerini ve kilit dosyalarini temizler."""
    p_dir = (target_dir or PROFILE_DIR).resolve()
    escaped_dir = str(p_dir).replace("'", "''")
    command = (
        f"$profile = '{escaped_dir}'; "
        "Get-CimInstance Win32_Process -Filter \"Name = 'chrome.exe'\" -ErrorAction SilentlyContinue | "
        "Where-Object { $_.CommandLine -and $_.CommandLine.Contains($profile) } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, timeout=8)
    except Exception:
        pass
    time.sleep(1)
    for lock_name in ("SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile", "DevToolsActivePort"):
        lock_p = p_dir / lock_name
        if lock_p.exists():
            try:
                lock_p.unlink(missing_ok=True)
            except Exception:
                pass


class FlowBot:
    def __init__(self, headless: bool = False, profile_dir: Path = None):
        self.headless = headless
        self.profile_dir = profile_dir or PROFILE_DIR
        self.playwright: Optional[Playwright] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    def step(self, name: str):
        log.info("ADIM: %s", name)
        for h in log.handlers + logging.getLogger().handlers:
            try:
                h.flush()
            except Exception:
                pass

    def diagnostic_state(self, prompt_text: str) -> dict:
        return self.page.evaluate(
            r"""
            (promptText) => {
                const visible = el => {
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
                };
                const normalize = value => String(value || '').replace(/\s+/g, ' ').trim().toLowerCase();
                const probe = normalize(promptText).slice(0, 100);
                const text = normalize(document.body.innerText);
                const videos = Array.from(document.querySelectorAll('video')).filter(visible).map(video => ({
                    src: video.currentSrc || video.src || '',
                    readyState: video.readyState,
                    duration: Number.isFinite(video.duration) ? video.duration : null
                }));
                const promptMatches = Array.from(document.querySelectorAll('div, p, span')).filter(el => {
                    if (!visible(el) || el.closest('[contenteditable="true"]')) return false;
                    return probe && normalize(el.textContent).includes(probe);
                }).length;
                const errors = [
                    'yetersiz kredi', 'insufficient credits',
                    'çok fazla istek', 'too many requests',
                    'bir hata oluştu', 'something went wrong',
                    'hizmet kullanılamıyor', 'service unavailable'
                ].filter(label => text.includes(label));
                return {
                    url: location.href,
                    title: document.title,
                    promptMatches,
                    videos,
                    errors,
                    bodyTail: String(document.body.innerText || '').replace(/\s+/g, ' ').slice(-1200)
                };
            }
            """,
            prompt_text,
        )

    def save_diagnostics(self, label: str, state: dict | None = None) -> None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_label = re.sub(r"[^a-zA-Z0-9_-]", "-", label)[:50]
        prefix = BOT_DIR / f"diagnostic_{stamp}_{safe_label}"
        try:
            self.page.screenshot(path=str(prefix.with_suffix(".png")), full_page=True)
        except Exception:
            log.exception("Tanı ekran görüntüsü kaydedilemedi")
        try:
            prefix.with_suffix(".html").write_text(self.page.content(), encoding="utf-8")
        except Exception:
            log.exception("Tanı HTML kaydedilemedi")
        try:
            prefix.with_suffix(".json").write_text(json.dumps(state or {}, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            log.exception("Tanı JSON kaydedilemedi")
        self.step(f"Tani dosyalari kaydedildi: {prefix.name}")

    def start(self):
        cleanup_orphan_chrome(self.profile_dir)
        time.sleep(0.5)

        self.step("Playwright baslatiliyor")
        self.playwright = sync_playwright().start()

        self.step("Chrome aciliyor (gercek Chrome kanali, kalici profil)")
        context_kwargs = dict(
            user_data_dir=str(self.profile_dir),
            channel="chrome",
            headless=self.headless,
            args=[
                "--start-maximized",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
            ignore_default_args=["--enable-automation"],
        )
        if self.headless:
            context_kwargs["viewport"] = {"width": 1600, "height": 1000}
            context_kwargs["args"].extend(["--headless=new"])
        else:
            context_kwargs["no_viewport"] = True
        self.context = self.playwright.chromium.launch_persistent_context(**context_kwargs)
        self.context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )

        self.step("Yeni sekme aciliyor")
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()

        self.step(f"Flow'a gidiliyor: {FLOW_URL}")
        self.page.goto(FLOW_URL, wait_until="domcontentloaded")

        self.step("Sayfa yuklenmesi bekleniyor")
        try:
            self.page.get_by_role("button", name="Yeni proje").first.wait_for(timeout=25000)
            self.step("Flow hazir")
        except PlaywrightTimeoutError:
            self.step("Giris ekrani bekleniyor olabilir (Google login gerekebilir)")

        self.dismiss_promo_modal()
        return self

    def dismiss_promo_modal(self) -> None:
        """Flow bazen sayfa acilisinda tam ekranli degisiklik-gunlugu/duyuru
        modal'i gosteriyor ("Flow has added a new 360p option..." + "Başlayın"
        butonu). Bu modal ust katmanda durup "Yeni proje" tiklamasini
        (force=True olsa bile) etkisiz kiliyor - tiklama gercek butona degil
        modal'in uzerine/gorunmez bir elemente gidiyor, sayfa navigate etmiyor.
        Escape bunu kapatmiyor, butonu dogrudan tiklamak gerekiyor."""
        page = self.page
        for label in ("Başlayın", "Get started", "close"):
            try:
                btn = page.get_by_role("button", name=label).first
                if btn.count() > 0 and btn.is_visible():
                    btn.click(force=True, timeout=3000)
                    self.step(f"Promosyon modal'i kapatildi ({label!r})")
                    page.wait_for_timeout(500)
                    return
            except Exception:
                pass

    def close(self):
        self.step("Kapatiliyor")
        if self.context:
            try:
                self.context.close()
            except Exception:
                log.exception("Tarayıcı bağlamı kapatılamadı")
            finally:
                self.context = None
        if self.playwright:
            try:
                self.playwright.stop()
            except Exception:
                log.exception("Playwright kapatılamadı")
            finally:
                self.playwright = None

    # Ayarlar panelindeki butonlarin TAM gorunen metni (ikon-ligature + kelime,
    # bosluksuz birlesim) - Flow arayuzunden birebir okundu. Kisa/genel
    # kelimeler (orn. sadece "Video") sayfadaki gizli accessibility duyuru
    # elemanlarina (__next-route-announcer__) yanlislikla eslesebiliyor, bu
    # yuzden TAM metni + exact=True kullaniyoruz.
    SETTINGS_TYPE = {"video": "videocamVideo", "image": "imageGörüntü"}
    SETTINGS_RATIO = {"16:9": "crop_16_916:9", "9:16": "crop_9_169:16"}
    SETTINGS_RESOLUTION = {"360p": "360p", "720p": "720p"}
    SETTINGS_DURATION = {"4s": "4s", "6s": "6s", "8s": "8s", "10s": "10s"}
    SETTINGS_COUNT = {"x1": "x1", "x2": "x2", "x3": "x3", "x4": "x4"}
    SETTINGS_MODEL = {
        "omni-flash": "Omni Flash",
        "veo-lite": "Veo 3.1 - Lite",
        "veo-fast": "Veo 3.1 - Fast",
        "veo-quality": "Veo 3.1 - Quality",
    }

    def apply_settings(self, settings: dict) -> None:
        """Ajan ayarlari panelini acar, verilen degerleri tiklar, panelini kapatir.
        `settings` anahtarlari: type, ratio, resolution, duration, count, model - hepsi opsiyonel.

        ONEMLI: Flow'un composer butonlarinda gercek aria-label YOK, sadece
        gorunen metin var (ikon-ligature + kelimenin bosluksuz birlesimi, ornek
        "videocamVideo", "crop_16_916:9"). Ayrica JS'ten `element.click()`
        cagirmak bu React tabanli acilir panel/dropdown tetikleyicilerinde
        ISE YARAMIYOR (element bulunuyor ama tiklama gercek bir etki yaratmiyor) -
        panelin gercekten acilmasi icin Playwright'in KENDI .click() metodu
        (gercek mouse event dispatch eder) sart. Bu yuzden hem paneli acarken
        hem ic secenekleri tiklarken hep page.locator(...).click(force=True)
        kullaniyoruz, JS evaluate degil.
        """
        if not settings:
            return
        page = self.page
        self.step(f"Ayarlar uygulaniyor: {settings}")

        # Ozet pill'in metni moda gore degisiyor (video: "Video · 720p · 8s · x1",
        # goruntu: "🍌 Nano Banana 2crop_16_9x2") - ortak parca yok. Modelden
        # bagimsiz sabit bir parca ("Nano Banana" HER ZAMAN gorunmuyor ama en
        # azindan bir kere ilk acilista goruntu modundaysa gorunur; guvenilir
        # olmasi icin oncelikle "·" iceren pill'i, yoksa herhangi bir model
        # adi iceren pill'i dene) - pratikte her iki moda da uyan tek yontem:
        # "Oluştur" gonder butonunun az onceki (solundaki) kardes elemani.
        send_btn = page.locator("button", has_text="Oluştur").last
        if send_btn.count() == 0:
            self.step("Gonder butonu bulunamadi, ayarlar atlaniyor")
            return
        pill = send_btn.locator("xpath=preceding-sibling::*[1]")
        if pill.count() == 0:
            self.step("Ayarlar ozet pill'i bulunamadi, varsayilanlar kullanilacak")
            return
        pill.click(force=True)
        page.wait_for_timeout(500)

        def click_exact(label: str) -> None:
            opt = page.get_by_text(label, exact=True).first
            if opt.count() == 0:
                self.step(f"Ayar butonu bulunamadi: {label!r}")
                return
            opt.click(force=True)
            page.wait_for_timeout(250)

        if settings.get("type") in self.SETTINGS_TYPE:
            click_exact(self.SETTINGS_TYPE[settings["type"]])
        if settings.get("ratio") in self.SETTINGS_RATIO:
            click_exact(self.SETTINGS_RATIO[settings["ratio"]])
        if settings.get("model") in self.SETTINGS_MODEL:
            model_dd = page.locator('button:has-text("Omni Flash"), button:has-text("Veo 3.1")').first
            if model_dd.count() > 0:
                model_dd.click(force=True)
                page.wait_for_timeout(300)
                click_exact(self.SETTINGS_MODEL[settings["model"]])
        if settings.get("resolution") in self.SETTINGS_RESOLUTION:
            click_exact(self.SETTINGS_RESOLUTION[settings["resolution"]])
        if settings.get("duration") in self.SETTINGS_DURATION:
            click_exact(self.SETTINGS_DURATION[settings["duration"]])
        if settings.get("count") in self.SETTINGS_COUNT:
            click_exact(self.SETTINGS_COUNT[settings["count"]])

        page.keyboard.press("Escape")
        page.wait_for_timeout(300)

    def open_project_composer(self) -> None:
        page = self.page
        last_error = None
        for attempt in range(1, 4):
            self.step(f"Yeni proje aciliyor (deneme {attempt}/3)")
            try:
                if "/project/" not in page.url:
                    page.goto(FLOW_URL, wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(1500)
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
                self.dismiss_promo_modal()
                composer = page.locator('div[role="textbox"][contenteditable="true"]').first
                if composer.count() > 0 and composer.is_visible():
                    return
                new_project_btn = page.get_by_role("button", name="Yeni proje").first
                new_project_btn.wait_for(state="visible", timeout=30000)
                new_project_btn.click(force=True, timeout=15000)
                self.step("Proje composer'i bekleniyor")
                composer.wait_for(state="visible", timeout=60000)
                page.wait_for_timeout(700)
                return
            except Exception as exc:
                last_error = exc
                self.step(f"Composer acilamadi, yeniden denenecek: {str(exc)[:180]}")
                try:
                    page.screenshot(path=str(BOT_DIR / f"composer_error_{attempt}.png"), full_page=True)
                except Exception:
                    pass
                page.goto(FLOW_URL, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(2500)
        raise RuntimeError(f"Flow proje düzenleyicisi 3 denemede açılamadı: {last_error}")

    def generate_video(self, prompt_text: str, timeout_s: int = 480, settings: dict = None) -> dict:
        page = self.page
        self.open_project_composer()

        # 1.5 Istege bagli uretim ayarlari (oran, sure, sayi, cozunurluk, model, tur)
        if settings:
            self.apply_settings(settings)

        # 2. Prompt kutusuna yaz
        self.step(f"Prompt yaziliyor: {prompt_text[:60]!r}")
        box = page.locator('div[role="textbox"][contenteditable="true"]').first
        box.wait_for(state="visible", timeout=30000)
        box.click(force=True, timeout=15000)
        page.wait_for_timeout(200)
        box.fill(prompt_text, force=True, timeout=15000)
        page.wait_for_timeout(300)

        # 4. Olustur: buton tiklama guvenilir degil (baglantisiz kopya eleman
        #    yakalanabiliyor), bunun yerine kutuda Enter'a bas (gemini_bot'taki
        #    ask() ile ayni yontem).
        def check_policy_violation():
            """Icerik politikasi reddi toast'u kisa sureli gorunup kayboluyor -
            cagiran taraf bunu sik sik/erken cagirmali."""
            try:
                policy_text = page.get_by_text("politikalarımızı ihlal", exact=False).first
                if policy_text.count() > 0:
                    full_msg = page.locator('text=/Üretilen bu içerik.*ihlal.*/').first
                    msg = full_msg.inner_text() if full_msg.count() > 0 else policy_text.inner_text()
                    return msg
            except Exception:
                pass
            return None

        self.step("Video olusturma baslatiliyor")
        before_url = page.url
        box.press("Enter")
        page.wait_for_timeout(1000)
        prompt_after_submit = box.inner_text().strip()
        initial_state = self.diagnostic_state(prompt_text)
        self.step(
            f"Gonderim sonrasi durum: url={initial_state['url']!r}, "
            f"prompt_kutusu_uzunlugu={len(prompt_after_submit)}, "
            f"prompt_eslesmesi={initial_state['promptMatches']}, hatalar={initial_state['errors']}"
        )
        if prompt_after_submit == prompt_text.strip() and initial_state["promptMatches"] == 0 and page.url == before_url:
            self.save_diagnostics("submit-not-acknowledged", initial_state)
            return {"type": "submit_not_acknowledged", "message": "Flow oluşturma isteğini kabul etmedi. Tanı kaydı oluşturuldu."}

        # Icerik politikasi reddi toast'u birkac saniye icinde kaybolabiliyor -
        # buyuk 6sn'lik bekleyisten once sik sik (yaklasik her 500ms) kontrol et.
        for _ in range(10):
            page.wait_for_timeout(500)
            early_msg = check_policy_violation()
            if early_msg:
                self.step(f"Icerik politikasi reddi (erken yakalandi): {early_msg[:150]}")
                return {"type": "policy_violation", "message": early_msg}

        self.step("Video olusturulmasi bekleniyor (Flow arka planda render aliyor)")
        page.wait_for_timeout(1000)

        # 5. Video hazir olana kadar tekrar tekrar dene: ilk medya kartina tikla,
        #    gercek <video> etiketi + kaynagi var mi bak. Yoksa listeye geri don, bekle, tekrar dene.
        #    (Yuzde metni DOM'da guvenilir sekilde tek eleman icinde degil, bu yuzden video'nun
        #    kendisini aramak tek saglam sinyal.)
        # Not: "Başarısız" etiketi bazen render devam ederken de gecici olarak
        # DOM'da goruluyor (stale ikon/tooltip) - bu yuzden erken hata donusu
        # YAPMIYORUZ, sadece gercek <video> elementini ariyoruz. Gercekten basarisiz
        # olursa donmus zaman asimina ugrar (timeout_s), o zaman genel hata dondurulur.
        start_time = time.time()
        last_diagnostic_at = 0
        failure_first_seen = None
        src = None
        last_state = initial_state
        while time.time() - start_time < timeout_s and not src:
            elapsed = time.time() - start_time
            try:
                last_state = self.diagnostic_state(prompt_text)
                if elapsed - last_diagnostic_at >= 15:
                    self.step(
                        f"Flow durum t={int(elapsed)}sn: url={last_state['url']!r}, "
                        f"prompt_eslesmesi={last_state['promptMatches']}, "
                        f"video={len(last_state['videos'])}, hatalar={last_state['errors']}"
                    )
                    last_diagnostic_at = elapsed
                if last_state["videos"]:
                    candidate = next((video["src"] for video in last_state["videos"] if video["src"]), None)
                    if candidate:
                        src = candidate
                        self.step(f"Video hazir! ({int(elapsed)} sn)")
                        break
                if last_state["errors"]:
                    failure_first_seen = failure_first_seen or time.time()
                    if time.time() - failure_first_seen >= 12:
                        self.save_diagnostics("flow-terminal-error", last_state)
                        labels = ", ".join(last_state["errors"])
                        return {"type": "generation_failed", "message": f"Flow üretimi başarısız oldu: {labels}"}
                else:
                    failure_first_seen = None
            except Exception as exc:
                self.step(f"Flow durum okuma hatasi: {str(exc)[:180]}")

            # Icerik politikasi ihlali - yedek kontrol (erken yakalama kacirilirsa).
            late_msg = check_policy_violation()
            if late_msg:
                self.step(f"Icerik politikasi reddi: {late_msg[:150]}")
                return {"type": "policy_violation", "message": late_msg}

            # Onceki turda karta girdiysek (editor sayfasindayiz), her turda
            # once video var mi diye bakiyoruz - kart uzerine tekrar tiklamaya
            # calismiyoruz (o metin artik grid sayfasinda degil, editor'de yok).
            try:
                video_el = page.locator("video").first
                if video_el.count() > 0:
                    candidate = page.evaluate(
                        "() => { const v = document.querySelector('video'); return v ? (v.currentSrc || v.src) : null; }"
                    )
                    if candidate:
                        src = candidate
                        self.step(f"Video hazir! ({int(time.time() - start_time)} sn)")
                        break
            except Exception:
                pass

            # Kartlar <img>/<video> etiketi kullanmiyor (CSS arka plan gorseli),
            # bu yuzden promptun kendi metnini iceren en yakin buyukce kutuyu
            # JS ile bulup tikliyoruz (gemini_bot'taki click_visible_text ile ayni fikir).
            # ONEMLI: kart HALA render ediliyorsa bu tiklama "basarili" donuyor
            # (element bulundu, .click() cagrildi) ama HICBIR SEY OLMUYOR - Flow
            # bitmemis bir uretimi acmiyor. Bu yuzden BIR KEZ tiklayip birakmiyoruz,
            # video bulunana kadar HER turda tekrar deniyoruz (editor sayfasindaysak
            # zaten bu metin grid'de olmadigindan sorunsuzca hicbir sey bulunmaz).
            try:
                clicked = page.evaluate(
                    """
                    (promptText) => {
                        const normalize = value => String(value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                        const prompt = normalize(promptText);
                        const probes = [prompt.slice(0, 180), prompt.slice(0, 100), prompt.slice(0, 60)].filter(Boolean);
                        const visible = el => {
                            const rect = el.getBoundingClientRect();
                            const style = getComputedStyle(el);
                            return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
                        };
                        const all = Array.from(document.querySelectorAll('div, p, span'));
                        const hits = all.filter(el => {
                            if (!visible(el) || el.closest('[contenteditable="true"]')) return false;
                            const text = normalize(el.textContent);
                            return text && probes.some(probe => text.includes(probe));
                        }).sort((a, b) => a.textContent.length - b.textContent.length);
                        for (const hit of hits) {
                            let node = hit;
                            for (let i = 0; i < 10 && node; i++, node = node.parentElement) {
                                const rect = node.getBoundingClientRect();
                                if (rect.width > 120 && rect.height > 100 && rect.width < window.innerWidth * 0.95) {
                                    node.scrollIntoView({block: 'center'});
                                    node.click();
                                    return true;
                                }
                            }
                        }
                        return false;
                    }
                    """,
                    prompt_text,
                )
                if clicked:
                    page.wait_for_timeout(1200)
                    continue
            except Exception:
                pass

            page.wait_for_timeout(6000)
        else:
            if not src:
                self.step("Zaman asimina ugrandi")

        if not src:
            self.save_diagnostics("video-timeout", last_state)
            return {
                "type": "video_timeout",
                "message": "Flow video üretimini tamamlamadı veya sonuç kartı algılanamadı. Tanı kaydı oluşturuldu.",
                "diagnostic": last_state,
            }
        self.step(f"Video kaynagi: {src[:90]}")

        filename = f"flow_{uuid.uuid4().hex}.mp4"
        video_path = VIDEOS_DIR / filename
        if src.startswith("blob:"):
            download_name = f"flow-download-{uuid.uuid4().hex}.mp4"
            with page.expect_download(timeout=120000) as download_info:
                page.evaluate(
                    """
                    async ({src, filename}) => {
                        const response = await fetch(src);
                        if (!response.ok) throw new Error(`Video indirilemedi: ${response.status}`);
                        const blob = await response.blob();
                        const url = URL.createObjectURL(blob);
                        const anchor = document.createElement('a');
                        anchor.href = url;
                        anchor.download = filename;
                        document.body.appendChild(anchor);
                        anchor.click();
                        anchor.remove();
                        setTimeout(() => URL.revokeObjectURL(url), 1000);
                    }
                    """,
                    {"src": src, "filename": download_name},
                )
            download_info.value.save_as(video_path)
        else:
            response = self.context.request.get(src, timeout=120000)
            if not response.ok:
                raise RuntimeError(f"Video indirilemedi: HTTP {response.status}")
            video_path.write_bytes(response.body())
        if not video_path.exists() or video_path.stat().st_size < 1024:
            video_path.unlink(missing_ok=True)
            raise RuntimeError("İndirilen video geçersiz veya boş.")
        self.step(f"Video kaydedildi: {filename}")

        return {"type": "video", "file": filename}


def get_flow_credits(profile_dir: Path = None) -> dict:
    bot = FlowBot(headless=True, profile_dir=profile_dir)
    try:
        bot.start()
        page = bot.page
        profile_button = page.locator('button:has-text("PRO")').first
        profile_button.wait_for(state="visible", timeout=20000)
        profile_button.click(force=True, timeout=10000)
        credit_text = page.get_by_text("Google Flow kredileri", exact=False).first
        credit_text.wait_for(state="visible", timeout=15000)
        text = credit_text.inner_text()
        match = re.search(r"([\d.,]+)\s+Google Flow kredileri", text)
        if not match:
            raise RuntimeError("Kredi miktarı okunamadı.")
        value = int(match.group(1).replace(".", "").replace(",", ""))
        return {"ok": True, "credits": value, "label": text.strip()}
    except Exception as exc:
        log.exception("Flow kredi bilgisi okunamadı")
        return {"ok": False, "error": str(exc)[:300]}
    finally:
        bot.close()


def write_result(path: Path, result: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=path.parent, suffix=".tmp") as handle:
        json.dump(result, handle, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def run_generate_video_once(prompt_text: str, settings: dict = None, result_file: Path = RESULT_FILE, profile_dir: Path = None) -> None:
    log.info("=== Flow video uretim modu baslatildi: %s ===", datetime.now().isoformat())
    bot = FlowBot(headless=True, profile_dir=profile_dir)
    result: dict
    try:
        bot.start()
        data = bot.generate_video(prompt_text, settings=settings)
        if data.get("type") == "video" and data.get("file"):
            result = {"ok": True, "data": data}
        else:
            result = {"ok": False, "error": data.get("message", "Flow video dosyasi uretmedi."), "data": data}
    except Exception as exc:
        log.exception("Video olusturma hatasi")
        result = {"ok": False, "error": str(exc)[:500]}
    finally:
        bot.close()
    write_result(result_file, result)


def main():
    args = sys.argv[1:]
    profile_dir = None
    if "--profile-dir" in args:
        pi = args.index("--profile-dir")
        if pi + 1 < len(args):
            profile_dir = Path(args[pi + 1]).resolve()
    if "--credits" in args:
        result_file = RESULT_FILE
        if "--result-file" in args:
            ri = args.index("--result-file")
            if ri + 1 >= len(args):
                raise ValueError("--result-file için dosya yolu gerekli")
            result_file = Path(args[ri + 1]).resolve()
        write_result(result_file, get_flow_credits(profile_dir=profile_dir))
        return
    if "--generate-video" in args:
        idx = args.index("--generate-video")
        prompt_text = args[idx + 1]
        settings = None
        if "--settings" in args:
            si = args.index("--settings")
            try:
                settings = json.loads(args[si + 1])
            except Exception:
                settings = None
        result_file = RESULT_FILE
        if "--result-file" in args:
            ri = args.index("--result-file")
            if ri + 1 >= len(args):
                raise ValueError("--result-file için dosya yolu gerekli")
            result_file = Path(args[ri + 1]).resolve()
        run_generate_video_once(prompt_text, settings=settings, result_file=result_file, profile_dir=profile_dir)
        return
    print("Kullanim: python bot.py --generate-video \"prompt metni\" [--settings '{\"ratio\":\"16:9\"}'] [--profile-dir \"yol\"]")


if __name__ == "__main__":
    main()
