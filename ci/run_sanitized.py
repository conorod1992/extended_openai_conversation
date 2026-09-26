"""Run enhanced browser subprocesses without streaming private failure output."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from enhanced_evidence import sanitize_log

destination = Path(sys.argv[1])
command = sys.argv[3:] if sys.argv[2] == "--" else sys.argv[2:]
with TemporaryDirectory() as temporary:
    raw = Path(temporary) / "raw.log"
    with raw.open("w", encoding="utf-8") as stream:
        result = subprocess.run(
            command, stdout=stream, stderr=subprocess.STDOUT, check=False
        )
    sanitize_log(raw, destination)
print(destination.read_text(encoding="utf-8"))
raise SystemExit(result.returncode)
