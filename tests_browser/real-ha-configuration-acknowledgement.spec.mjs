import {expect, test} from "@playwright/test";
import {expectHarnessClean, trackPageErrors} from "./browser-helpers.mjs";

const backendUrl = process.env.REAL_HA_BACKEND_URL;
const failRender = process.env.CONFIGURATION_ACK_FAIL_RENDER === "1";
test.skip(!backendUrl, "requires the genuine Home Assistant management bridge");
const fixture = "/tests_browser/real-ha-fixture.html?bundle=1&route=assistant%2Fprompt-context&backend=" + encodeURIComponent(backendUrl);

test("a real configuration acknowledgement preserves newer edits and survives presentation failure", async ({page}) => {
  test.skip(Boolean(process.env.CONFIGURATION_ACK_GUEST_RENAME));
  const errors = trackPageErrors(page);
  const panel = page.locator("extended-openai-management-panel");
  const prompt = panel.locator('[data-config="prompt"]');
  await page.goto(fixture);
  await expect(prompt).toBeVisible();
  await prompt.fill("Submitted acknowledgement prompt");
  await page.evaluate(failRender => {
    const {panel, hass} = window.browserHarness;
    const original = hass.callWS.bind(hass), render = panel._render.bind(panel);
    let release;
    const gate = new Promise(resolve => { release = resolve; });
    const probe = window.configurationAckProbe = {writes:0, released:false, failRender, original, render,
      release:() => { probe.released = true; release(); }};
    hass.callWS = async message => {
      const result = await original(message);
      if (message.section === "configuration" && message.action === "save") {
        probe.writes++;
        probe.saved = structuredClone(result);
        if (probe.writes === 1) await gate;
      }
      return result;
    };
    panel._render = (...args) => {
      if (probe.failRender && probe.released && !probe.renderFailed) {
        probe.renderFailed = true;
        throw new Error("Post-save presentation unavailable");
      }
      return render(...args);
    };
  }, failRender);
  await panel.locator("#save-config").click();
  await expect.poll(() => page.evaluate(() => Boolean(window.configurationAckProbe.saved))).toBe(true);
  await prompt.fill("Newer acknowledgement prompt");
  await page.evaluate(() => window.configurationAckProbe.release());
  await expect(panel.locator("#toast")).toContainText("Changes saved");
  if (failRender) await expect(panel.locator("#toast")).toContainText("could not be refreshed");
  await expect(panel.locator("#toast")).not.toContainText("Unable to save");
  await expect(prompt).toHaveValue("Newer acknowledgement prompt");
  const first = await panel.evaluate(async host => ({baseline:host._configData, draft:host._draft,
    saved:window.configurationAckProbe.saved, persisted:await host._call("configuration", "get")}));
  expect(first.baseline.revision).toBe(first.saved.revision);
  expect(first.persisted.revision).toBe(first.saved.revision);
  expect(first.baseline.config.prompt).toBe("Submitted acknowledgement prompt");
  expect(first.persisted.config.prompt).toBe("Submitted acknowledgement prompt");
  expect(first.draft.prompt).toBe("Newer acknowledgement prompt");

  // The next genuine save must use the acknowledged revision and save only the
  // newer draft. A presentation failure must not have replayed the first write.
  expect(await page.evaluate(() => window.configurationAckProbe.writes)).toBe(1);
  await page.evaluate(() => { window.configurationAckProbe.failRender = false; });
  await expect(panel.locator("#save-config")).toBeEnabled();
  await panel.locator("#save-config").click();
  await expect(panel.locator("#save-config")).toHaveCount(0);
  expect(await page.evaluate(() => window.configurationAckProbe.writes)).toBe(2);
  await page.goto(fixture);
  await expect(prompt).toHaveValue("Newer acknowledgement prompt");
  await expectHarnessClean(page, errors);
});


test("partial Function acknowledgements reload renamed custom Guest permissions before preview", async ({page}) => {
  const kind = process.env.CONFIGURATION_ACK_GUEST_RENAME;
  test.skip(!kind);
  const errors = trackPageErrors(page);
  const panel = page.locator("extended-openai-management-panel");
  await page.goto(fixture.replace("assistant%2Fprompt-context", "capabilities%2Ffunctions"));
  const group = panel.locator('.function-group-card[data-group-id="old_group"]');
  await expect(group).toBeVisible();
  await panel.evaluate(host => host._call("guest_mode", "update", {indefinite:true}));
  await page.evaluate(() => {
    const {hass} = window.browserHarness;
    const original = hass.callWS.bind(hass);
    window.guestRenameProbe = {reads:0};
    hass.callWS = async message => {
      if (message.section === "configuration" && message.action === "get") window.guestRenameProbe.reads++;
      const result = await original(message);
      if (message.section === "tools" && ["save", "save_group"].includes(message.action)) {
        window.guestRenameProbe.saved = structuredClone(result);
      }
      return result;
    };
  });
  if (kind === "function") {
    await group.locator("summary").click();
    await group.locator(".edit-tool").click();
    const tool = await panel.evaluate(host => structuredClone(host._draft.functions[0]));
    tool.spec.name = "renamed_tool";
    await panel.locator("#tool-yaml").fill(JSON.stringify(tool));
    await panel.locator("#tool-save").click();
    await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", false);
  } else {
    await group.locator(".edit-group").click();
    await panel.locator("#group-id").fill("renamed_group");
    await panel.locator("#group-save").click();
    await expect(panel.locator("#group-dialog")).toHaveJSProperty("open", false);
  }
  expect(await page.evaluate(() => window.guestRenameProbe.saved.config)).toBeUndefined();
  const reads = await page.evaluate(() => window.guestRenameProbe.reads);
  // In-document navigation must not reuse a partial acknowledgement as a full snapshot.
  await panel.evaluate(host => host._navigate("capabilities", "guest-mode"));
  await panel.evaluate(host => host._navigate("assistant", "prompt-context"));
  await expect.poll(() => page.evaluate(() => window.guestRenameProbe.reads)).toBeGreaterThan(reads);
  const draft = await panel.evaluate(host => host._draft);
  expect(kind === "function" ? draft.guest_allowed_function_names : draft.guest_allowed_group_ids)
    .toEqual([kind === "function" ? "renamed_tool" : "renamed_group"]);
  await panel.locator("#preview-request").click();
  await expect.poll(async () => (await panel.locator("#request-preview-sections textarea").evaluateAll(nodes => nodes.map(node => node.value))).join("\n"))
    .toContain(kind === "function" ? "renamed_tool" : "old_tool");
  await expectHarnessClean(page, errors);
});
