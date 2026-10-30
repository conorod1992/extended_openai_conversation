"""Provider interpretation changes must exercise the supported SDK lanes."""

import json
from pathlib import Path


def test_provider_classification_selects_sdk_contract_and_its_regressions():
    root = Path(__file__).resolve().parents[1]
    registry = json.loads((root / "ci/pr_workflows.json").read_text())
    paths = registry["openai-sdk-compatibility"]["paths"]
    for name in ("assistant_response.py", "entity.py", "completion_events.py"):
        assert f"custom_components/extended_openai_conversation_responses/{name}" in paths
    workflow = (root / ".github/workflows/openai-sdk-compatibility.yml").read_text()
    assert "tests/test_provider_output_classification.py" in workflow
    assert "tests/test_responses_history_coherence.py" in workflow
