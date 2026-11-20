"""Remove temporary branches after cancelled validation cohorts have drained."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
import json
import re
import subprocess
import sys
from urllib.parse import urlencode

REF_PREFIX = "refs/heads/"
COHORT_REF = re.compile(r"refs/heads/full-validation-([1-9][0-9]*)-([1-9][0-9]*)")
CANCEL_GRACE = timedelta(hours=1)


def command(*args: str) -> str:
    return subprocess.check_output(args, text=True, encoding="utf-8", timeout=60)


def api(repository: str, path: str, *options: str):
    return json.loads(command("gh", "api", f"repos/{repository}/{path}", *options))


def eligible(repository: str, ref: str, sha: str, now: datetime) -> bool:
    match = COHORT_REF.fullmatch(ref)
    if not match:
        return False
    run_id, attempt = match.groups()
    path = f"actions/runs/{run_id}"
    latest = api(repository, path)
    if latest["status"] != "completed":
        return False
    parent = (
        latest
        if latest["run_attempt"] == int(attempt)
        else api(repository, f"{path}/attempts/{attempt}")
    )
    if (
        parent["id"] != int(run_id)
        or parent["run_attempt"] != int(attempt)
        or parent["path"] != ".github/workflows/full-validation.yml"
        or parent["event"] != "workflow_dispatch"
        or (parent["head_repository"] or {}).get("full_name") != repository
        or parent["head_sha"] != sha
        or parent["status"] != "completed"
        or parent["conclusion"] != "cancelled"
        or now - datetime.fromisoformat(parent["updated_at"]) < CANCEL_GRACE
    ):
        return False

    # Include every run on this exact branch, even if it was queued by a
    # different event or rerun independently of its parent.
    branch = ref.removeprefix(REF_PREFIX)
    query = urlencode({"branch": branch, "per_page": 100})
    pages = api(repository, f"actions/runs?{query}", "--paginate", "--slurp")
    runs = {run["id"]: run for page in pages for run in page["workflow_runs"]}
    if not pages or any(page["total_count"] != len(runs) for page in pages):
        raise ValueError(f"Incomplete workflow listing for {branch}")
    return all(
        run["head_branch"] == branch and run["status"] == "completed"
        for run in runs.values()
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("branches", nargs="*", help="Optional exact branch allowlist")
    args = parser.parse_args()
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", args.repo, flags=re.ASCII):
        parser.error("--repo must be a GitHub owner/repository")
    allowed = {REF_PREFIX + branch for branch in args.branches}
    if any(not COHORT_REF.fullmatch(ref) for ref in allowed):
        parser.error("Expected full-validation-RUN-ATTEMPT branches")
    remote = f"https://github.com/{args.repo}.git"
    refs = command(
        "git", "ls-remote", "--heads", remote, f"{REF_PREFIX}full-validation-*"
    )
    failed = False
    for line in refs.splitlines():
        sha, ref = line.split("\t")
        if not COHORT_REF.fullmatch(ref) or (allowed and ref not in allowed):
            continue
        try:
            if not eligible(args.repo, ref, sha, datetime.now(UTC)):
                print(f"Keeping {ref}")
                continue
            if args.apply:
                # The server rejects this deletion if the branch tip changed
                # after inspection. A rerun of the parent gets a new ref.
                command(
                    "git", "push", f"--force-with-lease={ref}:{sha}", remote, f":{ref}"
                )
                print(f"Deleted {ref}")
            else:
                print(f"Would delete {ref}")
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
            print(f"Could not clean up {ref}: {error}", file=sys.stderr)
            failed = True
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
