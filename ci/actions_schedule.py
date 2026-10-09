"""Select daily/weekly Enhanced work without weakening selected-case certification."""

from __future__ import annotations

import argparse
import json

ENHANCED_DAILY = "15 3 * * 1-6"
ENHANCED_WEEKLY = "15 3 * * 0"
CAMPAIGNS = (
    "runtime",
    "lifecycle",
    "ai-task",
    "voice-intercom",
    "archive",
    "feature-crossroads",
    "setup",
    "backup",
    "backup-transfer",
    "persistence",
    "request-rules",
    "guest-security",
    "quiet-hours",
    "functions",
    "provider-resilience",
    "memory-knowledge",
    "large-installation",
    "chaos",
    "process-chaos",
    "long-lifetime",
)


def enhanced_plan(
    event: str, schedule: str = "", campaign: str = "all", intensity: str = "normal"
) -> dict:
    if event == "schedule":
        if schedule not in {ENHANCED_DAILY, ENHANCED_WEEKLY}:
            raise ValueError("Unreviewed Enhanced schedule")
        weekly = schedule == ENHANCED_WEEKLY
        return {
            "campaigns": list(CAMPAIGNS if weekly else CAMPAIGNS[:-1]),
            "intensities": ["normal", "heavy"] if weekly else ["normal"],
        }
    if event != "workflow_dispatch":
        raise ValueError("Enhanced must be scheduled or explicitly dispatched")
    campaign = campaign or "all"
    intensity = intensity or "normal"
    if intensity not in {"normal", "heavy", "all"}:
        raise ValueError("Unreviewed stress intensity")
    if campaign in {"all", "browser"}:
        campaigns = list(CAMPAIGNS)
    elif campaign == "browser-engines":
        campaigns = []
    elif campaign in {*CAMPAIGNS, "diagnostics"}:
        campaigns = [campaign]
    else:
        raise ValueError("Unreviewed stress campaign")
    return {
        "campaigns": campaigns,
        "intensities": ["normal", "heavy"] if intensity == "all" else [intensity],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", required=True)
    parser.add_argument("--schedule", default="")
    parser.add_argument("--campaign", default="all")
    parser.add_argument("--intensity", default="normal")
    args = parser.parse_args()
    for key, value in enhanced_plan(
        args.event, args.schedule, args.campaign, args.intensity
    ).items():
        print(key + "=" + json.dumps(value, separators=(",", ":")))


if __name__ == "__main__":
    main()
