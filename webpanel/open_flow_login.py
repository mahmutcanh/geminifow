import argparse
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:
        pass

BASE_DIR = Path(__file__).parent.parent
PROFILES_DIR = BASE_DIR / "flow_bot" / "flow_profiles"
PROFILES_DIR.mkdir(parents=True, exist_ok=True)

parser = argparse.ArgumentParser(description="Google Flow hesabı için görünür Chrome penceresi açar.")
parser.add_argument("--profile-name", default="hesap1", help="Profil adı (örn: hesap1, hesap2)")
args = parser.parse_args()

profile_path = PROFILES_DIR / args.profile_name
profile_path.mkdir(parents=True, exist_ok=True)

print(f"=== Flow Hesabı Girişi: {args.profile_name} ===")
print(f"Profil Dizini: {profile_path}")
print("Açılan Chrome penceresinde Google Flow hesabınızla giriş yapın (labs.google/flow).")
print("Giriş tamamlandıktan sonra pencereyi kapatabilirsiniz.")

with sync_playwright() as playwright:
    context = playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile_path),
        headless=False,
        channel="chrome",
        locale="tr-TR",
        args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
        viewport=None,
    )
    page = context.pages[0] if context.pages else context.new_page()
    page.goto("https://labs.google/flow", wait_until="domcontentloaded", timeout=60000)
    try:
        while context.pages:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        context.close()
