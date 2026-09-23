"""Small structural checks for the MkDocs documentation tree."""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


def main() -> int:
    config = yaml.safe_load((ROOT / "mkdocs.yml").read_text(encoding="utf-8"))
    failures: list[str] = []

    if (DOCS / "docs.json").exists():
        failures.append("docs/docs.json remains as a competing docs source")
    if list(DOCS.rglob("*.mdx")):
        failures.append("MDX files remain under docs/")

    def check_nav(value: object) -> None:
        if isinstance(value, dict):
            for label, target in value.items():
                if isinstance(target, str) and target.endswith((".md", ".yml")):
                    if not (DOCS / target).is_file():
                        failures.append(f"nav target missing: {label}: {target}")
                else:
                    check_nav(target)
        elif isinstance(value, list):
            for item in value:
                check_nav(item)

    check_nav(config.get("nav", []))

    for page in DOCS.rglob("*.md"):
        source = page.read_text(encoding="utf-8")
        source = re.sub(r"```.*?```", "", source, flags=re.S)
        for match in LINK_RE.finditer(source):
            target = match.group(1).strip().split()[0].strip("<>")
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            path = (page.parent / unquote(parsed.path)).resolve()
            if not path.is_file():
                failures.append(f"broken local link: {page.relative_to(ROOT)} -> {target}")

    if failures:
        print("\n".join(failures))
        return 1
    print("Documentation navigation and local links are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
