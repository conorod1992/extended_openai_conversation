"""Validate and list the two fixed stable pytest file shards.

The manifest was balanced using completed-test timestamps from stable CI run
35937343559. New test files intentionally fail validation until assigned.
"""

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = Path(__file__).with_name("stable_test_shards.json")


def test_files() -> set[str]:
    return {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "tests").rglob("*.py")
        if path.name.startswith("test_") or path.name.endswith("_test.py")
    }


def validated_shards() -> dict[str, list[str]]:
    shards = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if set(shards) != {"a", "b"}:
        raise ValueError("stable shards must be exactly a and b")
    assigned = shards["a"] + shards["b"]
    if any(not isinstance(path, str) or not path.startswith("tests/") or not path.endswith(".py") for path in assigned):
        raise ValueError("stable shard entries must be Python test paths under tests/")
    if len(assigned) != len(set(assigned)):
        raise ValueError("duplicate stable shard assignment")
    expected = test_files()
    if set(assigned) != expected:
        raise ValueError(
            f"stable shard mismatch; missing={sorted(expected - set(assigned))}, "
            f"extra={sorted(set(assigned) - expected)}"
        )
    if any(not paths for paths in shards.values()):
        raise ValueError("stable shards must not be empty")
    return shards


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shard", choices=("a", "b"))
    args = parser.parse_args()
    shards = validated_shards()
    if args.shard:
        print("\n".join(shards[args.shard]))
    else:
        print(
            f"Stable test partition valid: {len(shards['a'])} + "
            f"{len(shards['b'])} Python files"
        )


if __name__ == "__main__":
    main()
