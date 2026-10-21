from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
UI_SOURCE = ROOT / "professional_creator_v2.py"


def test_timeline_view_and_master_render_have_distinct_entry_points() -> None:
    source = UI_SOURCE.read_text(encoding="utf-8")
    assert source.count("function renderTimelineView()") == 1
    assert source.count("async function renderEditedMaster()") == 1
    assert "function renderTL()" not in source
    assert 'onclick="renderEditedMaster()"' in source

    load_timeline = re.search(r"async function loadTL\(\)\{.*?\}", source)
    assert load_timeline is not None
    assert "renderTimelineView()" in load_timeline.group(0)
    assert "/timeline/render" not in load_timeline.group(0)

    move_scene = re.search(r"function mv\(i,d\)\{.*?\}", source)
    assert move_scene is not None
    assert "renderTimelineView()" in move_scene.group(0)
    assert "/timeline/render" not in move_scene.group(0)


def test_otio_actions_are_user_accessible_and_truthful() -> None:
    source = UI_SOURCE.read_text(encoding="utf-8")
    assert "Export editable OTIO" in source
    assert "Import edited OTIO" in source
    assert "Download portable ZIP" in source
    assert "/timeline/otio/export" in source
    assert "/timeline/otio/import" in source

    import_start = source.index("async function importOTIO(input)")
    import_end = source.index("async function revise()", import_start)
    import_body = source[import_start:import_end]
    assert "2*1024*1024" in import_body
    assert "renderTimelineView()" in import_body
    assert "renderEditedMaster()" not in import_body
    assert "human_review_status:r.human_review_status" in import_body
    assert "truth_verified:r.truth_verified" in import_body
