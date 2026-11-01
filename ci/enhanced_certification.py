"""Consolidate actual matrix job evidence into one nightly certification view."""

from __future__ import annotations

from collections import Counter
import json
import os
from pathlib import Path
import sys
from urllib.request import Request, urlopen

try:
    from .actions_schedule import CAMPAIGNS
    from .candidate_evidence import check_candidate, valid_sha
    from .compatibility_evidence import check_ha_environment, check_stable_point
    from .enhanced_evidence import SCHEMA, write_json
    from .execution_contract import CONTRACT, check_execution, expected_cases
except ImportError:
    from actions_schedule import CAMPAIGNS
    from candidate_evidence import check_candidate, valid_sha
    from compatibility_evidence import check_ha_environment, check_stable_point
    from enhanced_evidence import SCHEMA, write_json
    from execution_contract import CONTRACT, check_execution, expected_cases


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


def merge_lifetime_partitions(rows: list[dict]) -> dict:
    """Join two isolated processes without relaxing any campaign obligations."""
    return merge_partitions(
        rows, "long-lifetime", "lifetime_partition", {"reload", "uninterrupted"}
    )


def merge_browser_partitions(rows: list[dict]) -> dict:
    """Join fixture and genuine-HA journeys, retaining reviewed fixture skips."""
    return merge_partitions(rows, "browser", "browser_partition", {"static", "native"})


def merge_partitions(
    rows: list[dict], campaign: str, field: str, partitions: set[str]
) -> dict:
    assert len(rows) == 2
    assert {row.get(field) for row in rows} == partitions
    first, second = rows
    for key in (
        "schema",
        "campaign",
        "intensity",
        "seed",
        "eoai_sha",
        "ha_point",
        "ha_version",
        "environment",
        "environment_fingerprint",
        "source_version",
        "target_version",
    ):
        assert first.get(key) == second.get(key), key
    assert first["campaign"] == campaign
    nodes = [{case["nodeid"] for case in row["execution_cases"]} for row in rows]
    overlap = nodes[0] & nodes[1]
    if campaign == "browser":
        policy = json.loads(CONTRACT.read_text(encoding="utf-8"))
        inventory = json.loads(
            CONTRACT.with_name("browser_partition_contract.json").read_text(
                encoding="utf-8"
            )
        )
        assert inventory["schema"] == 1
        assert set(inventory["partitions"]) == partitions
        for row in rows:
            part = row[field]
            # Compare multisets: a passing native copy cannot conceal a missing
            # static copy, nor can the main suite replace its targeted recheck.
            assert Counter(
                case["nodeid"] for case in row["execution_cases"]
            ) == Counter(inventory["partitions"][part]), (
                f"Browser partition inventory mismatch: {part}"
            )
            for case in row["execution_cases"]:
                if part == "native" or case["outcome"] != "skipped":
                    assert case["outcome"] == "passed", case["nodeid"]
                else:
                    allowance = policy["allowances"].get(case["nodeid"], {})
                    assert allowance.get("requires_pass") is True
                    assert "skipped" in allowance.get("outcomes", [])
        # check_execution still enforces allowance expiry, review, and a genuine pass.
    else:
        assert not overlap, "Overlapping lifetime partitions"
    merged = dict(first)
    merged[field] = "merged"
    merged["status"] = (
        "success" if all(row["status"] == "success" for row in rows) else "failure"
    )
    for key in ("execution_cases", "execution_runs", "artifact_files"):
        merged[key] = [value for row in rows for value in row.get(key, [])]
    for key in ("measured_totals", "trace_outcomes"):
        total = Counter()
        for row in rows:
            values = row.get(key, {})
            # Addition must not turn a malformed boolean into a valid integer
            # before the ordinary execution-contract metric checks see it.
            assert all(
                isinstance(value, (int, float)) and not isinstance(value, bool)
                for value in values.values()
            ), key
            total.update(values)
        merged[key] = dict(total)
    merged["tests_traced"] = sum(row.get("tests_traced", 0) for row in rows)
    return merged


def main(root: Path) -> int:
    candidate_sha = os.environ.get("ENHANCED_CANDIDATE_SHA", "unknown")
    expected_stable = os.environ.get("ENHANCED_EXPECTED_STABLE_HA_VERSION")
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
        if not (
            os.environ.get("GITHUB_EVENT_NAME") == "schedule"
            and campaign == "long-lifetime"
            and intensity == "heavy"
        )
    }
    if selected in {"all", "all-except-long-lifetime", "browser", "diagnostics"}:
        browser_campaign = (
            "browser-diagnostics" if selected == "diagnostics" else "browser"
        )
        expected |= {(browser_campaign, intensity, None) for intensity in intensities}
    if selected in {"all", "all-except-long-lifetime", "browser", "browser-engines"}:
        expected |= {
            (f"browser-{engine}", intensity, None)
            for engine in ("firefox", "webkit")
            for intensity in intensities
        }
    if selected in {"all", "all-except-long-lifetime", "lifecycle"}:
        expected |= {
            ("lifecycle-matrix", "normal", point)
            for point in ("oldest", "stable", "dev")
        }
    found = {}
    artifact_names = {}
    partitioned = {}
    for path in root.rglob("certification.json"):
        item = json.loads(path.read_text(encoding="utf-8"))
        assert item["schema"] == SCHEMA, path
        key = (item["campaign"], item["intensity"], item.get("ha_point"))
        if item.get("lifetime_partition") or item.get("browser_partition"):
            partitioned.setdefault(key, []).append(item)
            artifact_names[key] = "isolated execution partitions"
            continue
        assert key not in found, key
        found[key] = item
        artifact_names[key] = path.parent.name
    for key, rows in partitioned.items():
        assert key not in found, key
        found[key] = (
            merge_browser_partitions(rows)
            if key[0] == "browser"
            else merge_lifetime_partitions(rows)
        )
    full_programme = (
        selected == "all"
        and set(campaigns) == set(CAMPAIGNS)
        and set(intensities) == {"normal", "heavy"}
    )
    scope = "full-programme" if full_programme else "selected-campaigns"
    lines = [
        f"## Enhanced certification — {scope}",
        "",
        f"Intended candidate SHA: `{candidate_sha}`",
        "",
        f"Run: `{os.environ.get('GITHUB_RUN_ID', 'local')}` · Seed: `{seed}` · Selected: `{selected}`",
        "",
        f"Scope: **{scope}** · Intensities: `{', '.join(intensities)}` · Expected rows: **{len(expected)}**",
        "",
        *(
            []
            if full_programme
            else [
                "This run certifies only the selected campaigns and intensities. It does not certify the full Enhanced programme.",
                "",
            ]
        ),
        "| Campaign | Intensity / HA | Result | SHA | HA | Evidence |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    failed = not valid_sha(candidate_sha)
    identity_errors = (
        [] if not failed else [f"Invalid intended candidate SHA: {candidate_sha!r}"]
    )
    shas = set()
    execution_errors = []
    for key in sorted(expected):
        campaign, intensity, point = key
        item = found.get(key)
        job_name = (
            f"HA {point} / shared lifecycle contract"
            if point
            else f"{'browser' if campaign == 'browser-diagnostics' else campaign} / {intensity}"
        )
        actual = conclusions.get(job_name, "unverified")
        if item and (
            item.get("lifetime_partition") == "merged"
            or item.get("browser_partition") == "merged"
        ):
            partitions = (
                ("static", "native")
                if campaign == "browser"
                else ("reload", "uninterrupted")
            )
            partition_results = [
                conclusions.get(f"{job_name} / {partition}", "unverified")
                for partition in partitions
            ]
            actual = (
                "success" if partition_results == ["success", "success"] else "failure"
            )
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
            identity_errors.extend(check_candidate(item, candidate_sha))
            if point in {None, "stable"}:
                if not expected_stable:
                    identity_errors.append("Stable lane has no intended HA version")
                identity_errors.extend(
                    check_ha_environment(
                        item,
                        version=expected_stable,
                        plugin_version=os.environ.get(
                            "ENHANCED_EXPECTED_HA_TEST_PLUGIN_VERSION"
                        ),
                    )
                )
            elif point == "oldest":
                minimum = json.loads(
                    (Path(__file__).resolve().parents[1] / "hacs.json").read_text(
                        encoding="utf-8"
                    )
                )["homeassistant"]
                identity_errors.extend(check_ha_environment(item, version=minimum))
            elif point == "dev":
                intended_dev = os.environ.get("ENHANCED_EXPECTED_HA_DEV_SHA")
                if not valid_sha(intended_dev):
                    identity_errors.append("Dev lane has no intended HA source SHA")
                else:
                    identity_errors.extend(
                        check_ha_environment(item, source_sha=intended_dev)
                    )
            identity_errors.extend(
                check_stable_point(
                    item, point, expected_stable, require_reported_version=True
                )
            )
            if selected != "diagnostics":
                execution_errors.extend(check_execution(item))
        lines.append(
            f"| {campaign} | {point or intensity} | **{status}** | "
            f"`{item['eoai_sha'][:12]}` | `{item.get('ha_version') or '-'}` | "
            f"`{artifact_names[key]}` |"
            if item
            else f"| {campaign} | {point or intensity} | **missing** | - | - | - |"
        )
    if identity_errors:
        failed = True
        lines += ["", "**Candidate/environment evidence rejected:**", ""]
        lines += [f"- {error}" for error in identity_errors]
    if execution_errors:
        failed = True
        lines += ["", "**Mandatory execution evidence rejected:**", ""]
        lines += [f"- {error}" for error in execution_errors]
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
    lines += [
        "",
        f"Selected scope: **{'FAILED' if failed else 'PASSED'}**. "
        + (
            "Full Enhanced programme: **PASSED**."
            if full_programme and not failed
            else "Full Enhanced programme has not been certified by this run."
        ),
    ]
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
            "candidate_sha": candidate_sha,
            "expected_stable_ha_version": expected_stable,
            "identity_errors": identity_errors,
            "seed": seed,
            "selected": selected,
            "scope": scope,
            "intensities": intensities,
            "expected_rows": len(expected),
            "full_programme_passed": full_programme and not failed,
            "passed": not failed,
            "jobs": list(found.values()),
            "execution_errors": execution_errors,
            "expected_cases": {
                campaign: sorted(
                    expected_cases(
                        json.loads(CONTRACT.read_text(encoding="utf-8")), campaign
                    )
                )
                for campaign, _, _ in expected
                if selected != "diagnostics"
            },
        },
    )
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
