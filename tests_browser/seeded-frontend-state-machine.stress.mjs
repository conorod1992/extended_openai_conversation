import {expect, test} from "@playwright/test";
import {acceptConfirmation, exerciseConfigurationDraftTransition, expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";
import {waitForManagementRouteReady} from "../ci/frontend_latency/routes.mjs";

const seeds = [7319, 20260930, 0x5eed];
function randomFor(seed) {
  let value = seed >>> 0;
  return limit => {
    value = (Math.imul(value, 1664525) + 1013904223) >>> 0;
    return (value >>> 8) % limit;
  };
}

for (const seed of seeds) {
  test(`seeded cross-surface state machine seed=${seed}`, async ({page, context}, testInfo) => {
    test.setTimeout(180000);
    page.setDefaultTimeout(10000);
    const errors = trackPageErrors(page);
    const history = [];
    const model = {title:"Jarvis", knowledge:[], memories:[], rules:[]};
    const random = randomFor(seed);
    const panel = page.locator("extended-openai-management-panel");
    const route = async path => {
      history.push({operation:"navigate", path});
      const [section, subsection] = path.split("/");
      await panel.evaluate((host, [section, subsection]) => host._navigate(section, subsection), [section, subsection]);
      await waitForManagementRouteReady(page, {name:path, path}, 30000);
    };
    const checkpoint = async () => {
      const saved = await page.evaluate(() => window.browserHarness.getState());
      expect(saved.configuration.title).toBe(model.title);
      expect(saved.knowledgeSources.filter(item => item.title.startsWith(`Seed ${seed}`)).map(item => item.title).sort()).toEqual([...model.knowledge].sort());
      expect(saved.memories.filter(item => item.content.startsWith(`Seed ${seed}`)).map(item => item.content).sort()).toEqual([...model.memories].sort());
      expect(saved.requestRules.rules.filter(item => item.name.startsWith(`Seed ${seed}`)).map(item => item.name)).toEqual(model.rules);
      expect(saved.requestRules.rules.map(item => item.order)).toEqual(saved.requestRules.rules.map((_, index) => index));
      await expect(panel.locator("#agent")).toHaveValue("agent-1");
      expect(await panel.evaluate(host => host._agentId)).toBe("agent-1");
      await expect(panel.locator("main")).toBeVisible();
      await expect.poll(() => panel.evaluate(host => Boolean(host._configDirty))).toBe(false);
      await expectHarnessClean(page, errors);
      // Every committed write must still target the assistant displayed above.
      const writes = await page.evaluate(() => window.browserHarness.calls.filter(call => ["save", "create", "update", "delete", "add", "duplicate", "move"].includes(call.action)));
      expect(writes.every(call => !call.subentry_id || call.subentry_id === "agent-1")).toBe(true);
    };
    try {
      // The static fixture server has no HA SPA fallback. Keep direct route
      // reloads on the same URL while serving the existing fixture document.
      await context.route("**/extended-openai/**", async request => {
        if (!request.request().isNavigationRequest()) return request.continue();
        const path = new URL(request.request().url()).pathname.replace("/extended-openai/", "");
        await request.fulfill({response:await request.fetch({url:new URL(fixtureUrl(path), request.request().url()).href})});
      });
      await page.goto(fixtureUrl("assistant/basics"));
      await waitForManagementRouteReady(page, {name:"basics", path:"assistant/basics"}, 30000);
      const rounds = process.env.STRESS_INTENSITY === "heavy" ? 36 : 18;
      for (let index = 0; index < rounds; index++) {
        const surface = ["knowledge", "memories", "assistant"][random(3)];
        const mode = ["create", "edit", "cancel", "delete"][random(4)];
        history.push({index, surface, mode, model:structuredClone(model)});
        if (surface === "assistant") {
          await route("assistant/basics");
          const title = panel.locator('[data-config="__title"]');
          await title.fill(`Seed ${seed} assistant ${index}`);
          await expect.poll(() => panel.evaluate(host => host._configDirty)).toBe(true);
          if (mode === "cancel") {
            await panel.locator("#revert-config").click();
          } else {
            model.title = `Seed ${seed} assistant ${index}`;
            await panel.locator("#save-config").click();
          }
          await expect(title).toHaveValue(model.title);
        } else {
          const knowledge = surface === "knowledge";
          const owned = model[surface];
          await route(`data-memory/${surface}`);
          const search = panel.locator("#list-search");
          await search.fill("");
          const prefix = knowledge ? "knowledge" : "memory";
          const marker = `Seed ${seed} ${surface} ${index}`;
          const card = panel.locator(".list-card").filter({hasText:owned[0] || "unused"});
          if (mode === "delete" && owned.length) {
            await card.locator(knowledge ? ".delete-source" : ".delete-memory").click();
            await acceptConfirmation(panel);
            owned.shift();
          } else {
            const editing = mode === "edit" && owned.length;
            if (editing) await card.locator(knowledge ? ".source-edit-button" : ".memory-edit-button").click();
            else await panel.locator(knowledge ? "#add-source" : "#add-memory").click();
            await panel.locator(knowledge ? "#knowledge-title" : "#memory-content").fill(marker);
            if (knowledge) await panel.locator("#knowledge-content").fill(`Body ${seed}-${index}`);
            if (mode === "cancel") {
              await panel.locator(`#${prefix}-dialog`).getByRole("button", {name:"Cancel", exact:true}).click();
            } else {
              await panel.locator(`#${prefix}-save`).click();
              if (editing) owned[0] = marker; else owned.push(marker);
            }
            await expect(panel.locator(`#${prefix}-dialog`)).not.toBeVisible();
          }
          await search.fill(`Seed ${seed}`);
          await expect(panel.locator(knowledge ? "[data-source-id]:visible" : "[data-memory-id]:visible")).toHaveCount(owned.length);
          await panel.evaluate(host => host._loadSection(true));
          await expect(panel.locator(knowledge ? "[data-source-id]:visible" : "[data-memory-id]:visible")).toHaveCount(owned.length);
        }
        if (index % 6 === 1) {
          history.push({operation:"draft-immediate-save-failure-reload", index});
          await exerciseConfigurationDraftTransition(page, `Seed ${seed} prompt ${index}`);
        }
        await checkpoint();
        if (index % 6 === 2) {
          const previous = new URL(page.url()).pathname;
          await route("assistant/conversation");
          await page.goBack();
          await expect(page).toHaveURL(new RegExp(`${previous}$`));
          await page.goForward();
          await expect(page).toHaveURL(/assistant\/conversation$/);
          await page.reload();
          await waitForManagementRouteReady(page, {name:"conversation", path:"assistant/conversation"}, 30000);
          history.push({operation:"back-forward-reload"});
          await checkpoint();
        }
      }
      // Duplicate, reorder and delete have a separate ordered model.
      await route("capabilities/request-rules");
      await panel.locator("#rule-add").click();
      await panel.locator("#rule-name").fill(`Seed ${seed} rule`);
      await panel.locator("#rule-phrases").fill(`seed route ${seed}`);
      await panel.locator("#rule-action-type").selectOption("model_routing");
      await panel.locator("#rule-model").fill("gpt-5-mini");
      await panel.locator("#rule-save").click();
      await expect(panel.locator("#rule-dialog")).not.toBeVisible();
      model.rules.push(`Seed ${seed} rule`);
      const rule = panel.locator(".request-rule-card").filter({hasText:model.rules[0]});
      await rule.locator(".rule-duplicate").click({timeout:10000});
      await expect.poll(() => page.evaluate(() => window.browserHarness.getState().requestRules.rules.length)).toBe(3);
      const copy = `${model.rules[0]} copy`;
      expect(await page.evaluate(() => window.browserHarness.getState().requestRules.rules.at(-1).name)).toBe(copy);
      model.rules.push(copy);
      const copied = panel.locator(".request-rule-card").filter({hasText:copy});
      await copied.locator(".rule-move-menu summary").click();
      await copied.locator('.rule-move[data-direction="up"]').click();
      model.rules.reverse();
      await checkpoint();
      await copied.locator(".rule-delete").click();
      await acceptConfirmation(panel);
      model.rules.shift();
      await checkpoint();
      history.push({operation:"rule-duplicate-reorder-delete"});

      // Reuse the reconnect campaign's offline callWS boundary.
      await page.evaluate(() => {
        const harness = window.browserHarness;
        const original = harness.hass.callWS.bind(harness.hass);
        harness.backendOnline = false;
        harness.hass.callWS = message => {
          if (!harness.backendOnline) return Promise.reject(new Error("Home Assistant backend disconnected"));
          return original(message);
        };
      });
      await route("usage-maintenance/usage");
      await expect(panel.getByText("Usage summary unavailable", {exact:true})).toBeVisible();
      await page.evaluate(() => { window.browserHarness.backendOnline = true; });
      await route("data-memory/knowledge");
      await expect(panel.locator("#add-source")).toBeEnabled();
      await checkpoint();
      history.push({operation:"disconnect-recover"});

      const other = await context.newPage();
      const otherErrors = trackPageErrors(other);
      await other.goto(fixtureUrl("assistant/basics"));
      await expect(other.locator('[data-config="__title"]')).toHaveValue(model.title);
      await expectHarnessClean(other, otherErrors);
      await other.close();
      history.push({operation:"second-tab-authoritative-read"});
      await checkpoint();
    } finally {
      await testInfo.attach(`state-machine-seed-${seed}`, {body:JSON.stringify({seed, history, model, backend:await page.evaluate(() => window.browserHarness?.getState()).catch(() => null)}, null, 2), contentType:"application/json"});
    }
  });
}
