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


def browser_medians(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    by_route: dict[str, dict[str, Any]] = {}
    for sample in payload["browser"]["samples"]:
        route = sample["route"]
        bucket = by_route.setdefault(
            route,
            {
                "ready": [],
                "lcp": [],
                "assets": [],
                "supported": True,
                "unavailable_reason": None,
            },
        )
        if sample.get("supported") is False:
            bucket["supported"] = False
            bucket["unavailable_reason"] = sample.get("unavailable_reason")
            continue
        bucket["ready"].append(float(sample["wall_ready_ms"]))
        if float(sample.get("lcp_ms", 0)) > 0:
            bucket["lcp"].append(float(sample["lcp_ms"]))
        bucket["assets"].append(float(sample.get("integration_assets_response_end_ms", 0)))

    result: dict[str, dict[str, Any]] = {}
    for route, values in by_route.items():
        if not values["ready"]:
            result[route] = {
                "supported": False,
                "ready_ms": None,
                "lcp_ms": None,
                "assets_ms": None,
                "unavailable_reason": values["unavailable_reason"],
            }
            continue
        result[route] = {
            "supported": True,
            "ready_ms": median(values["ready"]),
            "lcp_ms": median(values["lcp"]) if values["lcp"] else 0.0,
            "assets_ms": median(values["assets"]),
        }
    return result


def row(label: str, baseline: float, current: float) -> str:
    delta = current - baseline
    return (
        f"| {label} | {baseline:.1f} | {current:.1f} | "
        f"{delta:+.1f} | {pct(delta, baseline)} |"
    )


def main() -> None:
    baseline_path, current_path, output_dir = map(Path, sys.argv[1:4])
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    current = json.loads(current_path.read_text(encoding="utf-8"))
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

    backend_names = list(baseline["backend"])
    backend_names.extend(name for name in current["backend"] if name not in baseline["backend"])
    for name in backend_names:
        before_entry = baseline["backend"].get(name, {})
        after_entry = current["backend"].get(name, {})
        before = before_entry.get("median_ms")
        after = after_entry.get("median_ms")
        if before is not None and after is not None:
            before_value = float(before)
            after_value = float(after)
            lines.append(row(name, before_value, after_value))
            comparison["backend"][name] = {
                "baseline_ms": before_value,
                "current_ms": after_value,
                "delta_ms": after_value - before_value,
            }
            continue

        before_text = "n/a" if before is None else f"{float(before):.1f}"
        after_text = "n/a" if after is None else f"{float(after):.1f}"
        lines.append(f"| {name} | {before_text} | {after_text} | n/a | n/a |")
        comparison["backend"][name] = {
            "baseline_ms": None if before is None else float(before),
            "current_ms": None if after is None else float(after),
            "baseline_supported": before_entry.get("supported", before is not None),
            "current_supported": after_entry.get("supported", after is not None),
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
    route_names = list(before_routes)
    route_names.extend(route for route in after_routes if route not in before_routes)
    for route in route_names:
        before_entry = before_routes.get(route, {})
        after_entry = after_routes.get(route, {})
        before = before_entry.get("ready_ms")
        after = after_entry.get("ready_ms")
        if before is not None and after is not None:
            before_value = float(before)
            after_value = float(after)
            lines.append(row(route, before_value, after_value))
            comparison["browser"][route] = {
                "baseline": before_entry,
                "current": after_entry,
                "ready_delta_ms": after_value - before_value,
            }
            continue

        before_text = "n/a" if before is None else f"{float(before):.1f}"
        after_text = "n/a" if after is None else f"{float(after):.1f}"
        lines.append(f"| {route} | {before_text} | {after_text} | n/a | n/a |")
        comparison["browser"][route] = {
            "baseline": before_entry or None,
            "current": after_entry or None,
            "baseline_supported": before_entry.get("supported", before is not None),
            "current_supported": after_entry.get("supported", after is not None),
        }

    lines += [
        "",
        "## Notes",
        "",
        "- Absolute GitHub-runner timings are not expected to match an Odroid/LAN install.",
        "- The same runner executes the baseline and current develop sequentially, making deltas useful for repository-caused regressions and improvements.",
        "- Backend operations unavailable on the historical baseline are shown as `n/a`; current develop must still support every measured operation.\n- Raw JSON includes LCP, EOAI performance marks/measures, and integration resource timing for deeper diagnosis.",
        "",
    ]

    (output_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    (output_dir / "comparison.json").write_text(
        json.dumps(comparison, indent=2, sort_keys=True),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
