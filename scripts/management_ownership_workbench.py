"""Temporary PR4 stage-two patch transport."""
from base64 import b64decode
from bz2 import decompress
from hashlib import sha256
from pathlib import Path
import subprocess

parts = [
    Path(f"scripts/pr4_stage2_{index:02d}.b64").read_bytes()
    for index in range(4)
]
payload = b"".join(parts)
assert len(payload) == 29012
assert sha256(payload).hexdigest() == "4ef7d4b941e26e21ebf7770b8d1181d1e8b94fd4c4405ceec1a0f3e486301da7"
patch = decompress(b64decode(payload))
assert len(patch) == 138392
assert sha256(patch).hexdigest() == "1e48c5eef4dbc0cb95fb12c40ed223d96f752e89db3f6496ea2eea198d7c0006"
patch_path = Path("/tmp/pr4-stage2.patch")
patch_path.write_bytes(patch)
subprocess.run(["git", "apply", "--check", str(patch_path)], check=True)
subprocess.run(["git", "apply", str(patch_path)], check=True)
