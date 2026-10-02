"""Native staging locks protect other live owners and unrelated filesystem data."""

from custom_components.extended_openai_conversation_responses.transfer_staging import (
    _initialize_owner,
)


def test_live_owner_and_unmarked_data_survive_reconciliation(tmp_path):
    root = tmp_path / "private"
    first = _initialize_owner(root)
    second = None
    payload = first.directory / "extended-openai-backup-live.zip"
    payload.write_bytes(b"LIVE-OWNER")
    unknown = root / f"owner-{'a' * 32}"
    unknown.mkdir()
    (unknown / "sentinel").write_text("UNMARKED")
    (root / "unrelated-sentinel").write_text("KEEP")
    try:
        second = _initialize_owner(root)
        assert payload.read_bytes() == b"LIVE-OWNER"
        assert (unknown / "sentinel").read_text() == "UNMARKED"
        assert (root / "unrelated-sentinel").read_text() == "KEEP"
        assert first.directory != second.directory
    finally:
        first.close()
        if second is not None:
            second.close()


def test_unlocked_owned_payload_is_reclaimed_without_session_authority(tmp_path):
    root = tmp_path / "private"
    abandoned = _initialize_owner(root)
    payload = abandoned.directory / "extended-openai-backup-orphan.zip"
    payload.write_bytes(b"ORPHAN")
    abandoned.lock_handle.close()  # OS boundary left by process termination
    fresh = _initialize_owner(root)
    try:
        assert not abandoned.directory.exists()
        assert list(fresh.directory.glob("extended-openai-backup-*")) == []
    finally:
        fresh.close()
