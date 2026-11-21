from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from ai_infinity.native_image_runtime import install


def test_existing_creator_queue_dispatches_standalone_image_without_video_side_effects(tmp_path, monkeypatch):
    monkeypatch.delenv("AI_INFINITY_HF_IMAGE_ZERO_COST_CONFIRMED", raising=False)
    monkeypatch.delenv("AI_INFINITY_HF_FREE_TIER_AND_DATA_USE_ACK", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGINGFACEHUB_API_TOKEN", raising=False)

    updates = {}
    saved_assets = []
    registered = []
    original_runs = []
    events = []
    registry = {"studio-image-test": True}

    def fallback(prompt, outdir, index):
        source = Path(outdir) / "local-editorial-illustration.jpg"
        Image.new("RGB", (1280, 720), color=(35, 60, 90)).save(source, format="JPEG")
        return {
            "kind": "image",
            "path": str(source),
            "source": "AI Infinity original vector editorial illustration",
            "creator": "AI Infinity",
            "license": "Original local procedural illustration; review required",
            "validated_media": True,
        }

    project = {
        "project_id": "studio-image-test",
        "user_id": "test-user",
        "title": "Solar research station",
        "request_json": {
            "content_type": "image",
            "objective": "Create a cinematic image of a solar research station in the desert",
            "language": "English",
        },
    }
    studio = SimpleNamespace(
        SUPPORTED_CREATOR_CONTENT_TYPES=frozenset({"video", "article"}),
        normalize_creator_content_type=lambda value: value if value in {"video", "article"} else "video",
        run_project=lambda project_id, model_fn: original_runs.append(project_id),
        _get_project=lambda project_id: project if project_id == project["project_id"] else None,
        _project_dir=lambda project_id: tmp_path,
        _procedural_image=fallback,
        _stage=lambda *args, **kwargs: None,
        audit_event=lambda *args, **kwargs: events.append((args, kwargs)),
        checkpoint=lambda *args, **kwargs: None,
        _save_asset=lambda *args, **kwargs: saved_assets.append(args),
        register_artifact=lambda *args, **kwargs: registered.append(args),
        _update_project=lambda project_id, **kwargs: updates.update(kwargs),
        PROCESS_LOCK=threading.RLock(),
        PROCESS_REGISTRY=registry,
        ACTIVE_PROJECT=SimpleNamespace(project_id="studio-image-test"),
    )

    install(studio)
    assert studio.normalize_creator_content_type("image") == "image"
    result = studio.run_project("studio-image-test", None)

    assert not original_runs, "image jobs must use the image path, not invoke the video renderer"
    assert result["status"] == "completed_with_qc_warnings"
    assert result["content_type"] == "image"
    assert result["primary_content_delivery"]["status"] == "PASS"
    assert result["quality"]["state"] == "REVIEW_REQUIRED"
    assert result["rights_status"] == "REVIEW_REQUIRED"
    assert result["human_review_status"] == "PENDING"
    assert result["publish_status"] == "NOT_PUBLISHED"
    assert updates["status"] == "completed_with_qc_warnings"
    assert updates["progress"] == 100
    assert registry == {}
    assert studio.ACTIVE_PROJECT.project_id is None

    image = tmp_path / "image.png"
    image_manifest = json.loads((tmp_path / "image_manifest.json").read_text(encoding="utf-8"))
    image_package_manifest = json.loads((tmp_path / "image_package_manifest.json").read_text(encoding="utf-8"))
    import zipfile
    with zipfile.ZipFile(tmp_path / "image_package.zip") as bundle:
        assert bundle.testzip() is None
        assert "ai-infinity-image/image.png" in bundle.namelist()
        packaged_rights = json.loads(bundle.read("ai-infinity-image/rights_manifest.json"))
        assert packaged_rights["rights_status"] == "REVIEW_REQUIRED"
    assert image_package_manifest["entrypoint"] == "image.png"
    assert image_package_manifest["redistribution_authorized"] is False
    assert any(row["path"] == "image.png" and row["sha256"] == result["image"]["sha256"] for row in image_package_manifest["files"])
    rights = json.loads((tmp_path / "rights_manifest.json").read_text(encoding="utf-8"))
    production = json.loads((tmp_path / "production_manifest.json").read_text(encoding="utf-8"))
    primary = json.loads((tmp_path / "primary_content_delivery.json").read_text(encoding="utf-8"))

    assert image.is_file() and image.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert image_manifest["generation"]["mode"] == "local_editorial_illustration_fallback"
    assert image_manifest["generation"]["fallback_is_photograph"] is False
    assert rights["status"] == "REVIEW_REQUIRED"
    assert rights["redistribution_authorized"] is False
    assert rights["assets"][0]["rights_status"] == "REVIEW_REQUIRED"
    assert production["content_type"] == "image"
    assert production["primary_deliverable"]["sha256"] == result["image"]["sha256"]
    assert primary["status"] == "PASS"
    assert primary["human_review_status"] == "PENDING"
    assert any(Path(row[2]).name == "image.png" for row in saved_assets)
    assert any(Path(row[2]).name == "image_package.zip" for row in saved_assets)
    assert any(Path(row[1]).name == "image.png" for row in registered)

def test_image_provider_respects_global_external_provider_kill_switch(monkeypatch):
    from ai_infinity.native_image_runtime import _zero_cost_image_provider_allowed

    monkeypatch.setenv("AI_INFINITY_HF_IMAGE_ZERO_COST_CONFIRMED", "true")
    monkeypatch.setenv("AI_INFINITY_HF_FREE_TIER_AND_DATA_USE_ACK", "true")
    monkeypatch.setenv("HF_TOKEN", "test-token")
    disabled = SimpleNamespace(_external_providers_disabled=lambda: True)
    assert _zero_cost_image_provider_allowed(disabled) is False

    broken_policy = SimpleNamespace(_external_providers_disabled=lambda: (_ for _ in ()).throw(RuntimeError("policy unavailable")))
    assert _zero_cost_image_provider_allowed(broken_policy) is False

