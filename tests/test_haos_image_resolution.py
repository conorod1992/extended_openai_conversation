"""Board-only releases must not break validation on the OVA machine."""

import pytest

from ci.resolve_haos_image import select_image


def release(tag, *, vm=True, **flags):
    return {
        "tag_name": tag,
        "assets": [{
            "name": f"haos_ova-{tag}.qcow2.xz" if vm else f"haos_rpi5-64-{tag}.img.xz",
            "browser_download_url": f"https://example.test/{tag}",
        }],
        **flags,
    }


def test_newest_supported_stable_image_skips_board_only_and_prerelease():
    assert select_image([
        release("18.5", vm=False),
        release("18.5.rc1", prerelease=True),
        release("18.4"),
        release("18.3"),
    ]) == ("18.4", "https://example.test/18.4", "haos_ova-18.4.qcow2.xz")


def test_missing_or_draft_image_is_not_certified():
    assert select_image([release("18.5", vm=False), release("18.4", draft=True)]) is None


def test_ambiguous_release_fails_instead_of_selecting_arbitrarily():
    data = release("18.4")
    data["assets"] *= 2
    with pytest.raises(ValueError, match="Ambiguous"):
        select_image([data])
