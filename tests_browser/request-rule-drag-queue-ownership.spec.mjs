import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

for (const bundled of [false, true]) test(`queued Rule drags retain every acknowledged reorder (${bundled ? "bundle" : "source"})`, async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/request-rules", bundled ? "&bundle=1" : ""));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator(".request-rule-card")).toHaveCount(1);
  await panel.evaluate(async host => {
    for (const name of ["Drag second", "Drag third"]) await host._call("request_rules", "create", {
      rule:{name, enabled:true, phrases:[name], match_type:"equals", action_type:"model_routing", action:{model:"gpt-5-mini", scope:"conversation"}},
    });
    await host._loadSection(true);
    const original = host.hass.callWS.bind(host.hass);
    window.ruleDragQueue = {writes:[]};
    host.hass.callWS = async request => {
      if (request.section === "request_rules" && request.action === "move") {
        ruleDragQueue.writes.push(structuredClone(request));
        if (ruleDragQueue.writes.length === 1) await new Promise(resolve => {ruleDragQueue.release = resolve;});
      }
      return original(request);
    };
    const cards = [...host.shadowRoot.querySelectorAll(".request-rule-card")];
    ruleDragQueue.expected = [cards[2].dataset.ruleKey, cards[1].dataset.ruleKey, cards[0].dataset.ruleKey];
    const drag = (source, target, after) => {
      const dataTransfer = new DataTransfer(), bounds = target.getBoundingClientRect();
      source.dispatchEvent(new DragEvent("dragstart", {bubbles:true, dataTransfer}));
      target.dispatchEvent(new DragEvent("drop", {bubbles:true, cancelable:true, dataTransfer, clientY:after ? bounds.bottom - 1 : bounds.top + 1}));
    };
    drag(cards[0], cards[1], true);
    drag(cards[2], cards[1], false);
  });
  await expect.poll(() => page.evaluate(() => ruleDragQueue.writes.length)).toBe(1);
  await page.evaluate(() => ruleDragQueue.release());
  await expect.poll(() => page.evaluate(() => ruleDragQueue.writes.length)).toBe(2);
  await expect.poll(() => panel.evaluate(host => host._result.rules.map(rule => rule.id))).toEqual(await page.evaluate(() => ruleDragQueue.expected));
  expect(await page.evaluate(() => browserHarness.getState().requestRules.rules.map(rule => rule.id))).toEqual(await page.evaluate(() => ruleDragQueue.expected));
  await expectHarnessClean(page, errors);
});
