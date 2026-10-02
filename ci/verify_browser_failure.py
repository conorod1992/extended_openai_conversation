"""Validate the controlled Playwright failure artifacts, including zip contents."""

import base64
from io import BytesIO
from pathlib import Path
import re
import sys
from zipfile import ZipFile

from enhanced_evidence import CANARIES, fresh_privacy_canaries

root = Path(sys.argv[1])
files = [path for path in root.rglob("*") if path.is_file()]
if len(sys.argv) > 3:
    files.extend(path for path in Path(sys.argv[3]).rglob("*") if path.is_file())
assert any(path.suffix == ".png" for path in files), "missing failure screenshot"
assert any(path.name == "trace.zip" for path in files), "missing Playwright trace"
operation_attachment = False
fresh = fresh_privacy_canaries(sys.argv[2] if len(sys.argv) > 2 else "unknown")
useful_marker = False
for path in files:
    content = path.read_bytes()
    assert all(canary.encode() not in content for canary in (*CANARIES, *fresh)), path
    zipped = path if path.suffix == ".zip" else None
    if path.suffix == ".html":
        encoded = re.search(
            rb'<template id="playwrightReportBase64">data:application/zip;base64,([^<]+)',
            content,
        )
        if encoded:
            zipped = BytesIO(base64.b64decode(encoded[1]))
    if zipped is not None:
        with ZipFile(zipped) as archive:
            for name in archive.namelist():
                data = archive.read(name)
                if b"fixture-browser" in data:
                    operation_attachment = True
                useful_marker = useful_marker or b"USEFUL-PRIVACY-DIAGNOSTIC" in data
                assert all(
                    canary.encode() not in data for canary in (*CANARIES, *fresh)
                ), name
assert useful_marker, "missing useful diagnostic marker"
assert operation_attachment, "missing operation attachment in uploaded Playwright ZIP"
print("Controlled browser screenshot, trace, attachment, and canary scan verified")
