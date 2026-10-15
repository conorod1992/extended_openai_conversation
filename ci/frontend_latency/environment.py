"""Resolve one compatible environment for both latency revisions and audit drift."""

from __future__ import annotations

import argparse
from importlib.metadata import distributions, requires, version
import json
import os
from pathlib import Path
import subprocess
import sys

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from ci.enhanced_evidence import environment_identity as evidence_environment_identity
from ci.environment_fingerprint import _requirements


def installed_packages() -> dict[str, str]:
    """Include transitive packages, not only the headline HA and SDK versions."""
    return {
        canonicalize_name(dist.metadata["Name"]): dist.version
        for dist in distributions()
        if dist.metadata.get("Name")
    }


def environment_identity() -> dict:
    identity = evidence_environment_identity()
    identity["python_packages"] = installed_packages()
    identity["node"] = subprocess.check_output(["node", "--version"], text=True).strip()
    return identity


def declarations(
    requirements: Path, manifest: Path, components: Path
) -> list[tuple[str, str]]:
    """Read both EOAI and recursively referenced HA integration requirements."""
    result = [(str(requirements), value) for value in _requirements(requirements)]
    pending = [manifest]
    visited = set()
    while pending:
        path = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        result.extend((str(path), value) for value in data.get("requirements", []))
        pending.extend(
            components / domain / "manifest.json"
            for domain in (
                *data.get("dependencies", []),
                *data.get("after_dependencies", []),
            )
        )
    return result


def active_requirements(
    declared: list[tuple[str, str]],
) -> list[tuple[str, Requirement]]:
    result = []
    for source, value in declared:
        try:
            requirement = Requirement(value)
        except InvalidRequirement as err:
            raise ValueError(
                f"{source}: unsupported dependency declaration {value!r}: {err}"
            ) from err
        if requirement.url:
            raise ValueError(
                f"{source}: cannot verify installed provenance for {value!r}; use a versioned dependency"
            )
        if requirement.marker is None or requirement.marker.evaluate():
            result.append((source, requirement))
    return result


def compatibility_errors(
    declared: list[tuple[str, str]], packages: dict[str, str]
) -> list[str]:
    errors = []
    pending = active_requirements(declared)
    visited = set()
    while pending:
        source, requirement = pending.pop(0)
        name = canonicalize_name(requirement.name)
        installed = packages.get(name)
        if installed is None or not requirement.specifier.contains(
            installed, prereleases=True
        ):
            errors.append(
                f"{source}: requires {requirement}; resolved {name}=={installed or '<missing>'}"
            )
        if (
            installed is not None
            and requirement.extras
            and str(requirement) not in visited
        ):
            visited.add(str(requirement))
            for value in requires(requirement.name) or []:
                dependency = Requirement(value)
                if dependency.marker is None or any(
                    dependency.marker.evaluate({"extra": extra})
                    for extra in requirement.extras
                ):
                    dependency.marker = None
                    pending.extend(
                        active_requirements(
                            [(f"{source} ({requirement} extra)", str(dependency))]
                        )
                    )
    return errors


def environment_differences(
    before: dict, after: dict, path: str = "environment"
) -> list[str]:
    """Identify every differing pin/runtime component instead of opaque hashes."""
    errors = []
    for key in sorted(before.keys() | after.keys()):
        left, right = before.get(key, "<missing>"), after.get(key, "<missing>")
        component = f"{path}.{key}"
        if isinstance(left, dict) and isinstance(right, dict):
            errors.extend(environment_differences(left, right, component))
        elif left != right:
            errors.append(f"{component}: baseline={left!r}, candidate={right!r}")
    return errors


def verified_environment_identity() -> dict:
    """Reject mutations before and after a benchmark against its prepared pins."""
    path = Path(
        os.environ.get("EOAI_LATENCY_ENVIRONMENT", "latency-results/environment.json")
    )
    expected = json.loads(path.read_text(encoding="utf-8"))["environment"]
    actual = environment_identity()
    errors = environment_differences(expected, actual)
    if errors:
        raise SystemExit(
            "Latency environment changed after preparation:\n" + "\n".join(errors)
        )
    return actual


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for label in ("baseline", "current"):
        parser.add_argument(f"--{label}-requirements", type=Path, required=True)
        parser.add_argument(f"--{label}-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    import homeassistant

    components = Path(next(iter(homeassistant.__path__))) / "components"
    declared = []
    for label in ("baseline", "current"):
        declared.extend(
            declarations(
                getattr(args, f"{label}_requirements"),
                getattr(args, f"{label}_manifest"),
                components,
            )
        )
    # Match the existing HA dependency installer's OpenAI constraint. Core/test
    # plugin versions stay pinned to this job's image, never upgraded from PyPI.
    constraint = components.parent / "package_constraints.txt"
    declared.extend(
        (str(constraint), value)
        for value in _requirements(constraint)
        if canonicalize_name(Requirement(value).name) == "openai"
    )
    pins = [
        f"{name}=={version(name)}"
        for name in ("homeassistant", "pytest-homeassistant-custom-component")
    ]
    declared.extend(("CI image pin", pin) for pin in pins)
    errors = compatibility_errors(declared, installed_packages())
    if errors:
        print(
            "Reconciling one shared environment for both revisions:\n"
            + "\n".join(errors),
            flush=True,
        )
        requirements = sorted(
            {str(requirement) for _, requirement in active_requirements(declared)}
        )
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", *requirements], check=True
            )
        except subprocess.CalledProcessError as err:
            raise SystemExit(
                "Cannot resolve a shared environment with the pinned HA/test plugin. Review the conflicting requirements above and pip's dependency conflict report; the baseline was not changed."
            ) from err
    errors = compatibility_errors(declared, installed_packages())
    if errors:
        raise SystemExit(
            "Latency dependency compatibility rejected:\n" + "\n".join(errors)
        )
    subprocess.run([sys.executable, "-m", "pip", "check"], check=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {"environment": environment_identity(), "validated_declarations": declared},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        "Both revisions accept the same resolved Python environment; HA/test plugin pins preserved."
    )


if __name__ == "__main__":
    main()
