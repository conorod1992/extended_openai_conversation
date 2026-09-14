"""Print the union of Python requirements needed by upgrade acceptance payloads."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import homeassistant


components_root = Path(next(iter(homeassistant.__path__))) / "components"
requirements: set[str] = set()
pending_domains: list[str] = []

for raw_component_dir in sys.argv[1:]:
    component_dir = Path(raw_component_dir)
    manifest = json.loads((component_dir / "manifest.json").read_text(encoding="utf-8"))
    requirements.update(manifest.get("requirements", []))
    pending_domains.extend(manifest.get("dependencies", []))
    pending_domains.extend(manifest.get("after_dependencies", []))

visited: set[str] = set()
while pending_domains:
    domain = pending_domains.pop()
    if domain in visited:
        continue
    visited.add(domain)
    manifest_path = components_root / domain / "manifest.json"
    if not manifest_path.exists():
        raise RuntimeError(f"Home Assistant dependency manifest not found: {domain}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    requirements.update(manifest.get("requirements", []))
    pending_domains.extend(manifest.get("dependencies", []))
    pending_domains.extend(manifest.get("after_dependencies", []))

# These integrations are imported by HA while traversing the entity/platform graph
# in the clean-process acceptance environment even though they are not always
# reachable through the custom integration's manifest dependency graph.
for domain in ("infrared", "radio_frequency"):
    manifest_path = components_root / domain / "manifest.json"
    if not manifest_path.exists():
        continue
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    requirements.update(manifest.get("requirements", []))

for requirement in sorted(requirements):
    print(requirement)
