import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

from youtube_bot_service import channel_paths, cleanup_profile_chrome

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:
        pass

parser = argparse.ArgumentParser()
parser.add_argument("--channel-id", default="default")
args = parser.parse_args()
profile_dir, _ = channel_paths(args.channel_id)
login_marker = profile_dir / ".login_in_progress"
cleanup_profile_chrome(profile_dir)
login_marker.write_text(str(time.time()), encoding="utf-8")

chrome_exe = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
if not Path(chrome_exe).exists():
    chrome_exe = shutil.which("chrome") or "chrome"

try:
    cmd = [
        chrome_exe,
        f"--user-data-dir={profile_dir}",
        "--start-maximized",
        "--disable-blink-features=AutomationControlled",
        "https://studio.youtube.com"
    ]
    proc = subprocess.Popen(cmd)
    try:
        proc.wait()
    except KeyboardInterrupt:
        pass
finally:
    login_marker.unlink(missing_ok=True)
