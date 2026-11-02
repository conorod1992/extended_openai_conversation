"""Shared persisted representations for migration, dependency and recovery tests."""

from copy import deepcopy
import json
from pathlib import Path

FUNCTION_CONFIGURATION_CASES = json.loads(
    (
        Path(__file__).parent / "fixtures/function_configuration_representations.json"
    ).read_text(encoding="utf-8")
)


def corpus_config(case):
    """Keep source omissions and scalar representations independent per journey."""
    return {"function_groups": [], "skills": [], **deepcopy(case["config"])}
