from __future__ import annotations

"""Evidence-linked portable static website exports for Creator Studio projects."""
import hashlib
import html
import json
import re
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_http_url(value: Any) -> str:
    candidate = str(value or "").strip()
    try:
        parsed = urlsplit(candidate)
    except Exception:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return ""
    return candidate


def _vtt_from_srt(text: str) -> str:
    rows = ["WEBVTT", ""]
    for block in re.split(r"\n\s*\n", str(text or "").strip()):
        lines = [line.strip("\ufeff \t\r") for line in block.splitlines() if line.strip()]
        timing_index = next((index for index, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            continue
        timing = lines[timing_index].replace(",", ".")
        if not re.match(r"^\d{2,}:\d{2}:\d{2}\.\d{3}\s+-->\s+\d{2,}:\d{2}:\d{2}\.\d{3}$", timing):
            continue
        cue = [timing]
        cue.extend(line for line in lines[timing_index + 1:] if not re.fullmatch(r"\d+", line))
        if len(cue) > 1:
            rows.extend(cue)
            rows.append("")
    return "\n".join(rows).rstrip() + "\n"


def _iso_duration(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"PT{hours}H{minutes}M{secs}S"


def build_static_website_package(
    outdir: Path,
    *,
    project_id: str,
    title: str,
    description: str,
    chapters: list[dict[str, Any]],
    research: dict[str, Any],
    master_video: Path | None,
    thumbnail: Path | None,
    audio_master: Path | None,
    captions: Path | None,
    article: Path | None,
    duration_seconds: float,
    language: str = "English",
) -> dict[str, Any]:
    """Create a responsive, accessible static site ZIP with local media and source records."""
    root = Path(outdir).resolve()
    site = root / "ai-infinity-site"
    assets = site / "assets"
    shutil.rmtree(site, ignore_errors=True)
    assets.mkdir(parents=True, exist_ok=True)
    copied: list[dict[str, Any]] = []

    def copy_asset(source: Path | None, name: str) -> str:
        if source is None:
            return ""
        source = Path(source)
        if not source.is_file() or source.stat().st_size <= 0:
            return ""
        destination = assets / name
        shutil.copy2(source, destination)
        copied.append({
            "path": f"assets/{name}",
            "size_bytes": destination.stat().st_size,
            "sha256": _sha256(destination),
        })
        return f"assets/{name}"

    video_uri = copy_asset(master_video, "feature-film.mp4")
    poster_uri = copy_asset(thumbnail, "poster.jpg")
    audio_uri = copy_asset(audio_master, "programme-audio.mp3")
    article_uri = copy_asset(article, "article.md")
    captions_uri = ""
    if captions is not None and Path(captions).is_file():
        vtt_path = assets / "captions.vtt"
        vtt_path.write_text(
            _vtt_from_srt(Path(captions).read_text(encoding="utf-8-sig", errors="replace")),
            encoding="utf-8",
        )
        copied.append({
            "path": "assets/captions.vtt",
            "size_bytes": vtt_path.stat().st_size,
            "sha256": _sha256(vtt_path),
        })
        captions_uri = "assets/captions.vtt"

    source_rows = []
    for row in (research.get("sources") or [])[:40]:
        if not isinstance(row, dict) or row.get("error"):
            continue
        url = _safe_http_url(row.get("url"))
        if not url:
            continue
        source_rows.append({
            "title": str(row.get("title") or row.get("publisher") or url)[:240],
            "url": url,
            "publisher": str(row.get("source_publisher") or row.get("source") or "")[:120],
            "retrieved_at": str(row.get("retrieved_at") or "")[:80],
        })
    source_document = {
        "project_id": str(project_id),
        "source_count": len(source_rows),
        "sources": source_rows,
        "truthful": True,
    }
    (site / "source-notes.json").write_text(
        json.dumps(source_document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    safe_title = html.escape(str(title or "AI Infinity production")[:180], quote=True)
    safe_description = html.escape(str(description or title or "")[:500], quote=True)
    chapter_sections = []
    for index, chapter in enumerate(chapters or [], 1):
        heading = html.escape(str(chapter.get("heading") or f"Chapter {index}")[:240], quote=True)
        narration = html.escape(str(chapter.get("narration") or "")[:12000], quote=True)
        if narration:
            chapter_sections.append(
                f'<section class="chapter" id="chapter-{index}">'
                f'<p class="eyebrow">CHAPTER {index:02d}</p>'
                f'<h2>{heading}</h2><p>{narration}</p></section>'
            )
    if not chapter_sections:
        raise ValueError("website package requires at least one non-empty editorial chapter")

    language_tag = {
        "english": "en", "urdu": "ur", "hindi": "hi", "pashto": "ps", "dari": "fa",
        "farsi": "fa", "arabic": "ar",
    }.get(str(language or "English").strip().lower(), "en")
    if video_uri:
        video_markup = (
            '<figure class="feature-media"><video controls playsinline preload="metadata"'
            + (f' poster="{poster_uri}"' if poster_uri else "")
            + ' aria-label="Featured production video">'
            + f'<source src="{video_uri}" type="video/mp4">'
            + (f'<track kind="captions" src="{captions_uri}" srclang="{language_tag}" label="Captions" default>' if captions_uri else "")
            + "Your browser does not support HTML video.</video>"
            + '<figcaption>Play the finished film. Captions are included when available.</figcaption></figure>'
        )
    else:
        video_markup = '<p class="notice">No finished video master was included in this export.</p>'

    audio_markup = (
        f'<section class="audio-panel"><p class="eyebrow">AUDIO EDITION</p>'
        f'<h2>Listen to the programme</h2><audio controls preload="metadata" src="{audio_uri}">'
        'Your browser does not support HTML audio.</audio></section>'
        if audio_uri else ""
    )
    source_markup = (
        "\n".join(
            '<li><a href="' + html.escape(row["url"], quote=True)
            + '" rel="noopener noreferrer">' + html.escape(row["title"], quote=True)
            + "</a>" + (f' <span class="source-publisher">{html.escape(row["publisher"])}</span>' if row["publisher"] else "")
            + "</li>"
            for row in source_rows
        )
        if source_rows else '<li>No externally retrieved source records were available for this project.</li>'
    )
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    schema = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": str(title or "AI Infinity production")[:180],
        "description": str(description or title or "")[:500],
        "datePublished": now,
        "inLanguage": language_tag,
        "isAccessibleForFree": True,
    }
    if video_uri:
        schema["video"] = {
            "@type": "VideoObject",
            "name": str(title or "AI Infinity production")[:180],
            "description": str(description or title or "")[:500],
            "contentUrl": video_uri,
            "thumbnailUrl": [poster_uri] if poster_uri else [],
            "duration": _iso_duration(duration_seconds),
        }
    schema_text = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    schema_text = schema_text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    article_link = (
        '<p class="download-link"><a href="' + article_uri + '" download>Download the article (Markdown)</a></p>'
        if article_uri else ""
    )
    poster_markup = f'<link rel="preload" as="image" href="{poster_uri}">' if poster_uri else ""

    css = r"""
:root{color-scheme:dark;--bg:#0b1018;--surface:#121c29;--surface2:#172435;--ink:#f3f6fb;--muted:#b7c3d1;--line:#34475d;--accent:#bce77e;--max:1120px}
*{box-sizing:border-box}
html{scroll-behavior:smooth;background:var(--bg)}
body{margin:0;background:radial-gradient(ellipse at 15% 0%,#1b3043 0,transparent 42%),var(--bg);color:var(--ink);font:17px/1.7 Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
a{color:#d0f19f;text-underline-offset:4px}
a:hover{color:#fff}
a:focus-visible,video:focus-visible,audio:focus-visible{outline:3px solid var(--accent);outline-offset:4px}
.skip{position:absolute;left:-9999px;top:8px;background:#fff;color:#111;padding:10px 16px;z-index:10}
.skip:focus{left:8px}
.wrap{width:min(calc(100% - 40px),var(--max));margin:auto}
header{padding:28px 0 76px;border-bottom:1px solid var(--line)}
.nav{display:flex;justify-content:space-between;align-items:center;gap:20px;padding:4px 0 62px}
.brand{font-size:13px;font-weight:800;letter-spacing:.16em;text-transform:uppercase;color:var(--accent)}
.nav a{font-size:13px;text-decoration:none;color:var(--muted)}
.eyebrow{font-size:11px;font-weight:800;letter-spacing:.16em;color:var(--accent);text-transform:uppercase;margin:0 0 12px}
h1{max-width:950px;margin:0;font-size:clamp(2.7rem,8vw,6.6rem);line-height:.99;letter-spacing:-.055em}
.lead{max-width:780px;margin:26px 0 0;color:var(--muted);font-size:clamp(1.05rem,2vw,1.35rem)}
main{padding:54px 0 80px}
.feature-media{margin:0 0 56px;padding:12px;background:var(--surface);border:1px solid var(--line);border-radius:18px}
video{display:block;width:100%;height:auto;max-height:72vh;background:#05080d;border-radius:11px}
figcaption{padding:9px 5px 0;color:var(--muted);font-size:13px}
.grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(230px,.34fr);gap:clamp(24px,5vw,72px);align-items:start}
article{min-width:0}
.chapter{padding:30px 0;border-top:1px solid var(--line)}
.chapter h2,.audio-panel h2,.sources h2{font-size:clamp(1.5rem,3vw,2.3rem);line-height:1.15;letter-spacing:-.03em;margin:0 0 14px}
.chapter p:not(.eyebrow){margin:0;color:#e2e8f0;max-width:760px;white-space:pre-line}
aside{position:sticky;top:24px;padding:22px;border:1px solid var(--line);border-radius:14px;background:var(--surface)}
aside h2{font-size:15px;margin:0 0 12px}
aside ol{padding-left:20px;margin:0}
aside li{margin:7px 0;font-size:13px}
.audio-panel,.sources{margin-top:52px;padding:26px;border:1px solid var(--line);border-radius:16px;background:var(--surface)}
audio{width:100%;max-width:680px}
.sources ol{padding-left:22px}
.sources li{margin:12px 0;overflow-wrap:anywhere}
.source-publisher{color:var(--muted);font-size:12px}
.download-link{margin:20px 0}
footer{border-top:1px solid var(--line);padding:30px 0 54px;color:var(--muted);font-size:13px}
.notice{padding:16px;background:var(--surface2);border-radius:10px;color:var(--muted)}
@media(max-width:820px){.grid{grid-template-columns:1fr}aside{position:static;order:-1}header{padding-bottom:48px}.nav{padding-bottom:44px}}
@media(max-width:560px){body{font-size:16px}.wrap{width:min(calc(100% - 28px),var(--max))}.feature-media{padding:7px}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}*,*::before,*::after{animation-duration:.01ms!important;animation-iteration-count:1!important;transition-duration:.01ms!important}}
@media print{body{background:#fff;color:#111}.brand,.eyebrow{color:#31551a}a{color:#111}aside{position:static}.feature-media,.audio-panel{break-inside:avoid}footer{color:#444}}
"""
    (site / "styles.css").write_text(css.strip() + "\n", encoding="utf-8")
    (site / "manifest.webmanifest").write_text(
        json.dumps({
            "name": str(title or "AI Infinity")[:120],
            "short_name": "AI Infinity",
            "start_url": "./",
            "display": "standalone",
            "background_color": "#0b1018",
            "theme_color": "#0b1018",
            "description": str(description or title or "")[:240],
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    toc = "\n".join(
        f'<li><a href="#chapter-{index}">{html.escape(str(chapter.get("heading") or f"Chapter {index}"), quote=True)}</a></li>'
        for index, chapter in enumerate(chapters or [], 1) if str(chapter.get("narration") or "").strip()
    )
    document = f"""<!doctype html>
<html lang="{language_tag}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark">
<meta name="description" content="{safe_description}">
<meta name="robots" content="index,follow">
<meta property="og:type" content="article">
<meta property="og:title" content="{safe_title}">
<meta property="og:description" content="{safe_description}">
<meta property="og:image" content="{poster_uri}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{safe_title}">
<meta name="twitter:description" content="{safe_description}">
<link rel="stylesheet" href="styles.css">
<link rel="manifest" href="manifest.webmanifest">
{poster_markup}
<title>{safe_title}</title>
<script type="application/ld+json">{schema_text}</script>
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<header><div class="wrap">
<nav class="nav" aria-label="Primary"><span class="brand">AI Infinity · Creator Edition</span><a href="#sources">Sources &amp; notes</a></nav>
<p class="eyebrow">A researched creative production</p>
<h1>{safe_title}</h1>
<p class="lead">{safe_description}</p>
</div></header>
<main id="main" class="wrap">
{video_markup}
<div class="grid">
<article aria-label="Editorial content">
{"".join(chapter_sections)}
{article_link}
</article>
<aside aria-label="On this page"><h2>In this production</h2><ol>{toc}</ol></aside>
</div>
{audio_markup}
<section class="sources" id="sources"><p class="eyebrow">Research record</p><h2>Sources &amp; references</h2><ol>{source_markup}</ol></section>
</main>
<footer><div class="wrap">Exported from AI Infinity. Content, source records, and included media remain subject to their respective rights and review requirements.</div></footer>
</body></html>
"""
    index_path = site / "index.html"
    index_path.write_text(document, encoding="utf-8")
    generated = [index_path, site / "styles.css", site / "manifest.webmanifest", site / "source-notes.json"]
    generated += [path for path in assets.rglob("*") if path.is_file()]
    manifest_rows = []
    for path in sorted(generated):
        manifest_rows.append({
            "path": path.relative_to(site).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": _sha256(path),
        })
    site_manifest = {
        "schema": "ai-infinity.static-site-package.v1",
        "project_id": str(project_id),
        "title": str(title or "")[:180],
        "entrypoint": "index.html",
        "stylesheet": "styles.css",
        "generated_at_utc": now,
        "asset_count": len(copied),
        "research_source_count": len(source_rows),
        "files": manifest_rows,
        "deployment": {
            "status": "NOT_PUBLISHED",
            "target": "any standards-compliant static HTTPS host",
            "custom_domain_supplied": False,
            "authorization_required": True,
        },
        "accessibility": {
            "semantic_landmarks": True,
            "skip_link": True,
            "keyboard_focus_indicators": True,
            "reduced_motion": True,
            "native_media_controls": True,
            "captions_included": bool(captions_uri),
            "automated_accessibility_audit": "not_claimed",
        },
        "truthful": True,
    }
    (site / "site-manifest.json").write_text(
        json.dumps(site_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    readme = """# AI Infinity static website export

This is an exportable static website, not a published or hosted site.

## Open locally
Open index.html in a modern browser. Keep the assets folder beside it.

## Deploy
Upload the contents of this folder to a static HTTPS host that you control. Configure the domain and hosting provider there; AI Infinity has not published this package or claimed a live URL.

## Included
- Responsive editorial page with semantic landmarks and keyboard-visible focus.
- Reduced-motion support and native video/audio controls.
- Local video, poster, audio, and WebVTT captions when those source artifacts exist.
- Research links and a machine-readable source record.
- A SHA-256 manifest for every packaged file.

## Rights and review
Review source-notes.json, the media licences, and project rights/review records before redistribution. Attribution does not itself grant permission to reuse an asset. No external tracking scripts, remote fonts, or third-party JavaScript are included.
"""
    (site / "README.md").write_text(readme, encoding="utf-8")
    root_manifest = root / "website_manifest.json"
    root_manifest.write_text(
        json.dumps(site_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    root_readme = root / "website_README.md"
    root_readme.write_text(readme, encoding="utf-8")
    archive = root / "website.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in sorted(site.rglob("*")):
            if not path.is_file():
                continue
            archive_name = (Path("ai-infinity-site") / path.relative_to(site)).as_posix()
            compression = zipfile.ZIP_STORED if path.suffix.lower() in {".mp4", ".mp3", ".jpg", ".jpeg", ".png"} else zipfile.ZIP_DEFLATED
            bundle.write(path, arcname=archive_name, compress_type=compression)
    with zipfile.ZipFile(archive, "r") as bundle:
        archive_integrity = bundle.testzip() is None
        packaged_names = bundle.namelist()
    verification = {
        "status": "PASS" if archive_integrity and "ai-infinity-site/index.html" in packaged_names and manifest_rows else "FAIL",
        "archive_sha256": _sha256(archive),
        "archive_size_bytes": archive.stat().st_size,
        "entrypoint": "ai-infinity-site/index.html",
        "packaged_file_count": len(packaged_names),
        "source_count": len(source_rows),
        "copied_media": [row["path"] for row in copied],
        "archive_integrity": archive_integrity,
        "publication_status": "NOT_PUBLISHED",
        "truthful": True,
    }
    if verification["status"] != "PASS":
        raise RuntimeError("static website export failed its archive integrity checks")
    return {"package": archive, "manifest": root_manifest, "readme": root_readme, "verification": verification}
