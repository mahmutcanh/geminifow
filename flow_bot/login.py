"""
Google Hesabina Giris Yapma Araci (Flow icin)
Chrome'u gorunur modda acar, boylece Google Pro hesabiniza giris yapabilirsiniz.
Giris yaptiktan sonra oturum chrome_profile klasorune kalici olarak kaydedilir.
"""

import subprocess
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BOT_DIR = Path(__file__).parent
PROFILE_DIR = BOT_DIR / "chrome_profile"
PROFILE_DIR.mkdir(parents=True, exist_ok=True)


def cleanup_orphan_chrome(p_dir: Path) -> None:
    """Ayni profil dizinini kullanan asili Chrome islemlerini ve kilit dosyalarini temizler."""
    escaped_dir = str(p_dir.resolve()).replace("'", "''")
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


cleanup_orphan_chrome(PROFILE_DIR)

print("=" * 60)
print("GOOGLE HESABINA GIRIS ARACI (Flow)")
print(f"Profil Dizini: {PROFILE_DIR}")
print("Tarayici aciliyor... Lutfen acilan Chrome penceresinde Google hesabiniza giris yapin.")
print("Flow panelini gordukten sonra tarayiciyi kapatabilirsiniz.")
print("=" * 60)

with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        channel="chrome",
        headless=False,
        args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
        viewport=None,
    )
    page = context.new_page()
    page.goto("https://labs.google/fx/tr/tools/flow")

    print("\nGoogle Oturum acma ekrani acildi. Lutfen hesabiniza giris yapin...")
    print("Giris yapip Flow sayfasini gordukten sonra bu pencereyi kapatabilirsiniz.")
    try:
        while True:
            time.sleep(1)
            if not context.pages:
                break
    except KeyboardInterrupt:
        pass
    except Exception:
        pass
    finally:
        context.close()

print("\n✓ Google hesabi profili basariyla kaydedildi!")
