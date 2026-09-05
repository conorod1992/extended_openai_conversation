"""Temporary maintenance helper for PR #155; removed by the follow-up commit."""

import base64
import io
from pathlib import Path
import tarfile

_PATHS = (
    "custom_components/extended_openai_conversation_responses/memory.py",
    "custom_components/extended_openai_conversation_responses/temporary_memory.py",
    "tests/test_memory.py",
)
_buffer = io.BytesIO()
with tarfile.open(fileobj=_buffer, mode="w:gz") as archive:
    for _path in _PATHS:
        archive.add(Path(_path), arcname=_path)
_payload = base64.b64encode(_buffer.getvalue()).decode("ascii")
raise RuntimeError(f"PR155_EXPORT_BEGIN{_payload}PR155_EXPORT_END")
