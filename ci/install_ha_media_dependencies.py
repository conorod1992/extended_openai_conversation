"""Install Home Assistant media component requirements used by AI Task tests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

import homeassistant


_MEDIA_DOMAINS = ("camera", "stream")


def _media_requirements() -> list[str]:
    components = Path(next(iter(homeassistant.__path__))) / "components"
    requirements: set[str] = set()
    for domain in _MEDIA_DOMAINS:
        manifest = json.loads(
            (components / domain / "manifest.json").read_text(encoding="utf-8")
        )
        requirements.update(manifest.get("requirements", []))
    return sorted(requirements)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--marker",
        type=Path,
        help="Write the resolved requirement set here after installation succeeds.",
    )
    args = parser.parse_args()

    requirements = _media_requirements()
    if requirements:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", *requirements]
        )

    if args.marker is not None:
        args.marker.parent.mkdir(parents=True, exist_ok=True)
        args.marker.write_text(
            "\n".join(requirements) + ("\n" if requirements else ""),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
