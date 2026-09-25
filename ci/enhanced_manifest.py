"""Render the reviewed enhanced evidence map as a small Markdown table."""

from __future__ import annotations

import json
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads(
        (root / "tests_stress" / "evidence_manifest.json").read_text(encoding="utf-8")
    )
    layers = manifest["layers"]
    print("| Feature | " + " | ".join(layers) + " |")
    print("| --- | " + " | ".join("---" for _ in layers) + " |")
    for feature, evidence in manifest["features"].items():
        cells = [
            str(len(evidence.get(layer, []))) if layer in evidence else ""
            for layer in layers
        ]
        print("| " + feature + " | " + " | ".join(cells) + " |")


if __name__ == "__main__":
    main()
