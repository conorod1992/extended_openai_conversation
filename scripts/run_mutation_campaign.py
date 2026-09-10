"""Run a campaign with isolated configuration and the pinned Mutmut CLI."""

import argparse
from collections import Counter
from contextlib import contextmanager
from fnmatch import fnmatchcase
from importlib.metadata import version
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib


def campaign_config(config, campaign):
    """Keep the checked-in Function Tool configuration as the local default."""
    selected = config["mutation-campaigns"][campaign]
    return {
        **config["mutmut"],
        **{key: value for key, value in selected.items() if key != "selectors"},
    }


@contextmanager
def configured_campaign(path, config):
    """Restore the exact original bytes, including on test/tool failures."""
    original = path.read_bytes()
    text = original.decode("utf-8")
    section = (
        "[tool.mutmut]\n"
        + "\n".join(
            f"{key} = {json.dumps(value, ensure_ascii=False)}"
            for key, value in config.items()
        )
        + "\n\n"
    )
    updated, count = re.subn(
        r"(?ms)^\[tool\.mutmut\]\n.*?(?=^\[|\Z)", lambda _: section, text
    )
    if count != 1:
        raise ValueError("Expected exactly one [tool.mutmut] section")
    try:
        path.write_text(updated, encoding="utf-8", newline="")
        yield
    finally:
        path.write_bytes(original)


def selected_results(output, selectors):
    """Filter CLI results and reject a typo instead of claiming an empty run."""
    selected = {}
    for line in output.splitlines():
        name, separator, status = line.strip().partition(": ")
        if separator and any(fnmatchcase(name, selector) for selector in selectors):
            selected[name] = status
    for selector in selectors:
        if not any(fnmatchcase(name, selector) for name in selected):
            raise ValueError(f"No generated mutants matched selector: {selector}")
    return selected


def require_complete(selected):
    """Survivors need review; unchecked/error/timeout results need another run."""
    incomplete = {
        status for status in selected.values() if status not in {"killed", "survived"}
    }
    if incomplete:
        raise ValueError(f"Campaign has unresolved results: {sorted(incomplete)}")


def main():
    path = Path("pyproject.toml")
    config = tomllib.loads(path.read_text(encoding="utf-8"))["tool"]
    campaigns = config["mutation-campaigns"]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign", choices=campaigns)
    parser.add_argument("--baseline-only", action="store_true")
    parser.add_argument(
        "--target", default="", help="Optional literal Mutmut selector override"
    )
    args = parser.parse_args()
    selectors = [args.target] if args.target else campaigns[args.campaign]["selectors"]
    selected_config = campaign_config(config, args.campaign)
    with configured_campaign(path, selected_config):
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                *selected_config["pytest_add_cli_args_test_selection"],
            ],
            check=True,
        )
        if args.baseline_only:
            return
        if version("mutmut") != "3.7.0":
            raise SystemExit("This campaign's selectors require mutmut==3.7.0")
        root = Path.cwd().resolve()
        generated = root / "mutants"
        if generated.is_symlink() or generated.resolve() != generated:
            raise ValueError("Refusing to remove redirected mutants directory")
        if generated.exists():
            shutil.rmtree(generated)
        command = ["mutmut"]
        run = subprocess.run([*command, "run", *selectors], check=False)
        results = subprocess.run(
            [*command, "results", "--all", "true"],
            check=True,
            capture_output=True,
            text=True,
        )
        selected = selected_results(results.stdout, selectors)
        for name, status in selected.items():
            print(f"{name}: {status}", flush=True)
        print(f"{args.campaign}: {dict(Counter(selected.values()))}", flush=True)
        for name, status in selected.items():
            if status == "survived":
                subprocess.run([*command, "show", name], check=True)
        if run.returncode:
            raise SystemExit(run.returncode)
        require_complete(selected)


if __name__ == "__main__":
    main()
