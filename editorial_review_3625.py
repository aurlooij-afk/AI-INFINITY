from __future__ import annotations

"""TARGET-2050.3625 — versioned, evidence-linked editorial review.

This layer does not claim automated taste is human judgement. It scores only
observable signals from the actual brief, generated script, storyboard/source
registry, delivered media streams, captions, manifests, and rights evidence.
Two separate deterministic passes report narrative/brief evidence and rendered
media/accessibility evidence. Publish-ready status always requires a human
approval bound to the exact current artifact fingerprint.
"""

import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, Request, Response

VERSION = "TARGET-2050.3625"
SCHEMA = "ai-infinity.editorial-scorecard.v1"
_INSTALLED_UI = False

DIMENSIONS = [
    ("brief_alignment", "Brief alignment and usefulness", "narrative"),
    ("narrative_structure", "Narrative structure and clarity", "narrative"),
    ("hook_opening", "Hook and opening effectiveness", "narrative"),
    ("script_reliability", "Script quality and factual reliability", "narrative"),
    ("scene_script_alignment", "Scene-to-script alignment", "narrative"),
    ("visual_diversity", "Visual diversity and continuity", "media"),
    ("composition_framing", "Composition, framing and presentation", "media"),
    ("pacing_transitions", "Timing, pacing and transitions", "media"),
    ("voice_audio", "Voice intelligibility and audio mix", "media"),
    ("captions_accessibility", "Captions, accessibility and language", "media"),
    ("brand_creator_control", "Brand consistency and creator control", "media"),
    ("rights_provenance", "Licensing, provenance and platform suitability", "media"),
]
_HOOK_MARKERS = (
    "what if", "imagine", "picture this", "here is why", "here's why",
    "the surprising", "most people", "you might think", "the truth is",
    "but there is", "but here's", "but here is", "why does", "have you",
    "the first thing", "one important", "today we", "in this video",
)
_FACT_SENSITIVE = (
    "latest", "current", "today", "recent", "news", "report", "documentary",
    "educational", "tutorial", "science", "history", "finance", "medical",
    "health", "research", "facts", "explainer", "statistics", "evidence",
)
_SECRET_PATTERNS = [
    re.compile(r'''(?i)(?:api[_-]?key|secret[_-]?key|access[_-]?token|refresh[_-]?token|authorization)\s*['"]?\s*[:=]\s*['"]?[A-Za-z0-9_./+=-]{16,}'''),
    re.compile(r'''(?i)https?://[^\s"]+\?(?:[^\s"]*&)?(?:X-Amz-Signature|Signature|token)=.{12,}'''),
]


def _studio():
    import studio_ultimate
    return studio_ultimate


def _closure():
    import production_closure_3624
    return production_closure_3624


def _loads(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _read_json(path: Optional[Path]) -> Dict[str, Any]:
    if not path or not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _read_text(path: Optional[Path]) -> str:
    if not path or not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _project_files(project_id: str) -> Dict[str, Path]:
    s = _studio()
    root = s._project_dir(project_id)
    return {p.name: p for p in root.iterdir() if p.is_file()} if root.exists() else {}


def _score(score: int, evidence: List[str], method: str, limitation: str = "") -> Dict[str, Any]:
    return {
        "score": max(1, min(5, int(score))),
        "scale": "1=critical weakness, 2=weak, 3=adequate but needs review, 4=strong, 5=strong with direct evidence",
        "evidence": [str(x) for x in evidence if str(x).strip()],
        "method": method,
        "limitation": limitation or "Automated evidence does not substitute for human editorial judgement.",
    }


def _srt_metrics(text: str, duration: float) -> Dict[str, Any]:
    blocks = [x.strip() for x in re.split(r"\r?\n\s*\r?\n", (text or "").strip()) if x.strip()]
    cues = []
    errors = []
    last_end = 0.0
    timestamp = re.compile(r"^(\d{2,}):(\d{2}):(\d{2}),(\d{3})$")

    def seconds(value: str) -> Optional[float]:
        match = timestamp.match(value.strip())
        if not match:
            return None
        h, m, s, ms = (int(x) for x in match.groups())
        if m >= 60 or s >= 60:
            return None
        return h * 3600 + m * 60 + s + ms / 1000.0

    for index, block in enumerate(blocks, start=1):
        rows = block.splitlines()
        timing_line = next((i for i, row in enumerate(rows) if "-->" in row), None)
        if timing_line is None:
            errors.append(f"cue {index}: missing timing line")
            continue
        left, right = rows[timing_line].split("-->", 1)
        start = seconds(left.strip().split()[0])
        end = seconds(right.strip().split()[0])
        lines = [row.strip() for row in rows[timing_line + 1:] if row.strip()]
        if start is None or end is None:
            errors.append(f"cue {index}: invalid SRT timestamp")
            continue
        if end <= start:
            errors.append(f"cue {index}: non-positive duration")
        if start < last_end - 0.001:
            errors.append(f"cue {index}: overlap or out-of-order timing")
        if end > duration + 0.75:
            errors.append(f"cue {index}: beyond media duration")
        if not lines:
            errors.append(f"cue {index}: missing text")
        if any(len(line) > 84 for line in lines):
            errors.append(f"cue {index}: line longer than 84 characters")
        last_end = max(last_end, end)
        cues.append({
            "index": index, "start": start, "end": end,
            "text": " ".join(lines), "line_count": len(lines),
            "max_line_chars": max([len(line) for line in lines] or [0]),
        })
    return {
        "cue_count": len(cues),
        "cues": cues,
        "errors": errors,
        "passed": bool(cues) and not errors,
        "last_cue_end": last_end,
        "duration_coverage_ratio": min(1.0, last_end / duration) if duration > 0 else 0.0,
    }


def _source_list(sources: Dict[str, Any], blueprint: Dict[str, Any]) -> List[Dict[str, Any]]:
    for candidate in (
        sources.get("sources"),
        sources.get("items"),
        (blueprint.get("research") or {}).get("sources") if isinstance(blueprint.get("research"), dict) else None,
    ):
        if isinstance(candidate, list):
            return [x for x in candidate if isinstance(x, dict)]
    return []


def _scene_list(blueprint: Dict[str, Any]) -> List[Dict[str, Any]]:
    plan = blueprint.get("plan") if isinstance(blueprint.get("plan"), dict) else blueprint
    for key in ("chapters", "scenes", "storyboard"):
        value = plan.get(key) if isinstance(plan, dict) else None
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    return []


def _fingerprint(project: Dict[str, Any], files: Dict[str, Path]) -> str:
    s = _studio()
    canonical_current_version_id = None
    # Project content can be versioned canonically without replacing final.mp4.
    # Bind human approval to the active canonical version as well as bytes.
    try:
        with s.DB_LOCK, s._connect() as db:
            row = db.execute(
                "SELECT current_version_id FROM canonical_projects WHERE project_id=?",
                (str(project.get("project_id") or ""),),
            ).fetchone()
        if row:
            canonical_current_version_id = row["current_version_id"] if hasattr(row, "keys") else row[0]
    except Exception:
        canonical_current_version_id = None
    names = {
        "final.mp4", "thumbnail.jpg", "audio_master.mp3", "captions.srt", "script.md",
        "transcript.txt", "sources.json", "fact_check.json", "provenance.json",
        "visual_rights.json", "manifest.json", "production_manifest.json",
        "rights_manifest.json", "seo.json", "social_campaign.json", "package.zip",
        "accessibility.json", "timeline.json",
    }
    artifacts = []
    for name, path in sorted(files.items()):
        if not path.is_file() or name == "editorial_scorecard.json":
            continue
        if name in names or (name.startswith("scene_") and path.suffix.lower() in {".mp4", ".jpg", ".jpeg", ".png"}) or (name.startswith("version_") and path.suffix.lower() == ".mp4"):
            try:
                artifacts.append({
                    "name": name,
                    "size_bytes": path.stat().st_size,
                    "sha256": s.file_sha256(path),
                })
            except Exception:
                artifacts.append({"name": name, "unreadable": True})
    brief = {
        "project_id": str(project.get("project_id") or ""),
        "canonical_current_version_id": canonical_current_version_id,
        "request": _loads(project.get("request_json")),
        "blueprint": _loads(project.get("blueprint_json")),
        "artifact_evidence": artifacts,
    }
    payload = json.dumps(brief, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _security_scan(files: Dict[str, Path]) -> List[str]:
    findings = []
    scan_names = {
        "script.md", "transcript.txt", "article.md", "seo.json", "social_campaign.json",
        "production_manifest.json", "rights_manifest.json", "provenance.json",
        "sources.json", "manifest.json", "package.json",
    }
    for name in sorted(scan_names & set(files)):
        text = _read_text(files.get(name))
        for pattern in _SECRET_PATTERNS:
            if pattern.search(text):
                findings.append(f"possible credential or signed-URL leak in {name}")
                break
    return sorted(set(findings))


def build_scorecard(project: Dict[str, Any]) -> Dict[str, Any]:
    """Build the v1 scorecard from the real project state and actual deliverables."""
    s = _studio()
    c = _closure()
    pid = str(project.get("project_id") or "")
    files = _project_files(pid)
    req = _loads(project.get("request_json"))
    blueprint = _loads(project.get("blueprint_json"))
    plan = blueprint.get("plan") if isinstance(blueprint.get("plan"), dict) else blueprint
    chapters = _scene_list(blueprint)
    script = _read_text(files.get("script.md")) or _read_text(files.get("transcript.txt"))
    script_words = re.findall(r"\b[\w’'-]+\b", script, flags=re.UNICODE)
    script_word_count = len(script_words)
    title = str(req.get("title") or project.get("title") or "").strip()
    objective = str(req.get("objective") or req.get("topic") or "").strip()
    try:
        requested_duration = float(req.get("duration") or 0)
    except Exception:
        requested_duration = 0.0
    brief_text = " ".join(str(req.get(k) or "") for k in ("title", "objective", "topic")).lower()
    fact_sensitive = any(token in brief_text for token in _FACT_SENSITIVE)
    try:
        truth = c._professional_truth(project, reconcile=True)
    except Exception as exc:
        truth = {
            "verified": False,
            "checks": {},
            "failures": [f"technical truth evaluation unavailable: {type(exc).__name__}"],
            "warnings": [],
        }
    checks = truth.get("checks") if isinstance(truth.get("checks"), dict) else {}
    failures = [str(x) for x in (truth.get("failures") or [])]
    final_path = files.get("final.mp4")
    probe = {}
    streams = []
    video = {}
    audio = {}
    actual_duration = float(checks.get("actual_duration") or 0)
    try:
        if final_path and final_path.is_file():
            probe = s.ffprobe_json(final_path)
            streams = probe.get("streams") or []
            video = next((x for x in streams if x.get("codec_type") == "video"), {})
            audio = next((x for x in streams if x.get("codec_type") == "audio"), {})
            actual_duration = float((probe.get("format") or {}).get("duration") or actual_duration)
    except Exception:
        probe = {}
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    requested_ratio = str(req.get("aspect_ratio") or "16:9").strip()
    ratio_ok = False
    try:
        left, right = (float(x) for x in requested_ratio.replace("/", ":").split(":", 1))
        expected = left / right
        actual = width / height if width > 0 and height > 0 else 0
        ratio_ok = actual > 0 and abs(actual - expected) <= max(0.025, expected * 0.025)
    except Exception:
        ratio_ok = False

    sources_data = _read_json(files.get("sources.json"))
    sources = _source_list(sources_data, blueprint)
    fact_data = _read_json(files.get("fact_check.json"))
    provenance = _read_json(files.get("provenance.json"))
    rights = _read_json(files.get("visual_rights.json"))
    manifest = _read_json(files.get("manifest.json"))
    captions = _srt_metrics(_read_text(files.get("captions.srt")), actual_duration)
    visual_rows = []
    try:
        visual_rows = c._source_assets(project)
    except Exception:
        visual_rows = []
    visual_metrics = c._visual_diversity_metrics(visual_rows, int(checks.get("visual_scene_count") or max(1, len(chapters))))
    fallback_count = int(checks.get("fallback_visual_count") or 0)
    rights_assets = rights.get("assets") if isinstance(rights.get("assets"), list) else []
    unknown_rights = sum(1 for x in rights_assets if str((x or {}).get("rights_state") or "unknown") == "unknown")
    rights_evidence = bool(rights_assets) and unknown_rights == 0
    source_count = int(checks.get("research_sources") or len(sources))
    qc = _loads((_loads(project.get("result_json")).get("quality") or {}))
    if not qc:
        qc = _loads(project.get("result_json")).get("quality") or {}
    ratio_time_ok = requested_duration > 0 and actual_duration > 0 and abs(actual_duration - requested_duration) <= 1.0
    valid_audio = bool(audio) and str(audio.get("codec_name") or "").lower() in {"aac", "mp3"}
    audio_peak = checks.get("audio_peak_db")
    try:
        audio_peak_ok = audio_peak is not None and float(audio_peak) < -0.1 and float(audio_peak) >= -45
    except Exception:
        audio_peak_ok = False
    technical_media_pass = bool(
        final_path and final_path.is_file() and final_path.stat().st_size > 10000
        and video and audio and valid_audio and actual_duration > 0
    )
    scene_count = int(checks.get("visual_scene_count") or len(chapters) or 0)
    source_visual_count = int(checks.get("visual_source_count") or len(visual_rows))
    headings = [str(x.get("heading") or x.get("title") or "").strip() for x in chapters]
    full_script_lower = script.lower()
    beginning = " ".join(script_words[:max(1, min(len(script_words), max(15, int(len(script_words) * 0.25))))]).lower()
    ending = " ".join(script_words[-max(1, min(len(script_words), max(12, int(len(script_words) * 0.20)))):]).lower()
    hook_present = any(marker in beginning for marker in _HOOK_MARKERS)
    closing_present = any(marker in ending for marker in ("in summary", "to sum up", "the takeaway", "remember", "ultimately", "in conclusion", "final thought", "now you", "the key")) or any("conclu" in h.lower() or "takeaway" in h.lower() for h in headings[-2:])
    research_ok = source_count > 0 and bool(sources or (blueprint.get("research") or {}).get("sources"))
    fact_check_safe = bool(fact_data.get("auto_publish_safe")) if isinstance(fact_data, dict) else False
    source_aware = not fact_sensitive or (research_ok and "fact_check.json" in files and fact_check_safe)
    requested_brand = any(str(req.get(k) or "").strip() for k in ("brand_voice", "brand_name", "visual_style", "logo_url"))
    brand_explicit = any(str(req.get(k) or "").strip() for k in ("brand_voice", "brand_name", "visual_style", "logo_url", "tone"))
    critical_failures = []
    if not final_path or not final_path.is_file() or final_path.stat().st_size <= 10000:
        critical_failures.append("final media missing or too small")
    if not video or not audio or not valid_audio or not actual_duration:
        critical_failures.append("media streams or audio are invalid")
    if not checks.get("media_anomaly_scan_ok") or not checks.get("black_frame_ok") or not checks.get("freeze_ok") or not checks.get("audio_clipping_ok"):
        critical_failures.append("objective media anomaly or audio-clipping check failed")
    if not rights_evidence or unknown_rights:
        critical_failures.append("visual rights evidence missing or unknown")
    if fact_sensitive and not source_aware:
        critical_failures.append("factual request lacks adequate source/fact-check evidence")
    if not files.get("provenance.json") or not files.get("provenance.json").is_file():
        critical_failures.append("provenance artifact missing")
    security_findings = _security_scan(files)
    critical_failures.extend(security_findings)
    critical_failures = sorted(set(critical_failures))

    dimensions: Dict[str, Dict[str, Any]] = {}
    # 1: brief alignment and usefulness
    completeness = sum(bool(x) for x in (title, objective, req.get("content_type"), req.get("language"), req.get("aspect_ratio"), requested_duration > 0))
    score = 5 if completeness >= 6 and ratio_time_ok and ratio_ok else 4 if completeness >= 5 and ratio_ok else 3 if completeness >= 3 else 1
    dimensions["brief_alignment"] = _score(score, [
        f"brief_fields_present={completeness}/6",
        f"duration_within_1s={ratio_time_ok}",
        f"requested_aspect_ratio_matches_stream={ratio_ok}",
        f"title_present={bool(title)}; objective_present={bool(objective)}",
    ], "Brief fields are compared with the actual ffprobe duration and stream dimensions.")

    # 2: narrative structure and clarity
    has_script = bool(script.strip())
    if has_script and len(chapters) >= 4 and closing_present:
        score = 5
    elif has_script and len(chapters) >= 3:
        score = 4
    elif has_script and len(chapters) >= 1:
        score = 3
    else:
        score = 1
    dimensions["narrative_structure"] = _score(score, [
        f"script_word_count={script_word_count}", f"planned_scene_or_chapter_count={len(chapters)}",
        f"closing_signal_detected={closing_present}", f"chapter_headings_present={sum(bool(x) for x in headings)}",
    ], "Script file and persisted plan/chapter structure are inspected; narrative taste is not inferred as fact.")

    # 3: hook and opening
    if hook_present and script_word_count >= 40:
        score = 5
    elif hook_present or (script_word_count >= 30 and (headings and any(x for x in headings[:2]))):
        score = 4
    elif script_word_count >= 15:
        score = 3
    elif script_word_count:
        score = 2
    else:
        score = 1
    dimensions["hook_opening"] = _score(score, [
        f"opening_phrase_heuristic_match={hook_present}",
        f"opening_word_count={min(script_word_count, max(15, int(script_word_count * 0.25))) if script_word_count else 0}",
        f"first_two_headings={headings[:2]}",
    ], "Lexical hook indicators are a review aid only; human attention/retention judgement is required.")

    # 4: script quality and factual reliability; n/a for non-factual creative briefs
    dimensions["script_reliability"] = {
        "applicable": bool(fact_sensitive),
        **_score(
            5 if source_aware and script_word_count >= 20 and fact_check_safe else 4 if not fact_sensitive and script_word_count >= 20 else 3 if script_word_count >= 12 else 1,
            [f"script_word_count={script_word_count}", f"fact_sensitive_brief={fact_sensitive}",
             f"research_source_count={source_count}", f"fact_check_auto_publish_safe={fact_check_safe}"],
            "Stored script, sources and fact-check output are inspected. Automated fact-check safety remains an estimate.",
            "For fact-sensitive briefs, a human must examine claim-to-source correspondence and freshness before publishing.",
        ),
    }

    # 5: scene/script relationship
    source_alignment = scene_count > 0 and source_visual_count >= scene_count and len(chapters) >= min(scene_count, 1)
    if source_alignment and scene_count >= 3 and visual_metrics.get("unique_visual_hashes", 0) >= visual_metrics.get("required_unique_visual_hashes", 1):
        score = 5
    elif source_alignment:
        score = 4
    elif scene_count > 0 and (source_visual_count > 0 or chapters):
        score = 3
    else:
        score = 1
    dimensions["scene_script_alignment"] = _score(score, [
        f"rendered_scene_count={scene_count}", f"visual_source_rows={source_visual_count}",
        f"planned_chapters={len(chapters)}", f"source_visual_policy_passed={checks.get('source_visual_policy_passed')}",
    ], "Compares stored plan count, scene metadata, and visual-source records; it does not certify semantic alignment.")

    # 6: visual diversity and continuity
    unique = int(visual_metrics.get("unique_visual_hashes") or 0)
    required_unique = max(1, int(visual_metrics.get("required_unique_visual_hashes") or 1))
    diversity_ratio = min(1.0, unique / required_unique)
    if unique >= required_unique and fallback_count == 0 and checks.get("visual_diversity_ok"):
        score = 5 if unique >= max(required_unique, int(max(1, scene_count) * 0.8)) else 4
    elif unique > 0 and fallback_count == 0:
        score = 3
    else:
        score = 1
    dimensions["visual_diversity"] = _score(score, [
        f"unique_visual_hashes={unique}", f"required_unique_visual_hashes={required_unique}",
        f"fallback_visual_count={fallback_count}", f"visual_diversity_ratio={diversity_ratio:.3f}",
    ], "Hashes the actual scene-source media bytes and reuses the existing rendered-scene diversity threshold.")

    # 7: composition/framing; only objective crop/resolution/anomaly signals are automated
    composition_signals = bool(ratio_ok and width > 0 and height > 0 and checks.get("black_frame_ok") and checks.get("freeze_ok"))
    score = 4 if composition_signals and max(width, height) >= 1280 and min(width, height) >= 720 else 3 if composition_signals else 2 if final_path else 1
    dimensions["composition_framing"] = _score(score, [
        f"width={width}", f"height={height}", f"aspect_ratio_match={ratio_ok}",
        f"black_frame_scan_passed={checks.get('black_frame_ok')}", f"freeze_scan_passed={checks.get('freeze_ok')}",
    ], "Only stream geometry and anomaly scans are machine-checked; typography, color and aesthetic composition need a human review.")

    # 8: duration/pacing
    valid_scene_range = 3 <= scene_count <= 18
    if ratio_time_ok and valid_scene_range and len(chapters) >= 3:
        score = 5
    elif ratio_time_ok and scene_count >= 2:
        score = 4
    elif actual_duration > 0 and scene_count > 0:
        score = 3
    else:
        score = 1
    dimensions["pacing_transitions"] = _score(score, [
        f"requested_duration={requested_duration}", f"actual_duration={actual_duration}",
        f"duration_within_1s={ratio_time_ok}", f"scene_count={scene_count}",
        f"mean_seconds_per_scene={(actual_duration / scene_count) if scene_count else None}",
    ], "Uses actual duration and scene count as pacing proxies; rhythm and transitions still require human viewing.")

    # 9: voice/audio
    if valid_audio and checks.get("audio_stream") and checks.get("audio_clipping_ok") and audio_peak_ok:
        score = 5
    elif valid_audio and checks.get("audio_stream"):
        score = 4
    elif audio:
        score = 2
    else:
        score = 1
    dimensions["voice_audio"] = _score(score, [
        f"audio_codec={audio.get('codec_name')}", f"audio_peak_db={audio_peak}",
        f"audio_peak_in_review_range={audio_peak_ok}", f"professional_voice_present={checks.get('professional_voice_present')}",
    ], "Uses actual stream and loudness metadata; intelligibility, pronunciation, emotion and mix taste need a human listen.")

    # 10: captions/accessibility
    caption_ok = captions["passed"] and bool(checks.get("captions_present"))
    if caption_ok and captions["duration_coverage_ratio"] >= 0.6:
        score = 5
    elif captions["cue_count"] > 0 and not captions["errors"]:
        score = 4
    elif captions["cue_count"] > 0:
        score = 2
    else:
        score = 1
    dimensions["captions_accessibility"] = _score(score, [
        f"caption_cues={captions['cue_count']}", f"timestamp_and_readability_passed={captions['passed']}",
        f"caption_duration_coverage_ratio={captions['duration_coverage_ratio']:.3f}",
        f"caption_errors={captions['errors'][:8]}",
    ], "Parses the actual SRT file, checks timestamp order/overlap/media duration/line lengths; spoken completeness and language quality still need human review.")

    # 11: brand and creator control
    explicit_brand = bool(req.get("brand_name") or req.get("brand_voice") or req.get("logo_url"))
    explicit_style = bool(req.get("visual_style") or req.get("tone"))
    if explicit_brand and explicit_style:
        score = 5
    elif explicit_style:
        score = 4
    else:
        score = 3
    dimensions["brand_creator_control"] = _score(score, [
        f"explicit_brand_identity={explicit_brand}", f"visual_style_or_tone_requested={explicit_style}",
        f"creator_selected_language={bool(req.get('language'))}", f"creator_selected_aspect_ratio={bool(req.get('aspect_ratio'))}",
    ], "Uses the persisted request brief. A high score does not certify every frame matches a brand guide.")

    # 12: rights/provenance/platform
    provenance_ok = bool(provenance) and bool(provenance.get("project_id") or provenance.get("sources") or provenance.get("assets"))
    platform_ok = bool(req.get("aspect_ratio")) and bool(req.get("content_type")) and bool(files.get("manifest.json"))
    if rights_evidence and provenance_ok and platform_ok and not unknown_rights:
        score = 5
    elif rights_assets and provenance_ok:
        score = 4
    elif provenance_ok or rights_assets:
        score = 3
    else:
        score = 1
    dimensions["rights_provenance"] = _score(score, [
        f"rights_asset_count={len(rights_assets)}", f"unknown_rights_count={unknown_rights}",
        f"provenance_present={provenance_ok}", f"manifest_present={bool(files.get('manifest.json'))}",
        f"platform_brief_present={platform_ok}",
    ], "Checks stored source/rights/provenance manifests and request settings; does not make legal advice or grant a license.")

    normalized = []
    for key, title_text, critic in DIMENSIONS:
        value = dimensions[key]
        normalized.append({
            "id": key, "dimension": title_text, "critic_pass": critic,
            "applicable": value.get("applicable", True),
            "score": value["score"] if value.get("applicable", True) else None,
            "scale": value["scale"],
            "evidence": value["evidence"],
            "method": value["method"],
            "limitation": value["limitation"],
        })
    applicable = [x for x in normalized if x["applicable"] and x["score"] is not None]
    avg = round(sum(float(x["score"]) for x in applicable) / len(applicable), 3) if applicable else 0.0
    min_score = min([int(x["score"]) for x in applicable] or [0])
    gate = {
        "average_score": avg,
        "minimum_dimension_score": min_score,
        "required_average": 4.0,
        "required_minimum": 3,
        "critical_failure_count": len(critical_failures),
        "critical_failures": critical_failures,
        "technical_truth_passed": bool(truth.get("verified")),
        "score_threshold_passed": bool(applicable) and avg >= 4.0 and min_score >= 3,
        "eligible_for_human_approval": bool(applicable) and avg >= 4.0 and min_score >= 3 and not critical_failures and bool(truth.get("verified")),
        "publish_ready": False,
        "publish_ready_reason": "Human approval is required for the current exact artifact fingerprint; an automated score never auto-approves publication.",
    }
    security_review = {
        "scope": "Static scan of text/JSON package artifacts for obvious embedded credentials or signed-download URLs.",
        "passed": not security_findings,
        "findings": security_findings,
        "limitations": "This is not a full application security audit and cannot prove the absence of every possible secret leak.",
    }
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "project_id": pid,
        "project_status": project.get("status"),
        "content_fingerprint": _fingerprint(project, files),
        "generated_at_epoch": int(project.get("updated_at") or project.get("created_at") or 0),
        "scoring_policy": {
            "scale": {"1": "critical weakness", "2": "weak", "3": "adequate but needs review", "4": "strong observable evidence", "5": "strong evidence across the applicable checks"},
            "thresholds": {"average_at_least": 4.0, "no_applicable_dimension_below": 3, "critical_failures_allowed": 0},
            "automated_judgements_are_not_human_taste": True,
            "automatic_publish_approval": False,
        },
        "brief": {
            "title": title, "objective": objective, "duration_seconds": requested_duration,
            "aspect_ratio": requested_ratio, "language": req.get("language"),
            "audience": req.get("audience"), "tone": req.get("tone"),
            "visual_style": req.get("visual_style"), "content_type": req.get("content_type"),
        },
        "media": {
            "final_mp4_present": bool(final_path and final_path.is_file()),
            "actual_duration_seconds": actual_duration,
            "width": width, "height": height,
            "video_codec": video.get("codec_name"), "audio_codec": audio.get("codec_name"),
            "script_word_count": script_word_count, "chapter_count": len(chapters),
            "source_count": source_count, "caption_metrics": captions,
        },
        "dimensions": normalized,
        "critique_passes": {
            "narrative_editorial": {
                "type": "deterministic rule-based critique, not an independent LLM or human critic",
                "dimension_ids": [x[0] for x in DIMENSIONS if x[2] == "narrative"],
                "conclusion": "review the actual script and claims; use evidence above to accept or repair",
            },
            "rendered_media_accessibility": {
                "type": "independent deterministic media/metadata checks, not an independent human or vision-model critic",
                "dimension_ids": [x[0] for x in DIMENSIONS if x[2] == "media"],
                "conclusion": "watch the actual video and listen to the actual mix; metadata does not prove taste",
            },
        },
        "security_review": security_review,
        "technical_truth": {"verified": bool(truth.get("verified")), "failures": failures, "warnings": truth.get("warnings") or []},
        "release_gate": gate,
        "human_approval": {"state": "pending", "required": True, "publish_ready": False},
        "truthful": True,
    }


def _ensure_review_table() -> None:
    s = _studio()
    with s.DB_LOCK, s._connect() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS aii_editorial_reviews (
                review_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                content_fingerprint TEXT NOT NULL,
                decision TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                scorecard_version TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_aii_editorial_reviews_project
                ON aii_editorial_reviews(project_id, created_at DESC);
        """)


def _review_rows(project_id: str, user_id: str) -> List[Dict[str, Any]]:
    s = _studio()
    with s.DB_LOCK, s._connect() as db:
        rows = db.execute(
            "SELECT review_id, project_id, user_id, content_fingerprint, decision, note, scorecard_version, created_at FROM aii_editorial_reviews WHERE project_id=? AND user_id=? ORDER BY created_at DESC",
            (project_id, user_id),
        ).fetchall()
    return [dict(row) for row in rows]


def _review_state(rows: List[Dict[str, Any]], fingerprint: str) -> Dict[str, Any]:
    if not rows:
        return {"state": "pending", "required": True, "publish_ready": False, "latest_review": None}
    latest = rows[0]
    if str(latest.get("content_fingerprint") or "") != fingerprint:
        return {
            "state": "stale",
            "required": True,
            "publish_ready": False,
            "latest_review": latest,
        }
    state = "approved" if latest.get("decision") == "approve" else "rejected"
    return {
        "state": state,
        "required": state != "approved",
        "publish_ready": state == "approved",
        "latest_review": latest,
    }


def _persist_scorecard(project: Dict[str, Any], scorecard: Dict[str, Any], review_state: Dict[str, Any]) -> Dict[str, Any]:
    s = _studio()
    pid = str(project.get("project_id") or "")
    files = _project_files(pid)
    path = s._project_dir(pid) / "editorial_scorecard.json"
    scorecard = dict(scorecard)
    scorecard["human_approval"] = {
        "state": review_state.get("state") or "pending",
        "required": bool(review_state.get("required", True)),
        "publish_ready": bool(review_state.get("publish_ready", False)),
        "latest_review": review_state.get("latest_review"),
    }
    encoded = json.dumps(scorecard, ensure_ascii=False, sort_keys=True, indent=2, default=str)
    before = _read_text(path)
    wrote = before != encoded
    if wrote:
        tmp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
        tmp.write_text(encoded, encoding="utf-8")
        tmp.replace(path)
        try:
            s.register_artifact(pid, path, "application/json", {
                "kind": "editorial_scorecard", "schema": SCHEMA,
                "content_fingerprint": scorecard.get("content_fingerprint"),
            })
        except Exception:
            pass
    durable_result = {"status": "unchanged" if not wrote else "not_attempted", "truthful": True}
    if wrote:
        try:
            from ai3704_storage_fabric import _persist_path
            user_id = str(project.get("user_id") or "")
            if user_id:
                durable_result = _persist_path(
                    path, user_id, path.name, "application/json",
                    {"project_id": pid, "kind": "editorial_scorecard", "scorecard_version": VERSION},
                )
        except Exception as exc:
            durable_result = {"status": "not_persisted", "reason": type(exc).__name__, "truthful": True}
    return {"path": path.name, "available": path.is_file(), "size_bytes": path.stat().st_size if path.is_file() else 0, "durable_storage": durable_result, "changed": wrote}


def _owned_project(project_id: str, request: Request, response: Response) -> Tuple[str, Dict[str, Any]]:
    s = _studio()
    user_id = str(s._get_user_id(request))
    s._set_session(response, request, user_id)
    p = s._get_project(project_id)
    if not p or str(p.get("user_id") or "") != user_id:
        raise HTTPException(404, "project not found")
    return user_id, p


def _decorate(scorecard: Dict[str, Any], review_state: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(scorecard)
    out["human_approval"] = {
        "state": review_state.get("state") or "pending",
        "required": bool(review_state.get("required", True)),
        "publish_ready": bool(review_state.get("publish_ready", False)),
        "latest_review": review_state.get("latest_review"),
    }
    out["release_gate"] = dict(scorecard.get("release_gate") or {})
    out["release_gate"]["publish_ready"] = bool(review_state.get("publish_ready", False))
    out["release_gate"]["human_approval_state"] = review_state.get("state") or "pending"
    return out


def install() -> None:
    """Add the review UI to the actual main:app-served Creator Studio shell."""
    global _INSTALLED_UI
    if _INSTALLED_UI:
        return
    s = _studio()
    html = getattr(s, "CREATOR_STUDIO_UI", "")
    if not html or "aii-editorial-review-3625" in html:
        _INSTALLED_UI = True
        return
    css_js = r"""
<style id="aii-editorial-review-3625">
#aiiEditorialPanel{margin:14px 0;padding:14px;border:1px solid #334155;border-radius:14px;background:#0c1420;color:#e5edf7}
#aiiEditorialPanel .aiiDim{border-top:1px solid #243143;padding:9px 0;display:grid;grid-template-columns:minmax(150px,1fr) 48px minmax(200px,2fr);gap:10px}
#aiiEditorialPanel .aiiScore{font-weight:800;text-align:center}
#aiiEditorialPanel .aiiSmall{font-size:12px;color:#b9c6d8;white-space:pre-wrap}
#aiiEditorialPanel button{cursor:pointer}
@media(max-width:760px){#aiiEditorialPanel .aiiDim{grid-template-columns:1fr 40px}#aiiEditorialPanel .aiiEvidence{grid-column:1/-1}}
</style>
<script>
(function(){
  if(window.__aiiEditorialReview3625)return;
  window.__aiiEditorialReview3625=true;
  var activeId='';
  function escHtml(v){if(typeof esc==='function')return esc(String(v==null?'':v));return String(v==null?'':v).replace(/[&<>"]/g,function(c){return({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]);});}
  function findHost(){
    return document.getElementById('aiiTruthPanel')||document.getElementById('content')||document.body;
  }
  async function load(id){
    activeId=String(id||'');
    if(!activeId)return;
    try{
      var data=await api('/infinity/studio/project/'+encodeURIComponent(activeId)+'/editorial-scorecard');
      var q=data.scorecard||{};
      var dims=q.dimensions||[];
      var gate=q.release_gate||{};
      var review=q.human_approval||{};
      var rows=dims.map(function(d){
        return '<div class="aiiDim"><b>'+escHtml(d.dimension)+(d.applicable?'':' <span class="aiiSmall">(N/A)</span>')+'</b><span class="aiiScore">'+(d.applicable?escHtml(d.score)+'/5':'—')+'</span><div class="aiiEvidence"><div class="aiiSmall">'+escHtml((d.evidence||[]).join(' · '))+'</div><div class="aiiSmall">'+escHtml(d.method||'')+'</div></div></div>';
      }).join('');
      var status=gate.eligible_for_human_approval?'Eligible for human review':'Repair required before approval';
      var approveDisabled=!(gate.eligible_for_human_approval);
      var panel=document.getElementById('aiiEditorialPanel');
      if(!panel){panel=document.createElement('section');panel.id='aiiEditorialPanel';findHost().appendChild(panel);}
      panel.innerHTML='<div style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap"><b>Editorial scorecard '+escHtml(q.version||'')+'</b><span>'+escHtml(review.state||'pending')+'</span></div>'+
        '<p class="aiiSmall">Automated evidence review only. It does not guarantee human-level taste. Publication requires explicit human approval tied to this exact artifact fingerprint.</p>'+
        '<div class="aiiSmall">Automated average: '+escHtml(gate.average_score)+'/5 · minimum dimension: '+escHtml(gate.minimum_dimension_score)+'/5 · critical failures: '+escHtml(gate.critical_failure_count)+'</div>'+
        '<div class="aiiSmall">'+escHtml(status)+'</div>'+
        rows+
        '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px">'+
          '<button class="btn primary" id="aiiEditorialApprove" '+(approveDisabled?'disabled':'')+'>Approve reviewed version</button>'+
          '<button class="btn" id="aiiEditorialReject">Reject / request repair</button>'+
          '<a class="btn" target="_blank" rel="noopener" href="/infinity/studio/project/'+encodeURIComponent(activeId)+'/asset/editorial_scorecard.json">Open scorecard JSON</a>'+
        '</div><div class="aiiSmall" style="margin-top:8px">Fingerprint: '+escHtml(q.content_fingerprint||'')+'</div>'+
        '<div id="aiiEditorialActionResult" class="aiiSmall" style="margin-top:8px"></div>';
      document.getElementById('aiiEditorialApprove').onclick=function(){submit('approve',q.content_fingerprint);};
      document.getElementById('aiiEditorialReject').onclick=function(){submit('reject',q.content_fingerprint);};
    }catch(e){}
  }
  async function submit(decision,fingerprint){
    if(!activeId)return;
    var note='';
    try{note=window.prompt(decision==='approve'?'Optional human review note (what was checked)':'Describe required repairs or rejection reason','')||'';}catch(e){}
    try{
      var result=await api('/infinity/studio/project/'+encodeURIComponent(activeId)+'/editorial-review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({decision:decision,note:note,expected_fingerprint:fingerprint})});
      var box=document.getElementById('aiiEditorialActionResult');
      if(box)box.textContent='Saved review: '+String((result.review||{}).decision||decision)+'. Reloading evidence…';
      await load(activeId);
    }catch(e){
      var err=document.getElementById('aiiEditorialActionResult');
      if(err)err.textContent='Review not saved: '+String(e.message||e);
    }
  }
  var oldOpen=window.openProject;
  if(typeof oldOpen==='function'){
    window.openProject=async function(id){
      var result=await oldOpen.apply(this,arguments);
      try{await load(id);}catch(e){}
      return result;
    };
  }
  window.aiiLoadEditorialReview3625=load;
})();
</script>
"""
    marker = "</body>"
    s.CREATOR_STUDIO_UI = html.replace(marker, css_js + marker, 1) if marker in html else html + css_js
    s.CREATOR_STUDIO_2030_UI = getattr(s, "CREATOR_STUDIO_2030_UI", html)
    if "aii-editorial-review-3625" not in s.CREATOR_STUDIO_2030_UI:
        s.CREATOR_STUDIO_2030_UI = s.CREATOR_STUDIO_2030_UI.replace(marker, css_js + marker, 1) if marker in s.CREATOR_STUDIO_2030_UI else s.CREATOR_STUDIO_2030_UI + css_js
    _INSTALLED_UI = True


def register(app) -> None:
    from fastapi.responses import JSONResponse

    _ensure_review_table()

    @app.get("/infinity/studio/project/{project_id}/editorial-scorecard")
    def get_editorial_scorecard(project_id: str, request: Request, response: Response):
        user_id, project = _owned_project(project_id, request, response)
        card = build_scorecard(project)
        rows = _review_rows(project_id, user_id)
        state = _review_state(rows, str(card["content_fingerprint"]))
        persisted = _persist_scorecard(project, card, state)
        return JSONResponse({
            "scorecard": _decorate(card, state),
            "scorecard_artifact": persisted,
            "truthful": True,
        })

    @app.get("/infinity/studio/project/{project_id}/editorial-reviews")
    def get_editorial_reviews(project_id: str, request: Request, response: Response):
        user_id, _ = _owned_project(project_id, request, response)
        rows = _review_rows(project_id, user_id)
        return {"project_id": project_id, "reviews": rows, "truthful": True}

    @app.post("/infinity/studio/project/{project_id}/editorial-review")
    async def submit_editorial_review(project_id: str, request: Request, response: Response):
        s = _studio()
        user_id, project = _owned_project(project_id, request, response)
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        decision = str((payload or {}).get("decision") or "").strip().lower()
        note = str((payload or {}).get("note") or "").strip()[:4000]
        expected = str((payload or {}).get("expected_fingerprint") or "").strip()
        if decision not in {"approve", "reject"}:
            raise HTTPException(422, "decision must be approve or reject")
        card = build_scorecard(project)
        current = str(card.get("content_fingerprint") or "")
        if not expected or expected != current:
            raise HTTPException(409, "project content changed since this scorecard was viewed; reload the scorecard before reviewing")
        gate = card.get("release_gate") or {}
        if decision == "approve" and not gate.get("eligible_for_human_approval"):
            raise HTTPException(409, {
                "message": "approval blocked until technical truth and the documented score thresholds pass with zero critical failures",
                "release_gate": gate,
            })
        review = {
            "review_id": "edrev_" + uuid.uuid4().hex,
            "project_id": project_id,
            "user_id": user_id,
            "content_fingerprint": current,
            "decision": decision,
            "note": note,
            "scorecard_version": VERSION,
            "created_at": time.time(),
        }
        with s.DB_LOCK, s._connect() as db:
            db.execute(
                "INSERT INTO aii_editorial_reviews(review_id,project_id,user_id,content_fingerprint,decision,note,scorecard_version,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (review["review_id"], project_id, user_id, current, decision, note, VERSION, review["created_at"]),
            )
        try:
            s.audit_event(project_id, "editorial_human_review", {
                "decision": decision,
                "review_id": review["review_id"],
                "content_fingerprint": current,
                "scorecard_version": VERSION,
            })
        except Exception:
            pass
        rows = _review_rows(project_id, user_id)
        state = _review_state(rows, current)
        persisted = _persist_scorecard(project, card, state)
        return JSONResponse({
            "review": review,
            "human_approval": state,
            "scorecard_artifact": persisted,
            "truthful": True,
        })
