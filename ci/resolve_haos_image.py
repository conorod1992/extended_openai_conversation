"""Resolve the newest stable HA OS release available for the OVA machine."""

import json
import os
from urllib.request import Request, urlopen


def select_image(releases):
    """Board-specific stable releases need not contain an OVA image."""
    for release in releases:
        if release.get("draft") or release.get("prerelease"):
            continue
        tag = release["tag_name"]
        name = f"haos_ova-{tag}.qcow2.xz"
        matches = [asset for asset in release.get("assets", []) if asset["name"] == name]
        if len(matches) > 1:
            raise ValueError(f"Ambiguous HA OS OVA image in {tag}")
        if matches:
            return tag, matches[0]["browser_download_url"], name
    return None


def main():
    for page in range(1, 11):
        request = Request(
            "https://api.github.com/repos/home-assistant/operating-system/releases"
            f"?per_page=100&page={page}",
            headers={
                "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urlopen(request, timeout=30) as response:
            releases = json.load(response)
        image = select_image(releases)
        if image:
            print("\n".join(image))
            return
        if len(releases) < 100:
            break
    raise SystemExit("No stable HA OS OVA qcow2.xz image found")


if __name__ == "__main__":
    main()
