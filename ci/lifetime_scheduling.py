"""Give real-time lifetime jobs a bounded head start, never acceptance credit."""

from __future__ import annotations

from collections.abc import Callable
import json
import os
import time


def lifetime_job_names(rows: list[dict]) -> set[str]:
    return {
        f"long-lifetime / {row['intensity']} / {row['partition']}"
        for row in rows
        if row["campaign"] == "long-lifetime"
    }


def wait_for_lifetime_start(
    get: Callable[[str], dict],
    run_id: int | str,
    run_attempt: int | str,
    candidate_sha: str,
    expected: set[str],
    *,
    timeout: float = 600,
) -> bool:
    """Release short work after every selected lifetime job starts or terminates.

    Failure/timeout of this scheduling hint releases the remaining work. The
    ordinary result and evidence checks still decide whether validation passes.
    Reads are attempt-scoped; another candidate or retry cannot open the gate.
    """
    pending = set(expected)
    deadline = time.monotonic() + timeout
    while pending and time.monotonic() < deadline:
        try:
            for page in range(1, 11):
                if time.monotonic() >= deadline:
                    break
                batch = get(
                    f"actions/runs/{run_id}/attempts/{run_attempt}/jobs"
                    f"?per_page=100&page={page}"
                )["jobs"]
                for job in batch:
                    if (
                        job.get("head_sha") == candidate_sha
                        and job.get("run_attempt") == int(run_attempt)
                        and job.get("status") in {"in_progress", "completed"}
                    ):
                        pending.discard(job["name"])
                if len(batch) < 100 or not pending:
                    break
        except Exception as error:
            print(f"::warning::Lifetime start status unavailable: {error}", flush=True)
            return False
        if pending:
            time.sleep(min(5, max(0, deadline - time.monotonic())))
    if pending:
        print(
            "::warning::Lifetime scheduling head start expired; releasing all "
            f"remaining tests. Not yet started: {', '.join(sorted(pending))}",
            flush=True,
        )
        return False
    if expected:
        print("Lifetime jobs started; releasing remaining validation work", flush=True)
    return True


def main() -> None:
    try:
        from ci.release_certification import GitHubActions
    except ModuleNotFoundError:
        from release_certification import GitHubActions

    actions = GitHubActions(os.environ["GITHUB_REPOSITORY"], os.environ["GH_TOKEN"])
    wait_for_lifetime_start(
        actions.get,
        os.environ["GITHUB_RUN_ID"],
        os.environ["GITHUB_RUN_ATTEMPT"],
        os.environ["GITHUB_SHA"],
        lifetime_job_names(json.loads(os.environ["LIFETIME_MATRIX"])),
    )


if __name__ == "__main__":
    main()
