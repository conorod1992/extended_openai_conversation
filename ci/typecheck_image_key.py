#!/usr/bin/env python3
"""Key the Type Check image to the environment it installs, not the HA dev SHA."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

BUILD_INPUTS = (
    "PYTHON_VERSION",
    "OPENAI_VERSION",
    "HASSIL_VERSION",
    "HA_INTENTS_VERSION",
    "XMLTODICT_VERSION",
    "BEAUTIFULSOUP4_VERSION",
    "LXML_VERSION",
)


def main() -> None:
    digest = hashlib.sha256()
    for name in BUILD_INPUTS:
        value = os.environ[name]
        if not value:
            raise ValueError(f"{name} is empty")
        digest.update(f"{name}={value}\n".encode())

    for path in (Path(__file__), Path(__file__).with_name("Dockerfile.typecheck")):
        digest.update(path.read_bytes())

    print(digest.hexdigest())


if __name__ == "__main__":
    main()
