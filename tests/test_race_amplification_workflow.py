"""Guard the race lane's shared runtime prerequisites before tests begin."""

from pathlib import Path
import re

import yaml


def test_race_workflow_resolves_every_required_reconciliation_version():
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load(
        (root / ".github/workflows/race-amplification.yml").read_text()
    )
    steps = workflow["jobs"]["amplify"]["steps"]
    reconcile = next(
        number
        for number, step in enumerate(steps)
        if step.get("run") == "bash ci/reconcile_stable_environment.sh"
    )
    before = "\n".join(step.get("run", "") for step in steps[:reconcile])
    script = (root / "ci/reconcile_stable_environment.sh").read_text()
    required = set(re.findall(r"\$\{(EOAI_EXPECTED_[A-Z_]+):\?", script))
    assert required
    for variable in required:
        assert re.search(rf'echo "{variable}=\$[A-Z_]+" >> "\$GITHUB_ENV"', before)
    assert 'ci/resolve_ha_test_plugin.py "$VERSION"' in before
