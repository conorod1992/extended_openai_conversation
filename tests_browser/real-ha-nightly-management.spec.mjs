import {expect, test} from "@playwright/test";
import {expectHarnessClean, trackPageErrors} from "./browser-helpers.mjs";
import {expectContractCalls} from "./real-ha-contract.mjs";

const backendUrl = process.env.REAL_HA_BACKEND_URL;
test.skip(!backendUrl, "requires the Enhanced genuine Home Assistant backend bridge");
const realFixtureUrl = (route) => `/tests_browser/real-ha-fixture.html?route=${encodeURIComponent(route)}&backend=${encodeURIComponent(backendUrl)}`;

test("shipped frontend duplicates, updates, and imports agents through genuine HA", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(realFixtureUrl("assistant/basics"));
  await expect(page.locator('extended-openai-management-panel [data-config="__title"]')).toBeVisible();
  const result = await page.evaluate(async () => {
    const panel = window.browserHarness.panel;
    const original = await panel._call("configuration", "get");
    const exported = await panel._call("configuration", "export");
    const updated = await panel._call("configuration", "update", {
      config: {current_datetime_enabled: false}, revision: original.revision,
    });
    const duplicate = await panel._call("configuration", "duplicate", {title: "Nightly duplicate"});
    const imported = await panel._call("configuration", "import", {
      document: {...exported.document, title: "Nightly imported"}, mode: "new", confirm: false,
    });
    return {updated, duplicate, imported};
  });
  expect(result.updated.config.current_datetime_enabled).toBe(false);
  expect(result.duplicate.status).toBe("created");
  expect(result.imported.status).toBe("created");
  expect(result.duplicate.subentry_id).not.toBe(result.imported.subentry_id);

  await page.goto(realFixtureUrl("assistant/basics"));
  await expect(page.locator('extended-openai-management-panel [data-config="__title"]')).toBeVisible();
  const persisted = await page.evaluate(async ({duplicateId, importedId}) => {
    const panel = window.browserHarness.panel;
    await panel._loadAgents(duplicateId);
    const duplicate = await panel._call("configuration", "get");
    await panel._loadAgents(importedId);
    const imported = await panel._call("configuration", "get");
    return {duplicate, imported};
  }, {duplicateId: result.duplicate.subentry_id, importedId: result.imported.subentry_id});
  expect(persisted.duplicate.title).toBe("Nightly duplicate");
  expect(persisted.imported.title).toBe("Nightly imported");
  await expectContractCalls(page, "configuration_extended");
  await expectHarnessClean(page, pageErrors);
});

test("shipped frontend persists an empty Request Rule wording list through genuine HA", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(realFixtureUrl("capabilities/request-rules"));
  let panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Request Rules", exact: true})).toBeVisible();
  await panel.locator(".wording-editor summary").click();
  await panel.locator("#wording-add").click();
  await panel.locator(".wording-group").last().locator(".wording-canonical").fill("nightly wording");
  await panel.locator(".wording-group").last().locator(".wording-alternatives").fill("nightly alternative");
  await panel.locator("#save-page").click();
  await expect(panel.locator(".save-bar")).toHaveCount(0);
  await page.goto(realFixtureUrl("capabilities/request-rules"));
  panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator(".wording-canonical").last()).toHaveValue("nightly wording");
  await panel.locator(".wording-editor summary").click();
  while (await panel.locator(".wording-remove").count()) {
    await panel.locator(".wording-remove").first().click();
  }
  await expect(panel.locator(".wording-group")).toHaveCount(0);
  await expect.poll(() => panel.evaluate((element) => element._rulesSettingsDraft?.wording_groups)).toEqual([]);
  await panel.locator("#save-page").click();
  // Do not abandon the document while its committed settings response is pending.
  await expect(panel.locator(".save-bar")).toHaveCount(0);
  await page.goto(realFixtureUrl("capabilities/request-rules"));
  panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Request Rules", exact: true})).toBeVisible();
  await expect(panel.locator(".wording-group")).toHaveCount(0);
  await expectContractCalls(page, "request_rules_empty");
  await expectHarnessClean(page, pageErrors);
});

test("shipped frontend starts and ends Guest Mode through genuine HA", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(realFixtureUrl("capabilities/guest-mode"));
  await page.waitForFunction(() => window.browserHarness?.panel?._selectedAgent?.());
  const started = await page.evaluate(() => window.browserHarness.panel._call(
    "guest_mode", "update", {indefinite: true},
  ));
  expect(started.status.state).toBe("active_indefinitely");
  await page.goto(realFixtureUrl("capabilities/guest-mode"));
  await page.waitForFunction(() => window.browserHarness?.panel?._selectedAgent?.());
  const active = await page.evaluate(() => window.browserHarness.panel._call("guest_mode", "get"));
  expect(active.status.state).toBe("active_indefinitely");
  const ended = await page.evaluate(() => window.browserHarness.panel._call("guest_mode", "disable"));
  expect(ended.status.state).toBe("inactive");
  await page.goto(realFixtureUrl("capabilities/guest-mode"));
  await page.waitForFunction(() => window.browserHarness?.panel?._selectedAgent?.());
  const inactive = await page.evaluate(() => window.browserHarness.panel._call("guest_mode", "get"));
  expect(inactive.status.state).toBe("inactive");
  await expectContractCalls(page, "guest_operations");
  await expectHarnessClean(page, pageErrors);
});




test("Guest save acknowledgement survives a failed refresh and edits in flight through genuine HA", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(realFixtureUrl("capabilities/guest-mode"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Guest Mode", exact: true})).toBeVisible();
  const migrationReview = panel.locator("#guest-review-converted");
  if (await migrationReview.isVisible()) await migrationReview.click();
  const control = panel.locator("#guest-controls-enabled");
  await expect(control).toBeAttached();
  await control.evaluate(input => { for (let parent=input.parentElement; parent; parent=parent.parentElement) if (parent.tagName==="DETAILS") parent.open=true; });
  const initial = await panel.evaluate(host => ({revision:host._result.revision, value:host._guestDraft.guest_mode_enabled}));
  await panel.evaluate(host => {
    const original = host._hass.callWS.bind(host._hass);
    let reject=true, gate=true, failDetails=true;
    host._hass.callWS = async message => {
      if (message.section==="guest_mode" && message.action==="save_policy") {
        if (reject) { reject=false; throw Error("Rejected before commit"); }
        if (gate) { gate=false; await new Promise(resolve => { window.releaseGuestCommit=resolve; }); }
      }
      if (message.section==="guest_mode" && message.action==="details" && window.releaseGuestCommit && failDetails) {
        failDetails=false; throw Error("Secondary details unavailable");
      }
      return original(message);
    };
  });
  await control.setChecked(!initial.value);
  await panel.locator("#save-page").click();
  await expect(panel.locator("#toast")).toContainText("Rejected before commit");
  expect(await panel.evaluate(host => host._unsavedState.scopes.get("capabilities/guest-mode").revision)).toBe(initial.revision);
  await panel.locator("#save-page").click();
  await page.waitForFunction(() => typeof window.releaseGuestCommit==="function");
  const exclusions = panel.locator('ha-selector[data-guest-key="guest_excluded_domains"]');
  await exclusions.evaluate(node => { node.value=["camera"]; node.dispatchEvent(new CustomEvent("value-changed", {detail:{value:["camera"]}, bubbles:true})); });
  await page.evaluate(() => window.releaseGuestCommit());
  await expect(panel.locator("#toast")).toContainText("Changes saved. Guest capability details could not be refreshed");
  await expect(panel.locator("#save-page")).toBeEnabled();
  const accepted = await panel.evaluate(async host => {
    const scope=host._unsavedState.scopes.get("capabilities/guest-mode");
    const backend=await host._call("guest_mode", "get");
    return {revision:scope.revision, backendRevision:backend.revision, baseline:scope.baseline, draft:scope.read(), dirty:scope.dirty()};
  });
  expect(accepted.revision).not.toBe(initial.revision);
  expect(accepted.revision).toBe(accepted.backendRevision);
  expect(accepted.baseline.guest_mode_enabled).toBe(!initial.value);
  expect(accepted.draft.guest_excluded_domains).toEqual(["camera"]);
  expect(accepted.dirty).toBe(true);
  await panel.locator("#save-page").click();
  await expect(panel.locator(".save-bar")).toHaveCount(0);
  const final = await panel.evaluate(async host => {
    const scope=host._unsavedState.scopes.get("capabilities/guest-mode"), backend=await host._call("guest_mode", "get");
    return {revision:scope.revision, backendRevision:backend.revision, domains:backend.config.guest_excluded_domains};
  });
  expect(final.revision).toBe(final.backendRevision);
  expect(final.revision).not.toBe(accepted.revision);
  expect(final.domains).toEqual(["camera"]);
  await expectHarnessClean(page, errors);
});


test("combined Request Rules settings remain current across warm and expired navigation through genuine HA", async ({page}) => {
  const errors=trackPageErrors(page);
  await page.goto(realFixtureUrl("capabilities/request-rules"));
  const panel=page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name:"Request Rules", exact:true})).toBeVisible();
  const initial=await panel.evaluate(host => ({revision:host._result.revision, word_forms:host._result.defaults.word_forms}));
  await panel.evaluate(host => {
    const original=host._hass.callWS.bind(host._hass); let reject=true;
    host._hass.callWS=async message => {
      if (reject && message.section==="request_rules" && message.action==="settings") { reject=false; throw Error("Rejected settings before commit"); }
      return original(message);
    };
  });
  const openDefaults=async () => panel.locator("#rules-default-word-forms").evaluate(input => { for(let parent=input.parentElement;parent;parent=parent.parentElement) if(parent.tagName==="DETAILS") parent.open=true; });
  await openDefaults();
  await panel.locator("#rules-default-word-forms").setChecked(!initial.word_forms);
  await panel.locator("#save-page").click();
  await expect(panel.locator("#toast")).toContainText("Rejected settings before commit");
  expect(await panel.evaluate(host => host._sectionCache.get(`${host._agentId}|capabilities/request-rules`).revision)).toBe(initial.revision);
  await panel.locator("#save-page").click(); await expect(panel.locator(".save-bar")).toHaveCount(0);
  const savedRevision=await panel.evaluate(host => host._result.revision);
  expect(savedRevision).not.toBe(initial.revision);
  await panel.evaluate(async host => { await host._navigate("guide"); await host._navigate("capabilities", "request-rules"); });
  await expect(panel.locator("#rules-default-word-forms")).toBeChecked({checked:!initial.word_forms});
  expect(await panel.evaluate(host => host._unsavedState.scopes.get("capabilities/request-rules").revision)).toBe(savedRevision);
  await openDefaults(); await panel.locator("#rules-default-word-forms").setChecked(initial.word_forms);
  await panel.locator("#save-page").click(); await expect(panel.locator(".save-bar")).toHaveCount(0);
  const secondRevision=await panel.evaluate(host => host._result.revision);
  expect(secondRevision).not.toBe(savedRevision);
  await panel.evaluate(async host => { await host._navigate("guide"); await host._navigate("capabilities", "request-rules"); });
  const readsBefore=await page.evaluate(() => browserHarness.calls.filter(call => call.section==="request_rules" && call.action==="list").length);
  await panel.evaluate(async host => {
    host._eocSectionCacheTimes.set(`${host._agentId}|capabilities/request-rules`, Date.now()-31000);
    await host._navigate("guide"); await host._navigate("capabilities", "request-rules");
  });
  expect(await page.evaluate(() => browserHarness.calls.filter(call => call.section==="request_rules" && call.action==="list").length)).toBeGreaterThan(readsBefore);
  expect(await panel.evaluate(host => host._unsavedState.scopes.get("capabilities/request-rules").revision)).toBe(secondRevision);
  await expectHarnessClean(page, errors);
});
