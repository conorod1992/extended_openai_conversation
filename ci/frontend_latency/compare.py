"""Compare manual latency diagnostic JSON artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from statistics import median
import sys
from typing import Any


def pct(delta: float, baseline: float) -> str:
    if baseline == 0:
        return "n/a"
    return f"{(delta / baseline) * 100:+.1f}%"


def browser_medians(payload: dict[str, Any]) -> dict[str, dict[str, float]]:
    by_route: dict[str, dict[str, list[float]]] = {}
    for sample in payload["browser"]["samples"]:
        route = sample["route"]
        bucket = by_route.setdefault(
            route,
            {"ready": [], "lcp": [], "assets": []},
        )
        bucket["ready"].append(float(sample["wall_ready_ms"]))
        if float(sample.get("lcp_ms", 0)) > 0:
            bucket["lcp"].append(float(sample["lcp_ms"]))
        bucket["assets"].append(float(sample.get("integration_assets_response_end_ms", 0)))
    return {
        route: {
            "ready_ms": median(values["ready"]),
            "lcp_ms": median(values["lcp"]) if values["lcp"] else 0.0,
            "assets_ms": median(values["assets"]),
        }
        for route, values in by_route.items()
    }


def row(label: str, baseline: float, candidate: float) -> str:
    delta = candidate - baseline
    return (
        f"| {label} | {baseline:.1f} | {candidate:.1f} | "
        f"{delta:+.1f} | {pct(delta, baseline)} |"
    )


def main() -> None:
    baseline_path, candidate_path, output_dir = map(Path, sys.argv[1:4])
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=True)

    lines = [
        "# Frontend latency diagnostics",
        "",
        "Same-run genuine Home Assistant comparison. Negative deltas mean current develop is faster than the baseline.",
        "",
        "## Backend WebSocket timings",
        "",
        "| Operation | baseline ms | current ms | delta ms | delta % |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    comparison: dict[str, Any] = {"backend": {}, "browser": {}}

    for name in baseline["backend"]:
        if name not in current["backend"]:
            continue
        before = float(baseline["backend"][name]["median_ms"])
        after = float(current["backend"][name]["median_ms"])
        lines.append(row(name, before, after))
        comparison["backend"][name] = {
            "develop_ms": before,
            "candidate_ms": after,
            "delta_ms": after - before,
        }

    before_routes = browser_medians(baseline)
    after_routes = browser_medians(current)
    lines += [
        "",
        "## Browser cold-route ready time",
        "",
        "| Route | baseline ms | current ms | delta ms | delta % |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for route in before_routes:
        if route not in after_routes:
            continue
        before = before_routes[route]["ready_ms"]
        after = after_routes[route]["ready_ms"]
        lines.append(row(route, before, after))
        comparison["browser"][route] = {
            "develop": before_routes[route],
            "candidate": after_routes[route],
            "ready_delta_ms": after - before,
        }

    lines += [
        "",
        "## Notes",
        "",
        "- Absolute GitHub-runner timings are not expected to match an Odroid/LAN install.",
        "- The same runner executes develop and candidate sequentially, making deltas useful for repository-caused regressions and improvements.",
        "- Raw JSON includes LCP, EOAI performance marks/measures, and integration resource timing for deeper diagnosis.",
        "",
    ]

    (output_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    (output_dir / "comparison.json").write_text(
        json.dumps(comparison, indent=2, sort_keys=True),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
