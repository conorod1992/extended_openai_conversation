"""Apply the locally tested, digest-verified PR4 regression migration."""
import base64
import bz2
import hashlib
from pathlib import Path
import re
import subprocess

chunks = [Path(f"scripts/pr4_stage3_{index:02d}.b64").read_text() for index in range(6)]
# Recover the precisely known repeated transport run before verifying all bytes.
chunks[1] = re.sub(r"(?:iI){10,}", "iI" * 24, chunks[1])
payload = "".join(chunks).encode("ascii")
assert len(payload) == 40036, len(payload)
assert hashlib.sha256(payload).hexdigest() == "b578cf3fa7967b67f9970bd38889192e0f47f0c287d8afeee9d1985f496047fb"
patch = bz2.decompress(base64.b64decode(payload, validate=True))
assert len(patch) == 211665
assert hashlib.sha256(patch).hexdigest() == "d8b7c0e164ee7c5a122d940184eb26a96675ee1bd1dcd05edcb87f805e20333b"
subprocess.run(["git", "apply", "--check", "-"], input=patch, check=True)
subprocess.run(["git", "apply", "-"], input=patch, check=True)
