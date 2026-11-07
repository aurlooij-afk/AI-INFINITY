from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from ai_infinity.native_image import build_native_image


def _write_image(path: Path, color=(20, 40, 80)) -> Path:
    Image.new("RGB", (1280, 720), color=color).save(path, format="JPEG")
    return path


def test_native_image_output_verifies_png_and_binds_prompt_source_rights_and_hash(tmp_path):
    source = _write_image(tmp_path / "provider.jpg")

    def provider(prompt, outdir, index):
        return {
            "kind": "image",
            "path": str(source),
            "source": "Test image provider",
            "source_url": "https://example.org/model",
            "creator": "test creator",
            "license": "provider terms require review",
            "model": "test-model",
        }

    def fallback(prompt, outdir, index):
        raise AssertionError("valid provider output must not fall back")

    result = build_native_image(
        tmp_path,
        project_id="studio_image_test",
        title="A standalone generated image",
        prompt="A cinematic editorial image of a creator workspace",
        language="English",
        image_generator=provider,
        fallback_generator=fallback,
    )
    output = result["file"]
    manifest = json.loads(result["manifest"].read_text(encoding="utf-8"))
    assert output.name == "image.png" and output.is_file()
    assert output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert hashlib.sha256(output.read_bytes()).hexdigest() == manifest["output"]["sha256"]
    assert manifest["project_id"] == "studio_image_test"
    assert manifest["generation"]["mode"] == "ai_provider"
    assert manifest["generation"]["source_url"] == "https://example.org/model"
    assert manifest["review"]["rights_status"] == "REVIEW_REQUIRED"
    assert manifest["review"]["human_review_required"] is True
    assert manifest["review"]["redistribution_authorized"] is False
    assert manifest["review"]["publication_status"] == "NOT_PUBLISHED"
    assert result["verification"]["status"] == "PASS"
    assert result["verification"]["width"] == 1280
    assert result["verification"]["height"] == 720


def test_native_image_uses_truthfully_labelled_local_illustration_when_provider_is_unavailable(tmp_path):
    source = _write_image(tmp_path / "illustration.jpg", color=(120, 70, 20))

    def provider(prompt, outdir, index):
        return None

    def fallback(prompt, outdir, index):
        return {
            "kind": "image",
            "path": str(source),
            "source": "AI Infinity original vector editorial illustration",
            "creator": "AI Infinity",
            "license": "Original local procedural illustration",
            "validated_media": True,
        }

    result = build_native_image(
        tmp_path,
        project_id="studio_image_fallback",
        title="Illustrative fallback",
        prompt="An editorial illustration of an idea becoming a production",
        language="English",
        image_generator=provider,
        fallback_generator=fallback,
    )
    manifest = json.loads(result["manifest"].read_text(encoding="utf-8"))
    assert result["verification"]["status"] == "PASS"
    assert result["verification"]["fallback_used"] is True
    assert manifest["generation"]["mode"] == "local_editorial_illustration_fallback"
    assert manifest["generation"]["fallback_is_photograph"] is False
    assert manifest["review"]["rights_status"] == "REVIEW_REQUIRED"
    assert manifest["review"]["redistribution_authorized"] is False


def test_native_image_rejects_invalid_provider_and_fallback_media(tmp_path):
    invalid = tmp_path / "not-an-image.bin"
    invalid.write_bytes(b"not an image")

    def provider(prompt, outdir, index):
        return {"path": str(invalid), "source": "provider"}

    def fallback(prompt, outdir, index):
        return {"path": str(invalid), "source": "fallback"}

    with pytest.raises(RuntimeError, match="no valid image"):
        build_native_image(
            tmp_path,
            project_id="studio_image_invalid",
            title="Invalid image",
            prompt="A broken image",
            language="English",
            image_generator=provider,
            fallback_generator=fallback,
        )
