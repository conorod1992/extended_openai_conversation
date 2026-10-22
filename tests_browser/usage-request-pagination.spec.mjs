import {expect, test} from "@playwright/test";
import fs from "node:fs";
import {fixtureUrl, trackPageErrors, expectHarnessClean} from "./browser-helpers.mjs";

const reachabilityInventory = JSON.parse(fs.readFileSync(new URL("../tests_stress/frontend_control_reachability_inventory.json", import.meta.url), "utf8"));
for (const viewport of reachabilityInventory.viewports) for (const bundled of [false, true]) test(`Usage request pages expose later failures (${viewport.name}, ${bundled})`, async ({page}) => {
  const errors = trackPageErrors(page);
  await page.setViewportSize({width:viewport.width,height:viewport.height});
  await page.clock.install({time:new Date("2026-09-29T12:00:00Z")});
  await page.goto(fixtureUrl("overview", bundled ? "&bundle=1" : ""));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator(".dashboard-grid")).toBeVisible();
  await panel.evaluate(async (host, retryPage) => {
    const original = host._hass.callWS.bind(host._hass);
    host.__requestPages = [];
    host._hass.callWS = async message => {
      if (message.section !== "usage") return original(message);
      if (message.action === "summary") return {today:{date:"2026-09-29",total_tokens:110},lifetime:{total_tokens:110}};
      if (message.action === "daily") return {days:[{date:"2026-09-29",total_tokens:110}]};
      if (message.action === "retention") return {};
      if (message.action === "runs") return {runs:[{run_id:"embedding-run",completed_at:"2026-09-29T12:00:00Z",total_tokens:110,request_count:110,successful:false,error_type:"EmbeddingFailure"}]};
      if (message.action === "requests") {
        host.__requestPages.push(structuredClone(message));
        const offset = message.offset || 0;
        if (offset === 100 && retryPage && !host.__pageRetrySeen) {
          host.__pageRetrySeen = true;
          throw new Error("Temporary page error");
        }
        const count = Math.min(message.limit, 110 - offset);
        return {requests:Array.from({length:count}, (_, i) => ({successful:offset+i!==105,error_type:offset+i===105?"LateEmbeddingFailure":null,provider:"OpenAI",model:"embedding",total_tokens:1,timestamp:"2026-09-29T12:00:00Z"})),offset,limit:message.limit,returned:count,has_more:offset+count<110,next_offset:offset+count<110?offset+count:null};
      }
      return original(message);
    };
    await host._navigate("usage-maintenance", "usage");
  }, !bundled);
  await panel.locator('[data-usage-run-id="embedding-run"]').click();
  await expect(panel.locator(".usage-request-card")).toHaveCount(100);
  await expect(panel.locator("#usage-request-range")).toHaveText("Requests 1–100");
  await expect(panel.locator("#usage-request-previous")).toBeDisabled();
  await panel.locator("#usage-request-next").click();
  if (!bundled) {
    await expect(panel.locator("#usage-request-body [role=alert]")).toHaveText("Temporary page error");
    await panel.locator("#usage-request-retry").click();
  }
  await expect(panel.locator(".usage-request-card")).toHaveCount(10);
  await expect(panel.locator("#usage-request-range")).toHaveText("Requests 101–110");
  await expect(panel.locator(".usage-request-card h3").filter({hasText:"LateEmbeddingFailure"})).toHaveText("Request 106 · LateEmbeddingFailure");
  await expect(panel.locator("#usage-request-next")).toBeDisabled();
  await panel.locator("#usage-request-previous").click();
  await expect(panel.locator(".usage-request-card")).toHaveCount(100);
  await expect(panel.locator("#usage-request-range")).toHaveText("Requests 1–100");
  expect(await panel.evaluate(host => host.__requestPages.map(call => call.offset || 0))).toEqual(bundled ? [0,100,0] : [0,100,100,0]);
  await panel.locator(".close-usage-requests").first().click();
  await expect(panel.locator("#usage-request-dialog")).toHaveJSProperty("open", false);
  await expectHarnessClean(page,errors);
});
