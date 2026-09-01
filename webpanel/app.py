import hmac
import json
import os
import queue
import re
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from PIL import Image, ImageDraw, ImageFont

BASE_DIR = Path(__file__).resolve().parent
BOT_DIR = BASE_DIR.parent / "flow_bot"
BOT_SCRIPT = BOT_DIR / "bot.py"
VIDEOS_DIR = BASE_DIR / "static" / "videos"
POOL_IMAGES_DIR = BASE_DIR / "static" / "pool_images"
JOBS_FILE = BASE_DIR / "jobs.json"
POOL_FILE = BASE_DIR / "pool.json"
USERS_FILE = BASE_DIR / "google_users.json"
AUTOMATION_FILE = BASE_DIR / "automation.json"
SESSION_SECRET_FILE = BASE_DIR / ".session_secret"
YOUTUBE_BOT_DIR = BASE_DIR
OAUTH_CONFIG = {}
for credentials_file in BASE_DIR.parent.glob("client_secret_*.json"):
    try:
        OAUTH_CONFIG = json.loads(credentials_file.read_text(encoding="utf-8")).get("web", {})
        if OAUTH_CONFIG:
            break
    except (OSError, json.JSONDecodeError):
        continue
AI_API_BASE = os.environ.get("GEMINIFLOW_AI_API_BASE", "https://omni.homaklab.com/v1").rstrip("/")
AI_API_KEY = os.environ.get("GEMINIFLOW_AI_API_KEY")
if not AI_API_KEY and sys.platform == "win32":
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
            AI_API_KEY, _ = winreg.QueryValueEx(key, "GEMINIFLOW_AI_API_KEY")
    except Exception:
        AI_API_KEY = ""
AI_API_KEY = (AI_API_KEY or "").strip()
AI_MODEL = os.environ.get("GEMINIFLOW_AI_MODEL", "maho")
PANEL_HOST = os.environ.get("GEMINIFLOW_HOST", "127.0.0.1")
PANEL_PORT = int(os.environ.get("GEMINIFLOW_PORT", "5051"))
PANEL_API_TOKEN = os.environ.get("GEMINIFLOW_PANEL_TOKEN", "")
GOOGLE_CLIENT_ID = os.environ.get("GEMINIFLOW_GOOGLE_CLIENT_ID", OAUTH_CONFIG.get("client_id", ""))
GOOGLE_CLIENT_SECRET = os.environ.get("GEMINIFLOW_GOOGLE_CLIENT_SECRET", OAUTH_CONFIG.get("client_secret", ""))
GOOGLE_REDIRECT_URI = os.environ.get("GEMINIFLOW_GOOGLE_REDIRECT_URI", next(iter(OAUTH_CONFIG.get("redirect_uris", [])), ""))
ADMIN_SETUP_TOKEN = os.environ.get("GEMINIFLOW_ADMIN_TOKEN", "")
PANEL_ADMIN_EMAIL = os.environ.get("GEMINIFLOW_ADMIN_EMAIL", "mahmutcan.homak@gmail.com").strip().lower()
BOT_TIMEOUT_SECONDS = int(os.environ.get("GEMINIFLOW_BOT_TIMEOUT", "660"))
MAX_PROMPT_LENGTH = 50_000
MAX_AI_MESSAGE_LENGTH = 40_000
MAX_AI_TOTAL_LENGTH = 80_000
MAX_AI_MESSAGES = 30
MAX_QUEUE_SIZE = 100
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
ALLOWED_SETTINGS = {
    "type": {"video"},
    "ratio": {"16:9", "9:16"},
    "resolution": {"360p", "720p"},
    "duration": {"4s", "6s", "8s", "10s"},
    "count": {"x1"},
    "model": {"omni-flash", "veo-lite", "veo-fast", "veo-quality"},
}
IMAGE_SIGNATURES = {
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".gif": (b"GIF87a", b"GIF89a"),
    ".webp": (b"RIFF",),
}

VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
POOL_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
app = Flask(__name__)
if os.environ.get("GEMINIFLOW_SESSION_SECRET"):
    app.secret_key = os.environ["GEMINIFLOW_SESSION_SECRET"]
elif SESSION_SECRET_FILE.exists():
    app.secret_key = SESSION_SECRET_FILE.read_text(encoding="utf-8").strip()
else:
    app.secret_key = secrets.token_hex(32)
    SESSION_SECRET_FILE.write_text(app.secret_key, encoding="utf-8")
app.config.update(
    MAX_CONTENT_LENGTH=MAX_UPLOAD_BYTES,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("GEMINIFLOW_COOKIE_SECURE", "0") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
)
JOBS_LOCK = threading.RLock()
POOL_LOCK = threading.RLock()
JOB_QUEUE: queue.Queue[str] = queue.Queue(maxsize=MAX_QUEUE_SIZE)
YOUTUBE_QUEUE: queue.Queue[tuple[str, bool]] = queue.Queue(maxsize=MAX_QUEUE_SIZE)
YOUTUBE_CANCELLED: set[str] = set()
YOUTUBE_ACTIVE: set[str] = set()
YOUTUBE_LOCK = threading.RLock()
JOBS: dict[str, dict] = {}
POOL: list[dict] = []
ALLOWED_USERS: set[str] = set()
AUTOMATION_LOCK = threading.RLock()
AUTOMATION_CATEGORIES: dict[str, dict] = {}
CREDITS_CACHE = {"credits": None, "checkedAt": None, "expiresAt": 0.0, "loading": False}
CREDITS_LOCK = threading.RLock()
USERS_LOCK = threading.RLock()
WORKER_STARTED = False
YOUTUBE_WORKER_STARTED = False
WORKER_LOCK = threading.Lock()
BOT_EXECUTION_LOCK = threading.Lock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def read_json_list(path: Path) -> list:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        app.logger.error("JSON okunamadı: %s: %s", path, exc)
        return []
    if not isinstance(data, list):
        app.logger.error("JSON liste değil: %s", path)
        return []
    return data


def valid_job(item: object) -> bool:
    return isinstance(item, dict) and isinstance(item.get("id"), str) and isinstance(item.get("prompt"), str) and isinstance(item.get("createdAt"), str)


def valid_pool_item(item: object) -> bool:
    return isinstance(item, dict) and isinstance(item.get("id"), str) and isinstance(item.get("text"), str) and isinstance(item.get("createdAt"), str)


def normalize_email(value: object) -> str:
    email = str(value or "").strip().lower()
    if len(email) > 254 or email.count("@") != 1 or not all(email.split("@")):
        return ""
    return email


def save_users() -> None:
    with USERS_LOCK:
        atomic_write_json(USERS_FILE, sorted(ALLOWED_USERS))


def save_jobs() -> None:
    with JOBS_LOCK:
        data = sorted((dict(job) for job in JOBS.values()), key=lambda job: job.get("createdAt", ""))
        atomic_write_json(JOBS_FILE, data)


def save_pool() -> None:
    with POOL_LOCK:
        atomic_write_json(POOL_FILE, list(POOL))


def load_state() -> None:
    for item in read_json_list(JOBS_FILE):
        if not valid_job(item):
            app.logger.warning("Geçersiz iş kaydı atlandı")
            continue
        if item.get("status") == "işleniyor":
            item["status"] = "hata"
            item["note"] = "Panel yeniden başlatıldığı için yarım kalan iş iptal edildi."
        if item.get("youtubeStatus") in {"sırada", "paylaşılıyor"}:
            item["youtubeStatus"] = "iptal"
            item["youtubeQueuePosition"] = None
            item["youtubeError"] = "Panel yeniden başlatıldığı için YouTube işlemi iptal edildi."
        JOBS[item["id"]] = item
    for item in read_json_list(POOL_FILE):
        if valid_pool_item(item):
            POOL.append(item)
        else:
            app.logger.warning("Geçersiz havuz kaydı atlandı")
    for value in read_json_list(USERS_FILE):
        email = normalize_email(value)
        if email:
            ALLOWED_USERS.add(email)
    load_automation()
    sync_automation_categories_from_pool()
    waiting = [job for job in JOBS.values() if job.get("status") == "bekliyor"]
    for job in sorted(waiting, key=lambda value: value.get("createdAt", ""))[:MAX_QUEUE_SIZE]:
        JOB_QUEUE.put_nowait(job["id"])
    save_jobs()


def slugify_category(name: str) -> str:
    lowered = name.strip().lower()
    translit = lowered.translate(str.maketrans("çğıöşü", "cgiosu"))
    slug = re.sub(r"[^a-z0-9]+", "-", translit).strip("-")
    return slug[:60] or uuid.uuid4().hex[:10]


def save_automation() -> None:
    with AUTOMATION_LOCK:
        atomic_write_json(AUTOMATION_FILE, AUTOMATION_CATEGORIES)


def load_automation() -> None:
    if not AUTOMATION_FILE.exists():
        return
    try:
        data = json.loads(AUTOMATION_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        app.logger.error("Otomasyon dosyası okunamadı: %s", AUTOMATION_FILE)
        return
    if not isinstance(data, dict):
        return
    for slug, entry in data.items():
        if isinstance(entry, dict) and isinstance(entry.get("name"), str):
            AUTOMATION_CATEGORIES[slug] = entry


def new_automation_entry(name: str) -> dict:
    return {
        "name": name,
        "enabled": False,
        "channelConnected": False,
        "channelName": "",
        "privacy": "public",
        "createdAt": utc_now(),
        "publishedCount": 0,
    }


def sync_automation_categories_from_pool() -> None:
    """Prompt havuzundaki her kategori adı için otomatik olarak bir otomasyon
    kategorisi oluşturur (yoksa). Havuz kategorileri tek doğruluk kaynağıdır -
    burada silme yapılmaz, sadece eksik olanlar eklenir."""
    with POOL_LOCK:
        names = {(item.get("category") or "Genel").strip() for item in POOL if item.get("category")}
    changed = False
    with AUTOMATION_LOCK:
        for name in names:
            slug = slugify_category(name)
            if slug not in AUTOMATION_CATEGORIES:
                AUTOMATION_CATEGORIES[slug] = new_automation_entry(name)
                changed = True
        if changed:
            save_automation()


def public_automation_category(slug: str, entry: dict) -> dict:
    return {
        "id": slug,
        "name": entry.get("name", ""),
        "enabled": bool(entry.get("enabled")),
        "connected": bool(entry.get("channelConnected")),
        "channelName": entry.get("channelName") or "",
        "connectedAt": entry.get("connectedAt"),
        "lastPublishedAt": entry.get("lastPublishedAt"),
        "lastError": entry.get("lastError"),
        "publishedCount": entry.get("publishedCount", 0),
        "privacy": entry.get("privacy", "public"),
    }


def validate_settings(raw: object) -> tuple[dict, str | None]:
    if raw is None:
        return {}, None
    if not isinstance(raw, dict):
        return {}, "Ayarlar geçersiz."
    settings = {}
    for key, value in raw.items():
        if key not in ALLOWED_SETTINGS or value not in ALLOWED_SETTINGS[key]:
            return {}, f"Desteklenmeyen ayar: {key}={value}"
        settings[key] = value
    settings.setdefault("type", "video")
    settings.setdefault("count", "x1")
    return settings, None


def terminate_process_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=15, check=False)
    else:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def run_bot_generate(job_id: str, prompt: str, settings: dict | None = None, timeout_s: float = BOT_TIMEOUT_SECONDS, profile_dir: Path | str | None = None) -> dict:
    result_file = BOT_DIR / f"result_{job_id}.json"
    result_file.unlink(missing_ok=True)
    cmd = [sys.executable, str(BOT_SCRIPT), "--generate-video", prompt, "--result-file", str(result_file)]
    if profile_dir:
        cmd.extend(["--profile-dir", str(profile_dir)])
    if settings:
        cmd.extend(["--settings", json.dumps(settings, ensure_ascii=False)])
    err_log = BOT_DIR / f"bot_stderr_{job_id}.log"
    process_log = BOT_DIR / f"bot_{job_id}.log"
    log_handle = process_log.open("w", encoding="utf-8")
    popen_kwargs = {"cwd": str(BOT_DIR), "stdout": log_handle, "stderr": subprocess.STDOUT, "text": True, "encoding": "utf-8", "errors": "replace"}
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    with BOT_EXECUTION_LOCK:
        proc = subprocess.Popen(cmd, **popen_kwargs)
        try:
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            terminate_process_tree(proc)
            if result_file.exists():
                try:
                    result = json.loads(result_file.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    result = {"ok": False, "error": f"Video oluşturma toplam zaman sınırını aştı. Log: {process_log.name}"}
            else:
                result = {"ok": False, "error": f"Video oluşturma toplam zaman sınırını aştı. Log: {process_log.name}"}
        else:
            if result_file.exists():
                try:
                    result = json.loads(result_file.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    result = {"ok": False, "error": "Bot sonucu doğrulanamadı."}
            elif proc.returncode:
                result = {"ok": False, "error": f"Bot beklenmedik biçimde kapandı. Log: {process_log.name}"}
            else:
                result = {"ok": False, "error": f"Bot sonuç üretmeden kapandı. Log: {process_log.name}"}
        finally:
            log_handle.close()
    if process_log.exists() and not result.get("ok"):
        err_log.write_text(process_log.read_text(encoding="utf-8", errors="replace")[-20_000:], encoding="utf-8")
    result_file.unlink(missing_ok=True)
    return result


def invalidate_credits() -> None:
    with CREDITS_LOCK:
        CREDITS_CACHE["expiresAt"] = 0.0


def refresh_credits(profile_dir: Path | str | None = None) -> None:
    result_file = BOT_DIR / f"credits_{uuid.uuid4().hex}.json"
    try:
        cmd = [sys.executable, str(BOT_SCRIPT), "--credits", "--result-file", str(result_file)]
        if profile_dir:
            cmd.extend(["--profile-dir", str(profile_dir)])
        with BOT_EXECUTION_LOCK:
            completed = subprocess.run(cmd, cwd=BOT_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90, check=False)
        if completed.returncode == 0 and result_file.exists():
            result = json.loads(result_file.read_text(encoding="utf-8"))
        else:
            result = {"ok": False, "error": "Flow kredi sorgusu başarısız."}
    except Exception as exc:
        result = {"ok": False, "error": str(exc)[:300]}
    finally:
        result_file.unlink(missing_ok=True)
    with CREDITS_LOCK:
        if result.get("ok"):
            CREDITS_CACHE["credits"] = result.get("credits")
            CREDITS_CACHE["checkedAt"] = utc_now()
            CREDITS_CACHE["error"] = None
            CREDITS_CACHE["expiresAt"] = datetime.now(timezone.utc).timestamp() + 300
        else:
            CREDITS_CACHE["error"] = result.get("error")
            CREDITS_CACHE["expiresAt"] = datetime.now(timezone.utc).timestamp() + 60
        CREDITS_CACHE["loading"] = False


def generate_youtube_metadata(prompt: str, category_name: str) -> dict:
    fallback_title = f"{category_name}: {prompt.strip()[:65]} #Shorts"
    fallback = {"title": fallback_title[:100], "description": f"{prompt.strip()[:1200]}\n\n#Shorts #AI #{re.sub(r'[^A-Za-z0-9]+', '', category_name) or 'Video'}", "tags": [category_name, "Shorts", "AI"]}
    if not AI_API_KEY:
        return fallback
    messages = [{"role": "system", "content": "You are a YouTube Shorts content editor. Return valid JSON only: title (engaging English, maximum 90 characters), description (English, maximum 900 characters, ending with 3-5 English hashtags), tags (8-12 short English tags). Every public-facing word, including hashtags, must be English. Do not write misleading claims."}, {"role": "user", "content": f"Category: {category_name}\nVideo concept: {prompt[:6000]}"}]
    payload = json.dumps({"model": AI_MODEL, "stream": False, "messages": messages, "response_format": {"type": "json_object"}}).encode("utf-8")
    upstream = urllib.request.Request(f"{AI_API_BASE}/chat/completions", data=payload, headers={"Content-Type": "application/json", "Authorization": f"Bearer {AI_API_KEY}", "User-Agent": "curl/8.0"}, method="POST")
    try:
        with urllib.request.urlopen(upstream, timeout=60) as response:
            body = json.loads(response.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"].strip()
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content)
        data = json.loads(content)
        title = str(data.get("title") or fallback["title"]).strip()[:100]
        if "#Shorts" not in title:
            title = f"{title[:91]} #Shorts"
        description = str(data.get("description") or fallback["description"]).strip()[:4900]
        tags = [str(tag).strip()[:60] for tag in data.get("tags", []) if str(tag).strip()][:12]
        return {"title": title, "description": description, "tags": tags or fallback["tags"]}
    except Exception:
        app.logger.exception("YouTube metadatası üretilemedi")
        return fallback


def create_youtube_thumbnail(video_path: Path, title: str, job_id: str) -> Path | None:
    thumbnail = VIDEOS_DIR / f"thumbnail_{job_id}.jpg"
    try:
        completed = subprocess.run(["ffmpeg", "-y", "-ss", "00:00:01", "-i", str(video_path), "-frames:v", "1", str(thumbnail)], capture_output=True, timeout=30, check=False)
        if completed.returncode != 0 or not thumbnail.exists():
            return None
        with Image.open(thumbnail) as source:
            image = source.convert("RGB").resize((1280, 720))
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        draw.rectangle((0, 470, 1280, 720), fill=(0, 0, 0, 175))
        font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arialbd.ttf"
        font = ImageFont.truetype(str(font_path), 60) if font_path.exists() else ImageFont.load_default()
        words, lines, line = title.replace("#Shorts", "").strip().split(), [], ""
        for word in words:
            candidate = f"{line} {word}".strip()
            if draw.textlength(candidate, font=font) > 1120 and line:
                lines.append(line)
                line = word
            else:
                line = candidate
        if line:
            lines.append(line)
        y = 500
        for text in lines[:2]:
            draw.text((80, y), text, font=font, fill="white", stroke_width=2, stroke_fill="black")
            y += 75
        image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
        image.save(thumbnail, "JPEG", quality=92, optimize=True)
        return thumbnail
    except Exception:
        app.logger.exception("YouTube kapağı üretilemedi")
        thumbnail.unlink(missing_ok=True)
        return None


def publish_job_to_youtube(job: dict, force: bool = False, draft: bool = False) -> None:
    slug = job.get("categoryId")
    if not slug:
        return
    with AUTOMATION_LOCK:
        entry = AUTOMATION_CATEGORIES.get(slug)
        if not entry or not entry.get("channelConnected"):
            return
        category_name = entry.get("name", slug)
        privacy = entry.get("privacy", "public")
    video_file = job.get("videoFile")
    if not video_file:
        return
    video_path = VIDEOS_DIR / video_file
    if not video_path.exists():
        return
    try:
        sys.path.insert(0, str(YOUTUBE_BOT_DIR))
        from youtube_bot_service import upload_video_to_youtube
    except Exception:
        app.logger.exception("youtube_bot_service içe aktarılamadı")
        return
    metadata = generate_youtube_metadata(job.get("prompt") or category_name, category_name)
    thumbnail_path = create_youtube_thumbnail(video_path, metadata["title"], str(job.get("id") or uuid.uuid4().hex))
    try:
        result = upload_video_to_youtube(
            video_path=video_path,
            title=metadata["title"],
            description=metadata["description"],
            tags=metadata["tags"],
            privacy_status=privacy,
            headless=True,
            channel_id=slug,
            draft=draft,
            thumbnail_path=thumbnail_path,
        )
    except Exception as exc:
        result = {"ok": False, "error": str(exc)[:400]}
    with AUTOMATION_LOCK:
        entry = AUTOMATION_CATEGORIES.get(slug)
        if not entry:
            return
        if result.get("ok"):
            entry["lastPublishedAt"] = utc_now()
            entry["lastError"] = None
            entry["publishedCount"] = int(entry.get("publishedCount", 0)) + 1
            entry["lastVideoUrl"] = result.get("video_url")
        else:
            entry["lastError"] = str(result.get("error") or "YouTube paylaşımı başarısız")[:400]
        save_automation()
    with JOBS_LOCK:
        current = JOBS.get(job.get("id"))
        if current:
            current["youtubeStatus"] = ("taslak" if draft else "paylaşıldı") if result.get("ok") else "hata"
            current["youtubeError"] = None if result.get("ok") else result.get("error")
            current["youtubeUrl"] = result.get("video_url") if result.get("ok") else None
            current["youtubeMetadata"] = metadata if result.get("ok") else None
            current["youtubeThumbnail"] = thumbnail_path.name if thumbnail_path and thumbnail_path.exists() else None
            save_jobs()


def enqueue_youtube_job(job_id: str, draft: bool) -> None:
    with YOUTUBE_LOCK:
        YOUTUBE_CANCELLED.discard(job_id)
        YOUTUBE_QUEUE.put_nowait((job_id, draft))
        queued_ids = [queued_id for queued_id, _ in list(YOUTUBE_QUEUE.queue)]
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job:
            job["youtubeStatus"] = "sırada"
            job["youtubeQueuePosition"] = queued_ids.index(job_id) + 1 if job_id in queued_ids else 1
            job["youtubeError"] = None
            save_jobs()


def refresh_youtube_queue_positions() -> None:
    with YOUTUBE_LOCK:
        queued_ids = [job_id for job_id, _ in list(YOUTUBE_QUEUE.queue) if job_id not in YOUTUBE_CANCELLED]
    with JOBS_LOCK:
        for job in JOBS.values():
            if job.get("youtubeStatus") == "sırada":
                job_id = job.get("id")
                job["youtubeQueuePosition"] = queued_ids.index(job_id) + 1 if job_id in queued_ids else None
        save_jobs()


def youtube_worker_loop() -> None:
    while True:
        job_id, draft = YOUTUBE_QUEUE.get()
        try:
            with YOUTUBE_LOCK:
                cancelled = job_id in YOUTUBE_CANCELLED
                YOUTUBE_CANCELLED.discard(job_id)
                if not cancelled:
                    YOUTUBE_ACTIVE.add(job_id)
            if cancelled:
                continue
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if not job:
                    continue
                job["youtubeStatus"] = "paylaşılıyor"
                job["youtubeQueuePosition"] = None
                save_jobs()
                payload = dict(job)
            publish_job_to_youtube(payload, True, draft)
        except Exception:
            app.logger.exception("YouTube worker hatası")
            with JOBS_LOCK:
                if job_id in JOBS:
                    JOBS[job_id]["youtubeStatus"] = "hata"
                    JOBS[job_id]["youtubeError"] = "YouTube yüklemesinde beklenmeyen sunucu hatası oluştu."
                    save_jobs()
        finally:
            with YOUTUBE_LOCK:
                YOUTUBE_ACTIVE.discard(job_id)
            YOUTUBE_QUEUE.task_done()
            refresh_youtube_queue_positions()


def worker_loop() -> None:
    while True:
        job_id = JOB_QUEUE.get()
        try:
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if not job or job.get("status") != "bekliyor":
                    continue
                job["status"] = "işleniyor"
                job["startedAt"] = utc_now()
                save_jobs()
                prompt = job["prompt"]
                settings = dict(job.get("settings") or {})
            result = run_bot_generate(job_id, prompt, settings)
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if not job:
                    continue
                succeeded = False
                if result.get("ok") and isinstance(result.get("data"), dict):
                    job["status"] = "hazır"
                    job["videoFile"] = result["data"].get("file")
                    job["videoFileSlow"] = result["data"].get("fileSlow")
                    job["note"] = ""
                    succeeded = True
                else:
                    job["status"] = "hata"
                    job["note"] = str(result.get("error") or "Bilinmeyen hata")[:500]
                job["finishedAt"] = utc_now()
                job_snapshot = dict(job)
                save_jobs()
                invalidate_credits()
                if not CREDITS_CACHE["loading"]:
                    CREDITS_CACHE["loading"] = True
                    threading.Thread(target=refresh_credits, name="credits-after-job", daemon=True).start()
            if succeeded and job_snapshot.get("categoryId"):
                try:
                    enqueue_youtube_job(job_id, True)
                except queue.Full:
                    with JOBS_LOCK:
                        if job_id in JOBS:
                            JOBS[job_id]["youtubeStatus"] = "hata"
                            JOBS[job_id]["youtubeError"] = "YouTube kuyruğu dolu."
                            save_jobs()
        except Exception:
            app.logger.exception("Worker hatası")
            with JOBS_LOCK:
                if job_id in JOBS:
                    JOBS[job_id]["status"] = "hata"
                    JOBS[job_id]["note"] = "İşlenirken beklenmeyen bir sunucu hatası oluştu."
                    JOBS[job_id]["finishedAt"] = utc_now()
                    save_jobs()
        finally:
            JOB_QUEUE.task_done()


def ensure_worker() -> None:
    global WORKER_STARTED, YOUTUBE_WORKER_STARTED
    with WORKER_LOCK:
        if not WORKER_STARTED:
            threading.Thread(target=worker_loop, name="flow-worker", daemon=True).start()
            WORKER_STARTED = True
        if not YOUTUBE_WORKER_STARTED:
            threading.Thread(target=youtube_worker_loop, name="youtube-worker", daemon=True).start()
            YOUTUBE_WORKER_STARTED = True


def image_extension(data: bytes, requested_ext: str) -> str | None:
    ext = requested_ext.lower()
    signatures = IMAGE_SIGNATURES.get(ext)
    if not signatures or not any(data.startswith(signature) for signature in signatures):
        return None
    if ext == ".webp" and (len(data) < 12 or data[8:12] != b"WEBP"):
        return None
    return ext


def cleanup_old_files(days: int = 30) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    referenced = {name for job in JOBS.values() for name in (job.get("videoFile"), job.get("videoFileSlow")) if name}
    for path in VIDEOS_DIR.glob("flow_*.mp4"):
        modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        if path.name not in referenced and modified < cutoff:
            path.unlink(missing_ok=True)


def oauth_ready() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


def current_user() -> str:
    return normalize_email(session.get("user"))


def is_admin() -> bool:
    user = current_user()
    with USERS_LOCK:
        return bool(user and user in ALLOWED_USERS and user == PANEL_ADMIN_EMAIL)


def csrf_token() -> str:
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def external_callback_url() -> str:
    return GOOGLE_REDIRECT_URI or url_for("google_callback", _external=True)


@app.before_request
def protect_panel():
    public_endpoints = {"login", "google_login", "google_callback", "initial_setup", "static"}
    if request.endpoint not in public_endpoints and not current_user():
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "Oturum açmanız gerekli."}), 401
        return redirect(url_for("login"))
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("Origin")
        if origin:
            origin_host = urllib.parse.urlsplit(origin).netloc.lower()
            forwarded_host = request.headers.get("X-Forwarded-Host", "").split(",", 1)[0].strip().lower()
            allowed_hosts = {request.host.lower(), forwarded_host}
            if origin_host not in allowed_hosts:
                return jsonify({"ok": False, "error": "Geçersiz istek kaynağı."}), 403
        supplied = request.headers.get("X-CSRF-Token", "") or request.form.get("csrf_token", "")
        if not hmac.compare_digest(supplied, str(session.get("csrf_token", ""))):
            return jsonify({"ok": False, "error": "Geçersiz güvenlik belirteci."}), 403
    return None


@app.after_request
def secure_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src 'self' data:; media-src 'self'; connect-src 'self'; frame-ancestors 'none'"
    return response


@app.errorhandler(413)
def too_large(_error):
    return jsonify({"ok": False, "error": "İstek veya dosya 5 MB sınırını aşıyor."}), 413


@app.route("/login")
def login():
    if current_user():
        return redirect(url_for("index"))
    setup_allowed = not ALLOWED_USERS and bool(ADMIN_SETUP_TOKEN)
    return render_template("login.html", oauth_ready=oauth_ready(), setup_allowed=setup_allowed, csrf_token=csrf_token(), oauth_error=request.args.get("oauth_error", ""))


@app.route("/setup", methods=["POST"])
def initial_setup():
    if ALLOWED_USERS or not ADMIN_SETUP_TOKEN:
        return jsonify({"ok": False, "error": "İlk kurulum kapalı."}), 403
    if not hmac.compare_digest(request.form.get("setup_token", ""), ADMIN_SETUP_TOKEN):
        return jsonify({"ok": False, "error": "Kurulum anahtarı geçersiz."}), 403
    email = normalize_email(request.form.get("email"))
    if not email:
        return jsonify({"ok": False, "error": "Geçerli e-posta gerekli."}), 400
    with USERS_LOCK:
        ALLOWED_USERS.add(email)
        save_users()
    return redirect(url_for("google_login"))


@app.route("/auth/google")
def google_login():
    if not oauth_ready():
        return redirect(url_for("login"))
    state = secrets.token_urlsafe(32)
    session["oauth_state"] = state
    callback = external_callback_url()
    query = urllib.parse.urlencode({"client_id": GOOGLE_CLIENT_ID, "redirect_uri": callback, "response_type": "code", "scope": "openid email profile", "state": state, "access_type": "online", "prompt": "select_account"})
    return redirect(f"https://accounts.google.com/o/oauth2/v2/auth?{query}")


@app.route("/auth/google/callback")
def google_callback():
    state = request.args.get("state", "")
    expected = session.pop("oauth_state", "")
    if not state or not expected or not hmac.compare_digest(state, expected):
        app.logger.warning("OAuth state doğrulanamadı; host=%s secure=%s session_state=%s", request.host, request.is_secure, bool(expected))
        return redirect(url_for("login", oauth_error="Oturum doğrulanamadı; lütfen tekrar deneyin."))
    code = request.args.get("code", "")
    if not code:
        return redirect(url_for("login"))
    callback = external_callback_url()
    token_payload = urllib.parse.urlencode({"code": code, "client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET, "redirect_uri": callback, "grant_type": "authorization_code"}).encode("utf-8")
    try:
        token_request = urllib.request.Request("https://oauth2.googleapis.com/token", data=token_payload, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
        with urllib.request.urlopen(token_request, timeout=20) as response:
            token_data = json.loads(response.read().decode("utf-8"))
        user_request = urllib.request.Request("https://openidconnect.googleapis.com/v1/userinfo", headers={"Authorization": f"Bearer {token_data['access_token']}"})
        with urllib.request.urlopen(user_request, timeout=20) as response:
            profile = json.loads(response.read().decode("utf-8"))
    except Exception:
        app.logger.exception("Google OAuth başarısız")
        return "Google girişi tamamlanamadı.", 502
    email = normalize_email(profile.get("email"))
    if not email or not profile.get("email_verified"):
        return "Doğrulanmış Google e-postası gerekli.", 403
    with USERS_LOCK:
        allowed = email in ALLOWED_USERS
    if not allowed:
        return "Bu Google hesabının erişim izni yok.", 403
    session.clear()
    session["user"] = email
    session.permanent = True
    csrf_token()
    return redirect(url_for("index"))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def index():
    ensure_worker()
    with JOBS_LOCK:
        jobs = sorted(JOBS.values(), key=lambda job: job.get("createdAt", ""), reverse=True)
    return render_template("index.html", jobs=jobs, current_user=current_user(), is_admin=is_admin(), csrf_token=csrf_token())


@app.route("/api/settings/users", methods=["GET", "POST"])
def api_settings_users():
    if not is_admin():
        return jsonify({"ok": False, "error": "Yönetici yetkisi gerekli."}), 403
    if request.method == "GET":
        with USERS_LOCK:
            return jsonify({"ok": True, "users": sorted(ALLOWED_USERS), "oauthReady": oauth_ready()})
    data = request.get_json(silent=True)
    email = normalize_email(data.get("email") if isinstance(data, dict) else "")
    if not email:
        return jsonify({"ok": False, "error": "Geçerli e-posta gerekli."}), 400
    with USERS_LOCK:
        ALLOWED_USERS.add(email)
        save_users()
    return jsonify({"ok": True, "users": sorted(ALLOWED_USERS)}), 201


@app.route("/api/settings/users/<path:email>", methods=["DELETE"])
def api_settings_user_delete(email):
    if not is_admin():
        return jsonify({"ok": False, "error": "Yönetici yetkisi gerekli."}), 403
    target = normalize_email(email)
    with USERS_LOCK:
        if target not in ALLOWED_USERS:
            return jsonify({"ok": False, "error": "Kullanıcı bulunamadı."}), 404
        if target == current_user():
            return jsonify({"ok": False, "error": "Kendi yönetici hesabınızı silemezsiniz."}), 400
        ALLOWED_USERS.remove(target)
        save_users()
    return jsonify({"ok": True})


@app.route("/api/automation/categories", methods=["GET", "POST"])
def api_automation_categories():
    if request.method == "GET":
        with AUTOMATION_LOCK:
            items = [public_automation_category(slug, entry) for slug, entry in sorted(AUTOMATION_CATEGORIES.items(), key=lambda kv: kv[1].get("name", ""))]
        return jsonify({"ok": True, "categories": items})
    if not is_admin():
        return jsonify({"ok": False, "error": "Yönetici yetkisi gerekli."}), 403
    data = request.get_json(silent=True)
    name = str(data.get("name") or "").strip()[:60] if isinstance(data, dict) else ""
    if not name:
        return jsonify({"ok": False, "error": "Kategori adı gerekli."}), 400
    slug = slugify_category(name)
    with AUTOMATION_LOCK:
        if slug in AUTOMATION_CATEGORIES:
            return jsonify({"ok": False, "error": "Bu kategori zaten var."}), 409
        AUTOMATION_CATEGORIES[slug] = {
            "name": name,
            "enabled": False,
            "channelConnected": False,
            "channelName": "",
            "privacy": "public",
            "createdAt": utc_now(),
            "publishedCount": 0,
        }
        save_automation()
        entry = AUTOMATION_CATEGORIES[slug]
    return jsonify({"ok": True, "category": public_automation_category(slug, entry)}), 201


@app.route("/api/automation/categories/<slug>", methods=["PATCH", "DELETE"])
def api_automation_category_detail(slug):
    if not is_admin():
        return jsonify({"ok": False, "error": "Yönetici yetkisi gerekli."}), 403
    with AUTOMATION_LOCK:
        entry = AUTOMATION_CATEGORIES.get(slug)
        if not entry:
            return jsonify({"ok": False, "error": "Kategori bulunamadı."}), 404
        if request.method == "DELETE":
            del AUTOMATION_CATEGORIES[slug]
            save_automation()
            return jsonify({"ok": True})
        data = request.get_json(silent=True) or {}
        if "enabled" in data:
            entry["enabled"] = bool(data["enabled"]) and entry.get("channelConnected", False)
        if "privacy" in data and data["privacy"] in {"public", "unlisted", "private"}:
            entry["privacy"] = data["privacy"]
        if "name" in data and str(data["name"]).strip():
            entry["name"] = str(data["name"]).strip()[:60]
        save_automation()
        return jsonify({"ok": True, "category": public_automation_category(slug, entry)})


@app.route("/api/automation/categories/<slug>/connect", methods=["POST"])
def api_automation_connect(slug):
    if not is_admin():
        return jsonify({"ok": False, "error": "Yönetici yetkisi gerekli."}), 403
    with AUTOMATION_LOCK:
        if slug not in AUTOMATION_CATEGORIES:
            return jsonify({"ok": False, "error": "Kategori bulunamadı."}), 404
    try:
        sys.path.insert(0, str(YOUTUBE_BOT_DIR))
        from youtube_bot_service import open_youtube_login_browser
        result = open_youtube_login_browser(channel_id=slug)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)[:300]}), 500
    return jsonify(result)


@app.route("/api/automation/categories/<slug>/sync-channel", methods=["POST"])
def api_automation_sync_channel(slug):
    if not is_admin():
        return jsonify({"ok": False, "error": "Yönetici yetkisi gerekli."}), 403
    with AUTOMATION_LOCK:
        if slug not in AUTOMATION_CATEGORIES:
            return jsonify({"ok": False, "error": "Kategori bulunamadı."}), 404
    try:
        sys.path.insert(0, str(YOUTUBE_BOT_DIR))
        from youtube_bot_service import get_youtube_channel_info
        result = get_youtube_channel_info(channel_id=slug)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)[:300]}), 500
    if not result.get("ok"):
        return jsonify(result), 409
    channel_name = str(result.get("channel_name") or "").strip()[:120]
    with AUTOMATION_LOCK:
        entry = AUTOMATION_CATEGORIES[slug]
        entry["channelConnected"] = True
        entry["channelName"] = channel_name
        entry["connectedAt"] = entry.get("connectedAt") or utc_now()
        save_automation()
        public_entry = public_automation_category(slug, entry)
    return jsonify({"ok": True, "category": public_entry})


@app.route("/api/automation/categories/<slug>/confirm-connect", methods=["POST"])
def api_automation_confirm_connect(slug):
    if not is_admin():
        return jsonify({"ok": False, "error": "Yönetici yetkisi gerekli."}), 403
    data = request.get_json(silent=True) or {}
    channel_name = str(data.get("channelName") or "").strip()[:120]
    with AUTOMATION_LOCK:
        entry = AUTOMATION_CATEGORIES.get(slug)
        if not entry:
            return jsonify({"ok": False, "error": "Kategori bulunamadı."}), 404
        entry["channelConnected"] = True
        entry["channelName"] = channel_name or entry.get("channelName") or "Bağlandı"
        entry["connectedAt"] = utc_now()
        save_automation()
        result_entry = public_automation_category(slug, entry)
    return jsonify({"ok": True, "category": result_entry})


@app.route("/api/credits")
def api_credits():
    now = datetime.now(timezone.utc).timestamp()
    with CREDITS_LOCK:
        if now >= CREDITS_CACHE["expiresAt"] and not CREDITS_CACHE["loading"]:
            CREDITS_CACHE["loading"] = True
            threading.Thread(target=refresh_credits, name="credits-refresh", daemon=True).start()
        return jsonify({"ok": CREDITS_CACHE.get("credits") is not None, "credits": CREDITS_CACHE.get("credits"), "checkedAt": CREDITS_CACHE.get("checkedAt"), "loading": CREDITS_CACHE["loading"], "error": CREDITS_CACHE.get("error")})


@app.route("/api/generate", methods=["POST"])
def api_generate():
    ensure_worker()
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "Geçerli JSON gerekli."}), 400
    prompt = str(data.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"ok": False, "error": "Prompt boş olamaz."}), 400
    if len(prompt) > MAX_PROMPT_LENGTH:
        return jsonify({"ok": False, "error": f"Prompt en fazla {MAX_PROMPT_LENGTH} karakter olabilir."}), 400
    settings, error = validate_settings(data.get("settings"))
    if error:
        return jsonify({"ok": False, "error": error}), 400
    category_id = str(data.get("categoryId") or "").strip()
    with AUTOMATION_LOCK:
        if category_id and category_id not in AUTOMATION_CATEGORIES:
            return jsonify({"ok": False, "error": "Geçersiz otomasyon kategorisi."}), 400
        if not category_id:
            connected_categories = [
                slug
                for slug, entry in AUTOMATION_CATEGORIES.items()
                if entry.get("channelConnected")
            ]
            if len(connected_categories) == 1:
                category_id = connected_categories[0]
    if JOB_QUEUE.full():
        return jsonify({"ok": False, "error": "Kuyruk dolu; daha sonra tekrar deneyin."}), 429
    job_id = uuid.uuid4().hex
    job = {"id": job_id, "prompt": prompt, "status": "bekliyor", "createdAt": utc_now(), "videoFile": None, "videoFileSlow": None, "note": "", "settings": settings, "categoryId": category_id or None}
    with JOBS_LOCK:
        JOBS[job_id] = job
        save_jobs()
    try:
        JOB_QUEUE.put_nowait(job_id)
    except queue.Full:
        with JOBS_LOCK:
            JOBS.pop(job_id, None)
            save_jobs()
        return jsonify({"ok": False, "error": "Kuyruk dolu; daha sonra tekrar deneyin."}), 429
    return jsonify({"ok": True, "job": job}), 202


@app.route("/api/jobs")
def api_jobs():
    with JOBS_LOCK:
        jobs = sorted(JOBS.values(), key=lambda job: job.get("createdAt", ""), reverse=True)
    return jsonify({"ok": True, "jobs": jobs})


@app.route("/api/jobs/<job_id>/category", methods=["PATCH"])
def api_job_category(job_id):
    data = request.get_json(silent=True) or {}
    category_id = str(data.get("categoryId") or "").strip()
    if category_id:
        with AUTOMATION_LOCK:
            if category_id not in AUTOMATION_CATEGORIES:
                return jsonify({"ok": False, "error": "Geçersiz kategori."}), 400
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"ok": False, "error": "Video bulunamadı."}), 404
        job["categoryId"] = category_id or None
        save_jobs()
    return jsonify({"ok": True})


@app.route("/api/jobs/<job_id>/youtube", methods=["POST"])
def api_job_youtube(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job or job.get("status") != "hazır" or not job.get("videoFile"):
            return jsonify({"ok": False, "error": "Hazır video bulunamadı."}), 404
        category_id = job.get("categoryId")
        if job.get("youtubeStatus") in {"sırada", "paylaşılıyor", "paylaşıldı", "taslak"}:
            return jsonify({"ok": False, "error": "Video zaten sırada, yükleniyor veya taslaklara eklenmiş."}), 409
    with AUTOMATION_LOCK:
        category = AUTOMATION_CATEGORIES.get(category_id)
        if not category or not category.get("channelConnected"):
            return jsonify({"ok": False, "error": "Önce bağlı YouTube kategorisi seçin."}), 400
    try:
        enqueue_youtube_job(job_id, True)
    except queue.Full:
        return jsonify({"ok": False, "error": "YouTube kuyruğu dolu."}), 503
    with JOBS_LOCK:
        position = JOBS[job_id].get("youtubeQueuePosition")
    return jsonify({"ok": True, "position": position}), 202


@app.route("/api/jobs/<job_id>/youtube/cancel", methods=["POST"])
def api_job_youtube_cancel(job_id):
    with YOUTUBE_LOCK:
        if job_id in YOUTUBE_ACTIVE:
            return jsonify({"ok": False, "error": "Aktif YouTube yüklemesi güvenli biçimde iptal edilemiyor."}), 409
        YOUTUBE_CANCELLED.add(job_id)
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job or job.get("youtubeStatus") != "sırada":
            with YOUTUBE_LOCK:
                YOUTUBE_CANCELLED.discard(job_id)
            return jsonify({"ok": False, "error": "İptal edilebilir sırada işlem bulunamadı."}), 404
        job["youtubeStatus"] = "iptal"
        job["youtubeQueuePosition"] = None
        job["youtubeError"] = None
        save_jobs()
    refresh_youtube_queue_positions()
    return jsonify({"ok": True})


@app.route("/api/pool", methods=["GET"])
def api_pool_list():
    with POOL_LOCK:
        items = sorted(POOL, key=lambda item: item.get("createdAt", ""), reverse=True)
    return jsonify({"ok": True, "items": items})


@app.route("/api/pool", methods=["POST"])
def api_pool_add():
    added = []
    staged_images = []
    try:
        if request.mimetype in {"multipart/form-data", "application/x-www-form-urlencoded"} or request.form:
            indices = sorted({int(key.split("_", 1)[1]) for key in request.form if key.startswith("text_") and key.split("_", 1)[1].isdigit()})
            for idx in indices:
                text = (request.form.get(f"text_{idx}") or "").strip()
                if not text:
                    continue
                if len(text) > MAX_PROMPT_LENGTH:
                    return jsonify({"ok": False, "error": "Havuz promptu çok uzun."}), 400
                category = (request.form.get(f"category_{idx}") or "Genel").strip()[:60] or "Genel"
                description = (request.form.get(f"description_{idx}") or "").strip()[:180]
                upload = request.files.get(f"image_{idx}")
                image_name = None
                if upload and upload.filename:
                    content = upload.read(MAX_UPLOAD_BYTES + 1)
                    if len(content) > MAX_UPLOAD_BYTES:
                        return jsonify({"ok": False, "error": "Görsel 5 MB sınırını aşıyor."}), 413
                    ext = image_extension(content, Path(upload.filename).suffix)
                    if not ext:
                        return jsonify({"ok": False, "error": "Geçersiz veya desteklenmeyen görsel."}), 400
                    image_name = f"{uuid.uuid4().hex}{ext}"
                    image_path = POOL_IMAGES_DIR / image_name
                    image_path.write_bytes(content)
                    staged_images.append(image_path)
                added.append({"id": uuid.uuid4().hex, "text": text, "category": category, "description": description, "image": image_name, "createdAt": utc_now()})
        else:
            data = request.get_json(silent=True)
            prompts = data.get("prompts") if isinstance(data, dict) else None
            if not isinstance(prompts, list):
                return jsonify({"ok": False, "error": "Prompts listesi gerekli."}), 400
            for value in prompts[:100]:
                text = str(value or "").strip()
                if text and len(text) <= MAX_PROMPT_LENGTH:
                    added.append({"id": uuid.uuid4().hex, "text": text, "category": "Genel", "description": "", "image": None, "createdAt": utc_now()})
        if added:
            with POOL_LOCK:
                POOL.extend(added)
                save_pool()
            sync_automation_categories_from_pool()
        return jsonify({"ok": True, "added": added}), 201
    except Exception:
        for path in staged_images:
            path.unlink(missing_ok=True)
        raise


@app.route("/api/pool/<item_id>", methods=["DELETE"])
def api_pool_delete(item_id):
    with POOL_LOCK:
        idx = next((index for index, item in enumerate(POOL) if item.get("id") == item_id), None)
        if idx is None:
            return jsonify({"ok": False, "error": "Bulunamadı."}), 404
        removed = POOL.pop(idx)
        save_pool()
    image = removed.get("image")
    if image:
        (POOL_IMAGES_DIR / Path(image).name).unlink(missing_ok=True)
    return jsonify({"ok": True})


@app.route("/api/ai/chat", methods=["POST"])
def api_ai_chat():
    if not AI_API_KEY:
        return jsonify({"ok": False, "error": "AI servisi yapılandırılmamış."}), 503
    data = request.get_json(silent=True)
    messages = data.get("messages") if isinstance(data, dict) else None
    if not isinstance(messages, list) or not 1 <= len(messages) <= MAX_AI_MESSAGES:
        return jsonify({"ok": False, "error": "Mesaj listesi geçersiz."}), 400
    clean_messages = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {"system", "user", "assistant"}:
            return jsonify({"ok": False, "error": "Mesaj rolü geçersiz."}), 400
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            return jsonify({"ok": False, "error": "Mesaj içeriği boş olamaz."}), 400
        if len(content) > MAX_AI_MESSAGE_LENGTH:
            return jsonify({"ok": False, "error": f"Tek mesaj {MAX_AI_MESSAGE_LENGTH} karakter sınırını aşıyor."}), 400
        clean_messages.append({"role": message["role"], "content": content.strip()})
    if sum(len(message["content"]) for message in clean_messages) > MAX_AI_TOTAL_LENGTH:
        return jsonify({"ok": False, "error": "Konuşma geçmişi çok uzun; yeni bir otomatik pilot işlemi başlatın."}), 400
    if sum(message["role"] == "system" for message in clean_messages) > 1:
        return jsonify({"ok": False, "error": "Yalnız bir sistem mesajına izin verilir."}), 400
    payload = json.dumps({"model": AI_MODEL, "stream": False, "messages": clean_messages}).encode("utf-8")
    upstream = urllib.request.Request(f"{AI_API_BASE}/chat/completions", data=payload, headers={"Content-Type": "application/json", "Authorization": f"Bearer {AI_API_KEY}", "User-Agent": "curl/8.0"}, method="POST")
    try:
        with urllib.request.urlopen(upstream, timeout=90) as response:
            body = json.loads(response.read().decode("utf-8"))
        reply = body["choices"][0]["message"]["content"]
        if not isinstance(reply, str):
            raise ValueError("Geçersiz AI yanıtı")
        return jsonify({"ok": True, "reply": reply})
    except urllib.error.HTTPError as exc:
        app.logger.warning("AI API HTTP hatası: %s", exc.code)
        return jsonify({"ok": False, "error": "AI servisi isteği reddetti."}), 502
    except Exception:
        app.logger.exception("AI isteği başarısız")
        return jsonify({"ok": False, "error": "AI servisine ulaşılamadı."}), 502


@app.route("/videos/<path:filename>")
def serve_video(filename):
    response = send_from_directory(VIDEOS_DIR, Path(filename).name, conditional=True)
    response.headers["Cache-Control"] = "private, max-age=3600"
    return response


load_state()
cleanup_old_files()

if __name__ == "__main__":
    ensure_worker()
    if PANEL_HOST not in {"127.0.0.1", "localhost", "::1"} and not PANEL_API_TOKEN:
        raise RuntimeError("Ağ erişimi için GEMINIFLOW_PANEL_TOKEN zorunludur.")
    app.run(host=PANEL_HOST, port=PANEL_PORT, debug=False, threaded=True)
