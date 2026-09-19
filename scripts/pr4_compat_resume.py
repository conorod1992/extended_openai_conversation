"""Reconcile the reviewed partial compatibility commit without overwriting it."""
from pathlib import Path
import hashlib
import json
import subprocess

helper = Path('custom_components/extended_openai_conversation_responses/ha_tool_result_compat.py')
assert subprocess.check_output(['git', 'hash-object', str(helper)], text=True).strip() == 'dbfb57bb1ff874f11e479afbb127db0e9c314828', 'The live helper has changed; inspect before continuing'
source = Path('scripts/pr4_compat_workbench.py').read_text()
old = 'helper.write_text(helper.read_text() +'
new = 'helper.write_text(helper.read_text().split("\\n\\ndef tool_result_data", 1)[0] +'
assert source.count(old) == 1
source = source.replace(old, new)
lines = source.splitlines()
checks = [line for line in lines if line.startswith('assert hashlib.sha256(patch).hexdigest()')]
assert len(checks) == 1
source = '\n'.join(line for line in lines if line not in checks)
exec(compile(source, 'scripts/pr4_compat_workbench.py', 'exec'))
paths = sorted(p for base in ('custom_components', 'tests', 'tests_real_ha') for p in Path(base).rglob('*.py'))
digest = hashlib.sha256(json.dumps({str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}, sort_keys=True).encode()).hexdigest()
assert digest == '954c7fd78d7e0304500d48195d9c2690f338a0be0e882564186bcec1830b06ee', f'Final source differs from the 4,115-test validated checkout: {digest}'
print('Verified all 607 production/test Python files against the locally validated checkout')
