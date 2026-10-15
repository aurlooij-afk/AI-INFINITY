from pathlib import Path

from ai_infinity import persistence_truth


def test_confirmation_flags_without_real_mount_do_not_claim_durable(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_INFINITY_PERSISTENCE_MODE", "durable")
    monkeypatch.setenv("AI_INFINITY_PERSISTENT_VOLUME_CONFIRMED", "true")
    monkeypatch.setenv("AI_INFINITY_PROJECT_STATE_DURABLE", "true")
    monkeypatch.setenv("AI_INFINITY_PROJECT_STATE_BACKEND", "sqlite_persistent_volume")
    result = persistence_truth.project_state_durability(tmp_path / "data")
    assert result["persistent"] is False
    assert result["reason"] == "durable_mode_requested_but_backend_not_verified"


def test_tmp_path_is_not_persistent_even_if_mountinfo_calls_it_ext4():
    mountinfo = "31 22 0:28 / /tmp rw,relatime - ext4 /dev/vda1 rw\n"
    assert persistence_truth._has_confirmed_persistent_mount(Path("/tmp/ai-infinity"), mountinfo) is False


def test_a_real_separate_non_ephemeral_mount_can_supply_volume_evidence():
    mountinfo = "31 22 0:28 / /data rw,relatime - ext4 /dev/vda1 rw\n"
    assert persistence_truth._has_confirmed_persistent_mount(Path("/data/ai-infinity"), mountinfo) is True


def test_container_root_overlay_is_not_persistent_volume_evidence():
    mountinfo = "31 22 0:28 / / rw,relatime - overlay overlay rw\n"
    assert persistence_truth._has_confirmed_persistent_mount(Path("/var/data/ai-infinity"), mountinfo) is False
