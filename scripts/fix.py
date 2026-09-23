"""Apply deterministic repository hygiene fixes before final tests."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys

TEXT_SUFFIXES = {
    ".cfg",
    ".css",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".mjs",
    ".ps1",
    ".py",
    ".pyi",
    ".sh",
    ".toml",
    ".ts",
    ".txt",
    ".yaml",
    ".yml",
}
TEXT_NAMES = {".editorconfig", ".gitattributes", ".gitignore", "LICENSE"}
TRIM_SUFFIXES = TEXT_SUFFIXES - {".md"}
GENERATED_FRONTEND = Path(
    "custom_components/extended_openai_conversation_responses/frontend/dist"
)


def _tracked_text_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=False,
        capture_output=True,
    )
    if result.returncode:
        sys.stderr.buffer.write(result.stderr)
        raise RuntimeError("git ls-files failed")

    paths: list[Path] = []
    for raw_path in result.stdout.split(b"\0"):
        if not raw_path:
            continue
        relative = Path(raw_path.decode("utf-8"))
        if relative == GENERATED_FRONTEND or GENERATED_FRONTEND in relative.parents:
            continue
        if relative.suffix.lower() in TEXT_SUFFIXES or relative.name in TEXT_NAMES:
            paths.append(root / relative)
    return paths


def _normalize_text_file(path: Path) -> bool:
    raw = path.read_bytes()
    if b"\0" in raw:
        return False
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return False

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if path.suffix.lower() in TRIM_SUFFIXES:
        normalized = "\n".join(line.rstrip(" \t") for line in normalized.split("\n"))
    if normalized and not normalized.endswith("\n"):
        normalized += "\n"

    encoded = normalized.encode("utf-8")
    if encoded == raw:
        return False
    path.write_bytes(encoded)
    return True


def _run_ruff(root: Path) -> int:
    ruff = shutil.which("ruff")
    if ruff:
        command = [ruff]
    elif importlib.util.find_spec("ruff") is not None:
        command = [sys.executable, "-m", "ruff"]
    else:
        print(
            "Ruff is unavailable. Install the pinned development dependencies with "
            'python -m pip install -e ".[dev]".',
            file=sys.stderr,
        )
        return 127

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


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    try:
        changed = sum(_normalize_text_file(path) for path in _tracked_text_files(root))
    except (OSError, RuntimeError, UnicodeDecodeError) as error:
        print(f"Could not normalize repository text files: {error}", file=sys.stderr)
        return 1

    if changed:
        print(f"Normalized repository text hygiene in {changed} tracked file(s).")

    return _run_ruff(root)


if __name__ == "__main__":
    raise SystemExit(main())
