#!/usr/bin/env bash
set -euo pipefail

REQUIREMENTS_FILE="${1:-requirements_test.txt}"
MANIFEST_FILE="${2:-custom_components/extended_openai_conversation_responses/manifest.json}"
MEDIA_MARKER="${3:-/tmp/eoai-ha-dev-media-dependencies.ready}"

TMP_REQUIREMENTS="$(mktemp)"
TMP_PLUGIN_DEPS="$(mktemp)"
trap 'rm -f "$TMP_REQUIREMENTS" "$TMP_PLUGIN_DEPS"' EXIT

grep -v '^pytest-homeassistant-custom-component' "$REQUIREMENTS_FILE" > "$TMP_REQUIREMENTS"
python -m pip install -r "$TMP_REQUIREMENTS"

PLUGIN_SPEC="$(grep '^pytest-homeassistant-custom-component' "$REQUIREMENTS_FILE")"
test -n "$PLUGIN_SPEC"
python -m pip install --no-deps "$PLUGIN_SPEC"

PLUGIN_DEPS_FILE="$TMP_PLUGIN_DEPS" python - <<'PY'
import os
from importlib.metadata import requires
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from pathlib import Path

plugin = "pytest-homeassistant-custom-component"
filtered = []
for spec in requires(plugin) or []:
    requirement = Requirement(spec)
    if canonicalize_name(requirement.name) == "homeassistant":
        continue
    if requirement.marker is not None and not requirement.marker.evaluate():
        continue
    filtered.append(spec)

path = Path(os.environ["PLUGIN_DEPS_FILE"])
path.write_text("\n".join(filtered) + "\n", encoding="utf-8")
PY
python -m pip install -r "$TMP_PLUGIN_DEPS"

python ci/install_ha_dependencies.py --manifest "$MANIFEST_FILE"
python ci/install_ha_media_dependencies.py --marker "$MEDIA_MARKER"
python -m pip check
