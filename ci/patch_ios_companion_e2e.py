#!/usr/bin/env python3
"""Patch a pinned Home Assistant iOS E2E test to exercise EOAI in-app."""

from __future__ import annotations

import argparse
from pathlib import Path

CALLS = """        grantNotificationPermission()
        openNativeSettingsFromFrontend()
"""

REPLACEMENT_CALLS = """        grantNotificationPermission()
        openExtendedOpenAIAndEditAgent()
        verifyExtendedOpenAIAfterBackgroundResume()
"""

MARKER = """    // MARK: - Helpers
"""

HELPERS = r'''
    private func openExtendedOpenAIAndEditAgent() {
        let settings = webElement(labelContaining: "settings")
        tapWebElement(
            labelContaining: "sidebar toggle",
            until: settings,
            timeout: Timeout.frontend,
            "frontend sidebar"
        )

        // A custom panel can be below the visible portion of the mobile drawer.
        // Prove the drawer itself opened using the same Settings target as the
        // upstream E2E test, then scroll the drawer until EOAI is hittable.
        let extendedOpenAI = webElement(labelContaining: "extended openai")
        wait(for: extendedOpenAI, timeout: Timeout.frontend, "Extended OpenAI sidebar entry")
        var sidebarScrolls = 0
        while !extendedOpenAI.isHittable, sidebarScrolls < 6 {
            app.webViews.firstMatch.swipeUp()
            sidebarScrolls += 1
        }
        XCTAssertTrue(extendedOpenAI.isHittable, "Extended OpenAI sidebar entry stayed off-screen")
        extendedOpenAI.tap()

        let webView = app.webViews.firstMatch
        let heading = webView.descendants(matching: .any)
            .matching(NSPredicate(format: "label CONTAINS[c] %@", "Extended OpenAI"))
            .firstMatch
        wait(for: heading, timeout: Timeout.frontend, "Extended OpenAI management panel")

        tapWebElement(labelContaining: "assistant", timeout: Timeout.frontend, "EOAI Assistant section")
        let basics = webElement(labelContaining: "basics")
        if basics.waitForExistence(timeout: Timeout.optional) && basics.isHittable {
            basics.tap()
        }

        let nameField = webView.textFields
            .matching(NSPredicate(format: "value == %@", "Companion acceptance assistant"))
            .firstMatch
        wait(for: nameField, timeout: Timeout.frontend, "EOAI Agent name field")
        nameField.tap()
        nameField.typeKey("a", modifierFlags: .command)
        nameField.typeText("Companion app iOS saved title")

        tapWebElement(labelContaining: "save changes", timeout: Timeout.frontend, "EOAI Save changes")
        let savedField = webView.textFields
            .matching(NSPredicate(format: "value == %@", "Companion app iOS saved title"))
            .firstMatch
        wait(for: savedField, timeout: Timeout.frontend, "saved EOAI Agent name")
    }

    private func verifyExtendedOpenAIAfterBackgroundResume() {
        XCUIDevice.shared.press(.home)
        app.activate()

        let savedField = app.webViews.firstMatch.textFields
            .matching(NSPredicate(format: "value == %@", "Companion app iOS saved title"))
            .firstMatch
        wait(for: savedField, timeout: Timeout.frontend, "EOAI Agent name after background resume")
    }

'''

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("checkout", type=Path)
    args = parser.parse_args()

    target = args.checkout / "Tests/UI/OnboardingE2ETests.swift"
    text = target.read_text(encoding="utf-8")

    if CALLS not in text:
        raise SystemExit("Pinned iOS E2E source no longer has the expected onboarding call sequence")
    if MARKER not in text:
        raise SystemExit("Pinned iOS E2E source no longer has the expected helper marker")

    text = text.replace(CALLS, REPLACEMENT_CALLS, 1)
    text = text.replace(MARKER, HELPERS + MARKER, 1)
    target.write_text(text, encoding="utf-8")

    testing_lane = args.checkout / "fastlane/lanes/testing.rb"
    lane_text = testing_lane.read_text(encoding="utf-8")
    e2e_anchor = """    result_bundle: true,
    skip_package_dependencies_resolution: true,
"""
    e2e_replacement = """    result_bundle: true,
    collect_test_diagnostics: "never",
    skip_package_dependencies_resolution: true,
"""
    if e2e_anchor not in lane_text:
        raise SystemExit("Pinned iOS E2E Fastlane lane no longer has the expected run_tests options")
    lane_text = lane_text.replace(e2e_anchor, e2e_replacement, 1)
    testing_lane.write_text(lane_text, encoding="utf-8")

    assert text.count("openExtendedOpenAIAndEditAgent()") == 2
    assert "Companion app iOS saved title" in text
    assert 'collect_test_diagnostics: "never"' in lane_text
    print(f"Patched {target} and {testing_lane}")


if __name__ == "__main__":
    main()
