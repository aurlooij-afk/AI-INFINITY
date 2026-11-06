from __future__ import annotations

"""Verified standalone image deliverables for AI Infinity Creator Studio."""

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageOps


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_image(path: Path) -> tuple[bool, dict[str, Any]]:
    path = Path(path)
    if not path.is_file() or path.stat().st_size <= 0:
        return False, {}
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
            fmt = str(image.format or "").upper()
            mode = str(image.mode or "")
        valid = width >= 640 and height >= 360 and fmt in {
            "PNG", "JPEG", "WEBP"
        }
        return valid, {
            "width": int(width),
            "height": int(height),
            "format": fmt,
            "mode": mode,
        }
    except Exception:
        return False, {}


def build_native_image(
    outdir: Path,
    *,
    project_id: str,
    title: str,
    prompt: str,
    language: str,
    image_generator: Callable[[str, Path, int], dict[str, Any] | None],
    fallback_generator: Callable[[str, Path, int], dict[str, Any] | None],
) -> dict[str, Any]:
    """Create an image using the real configured image provider or a labelled local illustration.

    A fallback is a genuine locally rendered editorial illustration, not an AI
    photograph. All outputs remain REVIEW_REQUIRED for creative/rights review.
    """
    root = Path(outdir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    image_record: dict[str, Any] | None = None
    generation_mode = "ai_provider"
    provider_error = None

    try:
        candidate = image_generator(str(prompt), root, 1)
        if isinstance(candidate, dict):
            source_path = Path(str(candidate.get("path") or ""))
            valid, dimensions = _valid_image(source_path)
            if valid:
                image_record = {**candidate, "path": str(source_path), "dimensions": dimensions}
        if image_record is None:
            provider_error = "no_valid_image_returned"
    except Exception as exc:
        provider_error = type(exc).__name__

    if image_record is None:
        generation_mode = "local_editorial_illustration_fallback"
        try:
            candidate = fallback_generator(str(prompt), root, 1)
            if isinstance(candidate, dict):
                source_path = Path(str(candidate.get("path") or ""))
                valid, dimensions = _valid_image(source_path)
                if valid:
                    image_record = {**candidate, "path": str(source_path), "dimensions": dimensions}
        except Exception as exc:
            raise RuntimeError(
                "standalone image generation and local illustration fallback both failed: "
                + type(exc).__name__
            ) from None
    if image_record is None:
        raise RuntimeError("standalone image generators returned no valid image file")

    source_path = Path(str(image_record["path"]))
    output_path = root / "image.png"
    with Image.open(source_path) as image:
        normalized = ImageOps.exif_transpose(image).convert("RGBA")
        normalized.save(output_path, format="PNG", optimize=True)

    valid, dimensions = _valid_image(output_path)
    if not valid:
        raise RuntimeError("standalone image failed PNG verification after normalization")
    sha256 = _sha256(output_path)
    size_bytes = output_path.stat().st_size

    provider_source = str(image_record.get("source") or generation_mode)[:160]
    source_url = str(image_record.get("source_url") or "")[:1000]
    creator = str(image_record.get("creator") or "AI Infinity")[:160]
    model = str(image_record.get("model") or "")[:240]
    declared_license = str(image_record.get("license") or "Not independently verified")[:240]
    fallback = generation_mode != "ai_provider"

    manifest = {
        "schema": "ai-infinity.native-image-manifest.v1",
        "project_id": str(project_id),
        "title": str(title or "AI Infinity image")[:180],
        "prompt": str(prompt or "")[:4000],
        "language": str(language or "English")[:80],
        "output": {
            "file": output_path.name,
            "media_type": "image/png",
            "format": "PNG",
            "width": dimensions["width"],
            "height": dimensions["height"],
            "size_bytes": size_bytes,
            "sha256": sha256,
        },
        "generation": {
            "mode": generation_mode,
            "provider_or_method": provider_source,
            "source_url": source_url or None,
            "creator": creator,
            "model": model or None,
            "declared_license": declared_license,
            "provider_failure_class": provider_error,
            "fallback_used": fallback,
            "fallback_is_photograph": False if fallback else None,
        },
        "review": {
            "creative_status": "PENDING",
            "rights_status": "REVIEW_REQUIRED",
            "human_review_required": True,
            "redistribution_authorized": False,
            "publication_status": "NOT_PUBLISHED",
        },
        "truthful": True,
    }
    manifest_path = root / "image_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest_sha = _sha256(manifest_path)
    verification = {
        "status": "PASS",
        "file": output_path,
        "manifest": manifest_path,
        "sha256": sha256,
        "size_bytes": size_bytes,
        "width": dimensions["width"],
        "height": dimensions["height"],
        "format": "PNG",
        "generation_mode": generation_mode,
        "fallback_used": fallback,
        "creative_review": "PENDING",
        "rights_status": "REVIEW_REQUIRED",
        "redistribution_authorized": False,
        "manifest_sha256": manifest_sha,
        "truthful": True,
    }
    return {
        "file": output_path,
        "manifest": manifest_path,
        "record": manifest,
        "verification": verification,
    }
