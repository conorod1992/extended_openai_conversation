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

        // Mobile navigation is a native select named Page. A loose "assistant"
        // match instead opens the separate Editing assistant select on Overview.
        let pageSelector = webView.buttons
            .matching(NSPredicate(format: "label == %@", "Page"))
            .firstMatch
        wait(for: pageSelector, timeout: Timeout.frontend, "EOAI Page selector")
        pageSelector.tap()
        let assistantPage = app.descendants(matching: .any)
            .matching(NSPredicate(format: "label == %@", "Assistant"))
            .firstMatch
        wait(for: assistantPage, timeout: Timeout.frontend, "EOAI Assistant page option")
        assistantPage.tap()
        let basics = webElement(labelContaining: "basics")
        if basics.waitForExistence(timeout: Timeout.optional) && basics.isHittable {
            basics.tap()
        }

        // Simulator startup can delay the native notification sheet until here.
        grantNotificationPermission()

        let nameField = webView.textFields
            .matching(NSPredicate(format: "label == %@", "Agent name"))
            .firstMatch
        wait(for: nameField, timeout: Timeout.frontend, "EOAI Agent name field")
        XCTAssertEqual(nameField.value as? String, "Companion acceptance assistant")

        // The system permission alert can arrive after the in-app request
        // sheet disappears. Its dismissal steals focus from the web field.
        // Handle that specific alert, then establish keyboard focus again.
        var focusAttempts = 0
        repeat {
            nameField.tap()
            dismissLateNotificationAlert()
            focusAttempts += 1
        } while !app.keyboards.firstMatch.waitForExistence(timeout: Timeout.optional) && focusAttempts < 3
        guard app.keyboards.firstMatch.exists else {
            XCTFail("Keyboard never came up for the EOAI Agent name")
            return
        }
        nameField.typeKey("a", modifierFlags: .command)
        nameField.typeText("Companion app iOS saved title")

        // WebKit can report a covered Save button as hittable while the software
        // keyboard is up. Use the keyboard accessory's Done control first.
        let keyboardDone = app.buttons
            .matching(NSPredicate(format: "label == %@", "Done"))
            .firstMatch
        wait(for: keyboardDone, timeout: Timeout.screen, "keyboard Done button")
        keyboardDone.tap()
        XCTAssertTrue(
            app.keyboards.firstMatch.waitForNonExistence(timeout: Timeout.screen),
            "Keyboard stayed on screen before saving the EOAI Agent"
        )

        tapWebElement(labelContaining: "save changes", timeout: Timeout.frontend, "EOAI Save changes")
        let savedField = webView.textFields
            .matching(NSPredicate(format: "value == %@", "Companion app iOS saved title"))
            .firstMatch
        wait(for: savedField, timeout: Timeout.frontend, "saved EOAI Agent name")
    }

    private func dismissLateNotificationAlert() {
        let alert = springboard.alerts
            .matching(NSPredicate(format: "label CONTAINS[c] %@", "Would Like to Send You Notifications"))
            .firstMatch
        guard alert.waitForExistence(timeout: Timeout.optional) else { return }
        let allow = alert.buttons["Allow"]
        wait(for: allow, timeout: Timeout.screen, "notification permission Allow button")
        allow.tap()
        XCTAssertTrue(
            alert.waitForNonExistence(timeout: Timeout.screen),
            "Notification permission alert stayed on screen"
        )
        // The permission sheet may have consumed the original field tap.
        app.webViews.firstMatch.textFields
            .matching(NSPredicate(format: "label == %@", "Agent name"))
            .firstMatch.tap()
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
    earlier_lanes, lane_marker, e2e_lane = lane_text.partition("lane :e2e do |options|")
    if not lane_marker:
        raise SystemExit("Pinned iOS Fastlane source no longer has the expected E2E lane")
    e2e_anchor = "xcargs: 'COMPILER_INDEX_STORE_ENABLE=NO',"
    # The pinned Fastlane predates its collect_test_diagnostics option. xcargs
    # forwards the supported Xcode flag without requiring a Gemfile update.
    e2e_replacement = "xcargs: 'COMPILER_INDEX_STORE_ENABLE=NO -collect-test-diagnostics never',"
    if e2e_anchor not in e2e_lane:
        raise SystemExit("Pinned iOS E2E Fastlane lane no longer has the expected run_tests options")
    lane_text = earlier_lanes + lane_marker + e2e_lane.replace(e2e_anchor, e2e_replacement, 1)
    testing_lane.write_text(lane_text, encoding="utf-8")

    assert text.count("openExtendedOpenAIAndEditAgent()") == 2
    assert "Companion app iOS saved title" in text
    assert "-collect-test-diagnostics never" in lane_text
    print(f"Patched {target} and {testing_lane}")


if __name__ == "__main__":
    main()
