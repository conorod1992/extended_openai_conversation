"""Behavioural contracts for historical frontend latency comparisons."""

import json
from pathlib import Path
import sys

from ci.frontend_latency.compare import main


def test_comparison_keeps_historical_gaps_and_current_results(
    tmp_path: Path,
    monkeypatch,
) -> None:
    baseline = {
        "backend": {"agents": {"median_ms": 12}},
        "browser": {"samples": [{"route": "overview", "wall_ready_ms": 80}]},
    }
    current = {
        "backend": {"agents": {"median_ms": 9}, "new_operation": {"median_ms": 5}},
        "browser": {
            "samples": [
                {"route": "overview", "wall_ready_ms": 60},
                {"route": "new-route", "wall_ready_ms": 40},
            ]
        },
    }
    before = tmp_path / "baseline.json"
    after = tmp_path / "current.json"
    output = tmp_path / "result"
    before.write_text(json.dumps(baseline), encoding="utf-8")
    after.write_text(json.dumps(current), encoding="utf-8")
    monkeypatch.setattr(
        sys, "argv", ["compare.py", str(before), str(after), str(output)]
    )

    main()

    comparison = json.loads((output / "comparison.json").read_text(encoding="utf-8"))
    assert comparison["backend"]["agents"]["delta_ms"] == -3
    assert comparison["browser"]["overview"]["ready_delta_ms"] == -20
    assert comparison["backend"]["new_operation"]["baseline_supported"] is False
    assert comparison["backend"]["new_operation"]["current_supported"] is True
    assert comparison["browser"]["new-route"]["baseline_supported"] is False
    assert comparison["browser"]["new-route"]["current_supported"] is True
    summary = (output / "summary.md").read_text(encoding="utf-8")
    assert "new_operation" in summary and "new-route" in summary
    assert "n/a" in summary
