"""
YouTube Studio Playwright Otomasyon Servisi (shorts (1) Mimarisi).
API Anahtarı gerektirmeden Playwright tarayıcısı üzerinden doğrudan YouTube Studio'ya video yükler.
"""

import re
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
PROFILES_DIR = DATA_DIR / "youtube_profiles"
PROFILES_DIR.mkdir(parents=True, exist_ok=True)


def channel_paths(channel_id: str) -> tuple[Path, Path]:
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "", channel_id)[:64]
    if not safe_id:
        raise YouTubeBotError("Geçersiz kanal kimliği.")
    profile_dir = PROFILES_DIR / safe_id
    profile_dir.mkdir(parents=True, exist_ok=True)
    return profile_dir, DATA_DIR / f"youtube_cookies_{safe_id}.json"


def cleanup_profile_chrome(profile_dir: Path) -> None:
    if (profile_dir / ".login_in_progress").exists():
        return
    escaped = str(profile_dir.resolve()).replace("'", "''")
    command = (
        f"$profile = '{escaped}'; "
        "Get-CimInstance Win32_Process -Filter \"Name = 'chrome.exe'\" -ErrorAction SilentlyContinue | "
        "Where-Object { $_.CommandLine -and $_.CommandLine.Contains($profile) } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    if sys.platform == "win32":
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, timeout=10, check=False)
        except Exception:
            pass
    time.sleep(1)
    for lock_name in ("SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile", "DevToolsActivePort"):
        try:
            (profile_dir / lock_name).unlink(missing_ok=True)
        except OSError:
            pass


class YouTubeBotError(Exception):
    pass


def save_youtube_cookies(raw_data, channel_id: str = "default") -> dict:
    """Kullanıcının tarayıcıdan kopyaladığı veya yüklediği JSON çerezleri işleyip kaydeder."""
    import json
    try:
        if isinstance(raw_data, str):
            raw_data = json.loads(raw_data.strip())
        
        if not isinstance(raw_data, list):
            return {"ok": False, "error": "Çerez verisi bir JSON listesi (array) olmalıdır."}

        clean_cookies = []
        for c in raw_data:
            if not isinstance(c, dict) or "name" not in c or "value" not in c:
                continue
            cookie_obj = {
                "name": str(c["name"]),
                "value": str(c["value"]),
                "domain": str(c.get("domain", ".youtube.com")),
                "path": str(c.get("path", "/")),
                "httpOnly": bool(c.get("httpOnly", False)),
                "secure": bool(c.get("secure", True)),
            }
            if "sameSite" in c and c["sameSite"]:
                ss = str(c["sameSite"]).lower()
                if ss in ["strict", "lax", "none", "no_restriction"]:
                    cookie_obj["sameSite"] = "None" if ss == "no_restriction" else ss.capitalize()
            if "expirationDate" in c and c["expirationDate"]:
                try:
                    cookie_obj["expires"] = int(c["expirationDate"])
                except Exception:
                    pass
            clean_cookies.append(cookie_obj)

        if not clean_cookies:
            return {"ok": False, "error": "Geçerli çerez (cookie) bulunamadı."}

        _, cookies_file = channel_paths(channel_id)
        cookies_file.write_text(json.dumps(clean_cookies, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"ok": True, "count": len(clean_cookies), "message": f"{len(clean_cookies)} adet YouTube çerezi başarıyla kaydedildi!"}
    except Exception as e:
        return {"ok": False, "error": f"Çerez işleme hatası: {str(e)}"}


def open_youtube_login_browser(channel_id: str = "default", timeout_seconds: int = 180) -> dict:
    """Kullanıcının YouTube / Google hesabına bir kez giriş yapabilmesi için tarayıcıyı bağımsız süreç olarak açar."""
    try:
        import subprocess
        login_script = BASE_DIR / "open_youtube_login.py"
        if not login_script.exists():
            return {"ok": False, "error": "open_youtube_login.py bulunamadı."}

        command = [sys.executable, str(login_script), "--channel-id", channel_id]
        popen_options = {
            "cwd": str(BASE_DIR),
            "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
            "close_fds": True,
        }
        if sys.platform == "win32":
            popen_options["stdout"] = subprocess.DEVNULL
            popen_options["stderr"] = subprocess.DEVNULL
        process = subprocess.Popen(command, **popen_options)
        time.sleep(0.35)
        if process.poll() is not None:
            return {"ok": False, "error": "YouTube giriş tarayıcısı başlatılamadı."}
        return {
            "ok": True,
            "message": "YouTube Studio giriş penceresi açıldı."
        }
    except Exception as e:
        return {"ok": False, "error": f"Tarayıcı açma hatası: {str(e)}"}


def get_youtube_channel_info(channel_id: str = "default") -> dict:
    profile_dir, _ = channel_paths(channel_id)
    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                channel="chrome",
                headless=True,
                locale="tr-TR",
                args=["--disable-blink-features=AutomationControlled"],
                viewport={"width": 1280, "height": 800},
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto("https://studio.youtube.com", wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(4000)
                if "accounts.google.com" in page.url:
                    return {"ok": False, "connected": False, "error": "YouTube oturumu bulunamadı."}
                channel_name = page.locator("#channel-name").first
                if channel_name.count() > 0:
                    text = channel_name.inner_text(timeout=10000).strip()
                    if text:
                        return {"ok": True, "connected": True, "channel_name": text}
                title = page.title().strip()
                title = re.sub(r"\s*[-–]\s*YouTube Studio.*$", "", title, flags=re.I).strip()
                if title and title.lower() not in {"youtube studio", "kanal kontrol paneli"}:
                    return {"ok": True, "connected": True, "channel_name": title}
                return {"ok": False, "connected": True, "error": "Kanal adı okunamadı."}
            finally:
                context.close()
    except Exception as exc:
        return {"ok": False, "connected": False, "error": str(exc)[:300]}


def upload_video_to_youtube(
    video_path: Path | str,
    title: str,
    description: str = "",
    tags: list[str] | None = None,
    privacy_status: str = "public",  # public, unlisted, private, scheduled
    publish_at: str | None = None,
    headless: bool = True,
    channel_id: str = "default",
    draft: bool = False,
    thumbnail_path: Path | str | None = None,
) -> dict:
    """Videoyu Playwright kullanarak YouTube Studio'ya yükler ve isteğe bağlı olarak zamanlar."""
    v_path = Path(video_path)
    if not v_path.exists():
        return {"ok": False, "error": f"Video dosyası bulunamadı: {v_path}"}

    tags_list = tags or []
    tags_str = ", ".join(tags_list) if isinstance(tags_list, list) else str(tags_list)
    profile_dir, cookies_file = channel_paths(channel_id)
    cleanup_profile_chrome(profile_dir)

    try:
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                channel="chrome",
                headless=headless,
                locale="tr-TR",
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--start-maximized"
                ],
                viewport={"width": 1280, "height": 800} if headless else None
            )

            page = context.pages[0] if context.pages else context.new_page()
            
            # Load stored JSON cookies if present
            if cookies_file.exists():
                try:
                    import json
                    c_data = json.loads(cookies_file.read_text(encoding="utf-8"))
                    if isinstance(c_data, list) and c_data:
                        context.add_cookies(c_data)
                except Exception as c_err:
                    print(f"[Cookie Load Warning] {c_err}")

            try:
                page.goto("https://studio.youtube.com", wait_until="domcontentloaded", timeout=60000)
                time.sleep(5)

                # Dismiss potential interstitial dialogs
                skip_to_studio = page.get_by_text("SKIP TO YOUTUBE STUDIO", exact=False)
                if skip_to_studio.count() == 0:
                    skip_to_studio = page.get_by_text("YOUTUBE STUDIO'YA GEÇ", exact=False)
                if skip_to_studio.count() > 0:
                    skip_to_studio.first.click()
                    time.sleep(3)

                # Check if login is required
                if "accounts.google.com" in page.url:
                    raise YouTubeBotError("YouTube oturumu bulunamadı! Lütfen önce '🍪 YouTube Çerezlerini Aktar' veya '🔑 Sunucu Girişi' ile hesabınızı bağlayın.")

                # Click 'CREATE' / 'OLUŞTUR' button
                create_btn = page.get_by_role("button", name="Create", exact=True)
                if create_btn.count() == 0:
                    create_btn = page.get_by_role("button", name="Oluştur", exact=True)
                if create_btn.count() == 0:
                    create_btn = page.query_selector("#create-icon") or page.query_selector("ytcp-button#create-icon")
                
                if not create_btn:
                    raise YouTubeBotError("YouTube Studio 'Oluştur' düğmesi bulunamadı. Lütfen oturumun açık olduğunu doğrulayın.")
                
                if hasattr(create_btn, 'click'):
                    create_btn.click()
                else:
                    create_btn.first.click()
                time.sleep(1.5)

                # Click 'Upload videos' / 'Video yükle'
                upload_item = page.locator("tp-yt-paper-item").filter(has_text="Upload videos")
                if upload_item.count() == 0:
                    upload_item = page.locator("tp-yt-paper-item").filter(has_text="Video yükle")
                if upload_item.count() == 0:
                    upload_item = page.locator("#text-item-0")
                
                if upload_item.count() == 0:
                    raise YouTubeBotError("'Video Yükle' seçeneği bulunamadı.")
                upload_item.first.click()
                time.sleep(2.5)

                # File input
                file_input = page.query_selector("input[type='file']")
                if not file_input:
                    raise YouTubeBotError("Dosya yükleme alanı (file input) bulunamadı.")

                file_input.set_input_files(str(v_path.absolute()))
                time.sleep(5)

                # Title input
                title_box = page.query_selector("#title-textarea #textbox") or page.query_selector("ytcp-social-suggestions-textbox[label*='Başlık'] #textbox") or page.query_selector("ytcp-social-suggestions-textbox[label*='Title'] #textbox")
                if title_box:
                    title_box.click()
                    page.keyboard.press("Control+A")
                    page.keyboard.press("Backspace")
                    clean_title = (title[:95] + " #Shorts") if "#Shorts" not in title else title[:100]
                    title_box.fill(clean_title)
                    time.sleep(1)

                # Description input
                desc_box = page.query_selector("#description-textarea #textbox") or page.query_selector("ytcp-social-suggestions-textbox[label*='Açıklama'] #textbox") or page.query_selector("ytcp-social-suggestions-textbox[label*='Description'] #textbox")
                if desc_box and description:
                    desc_box.click()
                    clean_desc = description + "\n\n#Shorts #AI #Gemini #Video"
                    desc_box.fill(clean_desc[:4900])
                    time.sleep(1)

                if thumbnail_path:
                    thumb_path = Path(thumbnail_path)
                    if thumb_path.exists():
                        thumbnail_input = page.query_selector("input[type='file'][accept*='image']")
                        if thumbnail_input:
                            thumbnail_input.set_input_files(str(thumb_path.absolute()))
                            time.sleep(2)

                # Child safety: 'No, it's not made for kids'
                no_kids_radio = page.query_selector("tp-yt-paper-radio-button[name='VIDEO_MADE_FOR_KIDS_NOT_MFK']") or page.query_selector("#made-for-kids-group tp-yt-paper-radio-button:nth-child(2)")
                if no_kids_radio:
                    no_kids_radio.click()
                    time.sleep(0.5)

                # Tags if provided
                if tags_str:
                    try:
                        show_more = page.query_selector("ytcp-button#toggle-button") or page.get_by_text("DAHA FAZLA GÖSTER", exact=False) or page.get_by_text("SHOW MORE", exact=False)
                        if show_more:
                            if hasattr(show_more, 'click'):
                                show_more.click()
                            else:
                                show_more.first.click()
                            time.sleep(1)
                        tags_input = page.query_selector("#tags-container input") or page.query_selector("input[aria-label*='Etiket']") or page.query_selector("input[aria-label*='Tags']")
                        if tags_input:
                            tags_input.fill(tags_str[:480])
                            page.keyboard.press("Enter")
                    except Exception:
                        pass

                time.sleep(2)

                # Click 'NEXT' / 'İLERİ' (Video Details -> Monetization/Checks -> Visibility).
                # YouTube Studio artik '#next-button' id'sini guvenilir sekilde kullanmiyor
                # (yeni arayuzde native .click() cogu zaman sessizce hicbir sey yapmiyor);
                # rol/metin tabanli locator ile gercek tiklama simule edilip buton
                # etkinlesene kadar (video isleme surebilir) beklenir.
                ileri_btn = page.get_by_role("button", name="İleri")
                pub_radio_probe = page.locator(
                    "tp-yt-paper-radio-button[name='PUBLIC'], tp-yt-paper-radio-button[name='PRIVATE'], tp-yt-paper-radio-button[name='UNLISTED']"
                )
                nav_deadline = time.time() + 300  # video islenmesi uzun surebilir
                while time.time() < nav_deadline:
                    if pub_radio_probe.count() > 0 and pub_radio_probe.first.is_visible():
                        break
                    if ileri_btn.count() > 0:
                        try:
                            if ileri_btn.first.is_enabled():
                                ileri_btn.first.click(force=True)
                                time.sleep(1.5)
                            else:
                                time.sleep(2)
                        except Exception:
                            time.sleep(2)
                    else:
                        time.sleep(2)
                else:
                    raise YouTubeBotError("Görünürlük adımına ulaşılamadı (video işleme zaman aşımına uğradı).")

                # Get video URL (goruntuleme oncesi, gercek short linki)
                video_url_elem = page.query_selector("a.style-scope.ytcp-video-info") or page.query_selector("a[href*='youtu.be']")
                video_url = video_url_elem.get_attribute("href") if video_url_elem else ""

                # Visibility Step (Schedule or Instant Publish).
                # Yeni arayuzde ayri bir 'SCHEDULE' radio'su yok: 'Planlayin' paneli
                # tarih/saat iceren bagimsiz bir bolum, secilince ust panel otomatik kapaniyor.
                if draft:
                    vis_btn = page.query_selector("tp-yt-paper-radio-button[name='PRIVATE']")
                    if vis_btn:
                        vis_btn.click()
                        time.sleep(1)
                    expected_btn_re = re.compile(r"^(Kaydet|Save)$")
                elif publish_at:
                    plan_header = page.get_by_text("Planlayın", exact=False).first
                    if plan_header.count() > 0:
                        plan_header.click(force=True)

                    fill_js = """(newTime) => {
                        const inputs = Array.from(document.querySelectorAll('input[type="text"]'));
                        const el = inputs.find(i => /^\\d{1,2}:\\d{2}$/.test((i.value || '').trim()));
                        if (!el) return false;
                        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                        setter.call(el, newTime);
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        el.blur();
                        return true;
                    }"""
                    target_time = publish_at.split(" ")[-1]
                    filled = False
                    fill_deadline = time.time() + 12
                    try:
                        while time.time() < fill_deadline and not filled:
                            filled = page.evaluate(fill_js, target_time)
                            if not filled:
                                time.sleep(1)
                    except Exception as s_err:
                        print(f"[Scheduling Warning] {s_err}")
                    if not filled:
                        raise YouTubeBotError("Zamanlama saat alanı bulunamadı, video geçmiş bir saatte kalıp reddedilebilirdi.")
                    time.sleep(0.5)
                    expected_btn_re = re.compile(r"^Planla$")
                else:
                    priv = privacy_status.lower()
                    if priv == "private":
                        vis_name = "PRIVATE"
                    elif priv == "unlisted":
                        vis_name = "UNLISTED"
                    else:
                        vis_name = "PUBLIC"
                    vis_btn = page.query_selector(f"tp-yt-paper-radio-button[name='{vis_name}']")
                    if vis_btn:
                        vis_btn.click()
                        time.sleep(1)
                    expected_btn_re = re.compile(r"^(Yayınla|Kaydet)$")

                # Click the primary action button ('Yayınla' / 'Kaydet' / 'Planla').
                # '#done-button' id'si yeni arayuzde kaldirilmis; metin/rol bazli bulunmali.
                action_btn = page.get_by_role("button", name=expected_btn_re)
                if action_btn.count() == 0 or not action_btn.first.is_enabled():
                    raise YouTubeBotError("YouTube Studio 'Yayınla / Zamanla / Planla' butonuna tıklanamadı.")

                action_btn.first.click(force=True)

                # Diyalogun gercekten kapanip islemin tamamlandigini dogrula (sahte basari
                # raporlamamak icin - eskiden butona tiklanip tiklanmadigina bakilmiyordu).
                # NOT: buton bazen DOM'dan silinmiyor (count() hep >0 kaliyor), sadece
                # gorunmez/disabled hale geliyor ya da 'Kapat' onay dialogu aciliyor -
                # bu yuzden gorunurluk + kapat-dialogu da basari kabul edilmeli.
                close_dialog_btn = page.get_by_role("button", name=re.compile(r"^(Kapat|Close)$"))
                closed = False
                verify_deadline = time.time() + 60
                while time.time() < verify_deadline:
                    if action_btn.count() == 0:
                        closed = True
                        break
                    try:
                        if not action_btn.first.is_visible():
                            closed = True
                            break
                    except Exception:
                        closed = True
                        break
                    if close_dialog_btn.count() > 0:
                        closed = True
                        break
                    time.sleep(1)
                if not closed:
                    raise YouTubeBotError("Video onaylandı ama yükleme penceresi kapanmadı, işlem doğrulanamadı.")

                return {
                    "ok": True,
                    "video_url": video_url or "https://youtube.com/shorts",
                    "message": f"Video YouTube Shorts olarak {'taslağa kaydedildi' if draft else 'zamanlandı (' + publish_at + ')' if publish_at else 'başarıyla yüklendi'}!"
                }

            except Exception as e:
                raise YouTubeBotError(str(e)) from e
            finally:
                context.close()

    except Exception as exc:
        return {"ok": False, "error": f"YouTube Yükleme Hatası: {str(exc)}"}
