"""Check controlled failure output and absence of private canaries."""

from __future__ import annotations

import json
from pathlib import Path
import sys

from enhanced_evidence import CANARIES, SCHEMA, fresh_privacy_canaries


def verify(folder: Path, log: Path) -> None:
    traces = list(folder.glob("*test_diagnostics_failure_probe*.json"))
    assert len(traces) == 4, [item.name for item in traces]
    boundaries = set()
    fresh = set()
    for path in traces:
        report = json.loads(path.read_text(encoding="utf-8"))
        fresh.update(fresh_privacy_canaries(str(report["seed"])))
        assert "USEFUL-PRIVACY-DIAGNOSTIC" in json.dumps(report)
        assert report["schema"] == SCHEMA
        assert report["outcome"] == "failed"
        assert report["eoai_sha"] and report["seed"]
        assert report["operations"][-1]["operation"] == "before_assertion"
        boundary = report["operations"][0]["boundary"]
        boundaries.add(boundary)
        assert any(
            item.get("operation") == "configuration_error"
            for item in report["operations"]
        )
        if boundary != "python":
            assert isinstance(report["health"]["ha_state_count"], int)
        if boundary == "provider":
            assert any(
                item.get("phase") == "before_headers" and item.get("kind") == "dns"
                for item in report["operations"]
            )
            assert any(
                item.get("operation") == "assist_failure"
                and item.get("provider_requests") == 1
                for item in report["operations"]
            )
            assert any(
                item.get("operation") == "provider_error_payload"
                and item.get("provider_requests") == 1
                for item in report["operations"]
            )
        if boundary in {"lifecycle", "resource"}:
            assert any(
                isinstance(item.get("baseline_resources"), dict)
                and isinstance(item.get("peak_resources"), dict)
                and isinstance(item.get("after_resources"), dict)
                for item in report["operations"]
            )
    assert boundaries == {"python", "provider", "lifecycle", "resource"}
    inspected = [*folder.rglob("*.json"), log]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in inspected)
    assert all(canary not in combined for canary in (*CANARIES, *fresh))
    assert "USEFUL-PRIVACY-DIAGNOSTIC" in log.read_text(encoding="utf-8")
    assert "[REDACTED]" in combined


if __name__ == "__main__":
    verify(Path(sys.argv[1]), Path(sys.argv[2]))
    print("Controlled failure traces and canary redaction verified")
