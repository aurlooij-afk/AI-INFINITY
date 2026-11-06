from __future__ import annotations

"""Canonical standalone image production through Creator Studio's existing project queue."""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _request(project: dict[str, Any]) -> dict[str, Any]:
    value = project.get("request_json")
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _zero_cost_image_provider_allowed(studio_module=None) -> bool:
    """Require an explicit no-cost attestation and respect the global provider kill switch."""
    if studio_module is not None:
        disabled = getattr(studio_module, "_external_providers_disabled", None)
        if callable(disabled):
            try:
                if disabled():
                    return False
            except Exception:
                # A failed policy check must fail closed.
                return False
    return (
        os.getenv("AI_INFINITY_HF_IMAGE_ZERO_COST_CONFIRMED", "").strip().lower()
        in {"1", "true", "yes", "on"}
        and os.getenv("AI_INFINITY_HF_FREE_TIER_AND_DATA_USE_ACK", "").strip().lower()
        in {"1", "true", "yes", "on"}
        and bool(
            os.getenv("HF_TOKEN", "").strip()
            or os.getenv("HUGGINGFACEHUB_API_TOKEN", "").strip()
        )
    )


def run_native_image_project(
    project_id: str,
    *,
    studio_module=None,
    image_generator: Callable | None = None,
    fallback_generator: Callable | None = None,
    storage_persist: Callable | None = None,
) -> dict[str, Any]:
    """Produce one native image and truthful review/evidence records.

    AI inference is opt-in only when a no-cost model/provider has been explicitly
    confirmed. Otherwise the existing local editorial illustration engine is used.
    Rights and creative review remain pending for both generation modes.
    """
    if studio_module is None:
        import studio_ultimate as studio_module

    studio = studio_module
    project = studio._get_project(project_id)
    if not project:
        raise RuntimeError("image production requires an existing Creator Studio project")
    request = _request(project)
    user_id = str(project.get("user_id") or "")
    title = str(request.get("title") or project.get("title") or "AI Infinity image")[:180]
    prompt = str(
        request.get("image_prompt")
        or request.get("prompt")
        or request.get("objective")
        or request.get("topic")
        or title
    ).strip()[:4000]
    language = str(request.get("language") or "English")[:80]
    if len(prompt) < 8:
        raise RuntimeError("standalone image production requires a specific image prompt")

    outdir = Path(studio._project_dir(project_id))
    outdir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    if image_generator is None:
        if _zero_cost_image_provider_allowed(studio):
            image_generator = studio._hf_image
        else:
            image_generator = lambda _prompt, _outdir, _index: None
    if fallback_generator is None:
        fallback_generator = studio._procedural_image

    if callable(getattr(studio, "_stage", None)):
        studio._stage(project_id, "visuals_and_voice", 35, content_type="image")
    if callable(getattr(studio, "audit_event", None)):
        studio.audit_event(project_id, "native_image_generation_started", {
            "content_type": "image",
            "zero_cost_ai_provider_authorized": _zero_cost_image_provider_allowed(studio),
            "truthful": True,
        })

    from ai_infinity.native_image import build_native_image

    built = build_native_image(
        outdir,
        project_id=project_id,
        title=title,
        prompt=prompt,
        language=language,
        image_generator=image_generator,
        fallback_generator=fallback_generator,
    )
    image_path = Path(built["file"])
    manifest_path = Path(built["manifest"])
    record = dict(built["record"])
    image_hash = _sha256(image_path)
    image_size = image_path.stat().st_size

    # Keep source, rights and production records separate. The prompt is creator
    # input, not a fabricated external research citation.
    sources_path = outdir / "sources.json"
    sources = {
        "schema": "ai-infinity.sources.v1",
        "project_id": project_id,
        "source_count": 0,
        "sources": [],
        "creator_input": {
            "type": "user_prompt",
            "sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        },
        "truthful": True,
    }
    _json(sources_path, sources)

    generation = record.get("generation") if isinstance(record.get("generation"), dict) else {}
    rights_path = outdir / "rights_manifest.json"
    rights = {
        "schema": "ai-infinity.rights-manifest.v1",
        "project_id": project_id,
        "status": "REVIEW_REQUIRED",
        "rights_status": "REVIEW_REQUIRED",
        "assets": [{
            "asset": image_path.name,
            "sha256": image_hash,
            "generation_mode": generation.get("mode"),
            "provider_or_method": generation.get("provider_or_method"),
            "source_url": generation.get("source_url"),
            "creator": generation.get("creator"),
            "model": generation.get("model"),
            "declared_license": generation.get("declared_license"),
            "rights_status": "REVIEW_REQUIRED",
        }],
        "human_review_required": True,
        "redistribution_authorized": False,
        "reason": "Generated media still requires source-specific rights and human review before redistribution.",
        "truthful": True,
    }
    _json(rights_path, rights)

    # Register/persist the primary image through the canonical asset store exactly
    # once. The asset-store adapter is the source of truth for durable-storage
    # status; local file existence is never represented as durable recovery.
    storage_result: dict[str, Any] = {"status": "not_configured", "truthful": True}
    save_asset = getattr(studio, "_save_asset", None)
    image_saved_through_asset_store = False
    if callable(save_asset):
        try:
            saved = save_asset(
                project_id,
                "image",
                image_path,
                "image/png",
                {
                    "content_type": "image",
                    "sha256": image_hash,
                    "rights_status": "REVIEW_REQUIRED",
                    "primary_delivery_status": "PASS",
                },
            )
            image_saved_through_asset_store = True
            if isinstance(saved, dict) and isinstance(saved.get("durable_storage"), dict):
                storage_result = dict(saved["durable_storage"])
        except Exception as exc:
            storage_result = {
                "status": "not_persisted",
                "error_type": type(exc).__name__,
                "truthful": True,
            }
    elif callable(storage_persist) and user_id:
        try:
            candidate = storage_persist(
                image_path,
                user_id,
                image_path.name,
                "image/png",
                {"project_id": project_id, "kind": "image", "sha256": image_hash},
            )
            if isinstance(candidate, dict):
                storage_result = dict(candidate)
            else:
                storage_result = {"status": "unknown", "truthful": True}
        except Exception as exc:
            storage_result = {
                "status": "not_persisted",
                "error_type": type(exc).__name__,
                "truthful": True,
            }

    _json(manifest_path, record)
    manifest_hash = _sha256(manifest_path)

    production_path = outdir / "production_manifest.json"
    production = {
        "schema": "ai-infinity.production-manifest.v1",
        "project_id": project_id,
        "content_type": "image",
        "status": "REVIEW_REQUIRED",
        "created_at_utc": now,
        "primary_deliverable": {
            "file": image_path.name,
            "media_type": "image/png",
            "size_bytes": image_size,
            "sha256": image_hash,
            "manifest": manifest_path.name,
            "manifest_sha256": manifest_hash,
        },
        "storage": storage_result,
        "rights_status": "REVIEW_REQUIRED",
        "creative_review_status": "PENDING",
        "publication_status": "NOT_PUBLISHED",
        "truthful": True,
    }
    _json(production_path, production)

    primary_path = outdir / "primary_content_delivery.json"
    primary = {
        "schema": "ai-infinity.primary-content-delivery.v1",
        "project_id": project_id,
        "content_type": "image",
        "status": "PASS",
        "primary_delivery_verified": True,
        "primary_scope_only": True,
        "checks": {
            "image_exists": image_path.is_file(),
            "image_non_empty": image_size > 0,
            "image_png_signature": image_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n",
            "image_sha256_matches": _sha256(image_path) == image_hash,
            "image_manifest_hash_matches": _sha256(manifest_path) == manifest_hash,
            "rights_review_remains_open": rights.get("rights_status") == "REVIEW_REQUIRED",
            "redistribution_remains_blocked": rights.get("redistribution_authorized") is False,
        },
        "artifacts": [
            {"name": image_path.name, "media_type": "image/png", "size_bytes": image_size, "sha256": image_hash},
            {"name": manifest_path.name, "media_type": "application/json", "size_bytes": manifest_path.stat().st_size, "sha256": manifest_hash},
        ],
        "human_review_status": "PENDING",
        "rights_review_status": "REVIEW_REQUIRED",
        "external_publication_status": "NOT_PUBLISHED",
        "truthful": True,
    }
    if not all(primary["checks"].values()):
        raise RuntimeError("native image primary-delivery integrity verification failed")
    _json(primary_path, primary)

    artifacts = (
        (image_path, "image/png", "image"),
        (manifest_path, "application/json", "image_manifest"),
        (sources_path, "application/json", "sources"),
        (rights_path, "application/json", "rights_manifest"),
        (production_path, "application/json", "production_manifest"),
        (primary_path, "application/json", "primary_content_delivery"),
    )
    for path, media_type, kind in artifacts:
        metadata = {
            "content_type": "image",
            "sha256": _sha256(path),
            "rights_status": "REVIEW_REQUIRED",
            "primary_delivery_status": "PASS" if path == image_path else "EVIDENCE",
        }
        if path == image_path:
            metadata["durable_storage"] = storage_result
        if callable(getattr(studio, "_save_asset", None)) and not (
            path == image_path and image_saved_through_asset_store
        ):
            studio._save_asset(project_id, kind, path, media_type, metadata)
        if callable(getattr(studio, "register_artifact", None)):
            studio.register_artifact(project_id, path, media_type, metadata)

    blueprint = {
        "schema": "ai-infinity.image-production.v1",
        "title": title,
        "objective": prompt,
        "content_type": "image",
        "language": language,
        "generation": generation,
        "primary_delivery_status": "PASS",
        "creative_review_status": "PENDING",
        "rights_status": "REVIEW_REQUIRED",
        "truthful": True,
    }
    result = {
        "status": "completed_with_qc_warnings",
        "state": "DEGRADED",
        "project_id": project_id,
        "content_type": "image",
        "title": title,
        "primary_content_delivery": primary,
        "image": {
            "file": image_path.name,
            "sha256": image_hash,
            "size_bytes": image_size,
            "width": record["output"]["width"],
            "height": record["output"]["height"],
            "generation_mode": generation.get("mode"),
            "fallback_used": generation.get("fallback_used") is True,
        },
        "storage": storage_result,
        "quality": {
            "state": "REVIEW_REQUIRED",
            "checks": {
                "primary_image_integrity_passed": True,
                "rights_cleared": False,
                "creative_review_approved": False,
            },
        },
        "rights_status": "REVIEW_REQUIRED",
        "human_review_status": "PENDING",
        "publish_status": "NOT_PUBLISHED",
        "truthful": True,
    }
    if callable(getattr(studio, "checkpoint", None)):
        studio.checkpoint(project_id, "native_image_ready_for_review", {
            "image_sha256": image_hash,
            "manifest_sha256": manifest_hash,
            "rights_status": "REVIEW_REQUIRED",
            "truthful": True,
        })
    if callable(getattr(studio, "audit_event", None)):
        studio.audit_event(project_id, "native_image_ready_for_review", {
            "image_sha256": image_hash,
            "generation_mode": generation.get("mode"),
            "storage_status": storage_result.get("status"),
            "rights_status": "REVIEW_REQUIRED",
            "truthful": True,
        })
    if callable(getattr(studio, "_update_project", None)):
        studio._update_project(
            project_id,
            status="completed_with_qc_warnings",
            error="Image generated and integrity-verified; creative and rights approval remain required.",
            result_json=json.dumps(result, ensure_ascii=False, sort_keys=True),
            blueprint_json=json.dumps(blueprint, ensure_ascii=False, sort_keys=True),
            progress=100,
            stage="complete_with_review_required",
        )
    process_lock = getattr(studio, "PROCESS_LOCK", None)
    registry = getattr(studio, "PROCESS_REGISTRY", None)
    if process_lock is not None and registry is not None:
        with process_lock:
            registry.pop(project_id, None)
    active_project = getattr(studio, "ACTIVE_PROJECT", None)
    if active_project is not None and hasattr(active_project, "project_id"):
        active_project.project_id = None
    return result

def install(studio_module) -> None:
    """Attach image intent to the existing Creator Studio queue, without a second worker."""
    if getattr(studio_module, "_ai_infinity_native_image_runtime_installed", False):
        return
    original_normalizer = studio_module.normalize_creator_content_type
    original_runner = studio_module.run_project

    def normalize_content_type(value: Any) -> str:
        selected = str(value or "").strip().lower()
        if selected in {"image", "picture", "illustration", "poster", "cover_art"}:
            return "image"
        return original_normalizer(value)

    def dispatch_project(project_id: str, model_fn) -> None:
        project = studio_module._get_project(project_id)
        request = _request(project or {})
        if normalize_content_type(request.get("content_type")) == "image":
            run_native_image_project(project_id, studio_module=studio_module)
            return
        return original_runner(project_id, model_fn)

    existing = set(getattr(studio_module, "SUPPORTED_CREATOR_CONTENT_TYPES", ()))
    studio_module.SUPPORTED_CREATOR_CONTENT_TYPES = frozenset(existing | {"image"})
    studio_module.normalize_creator_content_type = normalize_content_type
    studio_module.run_project = dispatch_project
    studio_module._ai_infinity_native_image_runtime_installed = True

