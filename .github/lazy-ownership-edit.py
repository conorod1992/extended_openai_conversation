"""Temporary branch workbench; removed before opening the pull request."""
from pathlib import Path
import subprocess

BRANCH = 'refactor/frontend-lazy-feature-ownership'
assert subprocess.check_output(['git', 'branch', '--show-current'], text=True).strip() == BRANCH
path = Path('tests_browser/management-lazy-ownership.spec.mjs')
source = path.read_text()
assert 'providers:' in source or 'provider_requests:' in source, 'Debug fixture shape changed unexpectedly'
source = source.replace('providers:', 'provider_requests:').replace('21-25 of 25', '21–25 of 25')
path.write_text(source)
panel = Path('custom_components/extended_openai_conversation_responses/frontend/management-panel.js')
source = panel.read_text()
source = source.replace('import {formatUsageNumber, formatUsageTimestamp, tokenBreakdown} from "./usage-format.js";', 'import {formatUsageNumber, tokenBreakdown} from "./usage-format.js";')
panel.write_text(source)
for file in [path, panel]:
    subprocess.run(['node', '--check', str(file)], check=True)
subprocess.run(['git', 'add', str(path), str(panel)], check=True)
subprocess.run(['git', 'diff', '--cached', '--check'], check=True)
if subprocess.run(['git', 'diff', '--cached', '--quiet']).returncode:
    subprocess.run(['git', 'commit', '-m', 'Correct bounded Debug paging regression fixture'], check=True)
