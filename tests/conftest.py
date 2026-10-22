import pytest

@pytest.fixture(autouse=True)
def deterministic_media_sources(monkeypatch, request):
    import studio_ultimate as studio
    def no_remote(*args, **kwargs):
        return []
    for name in ("_nasa_images", "_openverse_images", "_commons_media", "_pexels", "_pixabay"):
        # The adapter-contract test deliberately supplies mocked HTTP/byte
        # responses and must execute the real NASA adapter rather than this
        # suite-wide no-network provider stub.
        if (
            request.node.name == "test_nasa_adapter_exports_asset_level_policy_url_and_rights_state"
            and name == "_nasa_images"
        ):
            continue
        monkeypatch.setattr(studio, name, no_remote)
    monkeypatch.setattr(studio, "_hf_video", lambda *a, **k: None)
    monkeypatch.setattr(studio, "_hf_image", lambda *a, **k: None)
