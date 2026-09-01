import importlib.util
import json
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "webpanel" / "app.py"


@pytest.fixture
def panel(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINIFLOW_AI_API_KEY", "")
    monkeypatch.setenv("GEMINIFLOW_SESSION_SECRET", "test-session-secret")
    spec = importlib.util.spec_from_file_location("geminiflow_test_app", APP_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.AI_API_KEY = ""
    module.JOBS_FILE = tmp_path / "jobs.json"
    module.POOL_FILE = tmp_path / "pool.json"
    module.USERS_FILE = tmp_path / "google_users.json"
    module.AUTOMATION_FILE = tmp_path / "automation.json"
    module.AUTOMATION_CATEGORIES.clear()
    module.VIDEOS_DIR = tmp_path / "videos"
    module.POOL_IMAGES_DIR = tmp_path / "images"
    module.VIDEOS_DIR.mkdir()
    module.POOL_IMAGES_DIR.mkdir()
    module.JOBS.clear()
    module.POOL.clear()
    module.ALLOWED_USERS.clear()
    module.ALLOWED_USERS.add("admin@example.com")
    module.PANEL_ADMIN_EMAIL = "admin@example.com"
    module.app.config.update(TESTING=True)
    return module


@pytest.fixture
def auth_client(panel):
    client = panel.app.test_client()
    with client.session_transaction() as session:
        session["user"] = "admin@example.com"
        session["csrf_token"] = "test-csrf"
    return client


def test_validate_settings_rejects_unsupported_options(panel):
    settings, error = panel.validate_settings({"type": "image", "count": "x4"})
    assert settings == {}
    assert "Desteklenmeyen" in error


def test_atomic_write_json_replaces_complete_document(panel, tmp_path):
    target = tmp_path / "state.json"
    panel.atomic_write_json(target, [{"id": "1"}])
    panel.atomic_write_json(target, [{"id": "2"}])
    assert json.loads(target.read_text(encoding="utf-8")) == [{"id": "2"}]
    assert not list(tmp_path.glob("*.tmp"))


def test_generate_validates_and_queues(panel, auth_client, monkeypatch):
    monkeypatch.setattr(panel, "ensure_worker", lambda: None)
    response = auth_client.post("/api/generate", json={"prompt": "test", "settings": {"type": "video", "count": "x1"}}, headers={"X-CSRF-Token": "test-csrf"})
    assert response.status_code == 202
    body = response.get_json()
    assert body["ok"] is True
    assert body["job"]["status"] == "bekliyor"
    assert panel.JOBS_FILE.exists()


def test_generate_rejects_invalid_media_type(panel, auth_client, monkeypatch):
    monkeypatch.setattr(panel, "ensure_worker", lambda: None)
    response = auth_client.post("/api/generate", json={"prompt": "test", "settings": {"type": "image"}}, headers={"X-CSRF-Token": "test-csrf"})
    assert response.status_code == 400


def test_ai_endpoint_requires_server_configuration(panel, auth_client):
    response = auth_client.post("/api/ai/chat", json={"messages": [{"role": "user", "content": "merhaba"}]}, headers={"X-CSRF-Token": "test-csrf"})
    assert response.status_code == 503


def test_protected_api_requires_login(panel):
    response = panel.app.test_client().get("/api/jobs")
    assert response.status_code == 401


def test_job_category_and_manual_youtube_trigger(panel, auth_client, monkeypatch):
    panel.JOBS["job1"] = {"id": "job1", "prompt": "test prompt", "status": "hazır", "videoFile": "test.mp4", "categoryId": None}
    panel.AUTOMATION_CATEGORIES["orgu-dunyasi"] = {"name": "Örgü Dünyası", "enabled": False, "channelConnected": True}

    res = auth_client.patch("/api/jobs/job1/category", json={"categoryId": "orgu-dunyasi"}, headers={"X-CSRF-Token": "test-csrf"})
    assert res.status_code == 200
    assert panel.JOBS["job1"]["categoryId"] == "orgu-dunyasi"

    queued = []
    monkeypatch.setattr(panel, "enqueue_youtube_job", lambda job_id, draft: (queued.append((job_id, draft)), panel.JOBS[job_id].update({"youtubeStatus": "sırada", "youtubeQueuePosition": 1})))

    res_yt = auth_client.post("/api/jobs/job1/youtube", json={}, headers={"X-CSRF-Token": "test-csrf"})
    assert res_yt.status_code == 202
    assert queued == [("job1", True)]
    assert panel.JOBS["job1"]["youtubeStatus"] == "sırada"


def test_pool_add_returns_success(panel, auth_client):
    response = auth_client.post("/api/pool", data={"text_0": "Test prompt", "category_0": "Test Kat"}, headers={"X-CSRF-Token": "test-csrf"})
    assert response.status_code == 201
    assert response.get_json()["ok"] is True


def test_forwarded_host_origin_is_allowed(panel, auth_client):
    response = auth_client.post(
        "/api/settings/users",
        json={"email": "forwarded@example.com"},
        headers={
            "X-CSRF-Token": "test-csrf",
            "Origin": "https://geminiflow.homaklab.com",
            "X-Forwarded-Host": "geminiflow.homaklab.com",
        },
    )
    assert response.status_code == 201


def test_admin_can_manage_allowed_users(panel, auth_client):
    response = auth_client.post("/api/settings/users", json={"email": "user@example.com"}, headers={"X-CSRF-Token": "test-csrf"})
    assert response.status_code == 201
    assert "user@example.com" in panel.ALLOWED_USERS
    delete = auth_client.delete("/api/settings/users/user@example.com", headers={"X-CSRF-Token": "test-csrf"})
    assert delete.status_code == 200


def test_image_signature_validation(panel):
    assert panel.image_extension(b"\x89PNG\r\n\x1a\nrest", ".png") == ".png"
    assert panel.image_extension(b"not-an-image", ".png") is None
    assert panel.image_extension(b"RIFFxxxxWEBPrest", ".webp") == ".webp"


def test_admin_can_create_and_manage_automation_category(panel, auth_client):
    response = auth_client.post("/api/automation/categories", json={"name": "Doğa"}, headers={"X-CSRF-Token": "test-csrf"})
    assert response.status_code == 201
    slug = response.get_json()["category"]["id"]
    assert slug in panel.AUTOMATION_CATEGORIES

    enable_before_connect = auth_client.patch(f"/api/automation/categories/{slug}", json={"enabled": True}, headers={"X-CSRF-Token": "test-csrf"})
    assert enable_before_connect.status_code == 200
    assert panel.AUTOMATION_CATEGORIES[slug]["enabled"] is False

    panel.AUTOMATION_CATEGORIES[slug]["channelConnected"] = True
    enable_after_connect = auth_client.patch(f"/api/automation/categories/{slug}", json={"enabled": True, "privacy": "unlisted"}, headers={"X-CSRF-Token": "test-csrf"})
    assert enable_after_connect.status_code == 200
    assert panel.AUTOMATION_CATEGORIES[slug]["enabled"] is True
    assert panel.AUTOMATION_CATEGORIES[slug]["privacy"] == "unlisted"

    delete = auth_client.delete(f"/api/automation/categories/{slug}", headers={"X-CSRF-Token": "test-csrf"})
    assert delete.status_code == 200
    assert slug not in panel.AUTOMATION_CATEGORIES


def test_confirm_connect_marks_category_connected(panel, auth_client):
    panel.AUTOMATION_CATEGORIES["doga"] = {
        "name": "Doğa",
        "enabled": False,
        "channelConnected": False,
        "channelName": "",
    }

    response = auth_client.post(
        "/api/automation/categories/doga/confirm-connect",
        json={},
        headers={"X-CSRF-Token": "test-csrf"},
    )

    assert response.status_code == 200
    assert response.get_json()["category"]["connected"] is True
    assert panel.AUTOMATION_CATEGORIES["doga"]["channelConnected"] is True


def test_sync_channel_marks_category_connected(panel, auth_client, monkeypatch):
    panel.AUTOMATION_CATEGORIES["doga"] = {
        "name": "Doğa",
        "enabled": False,
        "channelConnected": False,
        "channelName": "",
    }
    service = type("YouTubeService", (), {"get_youtube_channel_info": staticmethod(lambda channel_id: {"ok": True, "channel_name": "Doğa Kanalı"})})
    monkeypatch.setitem(sys.modules, "youtube_bot_service", service)

    response = auth_client.post(
        "/api/automation/categories/doga/sync-channel",
        headers={"X-CSRF-Token": "test-csrf"},
    )

    assert response.status_code == 200
    assert response.get_json()["category"]["channelName"] == "Doğa Kanalı"
    assert panel.AUTOMATION_CATEGORIES["doga"]["channelConnected"] is True


def test_youtube_uploads_for_same_channel_are_serialized(monkeypatch, tmp_path):
    import webpanel.youtube_bot_service as service

    first_started = threading.Event()
    release_first = threading.Event()
    calls = []

    def fake_upload(**kwargs):
        calls.append(kwargs["title"])
        if kwargs["title"] == "ilk":
            first_started.set()
            release_first.wait(timeout=2)
        return {"ok": True}

    monkeypatch.setattr(service, "_upload_video_to_youtube", fake_upload)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    results = []
    first = threading.Thread(target=lambda: results.append(service.upload_video_to_youtube(video, "ilk", channel_id="kanal")))
    second = threading.Thread(target=lambda: results.append(service.upload_video_to_youtube(video, "ikinci", channel_id="kanal")))

    first.start()
    assert first_started.wait(timeout=1)
    second.start()
    second.join(timeout=0.1)
    assert second.is_alive()
    release_first.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert calls == ["ilk", "ikinci"]
    assert all(result["ok"] for result in results)


def test_youtube_share_is_queued(panel, auth_client, monkeypatch):
    panel.AUTOMATION_CATEGORIES["doga"] = {"name": "Doğa", "channelConnected": True}
    panel.JOBS["job-1"] = {"id": "job-1", "status": "hazır", "videoFile": "video.mp4", "categoryId": "doga"}
    queued = []
    monkeypatch.setattr(panel, "enqueue_youtube_job", lambda job_id, draft: queued.append((job_id, draft)))

    response = auth_client.post("/api/jobs/job-1/youtube", headers={"X-CSRF-Token": "test-csrf"})

    assert response.status_code == 202
    assert queued == [("job-1", True)]


def test_queued_youtube_share_can_be_cancelled(panel, auth_client):
    panel.JOBS["job-1"] = {"id": "job-1", "youtubeStatus": "sırada", "youtubeQueuePosition": 1}

    response = auth_client.post("/api/jobs/job-1/youtube/cancel", headers={"X-CSRF-Token": "test-csrf"})

    assert response.status_code == 200
    assert panel.JOBS["job-1"]["youtubeStatus"] == "iptal"
    assert "job-1" in panel.YOUTUBE_CANCELLED


def test_generate_rejects_unknown_automation_category(panel, auth_client, monkeypatch):
    monkeypatch.setattr(panel, "ensure_worker", lambda: None)
    response = auth_client.post("/api/generate", json={"prompt": "test", "categoryId": "olmayan-kategori"}, headers={"X-CSRF-Token": "test-csrf"})
    assert response.status_code == 400


def test_publish_job_to_youtube_skips_when_disabled(panel):
    panel.AUTOMATION_CATEGORIES["doga"] = {"name": "Doğa", "enabled": False, "channelConnected": True, "privacy": "public"}
    panel.publish_job_to_youtube({"id": "job1", "categoryId": "doga", "videoFile": "missing.mp4", "prompt": "test"})
    assert panel.AUTOMATION_CATEGORIES["doga"].get("lastError") is None


def test_pool_categories_sync_into_automation_categories(panel):
    panel.POOL.append({"id": "p1", "text": "test", "category": "Rubik Küp", "description": "", "image": None, "createdAt": "2026-01-01T00:00:00+00:00"})
    panel.sync_automation_categories_from_pool()
    slugs = {entry["name"] for entry in panel.AUTOMATION_CATEGORIES.values()}
    assert "Rubik Küp" in slugs


def test_pool_add_creates_automation_category(panel, auth_client, monkeypatch):
    monkeypatch.setattr(panel, "ensure_worker", lambda: None)
    response = auth_client.post(
        "/api/pool",
        data={"text_0": "prompt fikri", "category_0": "Örgü Dünyası", "description_0": ""},
        headers={"X-CSRF-Token": "test-csrf"},
        content_type="multipart/form-data",
    )
    assert response.status_code == 201
    names = {entry["name"] for entry in panel.AUTOMATION_CATEGORIES.values()}
    assert "Örgü Dünyası" in names
