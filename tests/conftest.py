import pytest

@pytest.fixture(autouse=True)
def deterministic_media_sources(monkeypatch):
    import studio_ultimate as studio
    def no_remote(*args, **kwargs):
        return []
    for name in ("_nasa_images", "_openverse_images", "_commons_media", "_pexels", "_pixabay"):
        monkeypatch.setattr(studio, name, no_remote)
    monkeypatch.setattr(studio, "_hf_video", lambda *a, **k: None)
    monkeypatch.setattr(studio, "_hf_image", lambda *a, **k: None)
