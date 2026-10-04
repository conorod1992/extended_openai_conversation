import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

for (const fail of [false, true]) {
  test(`Guest Save completes while policy metrics are pending (${fail ? "failure" : "success"})`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl("capabilities/guest-mode"));
    const panel = page.locator("extended-openai-management-panel");
    await expect(panel.locator("#guest-controls-enabled")).toHaveCount(1);
    await panel.evaluate((host) => {
      const original = host._call.bind(host);
      window.policyReads = [];
      host._call = async (section, action, params) => {
        if (section === "guest_mode" && action === "policy") {
          return new Promise((resolve, reject) => window.policyReads.push({resolve, reject}));
        }
        return original(section, action, params);
      };
    });
    await panel.locator(".guest-advanced:has(#guest-controls-enabled)").evaluate(node => { node.open = true; });
    await panel.locator("#guest-controls-enabled").check();
    await panel.locator("#save-page").click();
    await expect(panel.locator(".save-bar")).toHaveCount(0);
    await expect.poll(() => page.evaluate(() => window.policyReads.length)).toBe(1);
    await expect.poll(() => panel.evaluate(host => host._unsavedState.scopes.get("capabilities/guest-mode").pending)).toBe(false);
    await expect(panel.locator(".metric-grid")).toHaveAttribute("aria-busy", "true");
    await panel.locator("#guest-controls-enabled").uncheck();
    await panel.locator("#save-page").click();
    await expect(panel.locator(".save-bar")).toHaveCount(0);
    await expect.poll(() => page.evaluate(() => window.policyReads.length)).toBe(2);
    await page.evaluate(fail => {
      if (fail) window.policyReads[0].reject(new Error("Old refresh offline"));
      else window.policyReads[0].resolve({policy:{readable_entity_count:999}});
    }, fail);
    await expect(panel.locator(".metric-grid")).not.toContainText("999");
    await page.evaluate(fail => {
      if (fail) window.policyReads[1].reject(new Error("Current refresh offline"));
      else window.policyReads[1].resolve({policy:{readable_entity_count:7, controllable_entity_count:3, configured_tool_count:2}});
    }, fail);
    await expect(panel.locator(".metric-grid")).not.toHaveAttribute("aria-busy");
    await expect(panel.locator("#guest-controls-enabled")).not.toBeChecked();
    expect(await page.evaluate(() => browserHarness.getState().guest.config.guest_mode_enabled)).toBe(false);
    if (fail) await expect(panel.locator(".metric-grid")).toContainText("Unavailable");
    else await expect(panel.locator(".metric-grid")).toContainText("7");
    await expectHarnessClean(page, errors);
  });
}

for (const bundled of [false, true]) {
  test(`Voice same-structure Save retains native controls and hydration (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl("assistant/voice", bundled ? "&bundle=1" : ""));
    const panel = page.locator("extended-openai-management-panel");
    const policy = panel.locator('[data-config="voice_scope_policy"]');
    await expect(policy).toBeVisible();
    await page.evaluate(() => {
      const state = browserHarness.getState();
      Object.assign(state.configuration.config, {voice_scope_policy:"device_mapping", voice_device_mappings:{"device-kitchen":"user:test-user"}});
      localStorage.setItem("extended-openai-browser-harness-state-v3", JSON.stringify(state));
    });
    await page.goto(fixtureUrl("assistant/voice", bundled ? "&bundle=1" : ""));
    await expect(panel.locator(".voice-satellite-picker").first()).toHaveCount(1);
    await panel.evaluate(host => {
      window.savedVoicePicker = host.shadowRoot.querySelector(".voice-satellite-picker");
      window.savedVoicePolicy = host.shadowRoot.querySelector('[data-config="voice_scope_policy"]');
      window.registryBeforeSave = browserHarness.calls.filter(call => call.type === "config/entity_registry/list").length;
    });
    await panel.locator('[data-config="voice_unmapped_policy"]').selectOption("shared");
    await panel.locator("#save-config").click();
    await expect(panel.locator(".save-bar")).toHaveCount(0);
    expect(await panel.evaluate(host => ({
      samePicker:host.shadowRoot.querySelector(".voice-satellite-picker") === window.savedVoicePicker,
      samePolicy:host.shadowRoot.querySelector('[data-config="voice_scope_policy"]') === window.savedVoicePolicy,
      registry:browserHarness.calls.filter(call => call.type === "config/entity_registry/list").length === window.registryBeforeSave,
    }))).toEqual({samePicker:true, samePolicy:true, registry:true});
    await panel.evaluate(host => host._render());
    expect(await panel.evaluate(host => host.shadowRoot.querySelector(".voice-satellite-picker") === window.savedVoicePicker)).toBe(true);
    await expectHarnessClean(page, errors);
  });
}

test("edits made during an ordinary Save remain visible", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/basics"));
  const panel = page.locator("extended-openai-management-panel");
  const tokens = panel.locator('[data-config="max_tokens"]');
  await expect(tokens).toBeVisible();
  await panel.evaluate(host => {
    window.savedTokens = host.shadowRoot.querySelector('[data-config="max_tokens"]');
    const original = host._call.bind(host);
    host._call = async (section, action, params) => {
      if (section === "configuration" && action === "save") {
        await new Promise(resolve => { window.releaseSettingsSave = resolve; });
      }
      return original(section, action, params);
    };
  });
  await tokens.fill("764");
  await panel.locator("#save-config").click();
  await expect.poll(() => page.evaluate(() => Boolean(window.releaseSettingsSave))).toBe(true);
  await tokens.fill("765");
  await page.evaluate(() => window.releaseSettingsSave());
  await expect.poll(() => panel.evaluate(host => host._configurationSaving)).toBe(false);
  await expect(tokens).toHaveValue("765");
  expect(await panel.evaluate(host => ({same:host.shadowRoot.querySelector('[data-config="max_tokens"]') === window.savedTokens, saved:host._configData.config.max_tokens, dirty:host._configDirty}))).toEqual({same:true, saved:764, dirty:true});
  await expectHarnessClean(page, errors);
});

for (const structural of [false, true]) {
  test(`canonical Save values ${structural ? "rebuild a changed model" : "preserve an unchanged form"}`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl("assistant/basics"));
    const panel = page.locator("extended-openai-management-panel");
    const tokens = panel.locator('[data-config="max_tokens"]');
    await expect(tokens).toBeVisible();
    await panel.evaluate((host, structural) => {
      window.savedCanonicalControl = host.shadowRoot.querySelector('[data-config="max_tokens"]');
      const original = host._call.bind(host);
      host._call = async (section, action, params) => {
        const result = await original(section, action, params);
        if (section === "configuration" && action === "save") {
          result.config.max_tokens = 768;
          if (structural) result.config.chat_model = "gpt-4o";
        }
        return result;
      };
    }, structural);
    await tokens.fill("764"); await panel.locator("#save-config").click();
    await expect.poll(() => panel.evaluate(host => host._configurationSaving)).toBe(false);
    await expect(tokens).toHaveValue("768");
    expect(await panel.evaluate(host => host.shadowRoot.querySelector('[data-config="max_tokens"]') === window.savedCanonicalControl)).toBe(!structural);
    await expectHarnessClean(page, errors);
  });
}
