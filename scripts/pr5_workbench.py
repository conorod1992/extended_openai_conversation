"""Run the explicitly reviewed PR5 edits only on their isolated branch."""
from pathlib import Path
import runpy
import subprocess

branch = subprocess.check_output(['git', 'branch', '--show-current'], text=True).strip()
# Actions checks out the exact event commit detached; a branch checkout must match.
assert not branch or branch == 'refactor/conversation-entry-ownership', branch
assert Path('scripts/pr5_implementation.py').exists()
assert Path('scripts/pr5_test_migration.py').exists()
runpy.run_path('scripts/pr5_implementation.py', run_name='__main__')
runpy.run_path('scripts/pr5_test_migration.py', run_name='__main__')
print('PR5 request-entry changes prepared; validation is not yet complete.')
