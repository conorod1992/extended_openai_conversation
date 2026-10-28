import {expect, test} from "@playwright/test";
import {acceptConfirmation, fixtureUrl, expectHarnessClean, trackPageErrors} from "./browser-helpers.mjs";

for (const bundle of [false, true]) test.describe(bundle ? "bundle" : "source", () => {
for (const fault of ["missing", "wrong identity", "lost"]) for (const action of ["duplicate", "update", "create"]) {
  test(`Rule ${action} recovers ${fault} acknowledgement before the next action`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl("capabilities/request-rules", bundle ? "&bundle=1" : ""));
    const panel = page.locator("extended-openai-management-panel");
    await expect(panel.locator(".rule-edit")).toBeVisible();
    await panel.evaluate((host, {action, fault}) => {
      window.ruleWrites = 0;
      const send = host.hass.callWS.bind(host.hass);
      host.hass.callWS = async message => {
        const result = await send(message);
        if (message.section !== "request_rules" || message.action !== action) return result;
        ruleWrites++;
        if (ruleWrites !== 1) return result;
        if (fault === "lost") throw {type:"result", success:false, error:{code:3, message:"Reply lost"}};
        // A wrong identity is meaningful for update; create/duplicate require a nonempty string.
        return {...result, rule:fault === "wrong identity" ? {...result.rule, id:action === "update" ? "another-rule" : action === "duplicate" ? message.rule_id : ""} : undefined};
      };
    }, {action, fault});
    if (action === "create") {
      await panel.locator("#rule-add").click();
      await panel.locator("#rule-name").fill("Committed once");
      await panel.locator("#rule-phrases").fill("committed once");
      await panel.locator("#rule-action-type").selectOption("model_routing");
      await panel.locator("#rule-model").fill("gpt-5-mini");
      await panel.locator("#rule-save").click();
    } else if (action === "duplicate") await panel.locator(".rule-duplicate").click();
    else await panel.locator(".rule-enabled").uncheck();
    const count = action === "update" ? 1 : 2;
    await expect.poll(() => page.evaluate(() => browserHarness.getState().requestRules.rules.length)).toBe(count);
    if (action === "create" && await panel.locator("#rule-dialog").evaluate(dialog => dialog.open)) await panel.locator(".rule-close").last().click();
    await expect(panel.locator(".request-rule-card")).toHaveCount(count);
    if (action === "update") await expect(panel.locator(".rule-enabled")).not.toBeChecked();
    // No retry of a create/duplicate. The next deliberate action updates the committed identity.
    const last = panel.locator(".rule-enabled").last();
    const before = await last.isChecked();
    await last.setChecked(!before);
    await expect.poll(() => page.evaluate(() => browserHarness.getState().requestRules.rules.at(-1).enabled)).toBe(!before);
    expect(await page.evaluate(() => browserHarness.getState().requestRules.rules.length)).toBe(count);
    expect(await page.evaluate(() => ruleWrites)).toBe(action === "update" ? 2 : 1);
    await expectHarnessClean(page, errors);
  });
}

for (const transition of ["assistant", "authentication"]) test(`delayed Group uncertainty cannot block another ${transition} owner`, async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/request-rules", `&agents=2${bundle ? "&bundle=1" : ""}`));
  const panel = page.locator("extended-openai-management-panel");
  await panel.locator("#rule-groups-manage").click();
  await panel.evaluate(async host => {
    const {createStateBackend} = await import("/tests_browser/harness-state.mjs");
    window.independentBackend = createStateBackend({});
    const send = host.hass.callWS.bind(host.hass);
    window.groupWrites = 0;
    host.hass.callWS = async message => {
      const result = message.subentry_id === "scale-agent-1" && message.section === "request_rules"
        ? await independentBackend.call(message) : await send(message);
      if (message.section === "request_rules" && message.action === "groups") {
        groupWrites++;
        if (groupWrites === 1) {
          await new Promise(resolve => {window.releaseGroupReply = resolve;});
          return {revision:result.revision};
        }
      }
      return result;
    };
  });
  await panel.locator("#rule-new-group-name").fill("A committed group");
  await panel.locator("#rule-group-add").click();
  await expect.poll(() => page.evaluate(() => groupWrites)).toBe(1);
  await panel.locator("#rule-groups-done").click();
  if (transition === "assistant") await panel.evaluate(async host => {
    // Exercise a selection restored externally while the old handler awaits its reply.
    host._agentId = "scale-agent-1";
    host._clearConfigDraft();
    await host._loadSection(true);
  });
  else await panel.evaluate(async host => {
    host.hass = {...host.hass, user:{id:"new-authentication"}};
    await host._loadSection(true);
  });
  await expect(panel.locator(".rule-edit")).toBeVisible();
  await page.evaluate(() => releaseGroupReply());
  await expect.poll(() => panel.evaluate(host => host._eocAgentMutations)).toBe(0);
  await panel.evaluate(async host => {host._sectionCache.clear(); await host._loadSection(true);});
  await panel.locator("#rule-groups-manage").click();
  await panel.locator("#rule-new-group-name").fill("Independent next group");
  await panel.locator("#rule-group-add").click();
  await expect.poll(() => page.evaluate(transition => (transition === "assistant" ? independentBackend.state() : browserHarness.getState()).requestRules.groups.map(g => g.name), transition)).toEqual(transition === "assistant" ? ["Independent next group"] : ["A committed group", "Independent next group"]);
  if (transition === "assistant") expect(await page.evaluate(() => browserHarness.getState().requestRules.groups.map(g => g.name))).toEqual(["A committed group"]);
  expect(await page.evaluate(() => groupWrites)).toBe(2);
  await expectHarnessClean(page, errors);
});


test("queued Rule mutations stop at an uncertain predecessor without replay", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/request-rules", bundle ? "&bundle=1" : ""));
  const panel = page.locator("extended-openai-management-panel");
  await panel.evaluate(async host => {
    await host._call("request_rules", "duplicate", {rule_id:"rule-1"});
    await host._loadSection(true);
    window.queuedWrites = 0;
    const send = host.hass.callWS.bind(host.hass);
    host.hass.callWS = async message => {
      const result = await send(message);
      if (message.section === "request_rules" && message.action === "duplicate") {
        queuedWrites++;
        if (queuedWrites === 1) {
          await new Promise(resolve => {window.releaseQueuedReply = resolve;});
          return {revision:result.revision};
        }
      }
      return result;
    };
  });
  await expect(panel.locator(".rule-duplicate")).toHaveCount(2);
  await panel.locator(".rule-duplicate").first().click();
  await expect.poll(() => page.evaluate(() => queuedWrites)).toBe(1);
  await panel.locator(".rule-duplicate").nth(1).click();
  await page.evaluate(() => releaseQueuedReply());
  await panel.evaluate(async host => {await host._eocRequestRuleMutationTail?.catch(() => {});});
  await expect.poll(() => panel.evaluate(host => host._eocAgentMutations)).toBe(0);
  await expect(panel.locator(".request-rule-card")).toHaveCount(3);
  expect(await page.evaluate(() => queuedWrites)).toBe(1);
  expect(await page.evaluate(() => browserHarness.getState().requestRules.rules.length)).toBe(3);
  await panel.locator(".rule-enabled").last().uncheck();
  await expect.poll(() => page.evaluate(() => browserHarness.getState().requestRules.rules.at(-1).enabled)).toBe(false);
  expect(await page.evaluate(() => browserHarness.getState().requestRules.rules.length)).toBe(3);
  await expectHarnessClean(page, errors);
});

test("failed Rule recovery blocks writes until an authoritative read", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/request-rules", bundle ? "&bundle=1" : ""));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator(".rule-duplicate")).toBeVisible();
  await panel.evaluate(host => {
    window.recoveryUnavailable = false;
    window.failedRecoveryWrites = 0;
    const send = host.hass.callWS.bind(host.hass);
    host.hass.callWS = async message => {
      if (recoveryUnavailable && message.section === "request_rules" && message.action === "list") throw {code:"offline", message:"Read unavailable"};
      const result = await send(message);
      if (message.section === "request_rules" && message.action === "duplicate") {
        failedRecoveryWrites++;
        if (failedRecoveryWrites === 1) {window.recoveryUnavailable = true; return {revision:result.revision};}
      }
      return result;
    };
  });
  await panel.locator(".rule-duplicate").click();
  await expect(panel.getByRole("button", {name:"Retry", exact:true})).toBeVisible();
  expect(await page.evaluate(() => browserHarness.getState().requestRules.rules.length)).toBe(2);
  await page.evaluate(() => {window.recoveryUnavailable = false;});
  await panel.getByRole("button", {name:"Retry", exact:true}).click();
  await expect(panel.locator(".rule-duplicate")).toHaveCount(2);
  // The accepted Retry read recovers the owner without another mutation.
  // The next deliberate action can update the committed identity directly.
  expect(await page.evaluate(() => failedRecoveryWrites)).toBe(1);
  await panel.locator(".rule-enabled").last().uncheck();
  await expect.poll(() => page.evaluate(() => browserHarness.getState().requestRules.rules.at(-1).enabled)).toBe(false);
  expect(await page.evaluate(() => browserHarness.getState().requestRules.rules.length)).toBe(2);
  await expectHarnessClean(page, errors);
});

});
