"""Read and write the native private recovery envelope with bounded scratch space."""

from io import BytesIO
import json
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.restore_disk import (
    RESTORE_STAGING_SUFFIX,
    read_large_journal,
    write_verified_store,
)


@pytest.mark.parametrize("escaped", [True, False])
def test_streamed_restore_reader_matches_native_json(escaped):
    data = {"configuration": ["🟢", "é", 2**64, 0.1, -0.0, True, None]}
    raw = json.dumps(data, ensure_ascii=escaped).encode()
    assert read_large_journal(BytesIO(raw)) == data


@pytest.mark.parametrize("raw", [b'{"key":"\\ud800"}', b'{"key":', b"{} {}"])
def test_streamed_restore_reader_rejects_malformed_json(raw):
    with pytest.raises(ValueError, match="Invalid"):
        read_large_journal(BytesIO(raw))


def test_failed_streamed_write_preserves_published_store(tmp_path):
    path = tmp_path / "archive"
    store = SimpleNamespace(
        path=str(path), key=path.name, version=1, minor_version=1,
        _read_only=False, _load_empty=False,
    )
    write_verified_store(store, {"turns": ["original"]}, max_bytes=1024)
    expected = path.read_bytes()
    with pytest.raises(ValueError, match="safety limit"):
        write_verified_store(store, {"turns": ["é" * 1024]}, max_bytes=1024)
    store._read_only = True
    with pytest.raises(OSError, match="read-only"):
        write_verified_store(store, {"turns": ["replacement"]}, max_bytes=1024)
    assert path.read_bytes() == expected
    assert not path.with_name(path.name + RESTORE_STAGING_SUFFIX).exists()
