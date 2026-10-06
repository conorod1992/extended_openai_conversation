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
        let extendedOpenAI = webElement(labelContaining: "extended openai")
        tapWebElement(
            labelContaining: "sidebar toggle",
            until: extendedOpenAI,
            timeout: Timeout.frontend,
            "frontend sidebar"
        )
        tapWebElement(
            labelContaining: "extended openai",
            timeout: Timeout.frontend,
            "Extended OpenAI sidebar entry"
        )

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

    assert text.count("openExtendedOpenAIAndEditAgent()") == 2
    assert "Companion app iOS saved title" in text
    print(f"Patched {target}")


if __name__ == "__main__":
    main()
