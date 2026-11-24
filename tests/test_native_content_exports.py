from __future__ import annotations

import json
import zipfile

from docx import Document
from pptx import Presentation

from ai_infinity.native_exports import build_powerpoint_presentation, build_word_document


def _inputs(tmp_path):
    chapters = [
        {
            "heading": "The underlying problem",
            "narration": "A creator needs a clear and useful explanation. The production must show evidence, not unsupported claims.",
            "visual_query": "A researcher reviewing a clear evidence board",
            "on_screen": "START WITH EVIDENCE",
        },
        {
            "heading": "A workable method",
            "narration": "Research the question, identify the strongest sources, explain the choices, and make the result editable.",
            "visual_query": "An editorial workstation and production timeline",
            "on_screen": "BUILD WITH INTENTION",
        },
    ]
    research = {
        "sources": [
            {
                "title": "W3C Web Accessibility Initiative",
                "source_publisher": "W3C",
                "url": "https://www.w3.org/WAI/",
                "retrieved_at": "2026-11-04T00:00:00Z",
            }
        ]
    }
    return {
        "outdir": tmp_path,
        "title": "Evidence-led production",
        "description": "A useful, source-aware editorial package.",
        "chapters": chapters,
        "research": research,
        "language": "English",
    }


def test_native_word_document_is_editable_and_reopens_with_source_records(tmp_path):
    result = build_word_document(**_inputs(tmp_path))
    path = result["file"]
    assert path.name == "document.docx" and path.stat().st_size > 1000
    reopened = Document(path)
    paragraphs = "\n".join(item.text for item in reopened.paragraphs)
    tables = "\n".join(cell.text for table in reopened.tables for row in table.rows for cell in row.cells)
    assert "Evidence-led production" in paragraphs
    assert "The underlying problem" in paragraphs
    assert "Research the question" in paragraphs
    assert "W3C Web Accessibility Initiative" in paragraphs
    assert "Language" in tables and "English" in tables
    manifest = json.loads(result["manifest"].read_text(encoding="utf-8"))
    assert manifest["verification"] == "reopened_with_python_docx"
    assert manifest["verified_table_count"] >= 1
    assert manifest["chapter_count"] == 2
    assert manifest["size_bytes"] == path.stat().st_size


def test_native_powerpoint_reopens_with_real_editable_slides_and_references(tmp_path):
    result = build_powerpoint_presentation(**_inputs(tmp_path))
    path = result["file"]
    assert path.name == "presentation.pptx" and path.stat().st_size > 1000
    reopened = Presentation(path)
    assert len(reopened.slides) >= 4
    slide_text = "\n".join(
        shape.text for slide in reopened.slides for shape in slide.shapes if getattr(shape, "has_text_frame", False)
    )
    assert "Evidence-led production" in slide_text
    assert "The underlying problem" in slide_text
    assert "Research the question" in slide_text
    assert "Sources and references" in slide_text
    assert "https://www.w3.org/WAI/" in slide_text
    manifest = json.loads(result["manifest"].read_text(encoding="utf-8"))
    assert manifest["verification"] == "reopened_with_python_pptx"
    assert manifest["slide_size"] == "16:9"
    assert manifest["verified_slide_count"] == len(reopened.slides)
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        assert "ppt/presentation.xml" in archive.namelist()


def test_native_office_exports_reject_empty_chapter_lists(tmp_path):
    args = _inputs(tmp_path)
    args["chapters"] = []
    for builder in (build_word_document, build_powerpoint_presentation):
        try:
            builder(**args)
        except ValueError as exc:
            assert "at least one non-empty chapter" in str(exc)
        else:
            raise AssertionError("native document export accepted empty editorial content")
