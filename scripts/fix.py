"""Apply the repository's Ruff fixes and formatting before final tests."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys


def main() -> int:
    ruff = shutil.which("ruff")
    if ruff:
        command = [ruff]
    elif importlib.util.find_spec("ruff") is not None:
        command = [sys.executable, "-m", "ruff"]
    else:
        print(
            "Ruff is unavailable. Install it with `python -m pip install ruff`.",
            file=sys.stderr,
        )
        return 127

    root = Path(__file__).resolve().parents[1]
    for arguments in (
        ("check", "--fix", "custom_components/"),
        ("format", "custom_components/"),
    ):
        try:
            result = subprocess.run([*command, *arguments], cwd=root, check=False)
        except OSError as error:
            print(f"Could not run Ruff: {error}", file=sys.stderr)
            return 127
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
