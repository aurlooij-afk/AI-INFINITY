from __future__ import annotations

"""Safe OpenTimelineIO interchange for completed AI Infinity scene projects.

This adapter is deliberately narrower than a general NLE importer: it only
accepts relative references to scene masters already owned by this project.
It never fetches remote URLs, writes outside the project root, or treats a
timeline export as proof that the underlying media is professionally verified.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

MAX_OTIO_BYTES = 2 * 1024 * 1024
MAX_BUNDLE_BYTES = 512 * 1024 * 1024
MAX_SCENES = 40
SCENE_NAME = re.compile(r"scene_(\d{2})\.mp4\Z")
SIDE_CARS = (
    "captions.srt", "script.md", "article.md", "sources.json", "rights.json",
    "rights_manifest.json", "visual_rights.json", "manifest.json",
    "production_manifest.json", "provenance.json", "fact_check.json",
    "seo.json", "social_campaign.json", "platform_manifest.json",
    "accessibility.json", "feature_execution.json", "creator_experiments.json",
    "asset_registry.json", "timeline.json", "thumbnail.jpg",
)
AUDIO_STEMS = ("narration_only.mp3", "music_only.mp3", "music_bed.mp3", "background_music.mp3")


class TimelineInterchangeError(ValueError):
    """A timeline cannot safely be exported or imported."""


def _otio():
    try:
        import opentimelineio as otio
    except Exception as exc:  # pragma: no cover - dependency/build failure
        raise TimelineInterchangeError("OpenTimelineIO is unavailable in this runtime") from exc
    return otio


def _scene_name(value: Any) -> str:
    """Allow only this project's stable scene filenames, never arbitrary paths or URLs."""
    raw = str(value or "").strip()
    if not raw or "\\" in raw or raw.startswith("/") or "://" in raw or raw.startswith("file:"):
        raise TimelineInterchangeError("timeline media reference must be a project-relative scene path")
    parts = PurePosixPath(raw).parts
    if any(part in {"", ".", ".."} for part in parts) or raw not in {PurePosixPath(raw).name, f"media/{PurePosixPath(raw).name}"}:
        raise TimelineInterchangeError("timeline media reference contains an unsafe path")
    name = PurePosixPath(raw).name
    if not SCENE_NAME.fullmatch(name):
        raise TimelineInterchangeError("timeline media reference is not a supported scene master")
    return name


def _inside(root: Path, path: Path) -> Path:
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    if resolved_path == resolved_root or resolved_root not in resolved_path.parents:
        raise TimelineInterchangeError("timeline path escapes the project directory")
    return resolved_path


def _duration_seconds(path: Path) -> float:
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(path),
            ],
            capture_output=True, text=True, timeout=20, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TimelineInterchangeError("media duration probe could not complete") from exc
    try:
        duration = float((result.stdout or "").strip())
    except (TypeError, ValueError):
        duration = 0.0
    if result.returncode != 0 or not path.is_file() or duration <= 0:
        raise TimelineInterchangeError(f"scene media is not a readable timed file: {path.name}")
    return duration


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _time_range(otio: Any, start_seconds: float, duration_seconds: float, fps: int):
    start = max(0, round(float(start_seconds) * fps))
    duration = round(float(duration_seconds) * fps)
    if duration < 1:
        raise TimelineInterchangeError("timeline clip duration is shorter than one frame")
    return otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(start, fps),
        duration=otio.opentime.RationalTime(duration, fps),
    )


def export_otio_bundle(
    project_root: Path | str,
    project_id: str,
    project: dict,
    timeline_data: dict,
    truth: dict,
) -> dict:
    """Write a portable OTIO file and a bounded bundle containing all referenced media."""
    otio = _otio()
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise TimelineInterchangeError("project directory is unavailable")
    rows = timeline_data.get("scenes")
    if not isinstance(rows, list) or not rows or len(rows) > MAX_SCENES:
        raise TimelineInterchangeError("timeline must contain between 1 and 40 scenes")
    try:
        fps = int(timeline_data.get("fps") or 30)
    except (TypeError, ValueError):
        fps = 30
    if fps < 1 or fps > 120:
        raise TimelineInterchangeError("timeline frame rate must be between 1 and 120 fps")

    scene_files: list[tuple[dict, Path, str, float]] = []
    total_bytes = 0
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise TimelineInterchangeError("timeline contains an invalid scene row")
        asset = _scene_name(row.get("source_asset"))
        if asset in seen:
            raise TimelineInterchangeError("timeline contains duplicate scene references")
        seen.add(asset)
        source = _inside(root, root / asset)
        if not source.is_file():
            raise TimelineInterchangeError(f"scene source is missing: {asset}")
        size = source.stat().st_size
        total_bytes += size
        duration = _duration_seconds(source)
        if total_bytes > MAX_BUNDLE_BYTES:
            raise TimelineInterchangeError("timeline media exceeds the 512 MiB export safety limit")
        scene_files.append((row, source, _sha256(source), duration))

    audio = root / "audio_master.mp3"
    if not audio.is_file():
        raise TimelineInterchangeError("audio_master.mp3 is required for a complete editable timeline")
    audio = _inside(root, audio)
    total_bytes += audio.stat().st_size
    if total_bytes > MAX_BUNDLE_BYTES:
        raise TimelineInterchangeError("timeline media exceeds the 512 MiB export safety limit")
    audio_duration = _duration_seconds(audio)

    metadata = {
        "schema": "ai-infinity-otio-export.v1",
        "project_id": str(project_id),
        "title": str(project.get("title") or timeline_data.get("title") or "AI Infinity project")[:240],
        "fps": fps,
        "scene_count": len(scene_files),
        "timeline_duration_seconds": float(timeline_data.get("duration_seconds") or 0),
        "truth_state": str(truth.get("state") or "UNKNOWN"),
        "truth_verified": truth.get("verified") is True,
        "publish_allowed_by_quality_gate": truth.get("publish_allowed_by_quality_gate") is True,
        "human_review_status": "PENDING",
        "media_references": "project-relative",
        "rights_and_source_records_included": True,
    }

    with tempfile.TemporaryDirectory(prefix=".aii-otio-", dir=str(root)) as work_value:
        work = Path(work_value)
        media_dir = work / "media"
        media_dir.mkdir()
        timeline = otio.schema.Timeline(name=metadata["title"])
        video = otio.schema.Track(name="Video", kind=otio.schema.TrackKind.Video)
        audio_track = otio.schema.Track(name="Audio", kind=otio.schema.TrackKind.Audio)
        timeline.tracks.append(video)
        timeline.tracks.append(audio_track)
        timeline.metadata["ai_infinity"] = metadata

        inventory = []
        inventory = []
        audio_stem_inventory = []
        stem_bytes = 0
        for stem_name in AUDIO_STEMS:
            stem = root / stem_name
            if not stem.exists():
                continue
            if stem.is_symlink():
                raise TimelineInterchangeError("refusing to package a symlinked audio stem")
            stem = _inside(root, stem)
            expected_size = stem.stat().st_size
            expected_sha = _sha256(stem)
            staged_stem = media_dir / stem_name
            shutil.copyfile(stem, staged_stem)
            stem_size = staged_stem.stat().st_size
            stem_sha = _sha256(staged_stem)
            if stem_size != expected_size or stem_sha != expected_sha:
                raise TimelineInterchangeError(f"audio stem changed while exporting: {stem_name}")
            stem_bytes += stem_size
            if total_bytes + stem_bytes > MAX_BUNDLE_BYTES:
                raise TimelineInterchangeError("timeline media and audio stems exceed the 512 MiB export safety limit")
            inventory.append({
                "path": f"media/{stem_name}",
                "sha256": stem_sha,
                "size_bytes": stem_size,
                "enabled": False,
                "kind": "audio_stem",
                "referenced_by_timeline": False,
            })
            audio_stem_inventory.append({
                "name": stem_name,
                "path": f"media/{stem_name}",
                "sha256": stem_sha,
                "size_bytes": stem_size,
                "included_as_editable_source": True,
            })
        metadata["available_audio_stems"] = audio_stem_inventory

        for row, source, digest, source_duration in scene_files:
            asset = source.name
            expected_size = source.stat().st_size
            staged_source = media_dir / asset
            shutil.copyfile(source, staged_source)
            copied_size = staged_source.stat().st_size
            copied_sha = _sha256(staged_source)
            if copied_size != expected_size or copied_sha != digest:
                raise TimelineInterchangeError(f"scene source changed while exporting: {asset}")
            digest = copied_sha
            duration = max(0.0, float(row.get("duration") or 0))
            trim_start = max(0.0, float(row.get("trim_start") or 0))
            trim_end = max(0.0, float(row.get("trim_end") or 0))
            if duration <= 0.25 or trim_start + duration + trim_end > source_duration + 0.1:
                raise TimelineInterchangeError(f"scene trim is invalid for {asset}")
            scene_meta = {
                "source_asset": asset,
                "source_index": int(row.get("source_index") or SCENE_NAME.fullmatch(asset).group(1)),
                "heading": str(row.get("heading") or "")[:240],
                "narration": str(row.get("narration") or "")[:6000],
                "image_prompt": str(row.get("image_prompt") or "")[:1000],
                "on_screen": str(row.get("on_screen") or "")[:500],
                "trim_start": trim_start,
                "trim_end": trim_end,
                "enabled": bool(row.get("enabled", True)),
                "source_duration_seconds": round(source_duration, 6),
                "sha256": digest,
                "size_bytes": copied_size,
            }
            reference = otio.schema.ExternalReference(
                target_url=f"media/{asset}",
                available_range=_time_range(otio, 0, source_duration, fps),
                metadata={"ai_infinity": {"sha256": digest, "size_bytes": copied_size}},
            )
            if scene_meta["enabled"]:
                clip = otio.schema.Clip(
                    name=scene_meta["heading"] or asset,
                    media_reference=reference,
                    source_range=_time_range(otio, trim_start, duration, fps),
                    metadata={"ai_infinity": scene_meta},
                )
                video.append(clip)
            else:
                gap = otio.schema.Gap(
                    name="Disabled: " + (scene_meta["heading"] or asset),
                    source_range=otio.opentime.TimeRange(
                        start_time=otio.opentime.RationalTime(0, fps),
                        duration=otio.opentime.RationalTime(0, fps),
                    ),
                    metadata={"ai_infinity": scene_meta},
                )
                video.append(gap)
            inventory.append({
                "path": f"media/{asset}",
                "sha256": digest,
                "size_bytes": copied_size,
                "enabled": scene_meta["enabled"],
                "kind": "scene_master",
                "referenced_by_timeline": scene_meta["enabled"],
            })

        audio_digest = _sha256(audio)
        audio_size = audio.stat().st_size
        staged_audio = media_dir / audio.name
        shutil.copyfile(audio, staged_audio)
        if staged_audio.stat().st_size != audio_size or _sha256(staged_audio) != audio_digest:
            raise TimelineInterchangeError("audio master changed while exporting")
        audio_ref = otio.schema.ExternalReference(
            target_url="media/audio_master.mp3",
            available_range=_time_range(otio, 0, audio_duration, fps),
            metadata={"ai_infinity": {"sha256": audio_digest, "size_bytes": audio_size}},
        )
        audio_clip = otio.schema.Clip(
            name="Narration and audio master",
            media_reference=audio_ref,
            source_range=_time_range(otio, 0, audio_duration, fps),
            metadata={"ai_infinity": {"role": "audio_master", "sha256": audio_digest}},
        )
        audio_track.append(audio_clip)
        inventory.append({
            "path": "media/audio_master.mp3",
            "sha256": audio_digest,
            "size_bytes": audio_size,
            "enabled": True,
            "kind": "programme_mix",
            "referenced_by_timeline": True,
        })

        otio_path = work / "project.otio"
        otio.adapters.write_to_file(timeline, str(otio_path), adapter_name="otio_json")
        timeline_digest = _sha256(otio_path)
        bundle_manifest = {
            **metadata,
            "files": [{
                "path": "project.otio",
                "sha256": timeline_digest,
                "size_bytes": otio_path.stat().st_size,
                "kind": "timeline",
            }, *inventory],
            "included_sidecars": [],
            "manifest_self_hash_excluded": True,
            "export_is_not_human_approval": True,
        }
        with zipfile.ZipFile(work / "timeline-export.zip", "w", allowZip64=True) as archive:
            archive.write(otio_path, "project.otio", compress_type=zipfile.ZIP_DEFLATED)
            for item in inventory:
                source_path = media_dir / PurePosixPath(item["path"]).name
                archive.write(
                    source_path,
                    item["path"],
                    compress_type=zipfile.ZIP_STORED if source_path.suffix.lower() in {".mp4", ".mp3"} else zipfile.ZIP_DEFLATED,
                )
            bundle_bytes = total_bytes + stem_bytes + otio_path.stat().st_size
            staged_metadata = work / "sidecars"
            staged_metadata.mkdir()
            for name in SIDE_CARS:
                candidate = root / name
                if not candidate.exists():
                    continue
                if candidate.is_symlink():
                    raise TimelineInterchangeError(f"refusing to package a symlinked sidecar: {name}")
                candidate = _inside(root, candidate)
                expected_size = candidate.stat().st_size
                expected_sha = _sha256(candidate)
                staged_sidecar = staged_metadata / name
                shutil.copyfile(candidate, staged_sidecar)
                sidecar_size = staged_sidecar.stat().st_size
                sidecar_sha = _sha256(staged_sidecar)
                if sidecar_size != expected_size or sidecar_sha != expected_sha:
                    raise TimelineInterchangeError(f"sidecar changed while exporting: {name}")
                bundle_bytes += sidecar_size
                if bundle_bytes > MAX_BUNDLE_BYTES:
                    raise TimelineInterchangeError("timeline sidecars exceed the 512 MiB export safety limit")
                archive.write(staged_sidecar, f"metadata/{name}", compress_type=zipfile.ZIP_DEFLATED)
                bundle_manifest["included_sidecars"].append(f"metadata/{name}")
                bundle_manifest["files"].append({
                    "path": f"metadata/{name}",
                    "sha256": sidecar_sha,
                    "size_bytes": sidecar_size,
                    "kind": "sidecar",
                })
            archive.writestr(
                "metadata/timeline-export-manifest.json",
                json.dumps(bundle_manifest, ensure_ascii=False, indent=2) + "\n",
                compress_type=zipfile.ZIP_DEFLATED,
            )

        staged_zip = work / "timeline-export.zip"
        otio_digest = _sha256(otio_path)
        bundle_digest = _sha256(staged_zip)
        final_otio_candidate = root / f"project-{otio_digest[:16]}.otio"
        final_zip_candidate = root / f"timeline-export-{bundle_digest[:16]}.zip"
        if final_otio_candidate.is_symlink() or final_zip_candidate.is_symlink():
            raise TimelineInterchangeError("refusing to replace a symlink at an export destination")
        final_otio = _inside(root, final_otio_candidate)
        final_zip = _inside(root, final_zip_candidate)
        for staged, destination, digest in (
            (otio_path, final_otio, otio_digest),
            (staged_zip, final_zip, bundle_digest),
        ):
            try:
                # Both paths are under the same project filesystem. A hard link
                # provides create-only semantics so concurrent exports and
                # pre-existing files cannot silently overwrite one another.
                os.link(staged, destination)
                staged.unlink()
            except FileExistsError:
                if not destination.is_file() or _sha256(destination) != digest:
                    raise TimelineInterchangeError("export destination collision; existing user data was preserved")

    return {
        "timeline_path": final_otio,
        "bundle_path": final_zip,
        "timeline_sha256": _sha256(final_otio),
        "bundle_sha256": _sha256(final_zip),
        "bundle_size_bytes": final_zip.stat().st_size,
        "scene_count": len(scene_files),
        "media_files": len(scene_files) + 1,
        "fps": fps,
        "truth_state": metadata["truth_state"],
        "truth_verified": metadata["truth_verified"],
        "human_review_status": "PENDING",
        "truthful": True,
    }


def import_otio_timeline(
    payload: bytes,
    project_root: Path | str,
    project_id: str,
    project_title: str,
) -> dict:
    """Parse bounded OTIO JSON and map scene clips to this project's existing masters."""
    if not isinstance(payload, bytes) or not payload:
        raise TimelineInterchangeError("an OTIO JSON file body is required")
    if len(payload) > MAX_OTIO_BYTES:
        raise TimelineInterchangeError("OTIO file exceeds the 2 MiB import limit")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TimelineInterchangeError("OTIO file must be UTF-8 JSON") from exc
    otio = _otio()
    try:
        value = otio.adapters.read_from_string(text, adapter_name="otio_json")
    except Exception as exc:
        raise TimelineInterchangeError("OTIO file could not be parsed") from exc
    if not isinstance(value, otio.schema.Timeline):
        raise TimelineInterchangeError("OTIO file must contain a Timeline root object")
    root = Path(project_root).resolve()
    info = value.metadata.get("ai_infinity") if isinstance(value.metadata, dict) else {}
    try:
        fps = int((info or {}).get("fps") or 30)
    except (TypeError, ValueError):
        fps = 30
    if fps < 1 or fps > 120:
        fps = 30

    video_tracks = [track for track in value.tracks if isinstance(track, otio.schema.Track) and track.kind == otio.schema.TrackKind.Video]
    if len(video_tracks) != 1:
        raise TimelineInterchangeError("OTIO import requires exactly one direct video track")
    rows = []
    seen = set()
    for item in video_tracks[0]:
        if isinstance(item, otio.schema.Gap):
            scene_meta = item.metadata.get("ai_infinity") if isinstance(item.metadata, dict) else {}
            if not isinstance(scene_meta, dict) or not scene_meta.get("source_asset"):
                if float(otio.opentime.to_seconds(item.duration())) <= 0:
                    continue
                raise TimelineInterchangeError("timeline contains an unsupported gap without AI Infinity scene metadata")
            asset = _scene_name(scene_meta.get("source_asset"))
            enabled = False
            trim_start = float(scene_meta.get("trim_start") or 0)
            duration = float(scene_meta.get("source_duration_seconds") or 0) - trim_start - float(scene_meta.get("trim_end") or 0)
            clip_meta = scene_meta
        elif isinstance(item, otio.schema.Clip):
            ref = item.media_reference
            if not isinstance(ref, otio.schema.ExternalReference):
                raise TimelineInterchangeError("OTIO clip must reference an external scene master")
            asset = _scene_name(ref.target_url)
            clip_meta = item.metadata.get("ai_infinity") if isinstance(item.metadata, dict) else {}
            if not isinstance(clip_meta, dict):
                clip_meta = {}
            source_range = item.source_range
            if source_range is None:
                raise TimelineInterchangeError("OTIO scene clip has no source range")
            trim_start = float(otio.opentime.to_seconds(source_range.start_time))
            duration = float(otio.opentime.to_seconds(source_range.duration))
            enabled = bool(clip_meta.get("enabled", True))
        else:
            raise TimelineInterchangeError("OTIO video track contains an unsupported item; only clips and exported disabled-scene gaps are supported")

        if asset in seen:
            raise TimelineInterchangeError("OTIO timeline contains duplicate scene masters")
        seen.add(asset)
        source = _inside(root, root / asset)
        if not source.is_file():
            raise TimelineInterchangeError(f"OTIO scene media is not present in this project: {asset}")
        source_duration = _duration_seconds(source)
        if trim_start < 0 or duration <= 0.25 or trim_start + duration > source_duration + 0.1:
            raise TimelineInterchangeError(f"OTIO source range is outside the available media: {asset}")
        trim_end = max(0.0, source_duration - trim_start - duration)
        match = SCENE_NAME.fullmatch(asset)
        rows.append({
            "source_asset": asset,
            "source_index": int(match.group(1)),
            "heading": str(clip_meta.get("heading") or item.name or asset)[:240],
            "narration": str(clip_meta.get("narration") or "")[:6000],
            "image_prompt": str(clip_meta.get("image_prompt") or "")[:1000],
            "on_screen": str(clip_meta.get("on_screen") or "")[:500],
            "trim_start": round(trim_start, 3),
            "trim_end": round(trim_end, 3),
            "enabled": enabled,
        })
        if len(rows) > MAX_SCENES:
            raise TimelineInterchangeError("OTIO timeline exceeds the 40-scene limit")

    if not rows or not any(row["enabled"] for row in rows):
        raise TimelineInterchangeError("OTIO timeline must include at least one enabled scene")
    return {
        "version": "ai-infinity-otio-import.v1",
        "project_id": str(project_id),
        "title": str(project_title or value.name or "AI Infinity project")[:240],
        "fps": fps,
        "scenes": rows,
        "import_source": "OpenTimelineIO JSON",
        "truthful": True,
    }
