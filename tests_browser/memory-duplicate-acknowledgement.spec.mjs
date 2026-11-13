import {test, expect} from "@playwright/test";
import {openDataCollection} from "./data-collection-helpers.mjs";
import {trackPageErrors, expectHarnessClean} from "./browser-helpers.mjs";

for (const bundled of [false, true]) {
  for (const status of ["created", "updated", "duplicate"]) {
    test(`persistent ${status} acknowledgement follows backend and preserves drafts (${bundled})`, async ({page}) => {
      const errors = trackPageErrors(page);
      const panel = await openDataCollection(page, "persistent", 3, bundled);
      await panel.evaluate((host, status) => {
        const send = host.hass.callWS.bind(host.hass);
        window.memoryCountsBefore = structuredClone(host._data.scopes);
        window.agentCountBefore = host._selectedAgent().memory_count;
        host.hass.callWS = async request => {
          if (request.section === "memories" && request.action === "add") {
            window.memoryAddRequest = request;
            await new Promise(resolve => { window.releaseMemoryAdd = resolve; });
            const memory = structuredClone(dataCollectionBackend.state.memories[0]);
            if (status === "created") return send(request);
            return {status, scope_id:request.scope_id, memory};
          }
          return send(request);
        };
        window.memoryStatus = status;
      }, status);
      await panel.locator("#add-memory").click();
      await panel.locator("#memory-content").fill("Duplicate draft");
      // Exercise metadata drafts without expanding the production Add form.
      await panel.evaluate(host => { host.shadowRoot.querySelector("#memory-valid-from").value = "2026-01-01T00:00:00Z"; });
      await panel.locator("#memory-save").click();
      await expect.poll(() => page.evaluate(() => typeof releaseMemoryAdd)).toBe("function");
      await panel.locator("#memory-content").fill("Amended while waiting");
      await page.evaluate(() => releaseMemoryAdd());
      const message = status === "created" ? "Memory added" : status === "updated" ? "Existing memory updated" : "Memory already exists; no changes made";
      await expect(panel.locator(".toast")).toContainText(message);
      await expect(panel.locator("#memory-dialog")).toHaveJSProperty("open", true);
      await expect(panel.locator("#memory-content")).toHaveValue("Amended while waiting");
      if (status === "duplicate") {
        await expect(panel.locator("#memory-error")).toContainText("metadata differs");
        await expect(panel.locator("#memory-dialog-title")).toHaveText("Add memory");
        await expect(panel.locator("#memory-save")).toBeEnabled();
        const state = await panel.evaluate(host => ({editing:host._editingMemory, scopeCounts:host._data.scopes,
          originalCounts:window.memoryCountsBefore, count:host._selectedAgent().memory_count, original:window.agentCountBefore,
          uncertain:!!host.shadowRoot.querySelector("#memory-dialog").__eocUncertainMutation}));
        expect(state.editing).toBeNull();
        expect(state.scopeCounts).toEqual(state.originalCounts);
        expect(state.count).toBe(state.original);
        expect(state.uncertain).toBe(false);
        await expect(panel.locator("#memory-valid-from")).toHaveValue("2026-01-01T00:00:00Z");
      } else {
        await expect(panel.locator("#memory-dialog-title")).toHaveText("Edit memory");
      }
      await expectHarnessClean(page, errors);
    });
  }
}
