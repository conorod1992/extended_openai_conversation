import {expect, test} from "@playwright/test";
import {openDataCollection} from "./data-collection-helpers.mjs";

for (const bundled of [false, true]) for (const kind of ["persistent", "temporary"]) for (const lost of [false, true, "recovered", "authentication"]) {
  test(`Memory amended retry retains its uncertain intent: ${kind} lost=${lost} bundle=${bundled}`, async ({page}) => {
    const panel = await openDataCollection(page, "persistent", 1, bundled);
    await panel.evaluate(async (host, {kind, lost}) => {
      host.hass = {...host.hass, user:{...host.hass.user, id:"memory-owner"}};
      const send = host.hass.callWS.bind(host.hass);
      host.hass.callWS = async request => {
        const result = await send(request);
        if (lost && ["add", "temporary_add"].includes(request.action)) {
          if (lost === "authentication") await new Promise(resolve => {window.releaseMemoryReply = resolve;});
          throw {code:"unknown_error", message:"Unknown error"};
        }
        return result;
      };
      await host._openMemory();
      host.shadowRoot.querySelector("#memory-type").value = kind;
      host.shadowRoot.querySelector("#memory-type").dispatchEvent(new Event("change"));
      const future = new Date(Date.now() + 86400000);
      host.shadowRoot.querySelector("#memory-expiry").value = future.toISOString().slice(0, 19);
    }, {kind, lost});
    await panel.locator("#memory-content").fill("Oscar eats dinner at 18:00");
    await panel.locator("#memory-save").click();
    if (lost === "authentication") {
      await expect.poll(() => page.evaluate(() => typeof window.releaseMemoryReply)).toBe("function");
      await panel.evaluate(host => {
        const original = host.hass;
        host.hass = {...original, user:{...original.user, id:"intervening-memory-user"}};
        host.hass = original;
      });
      await page.evaluate(() => releaseMemoryReply());
    }
    if (lost) {
      if (lost !== "authentication") await expect(panel.locator("#memory-error")).toContainText("outcome is unknown");
    } else {
      await expect(panel.locator("#memory-dialog")).not.toBeVisible();
      // Reopen the acknowledged record; the next action is an update.
      if (kind === "persistent") await panel.evaluate(host => host._openMemory(dataCollectionBackend.state.memories.at(-1).memory_id));
      else return;
    }
    if (["recovered", "authentication"].includes(lost)) {
      await panel.evaluate(host => {
        const parent = host.parentElement, hass = host.hass;
        host.remove();
        const replacement = document.createElement("extended-openai-management-panel");
        parent.append(replacement); replacement.hass = hass;
      });
      await expect(panel.locator("#memory-content")).toHaveValue("Oscar eats dinner at 18:00");
    }
    await panel.locator("#memory-content").fill("Oscar eats dinner at 19:00");
    // Exercise dispatch admission independently of the disabled button.
    await panel.evaluate(host => host._saveMemory());
    const result = await page.evaluate(kind => ({
      requests:dataCollectionBackend.calls.filter(call => ["add", "temporary_add", "update"].includes(call.action)).map(call => call.action),
      facts:(kind === "persistent" ? dataCollectionBackend.state.memories : dataCollectionBackend.state.temporary).filter(record => record.content.startsWith("Oscar")).map(record => record.content),
    }), kind);
    expect(result.requests).toEqual(lost ? [kind === "persistent" ? "add" : "temporary_add"] : ["add", "update"]);
    expect(result.facts).toEqual([lost ? "Oscar eats dinner at 18:00" : "Oscar eats dinner at 19:00"]);
    if (lost) await expect(panel.locator("#memory-save")).toBeDisabled();
  });
}
