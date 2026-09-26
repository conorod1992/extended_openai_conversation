"""Validate the controlled Playwright failure artifacts, including zip contents."""

from pathlib import Path
import sys
from zipfile import ZipFile

from enhanced_evidence import CANARIES

root = Path(sys.argv[1])
files = [path for path in root.rglob("*") if path.is_file()]
assert any(path.suffix == ".png" for path in files), "missing failure screenshot"
assert any(path.name == "trace.zip" for path in files), "missing Playwright trace"
operation_attachment = False
for path in files:
    content = path.read_bytes()
    assert all(canary.encode() not in content for canary in CANARIES), path
    if path.suffix == ".zip":
        with ZipFile(path) as archive:
            for name in archive.namelist():
                data = archive.read(name)
                if b'"boundary":"fixture-browser"' in data:
                    operation_attachment = True
                assert all(canary.encode() not in data for canary in CANARIES), name
assert operation_attachment, "missing operation attachment in uploaded Playwright ZIP"
print("Controlled browser screenshot, trace, attachment, and canary scan verified")
