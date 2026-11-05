from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

from ai_infinity.site_package import build_static_website_package


def _write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_static_site_export_packages_media_captions_sources_and_rights(tmp_path):
    media = _write(tmp_path / "final.mp4", b"real-media-bytes")
    poster = _write(tmp_path / "thumbnail.jpg", b"poster-bytes")
    audio = _write(tmp_path / "audio_master.mp3", b"audio-bytes")
    captions = _write(
        tmp_path / "captions.srt",
        b"1\n00:00:00,000 --> 00:00:02,000\nA clear first caption.\n\n"
        b"2\n00:00:02,000 --> 00:00:04,000\nA second caption.\n",
    )
    article = _write(tmp_path / "article.md", b"# A researched article\n\nUseful editorial copy.\n")
    _write(tmp_path / "rights_manifest.json", b'{"assets":[{"rights_status":"CLEARED"}]}\n')
    _write(tmp_path / "provenance.json", b'{"sources":[{"title":"Verified source"}]}\n')
    _write(tmp_path / "fact_check.json", b'{"claims_reviewed":2}\n')
    _write(tmp_path / "seo.json", b'{"title":"Export test"}\n')
    _write(tmp_path / "social_campaign.json", b'{"platforms":{"YouTube":{}}}\n')
    _write(tmp_path / "accessibility.json", b'{"alt_text":{"video":"An example"}}\n')
    _write(tmp_path / "platform_manifest.json", b'{"YouTube":{"video":"final.mp4"}}\n')

    result = build_static_website_package(
        tmp_path,
        project_id="studio_test_123",
        title='A "quoted" title <script>alert(1)</script>',
        description="A practical, researched production.",
        chapters=[
            {"heading": "First chapter", "narration": "A useful explanation."},
            {"heading": "Second chapter", "narration": "A concrete example."},
        ],
        research={
            "sources": [
                {
                    "title": "W3C accessibility guidance",
                    "url": "https://www.w3.org/WAI/",
                    "source_publisher": "W3C",
                    "retrieved_at": "2026-11-04T00:00:00Z",
                },
                {"title": "Unsafe", "url": "javascript:alert(1)"},
                {"title": "Credentials", "url": "https://user:secret@example.org/private"},
            ]
        },
        master_video=media,
        thumbnail=poster,
        audio_master=audio,
        captions=captions,
        article=article,
        duration_seconds=20,
        language="English",
    )

    archive = result["package"]
    assert archive.is_file() and archive.stat().st_size > 100
    assert result["verification"]["status"] == "PASS"
    assert result["verification"]["archive_integrity"] is True
    assert result["verification"]["publication_status"] == "NOT_PUBLISHED"
    assert result["verification"]["archive_sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()

    with zipfile.ZipFile(archive) as package:
        assert package.testzip() is None
        names = set(package.namelist())
        expected = {
            "ai-infinity-site/index.html",
            "ai-infinity-site/styles.css",
            "ai-infinity-site/manifest.webmanifest",
            "ai-infinity-site/README.md",
            "ai-infinity-site/site-manifest.json",
            "ai-infinity-site/source-notes.json",
            "ai-infinity-site/assets/feature-film.mp4",
            "ai-infinity-site/assets/poster.jpg",
            "ai-infinity-site/assets/programme-audio.mp3",
            "ai-infinity-site/assets/captions.vtt",
            "ai-infinity-site/assets/article.md",
            "ai-infinity-site/evidence/rights_manifest.json",
            "ai-infinity-site/evidence/provenance.json",
            "ai-infinity-site/evidence/fact_check.json",
        }
        assert expected <= names
        page = package.read("ai-infinity-site/index.html").decode("utf-8")
        vtt = package.read("ai-infinity-site/assets/captions.vtt").decode("utf-8")
        source_notes = json.loads(package.read("ai-infinity-site/source-notes.json"))
        manifest = json.loads(package.read("ai-infinity-site/site-manifest.json"))

    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<video controls" in page and "<track kind=\"captions\"" in page
    assert "<audio controls" in page
    assert 'rel="noopener noreferrer"' in page
    assert "prefers-reduced-motion" in (tmp_path / "ai-infinity-site" / "styles.css").read_text()
    assert "WEBVTT" in vtt and "00:00:00.000 --> 00:00:02.000" in vtt
    assert source_notes["source_count"] == 1
    assert source_notes["sources"][0]["url"] == "https://www.w3.org/WAI/"
    assert manifest["deployment"]["status"] == "NOT_PUBLISHED"
    assert {row["path"] for row in manifest["files"]} >= {
        "index.html", "styles.css", "assets/feature-film.mp4", "assets/captions.vtt",
        "evidence/rights_manifest.json", "evidence/provenance.json",
    }
    root_manifest = json.loads(result["manifest"].read_text(encoding="utf-8"))
    assert root_manifest["export_package"]["sha256"] == result["verification"]["archive_sha256"]


def test_static_site_export_rejects_empty_editorial_content(tmp_path):
    try:
        build_static_website_package(
            tmp_path,
            project_id="studio_empty",
            title="Empty",
            description="No chapters",
            chapters=[],
            research={"sources": []},
            master_video=None,
            thumbnail=None,
            audio_master=None,
            captions=None,
            article=None,
            duration_seconds=0,
        )
    except ValueError as exc:
        assert "at least one non-empty editorial chapter" in str(exc)
    else:
        raise AssertionError("an empty website was incorrectly packaged")
