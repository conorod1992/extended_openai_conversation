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
    if campaign == "all-except-long-lifetime":
        campaigns = [name for name in CAMPAIGNS if name != "long-lifetime"]
    elif campaign in {"all", "browser"}:
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


def python_matrix(plan: dict, *, scheduled: bool) -> list[dict]:
    """Keep lifetime processes on separate runners at the original intensity."""
    return [
        {"campaign": campaign, "intensity": intensity, "partition": partition}
        for campaign in plan["campaigns"]
        for intensity in plan["intensities"]
        if not (scheduled and campaign == "long-lifetime" and intensity == "heavy")
        for partition in (
            ("reload", "uninterrupted") if campaign == "long-lifetime" else ("",)
        )
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", required=True)
    parser.add_argument("--schedule", default="")
    parser.add_argument("--campaign", default="all")
    parser.add_argument("--intensity", default="normal")
    args = parser.parse_args()
    plan = enhanced_plan(args.event, args.schedule, args.campaign, args.intensity)
    plan["python_matrix"] = python_matrix(plan, scheduled=args.event == "schedule")
    plan["persistence_matrix"] = [
        row for row in plan["python_matrix"] if row["campaign"] == "persistence"
    ]
    plan["python_matrix"] = [
        row for row in plan["python_matrix"] if row["campaign"] != "persistence"
    ]
    # Offer the measured critical path first; actual starts still depend on
    # runner availability. Keep the original order of all other matrix rows.
    plan["python_matrix"].sort(key=lambda row: row["campaign"] != "large-installation")
    for key, value in plan.items():
        print(key + "=" + json.dumps(value, separators=(",", ":")))


if __name__ == "__main__":
    main()
