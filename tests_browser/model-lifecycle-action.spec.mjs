import {expect, test} from "@playwright/test";
import {fixtureUrl} from "./browser-helpers.mjs";

for (const bundled of [false, true]) for (const confirmed of [false, true]) test(`model warning reaches its advertised control: retired=${confirmed} bundle=${bundled}`, async ({page}) => {
  await page.goto(fixtureUrl("guide", bundled ? "&bundle=1" : ""));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator(".guide-search")).toBeVisible();
  await panel.evaluate(async (host, confirmed) => {
    const send = host.hass.callWS.bind(host.hass);
    host.hass.callWS = async request => {
      const response = await send(request);
      if (request.section === "overview" && (response.setup_health || request.kind === "setup_health")) {
        response.setup_health = {...response.setup_health, can_manage:true, model_lifecycle:{model:"deprecated-probe", status:"deprecated", confirmed_unavailable:confirmed}};
      }
      return response;
    };
    await host._navigate("overview");
  }, confirmed);
  await panel.getByRole("button", {name:"Change model", exact:true}).click();
  const selector = panel.locator('[data-config="chat_model"]');
  await expect(selector).toBeVisible();
  await expect(selector).toBeEnabled();
});
