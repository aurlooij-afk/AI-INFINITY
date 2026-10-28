#!/usr/bin/env python3
"""Assemble factual, hash-backed evidence for the AI Infinity cinematic CI run.

This creates a human-review packet only. It never converts missing evidence or
failed quality checks into an acceptance pass.
"""
import hashlib
import json
from pathlib import Path


def read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    root = Path("/tmp/ai-infinity-brand-film-review")
    media = root / "final.mp4"
    probe = read_json(root / "ffprobe.json")
    streams = probe.get("streams", [])
    video = next((item for item in streams if item.get("codec_type") == "video"), {})
    audio = next((item for item in streams if item.get("codec_type") == "audio"), {})
    fmt = probe.get("format", {})

    files = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "integrity_manifest.json":
            files.append({
                "path": str(path.relative_to(root)),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            })

    project = read_json(root / "project.json")
    result = project.get("result") if isinstance(project.get("result"), dict) else {}
    quality = result.get("quality") if isinstance(result.get("quality"), dict) else {}
    visual = read_json(root / "visual_rights.json")
    truth = read_json(root / "production_truth.json")
    truth_checks = truth.get("checks") if isinstance(truth.get("checks"), dict) else {}
    coverage = quality.get("visual_source_coverage") if isinstance(quality.get("visual_source_coverage"), dict) else {}
    quality_checks = quality.get("checks") if isinstance(quality.get("checks"), dict) else {}

    # The public project-status response does not include a reliable asset registry.
    # Read the saved visual source manifest and artifact registry collected from the
    # actual running container. Compare declared source hashes against the selected
    # media files copied from the project directory; do not infer source identity
    # from a provider label or generate hashes for absent media.
    sources_manifest = read_json(root / "sources.json")
    artifact_payload = read_json(root / "artifacts.json")
    artifact_rows = artifact_payload.get("artifacts") if isinstance(artifact_payload.get("artifacts"), list) else []
    visual_assets = sources_manifest.get("visual_assets") if isinstance(sources_manifest.get("visual_assets"), list) else []
    source_evidence = []
    for index, raw in enumerate(visual_assets, 1):
        if not isinstance(raw, dict):
            source_evidence.append({
                "scene_index": index, "source_id": None, "sha256": None,
                "fallback": False, "hash_verified_against_file": False,
                "evidence_status": "INVALID_SOURCE_RECORD",
            })
            continue
        fallback = bool(raw.get("fallback"))
        declared_hash = raw.get("asset_sha256") or raw.get("sha256")
        source_id = raw.get("source_id")
        candidates = []
        for key in ("asset_name", "filename", "file_name", "name"):
            value = raw.get(key)
            if isinstance(value, str) and value and Path(value).name == value:
                candidates.append(root / value)
        candidates.extend(sorted(root.glob(f"selected_visual_{index:02d}.*")))
        candidates.extend(sorted(root.glob(f"procedural_visual_{index:02d}.*")))
        candidates.extend(sorted(root.glob(f"pexels_launch_video_{index:02d}.*")))
        local_path = next((path for path in candidates if path.is_file() and not path.is_symlink() and path.stat().st_size > 0), None)
        observed_hash = sha256_file(local_path) if local_path else None
        matching_artifacts = []
        for artifact in artifact_rows:
            if not isinstance(artifact, dict):
                continue
            metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else {}
            if source_id is not None and str(metadata.get("source_id")) == str(source_id):
                matching_artifacts.append(artifact)
            elif local_path and Path(str(artifact.get("asset_name") or "")).name == local_path.name:
                matching_artifacts.append(artifact)
        artifact_hashes = sorted({
            str(artifact.get("sha256"))
            for artifact in matching_artifacts
            if isinstance(artifact.get("sha256"), str) and len(artifact.get("sha256")) == 64
        })
        hash_matches = bool(
            observed_hash
            and isinstance(declared_hash, str)
            and len(declared_hash) == 64
            and observed_hash.lower() == declared_hash.lower()
        )
        artifact_hash_matches = bool(observed_hash and observed_hash in artifact_hashes)
        source_evidence.append({
            "scene_index": index,
            "asset_name": local_path.name if local_path else next((Path(value).name for value in candidates if value.name), None),
            "source_id": source_id,
            "source_url": raw.get("source_url"),
            "provider": raw.get("provider"),
            "source": raw.get("source"),
            "declared_sha256": declared_hash,
            "sha256": observed_hash or declared_hash,
            "observed_file_sha256": observed_hash,
            "artifact_registry_sha256": artifact_hashes,
            "hash_verified_against_file": hash_matches or artifact_hash_matches,
            "hash_matches_declared_manifest": hash_matches,
            "hash_matches_artifact_registry": artifact_hash_matches,
            "fallback": fallback,
            "fallback_reason": raw.get("fallback_reason"),
            "rights_status": raw.get("rights_status"),
            "license": raw.get("license"),
            "license_url": raw.get("license_url"),
            "evidence_status": (
                "VERIFIED_FALLBACK_HASHED" if fallback and observed_hash
                else "VERIFIED_SOURCE_HASHED" if source_id is not None and (hash_matches or artifact_hash_matches)
                else "REVIEW_REQUIRED_MISSING_SOURCE_ID" if not fallback and source_id is None
                else "REVIEW_REQUIRED_MISSING_FILE_OR_HASH"
            ),
        })
    source_evidence_verified = bool(source_evidence) and all(
        item.get("hash_verified_against_file") is True
        and (item.get("fallback") is True or item.get("source_id") is not None)
        for item in source_evidence
    )

    # Preserve explicit zeroes from a completed QC record; only infer counts
    # from the file-backed source manifest when the QC record has not been
    # written yet (for example, an interrupted run captured at packaging).
    visual_assets = sources_manifest.get("visual_assets") if isinstance(sources_manifest.get("visual_assets"), list) else []
    verified_motion_source_ids = {
        str(item.get("source_id"))
        for item in source_evidence
        if item.get("source_id") is not None
        and item.get("fallback") is not True
        and item.get("hash_verified_against_file") is True
        and item.get("evidence_status") == "VERIFIED_SOURCE_HASHED"
    }
    verified_fallback_count = sum(
        1 for item in source_evidence
        if item.get("fallback") is True and item.get("hash_verified_against_file") is True
    )

    def _recorded_count(primary: dict, primary_key: str, secondary: dict, secondary_key: str, inferred: int) -> int:
        for record, key in ((primary, primary_key), (secondary, secondary_key)):
            if key in record:
                try:
                    value = int(record[key])
                    if value >= 0:
                        return value
                except (TypeError, ValueError, OverflowError):
                    pass
        return int(inferred)

    scene_count = max(1, int(
        coverage.get("scene_count")
        or project.get("total_scenes")
        or len(visual_assets)
        or 1
    ))
    min_motion_sources = max(
        int(coverage.get("minimum_motion_sources") or max(3, int(scene_count * 0.70 + 0.9999))),
        int(truth_checks.get("minimum_motion_source_count") or 0),
    )
    min_professional_scenes = int(truth_checks.get("minimum_professional_scene_count") or 0)
    actual_scene_count = int(truth_checks.get("actual_rendered_scene_count") or scene_count)
    max_fallback_visuals = int(
        coverage.get("maximum_fallback_visuals")
        if "maximum_fallback_visuals" in coverage
        else max(1, int(scene_count * 0.15))
    )
    source_motion_count = _recorded_count(
        coverage, "distinct_verified_motion_sources",
        truth_checks, "verified_motion_source_count",
        len(verified_motion_source_ids),
    )
    fallback_count = _recorded_count(
        coverage, "fallback_visual_count",
        truth_checks, "fallback_visual_count",
        verified_fallback_count,
    )

    canonical_quality_gate = (
        truth.get("truthful") is True
        and truth.get("verified") is True
        and truth_checks.get("black_frame_ok") is True
        and truth_checks.get("professional_scene_coverage_passed") is True
        and truth_checks.get("professional_visual_diversity_passed") is True
        and truth_checks.get("professional_motion_coverage_passed") is True
        and truth_checks.get("script_content_floor_passed") is True
        and truth_checks.get("audio_clipping_ok") is True
    )
    creative_visual_gate = (
        coverage.get("required") is True
        and source_motion_count >= min_motion_sources
        and fallback_count <= max_fallback_visuals
        and (not min_professional_scenes or actual_scene_count >= min_professional_scenes)
        and quality_checks.get("required_source_motion_coverage") is True
        and quality_checks.get("fallback_visual_budget_ok") is True
        and canonical_quality_gate
    )
    decoded = (
        (root / "full_decode_status.txt").is_file()
        and (root / "full_decode_status.txt").read_text(encoding="utf-8").strip() == "PASS"
    )
    try:
        duration_seconds = float(fmt.get("duration") or 0)
        width = int(video.get("width") or 0)
        height = int(video.get("height") or 0)
    except (TypeError, ValueError):
        duration_seconds, width, height = 0.0, 0, 0
    technical_spec = (
        "PASS"
        if media.is_file()
        and fmt
        and abs(duration_seconds - 60.0) <= 0.15
        and width == 1920
        and height == 1080
        and decoded
        else "REVIEW_REQUIRED"
    )
    editable_timeline_gate = (
        (root / "timeline.otio").is_file()
        and (root / "timeline-export.zip").is_file()
        and not (root / "otio-export-warning.txt").exists()
    )
    creator_package_gate = (root / "package.zip").is_file()
    release_spec = "PASS" if (
        technical_spec == "PASS"
        and quality.get("passed") is True
        and creative_visual_gate
        and canonical_quality_gate
        and source_evidence_verified
        and editable_timeline_gate
        and creator_package_gate
    ) else "REVIEW_REQUIRED"

    integrity = {
        "truthful": True,
        "generator": "AI Infinity real container workflow (GitHub Actions)",
        "project_id": (root / "project_id.txt").read_text(encoding="utf-8").strip() if (root / "project_id.txt").exists() else None,
        "media_available": media.is_file(),
        "duration_seconds": duration_seconds if fmt else None,
        "width": video.get("width"),
        "height": video.get("height"),
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name"),
        "video_stream_present": bool(video),
        "audio_stream_present": bool(audio),
        "full_decode": "PASS" if decoded else "NOT_VERIFIED_OR_FAILED",
        "contact_sheet_available": (root / "contact_sheet.jpg").is_file(),
        "captions_available": (root / "captions.srt").is_file(),
        "sources_evidence_available": (root / "sources.json").is_file(),
        "rights_or_visual_rights_evidence_available": any((root / name).is_file() for name in ("rights_manifest.json", "visual_rights.json")),
        "editable_timeline_available": (root / "timeline.otio").is_file(),
        "editable_timeline_bundle_available": (root / "timeline-export.zip").is_file(),
        "editable_timeline_gate": "PASS" if editable_timeline_gate else "REVIEW_REQUIRED",
        "creator_package_available": creator_package_gate,
        "source_motion_scene_count": source_motion_count,
        "minimum_source_motion_scenes": min_motion_sources,
        "actual_rendered_scene_count": actual_scene_count,
        "minimum_professional_scene_count": min_professional_scenes,
        "fallback_visual_count": fallback_count,
        "maximum_fallback_visuals": max_fallback_visuals,
        "source_counts_provenance": (
            "quality_control_record" if "distinct_verified_motion_sources" in coverage and "fallback_visual_count" in coverage
            else "production_truth_record" if "verified_motion_source_count" in truth_checks and "fallback_visual_count" in truth_checks
            else "verified_source_manifest_partial_evidence"
        ),
        "verified_motion_source_ids": sorted(verified_motion_source_ids),
        "canonical_quality_state": truth.get("state", "UNAVAILABLE"),
        "canonical_quality_verified": truth.get("verified") is True,
        "canonical_quality_failures": truth.get("failures") or [],
        "source_asset_evidence": source_evidence,
        "source_evidence_verified": source_evidence_verified,
        "source_evidence_count": len(source_evidence),
        "source_evidence_unverified_count": sum(1 for item in source_evidence if item.get("hash_verified_against_file") is not True),
        "creative_visual_gate": "PASS" if creative_visual_gate else "REVIEW_REQUIRED",
        "technical_spec_gate": technical_spec,
        "spec_gate": release_spec,
        "ai_infinity_quality_gate": "PASS" if quality.get("passed") is True and canonical_quality_gate else "REVIEW_REQUIRED",
        "human_creative_review": "PENDING",
        "files": files,
    }
    (root / "integrity_manifest.json").write_text(
        json.dumps(integrity, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    missing = []
    required_groups = [
        ("final MP4", ["final.mp4"]),
        ("creator package", ["package.zip"]),
        ("thumbnail", ["thumbnail.jpg"]),
        ("captions", ["captions.srt"]),
        ("programme audio master", ["audio_master.mp3"]),
        ("sources", ["sources.json"]),
        ("rights evidence", ["rights_manifest.json", "visual_rights.json"]),
        ("editable OTIO timeline", ["timeline.otio"]),
        ("portable timeline bundle", ["timeline-export.zip"]),
    ]
    for label, names in required_groups:
        if not any((root / name).exists() for name in names):
            missing.append(label)

    notes = [
        "# AI Infinity — ONE IDEA. INFINITE WORLDS. | Human Review Packet",
        "",
        "## Evidence source",
        "Generated by the real AI Infinity application inside the current GitHub Actions Docker runtime via /infinity/divine/create; this is CI-hosted production, not a live Render deployment.",
        "Media copied from this isolated run's project directory is for human review only; it does not override any failed verification or authorize production release.",
        "",
        "## Technical target check",
        f"- 60-second / 1920×1080 technical target: **{integrity['technical_spec_gate']}**",
        f"- Professional visual/source gate: **{integrity['creative_visual_gate']}**",
        f"- Editable timeline and package gate: **{integrity['editable_timeline_gate']} / {'PASS' if integrity['creator_package_available'] else 'REVIEW_REQUIRED'}**",
        f"- Verified distinct source-motion scenes: {integrity['source_motion_scene_count']}/{integrity['minimum_source_motion_scenes']} required",
        f"- Explicitly labelled fallback visuals: {integrity['fallback_visual_count']} (maximum allowed: {integrity['maximum_fallback_visuals']})",
        f"- Actual duration: {duration_seconds} seconds",
        f"- Actual resolution: {video.get('width')}×{video.get('height')}",
        f"- Video codec: {video.get('codec_name')}; audio codec: {audio.get('codec_name')}",
        f"- Full decode: {integrity['full_decode']}",
        f"- Contact sheet: {'available' if integrity['contact_sheet_available'] else 'not available'}",
        f"- Canonical QC: {integrity['canonical_quality_state']} (verified: {integrity['canonical_quality_verified']})",
        f"- Professional scene count: {integrity['actual_rendered_scene_count']}/{integrity['minimum_professional_scene_count']} required",
        *([f"- Canonical QC failure: {failure}" for failure in integrity["canonical_quality_failures"]] or ["- Canonical QC failure details: none recorded"]),
        "",
        "## Selected visual source IDs and SHA-256 evidence",
        f"- File-backed source evidence verified: {integrity['source_evidence_verified']} ({integrity['source_evidence_count']} visual records; {integrity['source_evidence_unverified_count']} without file-backed hash confirmation)",
        *([f"- scene={item['scene_index']}; source_id={item['source_id']}; sha256={item['sha256']}; observed_file_sha256={item['observed_file_sha256']}; hash_verified={item['hash_verified_against_file']}; fallback={item['fallback']}; evidence={item['evidence_status']}; rights={item['rights_status']}; url={item['source_url']}" for item in integrity["source_asset_evidence"]] or ["- No visual source records were available in sources.json; source acceptance is REVIEW_REQUIRED."]),
        "",
        "## Creative and rights review",
        "- Human creative review: **PENDING — not yet reviewed or approved**",
        "- Inspect the first 4 seconds, scene variation, story continuity, typography, pacing and emotional payoff.",
        "- Verify voice provider from genuine production evidence; do not infer neural voice from a voice name.",
        "- Check narration clarity, music balance, caption synchronization, loudness and ending.",
        "- Verify sources, creator, source URL, licence, attribution and provenance for each external asset.",
        "- If rights, provider, provenance, recovery or quality evidence is missing or uncertain, retain REVIEW_REQUIRED.",
        "",
        "## Missing items (automatically detected)",
        *(["- None of the required media is missing from the basic review packet."] if not missing else [f"- Missing: {', '.join(missing)}"]),
        "",
        "## Files and integrity",
        "integrity_manifest.json lists only files actually collected, with SHA-256 hashes, sizes and stream metadata.",
        "",
        "## Remaining release gates",
        "- Live Render deployment remains blocked while the configured service is suspended.",
        "- Durable object storage PUT→HEAD→GET→SHA and restart/fresh-instance recovery must be verified separately.",
        "- Human/native-language creative review and rights acceptance remain open until an authorized reviewer completes them.",
    ]
    (root / "human_review_packet.md").write_text("\n".join(notes) + "\n", encoding="utf-8")
    print(json.dumps({
        "review_packet": str(root / "human_review_packet.md"),
        "integrity_manifest": str(root / "integrity_manifest.json"),
        "spec_gate": release_spec,
        "duration_seconds": duration_seconds,
        "resolution": [video.get("width"), video.get("height")],
        "missing": missing,
    }, indent=2))


if __name__ == "__main__":
    main()
