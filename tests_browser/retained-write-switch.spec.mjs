import {expect, test} from "@playwright/test";
import {acceptConfirmation, expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

for (const bundled of [false, true]) for (const kind of ["knowledge", "memory", "configuration"]) for (const outcome of ["success", "failure", "cancelled"]) {
  test(`retained ${kind} write prevents assistant switching until ${outcome} (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const errors = trackPageErrors(page);
    const route = kind === "configuration" ? "assistant/basics" : `data-memory/${kind === "memory" ? "memories" : "knowledge"}`;
    await page.goto(fixtureUrl(route, `&agents=2${bundled ? "&bundle=1" : ""}`));
    const panel = page.locator("extended-openai-management-panel");
    const open = panel.locator(kind === "configuration" ? '[data-config="__title"]' : kind === "memory" ? "#add-memory" : "#add-source");
    await expect(open).toBeVisible();
    await panel.evaluate(host => {
      browserHarness.hass = {...host.hass, user:{id:"admin", is_admin:true}};
      host.hass = browserHarness.hass;
    });
    const text = `Retained ${kind} mutation`;
    if (kind === "configuration") await open.fill(text);
    else {
      await open.click();
      if (kind === "knowledge") await panel.locator("#knowledge-title").fill(text);
      await panel.locator(`#${kind}-content`).fill(text);
    }
    const section = kind === "memory" ? "memories" : kind;
    const action = kind === "memory" ? "add" : kind === "knowledge" ? "create" : "save";
    await page.evaluate(({section, action}) => {
      const original = browserHarness.hass.callWS.bind(browserHarness.hass);
      window.retainedWrites = [];
      browserHarness.hass.callWS = request => {
        if (request.section !== section || request.action !== action) return original(request);
        return new Promise((resolve, reject) => retainedWrites.push({request, finish: async outcome => {
          if (outcome === "success") resolve(await original(request));
          else reject(outcome === "cancelled" ? new DOMException("Cancelled retained write", "AbortError") : new Error("Retained write failed"));
        }}));
      };
    }, {section, action});
    await panel.locator(kind === "configuration" ? "#save-config" : `#${kind}-save`).click();
    await expect.poll(() => page.evaluate(() => retainedWrites.length)).toBe(1);
    await panel.evaluate(host => {
      window.retainedWritingSource = host;
      const parent = host.parentElement, hass = host.hass;
      host.remove();
      const next = document.createElement("extended-openai-management-panel");
      parent.append(next);
      next.hass = hass;
    });
    await expect.poll(() => panel.evaluate((host, kind) => !host._busy && (kind === "configuration" ? Boolean(host._configData?.config) : Array.isArray(host._result?.[kind === "memory" ? "memories" : "sources"])), kind)).toBe(true);
    expect(await panel.evaluate(host => host._unsavedState.scopes.get("retained-drafts")?.pending)).toBe(true);
    await panel.locator("#agent").selectOption("scale-agent-1");
    await expect(panel.locator("#agent")).toHaveValue("agent-1");
    await expect(panel.locator("#confirm-dialog")).not.toBeVisible();
    expect(await panel.evaluate(host => host._agentId)).toBe("agent-1");
    expect(await page.evaluate(() => Boolean(retainedWritingSource._configurationSaving || retainedWritingSource._eocAgentMutations))).toBe(true);
    await page.evaluate(outcome => retainedWrites[0].finish(outcome), outcome);
    await expect.poll(() => page.evaluate(() => Boolean(retainedWritingSource._configurationSaving || retainedWritingSource._eocAgentMutations))).toBe(false);
    await expect.poll(() => panel.evaluate(host => Boolean(host._unsavedState.scopes.get("retained-drafts")?.pending))).toBe(false);
    if (outcome === "success") {
      if (kind !== "configuration") await expect.poll(() => panel.evaluate(({_result}, {kind, text}) => _result?.[kind === "memory" ? "memories" : "sources"]?.some(record => record.content === text), {kind, text})).toBe(true);
    } else if (kind === "configuration") await expect(panel.locator('[data-config="__title"]')).toHaveValue(text);
    else await expect(panel.locator(`#${kind}-content`)).toHaveValue(text);
    await panel.locator("#agent").selectOption("scale-agent-1");
    if (outcome !== "success") await acceptConfirmation(panel);
    await expect.poll(() => panel.evaluate(host => host._agentId)).toBe("scale-agent-1");
    await expect.poll(() => panel.evaluate(host => host._busy)).toBe(false);
    await panel.locator("#agent").selectOption("agent-1");
    await expect.poll(() => panel.evaluate(host => !host._busy && host._agentId === "agent-1")).toBe(true);
    if (outcome === "success" && kind !== "configuration") await expect.poll(() => panel.evaluate(({_result}, {kind, text}) => _result?.[kind === "memory" ? "memories" : "sources"]?.some(record => record.content === text), {kind, text})).toBe(true);
    expect(await page.evaluate(() => retainedWrites.length)).toBe(1);
    expect(await page.evaluate(({section, action}) => browserHarness.calls.filter(call => call.section === section && call.action === action).length, {section, action})).toBe(outcome === "success" ? 1 : 0);
    await expectHarnessClean(page, errors);
  });
}
