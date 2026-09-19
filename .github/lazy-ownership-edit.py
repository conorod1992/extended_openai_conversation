"""Temporary branch workbench; removed before opening the pull request."""
from pathlib import Path
import base64
import hashlib
import subprocess
import zlib

BRANCH = 'refactor/frontend-lazy-feature-ownership'
assert subprocess.check_output(['git', 'branch', '--show-current'], text=True).strip() == BRANCH
stages = [
    ('prod', 'b3ff1209e08ef0676637b4ea5a24b695e70a1a83c944f4e8b705adfe17102945', 'Make lazy frontend features and embedded Debug explicitly owned'),
    ('tests', '5266d6032cf06aff134e09d260476708a52363d025a523f3aa8e7323c5bcb526', 'Cover lazy feature ownership, paging, embedded Debug and teardown'),
]
for name, digest, message in stages:
    encoded = Path(f'.github/lazy-{name}.patch.b64').read_text().strip()
    # Repair two transport transcription duplicates, then verify exact local bytes.
    if name == 'tests':
        encoded = encoded.replace('XcdJJpnSgada', 'XcdJpnSgada').replace('ttVlVlV64', 'ttVlV64')
    patch = zlib.decompress(base64.b64decode(encoded, validate=True))
    assert hashlib.sha256(patch).hexdigest() == digest, f'{name}: patch checksum mismatch'
    reverse = subprocess.run(['git', 'apply', '--unidiff-zero', '--reverse', '--check', '-'], input=patch, capture_output=True)
    if reverse.returncode == 0:
        print(f'{name}: already applied', flush=True)
        continue
    subprocess.run(['git', 'apply', '--unidiff-zero', '--check', '-'], input=patch, check=True)
    subprocess.run(['git', 'apply', '--unidiff-zero', '--index', '-'], input=patch, check=True)
    paths = subprocess.check_output(['git', 'diff', '--cached', '--name-only'], text=True).splitlines()
    assert paths
    for filename in paths:
        assert filename.startswith(('custom_components/extended_openai_conversation_responses/frontend/', 'tests/', 'tests_browser/', 'docs/development/')), filename
        if filename.endswith(('.js', '.mjs')):
            subprocess.run(['node', '--check', filename], check=True)
    subprocess.run(['git', 'diff', '--cached', '--check'], check=True)
    subprocess.run(['git', 'commit', '-m', message], check=True)
print('Reviewed edits applied; branch workbench will push without force.', flush=True)
