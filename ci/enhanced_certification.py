"""Consolidate actual matrix job evidence into one nightly certification view."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from urllib.request import Request, urlopen

from enhanced_evidence import SCHEMA, write_json


def actual_jobs() -> dict[str, str]:
    """Read final Actions conclusions; a trace written before upload is insufficient."""
    repository = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    token = os.environ.get("GH_TOKEN")
    if not all((repository, run_id, token)):
        return {}
    jobs: dict[str, str] = {}
    for page in range(1, 10):
        request = Request(
            f"https://api.github.com/repos/{repository}/actions/runs/{run_id}/jobs?per_page=100&page={page}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
        )
        with urlopen(request, timeout=20) as response:
            batch = json.load(response)["jobs"]
        jobs.update({job["name"]: job.get("conclusion") or "pending" for job in batch})
        if len(batch) < 100:
            break
    return jobs


def main(root: Path) -> int:
    campaigns = json.loads(os.environ["ENHANCED_CAMPAIGNS"])
    intensities = json.loads(os.environ["ENHANCED_INTENSITIES"])
    selected = os.environ.get("ENHANCED_SELECTED", "all")
    if selected in {"browser", "browser-engines"}:
        campaigns = []  # The Python matrix is intentionally skipped for browser-only runs.
    seed = os.environ["STRESS_SEED"]
    needs = json.loads(os.environ.get("ENHANCED_NEEDS", "{}"))
    conclusions = actual_jobs()
    expected = {
        (campaign, intensity, None)
        for campaign in campaigns
        for intensity in intensities
    }
    if selected in {"all", "browser", "diagnostics"}:
        browser_campaign = (
            "browser-diagnostics" if selected == "diagnostics" else "browser"
        )
        expected |= {(browser_campaign, intensity, None) for intensity in intensities}
    if selected in {"all", "browser", "browser-engines"}:
        expected |= {
            (f"browser-{engine}", intensity, None)
            for engine in ("firefox", "webkit")
            for intensity in intensities
        }
    if selected in {"all", "lifecycle"}:
        expected |= {
            ("lifecycle-matrix", "normal", point)
            for point in ("oldest", "stable", "dev")
        }
    found = {}
    artifact_names = {}
    for path in root.rglob("certification.json"):
        item = json.loads(path.read_text(encoding="utf-8"))
        assert item["schema"] == SCHEMA, path
        key = (item["campaign"], item["intensity"], item.get("ha_point"))
        assert key not in found, key
        found[key] = item
        artifact_names[key] = path.parent.name
    lines = [
        "## Enhanced nightly certification",
        "",
        f"Run: `{os.environ.get('GITHUB_RUN_ID', 'local')}` · Seed: `{seed}` · Selected: `{selected}`",
        "",
        "| Campaign | Intensity / HA | Result | SHA | HA | Evidence |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    failed = False
    shas = set()
    for key in sorted(expected):
        campaign, intensity, point = key
        item = found.get(key)
        job_name = (
            f"HA {point} / shared lifecycle contract"
            if point
            else f"{'browser' if campaign == 'browser-diagnostics' else campaign} / {intensity}"
        )
        actual = conclusions.get(job_name, "unverified")
        status = (
            "success"
            if item and item.get("status") == "success" and actual == "success"
            else actual
            if actual != "success"
            else "evidence-inconsistent"
            if item
            else "missing"
        )
        if status != "success":
            failed = True
        if item:
            shas.add(item["eoai_sha"])
        lines.append(
            f"| {campaign} | {point or intensity} | **{status}** | "
            f"`{item['eoai_sha'][:12]}` | `{item.get('ha_version') or '-'}` | "
            f"`{artifact_names[key]}` |"
            if item
            else f"| {campaign} | {point or intensity} | **missing** | - | - | - |"
        )
    if len(shas) > 1:
        failed = True
        lines += ["", "**Mixed tested SHAs: certification invalid.**"]
    if any(value.get("result") == "failure" for value in needs.values()):
        failed = True
        lines += ["", "**At least one matrix job failed after producing evidence.**"]
    lines += [
        "",
        f"Exact tested SHA: `{next(iter(shas)) if len(shas) == 1 else 'unavailable'}`",
        "",
    ]
    lines += [
        "Reproduce one row with:",
        "",
        "```sh",
        f"gh workflow run enhanced-stress.yml -f campaign=CAMPAIGN -f intensity=INTENSITY -f seed={seed}",
        "```",
        "",
    ]
    for key in sorted(expected):
        item = found.get(key)
        if not item:
            continue
        metrics = item.get("measured_totals") or {}
        if metrics:
            lines.append(
                f"- {key[0]} / {key[2] or key[1]}: "
                + ", ".join(
                    f"{name}={value}" for name, value in sorted(metrics.items())[:12]
                )
            )
    summary = "\n".join(lines) + "\n"
    print(summary)
    destination = os.environ.get("GITHUB_STEP_SUMMARY")
    if destination:
        with Path(destination).open("a", encoding="utf-8") as stream:
            stream.write(summary)
    write_json(
        Path("certification-final.json"),
        {
            "schema": SCHEMA,
            "seed": seed,
            "selected": selected,
            "passed": not failed,
            "jobs": list(found.values()),
        },
    )
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
