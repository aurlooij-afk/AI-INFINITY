from __future__ import annotations

"""Native editable document and presentation exports for Creator Studio."""
import hashlib
import json
import re
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clean(value: Any, limit: int = 12000) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _source_rows(research: dict[str, Any]) -> list[dict[str, str]]:
    rows = []
    for source in (research.get("sources") or [])[:80]:
        if not isinstance(source, dict) or source.get("error"):
            continue
        url = _clean(source.get("url"), 1200)
        if not url.startswith(("https://", "http://")):
            continue
        rows.append({
            "title": _clean(source.get("title") or source.get("source_publisher") or url, 240),
            "publisher": _clean(source.get("source_publisher") or source.get("source"), 120),
            "url": url,
            "retrieved_at": _clean(source.get("retrieved_at"), 80),
        })
    return rows


def build_word_document(
    outdir: Path,
    *,
    title: str,
    description: str,
    chapters: list[dict[str, Any]],
    research: dict[str, Any],
    language: str = "English",
) -> dict[str, Any]:
    """Create and reopen a native DOCX with chapter structure, editorial text and references."""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Inches, Pt, RGBColor

    root = Path(outdir)
    path = root / "document.docx"
    document = Document()
    section = document.sections[0]
    section.top_margin = Inches(0.72)
    section.bottom_margin = Inches(0.72)
    section.left_margin = Inches(0.82)
    section.right_margin = Inches(0.82)

    styles = document.styles
    styles["Normal"].font.name = "Aptos"
    styles["Normal"].font.size = Pt(10.5)
    styles["Normal"].font.color.rgb = RGBColor(38, 49, 61)
    for style_name, size, color in (
        ("Title", 30, RGBColor(23, 42, 58)),
        ("Heading 1", 19, RGBColor(28, 73, 86)),
        ("Heading 2", 13, RGBColor(57, 93, 104)),
    ):
        styles[style_name].font.name = "Aptos Display"
        styles[style_name].font.size = Pt(size)
        styles[style_name].font.bold = True
        styles[style_name].font.color.rgb = color

    header = section.header.paragraphs[0]
    header.text = "AI INFINITY  /  CREATOR EDITION"
    header.style = styles["Caption"]
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer.add_run("Prepared with AI Infinity  •  Verify sources and rights before redistribution").italic = True

    title_paragraph = document.add_paragraph()
    title_paragraph.paragraph_format.space_before = Pt(84)
    title_paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
    run = title_paragraph.add_run(_clean(title, 180) or "AI Infinity production")
    run.bold = True
    run.font.name = "Aptos Display"
    run.font.size = Pt(30)
    run.font.color.rgb = RGBColor(23, 42, 58)

    subtitle = document.add_paragraph(_clean(description, 700))
    subtitle.paragraph_format.space_after = Pt(22)
    for run in subtitle.runs:
        run.font.size = Pt(13)
        run.font.color.rgb = RGBColor(85, 102, 116)

    metadata = document.add_table(rows=2, cols=2)
    metadata.style = "Light Shading Accent 1"
    metadata.cell(0, 0).text = "Language"
    metadata.cell(0, 1).text = _clean(language, 60)
    metadata.cell(1, 0).text = "Chapters"
    metadata.cell(1, 1).text = str(len(chapters or []))
    document.add_page_break()

    document.add_heading("Contents", level=1)
    for index, chapter in enumerate(chapters or [], 1):
        heading = _clean(chapter.get("heading") or f"Chapter {index}", 240)
        document.add_paragraph(f"{index:02d}  {heading}", style="List Number")
    document.add_page_break()

    total_words = 0
    used_chapters = 0
    for index, chapter in enumerate(chapters or [], 1):
        heading = _clean(chapter.get("heading") or f"Chapter {index}", 240)
        narration = str(chapter.get("narration") or "").strip()
        if not narration:
            continue
        used_chapters += 1
        total_words += len(narration.split())
        document.add_heading(heading, level=1)
        document.add_paragraph(narration)
        visual = _clean(chapter.get("visual_query"), 800)
        on_screen = _clean(chapter.get("on_screen"), 300)
        proof = _clean(chapter.get("proof_needed"), 800)
        if visual:
            paragraph = document.add_paragraph()
            label = paragraph.add_run("Visual direction  ")
            label.bold = True
            label.font.color.rgb = RGBColor(28, 73, 86)
            paragraph.add_run(visual)
        if on_screen:
            paragraph = document.add_paragraph()
            label = paragraph.add_run("On-screen copy  ")
            label.bold = True
            paragraph.add_run(on_screen)
        if proof:
            paragraph = document.add_paragraph()
            label = paragraph.add_run("Verification required  ")
            label.bold = True
            label.font.color.rgb = RGBColor(145, 81, 33)
            paragraph.add_run(proof)
        document.add_paragraph()
    if not used_chapters:
        raise ValueError("Word export requires at least one non-empty chapter")

    sources = _source_rows(research)
    document.add_page_break()
    document.add_heading("Sources and references", level=1)
    if sources:
        for index, source in enumerate(sources, 1):
            paragraph = document.add_paragraph(style="List Number")
            paragraph.add_run(source["title"]).bold = True
            paragraph.add_run(f"\n{source['publisher']}\n{source['url']}")
            if source["retrieved_at"]:
                paragraph.add_run(f"\nRetrieved: {source['retrieved_at']}")
    else:
        document.add_paragraph("No externally retrieved source records were supplied for this project.")
    document.add_paragraph(
        "Source links are research references, not a rights grant. Check the project rights manifest and original licence terms before reuse."
    )
    document.save(path)

    reopened = Document(path)
    extracted = "\n".join(p.text for p in reopened.paragraphs)
    if len(reopened.paragraphs) < 8 or len(extracted.split()) < 20:
        raise RuntimeError("DOCX verification failed: document structure or text was not recoverable")
    manifest = {
        "schema": "ai-infinity.native-document.v1",
        "file": path.name,
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
        "verified_paragraph_count": len(reopened.paragraphs),
        "verified_table_count": len(reopened.tables),
        "chapter_count": used_chapters,
        "spoken_copy_word_count": total_words,
        "source_count": len(sources),
        "format": "Office Open XML Word document",
        "verification": "reopened_with_python_docx",
        "truthful": True,
    }
    manifest_path = root / "document_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"file": path, "manifest": manifest_path, "verification": manifest}


def build_powerpoint_presentation(
    outdir: Path,
    *,
    title: str,
    description: str,
    chapters: list[dict[str, Any]],
    research: dict[str, Any],
    language: str = "English",
) -> dict[str, Any]:
    """Create and reopen a 16:9 editable PPTX, one editorial beat per slide."""
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
    from pptx.util import Inches, Pt

    root = Path(outdir)
    path = root / "presentation.pptx"
    presentation = Presentation()
    presentation.slide_width = Inches(13.333)
    presentation.slide_height = Inches(7.5)
    blank = presentation.slide_layouts[6]

    def set_background(slide, color=(11, 19, 30)):
        fill = slide.background.fill
        fill.solid()
        fill.fore_color.rgb = RGBColor(*color)

    def add_text(slide, x, y, w, h, text, size, color, bold=False, font="Aptos Display"):
        box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        frame = box.text_frame
        frame.clear()
        frame.word_wrap = True
        frame.margin_left = Inches(0.02)
        frame.margin_right = Inches(0.02)
        frame.margin_top = Inches(0.02)
        frame.margin_bottom = Inches(0.02)
        frame.vertical_anchor = MSO_ANCHOR.MIDDLE
        paragraph = frame.paragraphs[0]
        paragraph.alignment = PP_ALIGN.LEFT
        run = paragraph.add_run()
        run.text = str(text or "")
        run.font.name = font
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = RGBColor(*color)
        return box

    title_slide = presentation.slides.add_slide(blank)
    set_background(title_slide)
    add_text(title_slide, 0.8, 0.55, 4.0, 0.35, "AI INFINITY  /  CREATOR EDITION", 11, (188, 231, 126), True)
    add_text(title_slide, 0.8, 1.55, 11.6, 2.5, _clean(title, 180) or "AI Infinity production", 34, (244, 247, 251), True)
    add_text(title_slide, 0.85, 4.35, 10.6, 1.15, _clean(description, 500), 17, (188, 202, 216))
    add_text(title_slide, 0.85, 6.55, 10.8, 0.4, f"Language: {_clean(language, 60)}  •  {len(chapters or [])} editorial chapters", 10, (145, 165, 184))

    used_chapters = 0
    total_words = 0
    for index, chapter in enumerate(chapters or [], 1):
        heading = _clean(chapter.get("heading") or f"Chapter {index}", 240)
        narration = str(chapter.get("narration") or "").strip()
        if not narration:
            continue
        used_chapters += 1
        total_words += len(narration.split())
        slide = presentation.slides.add_slide(blank)
        set_background(slide)
        add_text(slide, 0.72, 0.36, 8.5, 0.35, f"CHAPTER {index:02d}", 10, (188, 231, 126), True)
        add_text(slide, 0.72, 0.9, 11.9, 0.95, heading, 25, (245, 247, 251), True)
        # Split long copy into readable chunks and create continuation slides.
        chunks = re.split(r"(?<=[.!?؟۔])\s+", narration)
        current = ""
        pages = []
        for sentence in chunks:
            if current and len(current) + len(sentence) + 1 > 520:
                pages.append(current)
                current = sentence
            else:
                current = (current + " " + sentence).strip()
        if current:
            pages.append(current)
        visual = _clean(chapter.get("visual_query"), 220)
        on_screen = _clean(chapter.get("on_screen"), 140)
        for page_index, page in enumerate(pages or [narration], 1):
            if page_index > 1:
                slide = presentation.slides.add_slide(blank)
                set_background(slide)
                add_text(slide, 0.72, 0.36, 8.5, 0.35, f"CHAPTER {index:02d}  /  CONTINUED", 10, (188, 231, 126), True)
                add_text(slide, 0.72, 0.9, 11.9, 0.95, heading, 25, (245, 247, 251), True)
            add_text(slide, 0.78, 2.0, 11.65, 3.5, page, 17, (218, 226, 235), False, "Aptos")
            if visual:
                add_text(slide, 0.8, 5.75, 8.1, 0.6, "VISUAL  /  " + visual, 10, (145, 165, 184), False, "Aptos")
            if on_screen:
                add_text(slide, 9.05, 5.75, 3.25, 0.6, on_screen, 10, (188, 231, 126), True, "Aptos")
            add_text(slide, 11.7, 7.0, 0.8, 0.25, str(len(presentation.slides)).zfill(2), 9, (145, 165, 184))

    if not used_chapters:
        raise ValueError("PowerPoint export requires at least one non-empty chapter")

    sources = _source_rows(research)
    source_slide = presentation.slides.add_slide(blank)
    set_background(source_slide)
    add_text(source_slide, 0.72, 0.55, 9.5, 0.7, "Sources and references", 27, (245, 247, 251), True)
    if sources:
        for index, source in enumerate(sources[:10], 1):
            line = f"{index}. {source['title']} — {source['publisher']} — {source['url']}"
            add_text(source_slide, 0.84, 1.45 + (index - 1) * 0.52, 11.8, 0.45, line, 10, (210, 220, 230), False, "Aptos")
    else:
        add_text(source_slide, 0.84, 1.5, 11.4, 0.6, "No externally retrieved source records were supplied.", 14, (210, 220, 230), False, "Aptos")
    add_text(source_slide, 0.84, 6.85, 11.4, 0.35, "Check original rights and licence terms before redistribution.", 10, (188, 231, 126), False, "Aptos")

    presentation.save(path)
    reopened = Presentation(path)
    extracted = "\n".join(
        shape.text for slide in reopened.slides for shape in slide.shapes if getattr(shape, "has_text_frame", False)
    )
    if len(reopened.slides) < 3 or len(extracted.split()) < 20:
        raise RuntimeError("PPTX verification failed: slides or editable text were not recoverable")
    manifest = {
        "schema": "ai-infinity.native-presentation.v1",
        "file": path.name,
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
        "verified_slide_count": len(reopened.slides),
        "chapter_count": used_chapters,
        "editorial_word_count": total_words,
        "source_count": len(sources),
        "slide_size": "16:9",
        "format": "Office Open XML PowerPoint presentation",
        "verification": "reopened_with_python_pptx",
        "truthful": True,
    }
    manifest_path = root / "presentation_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"file": path, "manifest": manifest_path, "verification": manifest}
