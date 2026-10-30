import {expect, test} from "@playwright/test";

for (const code of ["invalid_request", "unknown_error", null]) {
  test(`real HA HTTP bridge preserves mutation classification: ${code}`, async ({page}) => {
    await page.route("http://bridge.test/callws*", async route => {
      const request = route.request().postDataJSON();
      const failure = request.type === "bridge-error-probe";
      await route.fulfill({
        status:failure ? 400 : 200,
        headers:{"Access-Control-Allow-Origin":"*"},
        json:failure ? {error:{...(code === null ? {} : {code}), message:"Probe refused"}} : {},
      });
    });
    await page.goto("/tests_browser/real-ha-fixture.html?backend=http%3A%2F%2Fbridge.test%2Fcallws");
    await page.waitForFunction(() => window.browserHarness?.hass);
    const outcome = await page.evaluate(async () => {
      const {callMutation} = await import("/custom_components/extended_openai_conversation_responses/frontend/mutation-response.js");
      try {
        await callMutation(() => browserHarness.hass.callWS({type:"bridge-error-probe"}));
        return {resolved:true};
      } catch (error) {
        return {code:error.code, outcome:error.mutationOutcome, message:error.message};
      }
    });
    expect(outcome.message).toContain("Probe refused");
    expect(outcome.outcome).toBe(code === "invalid_request" ? undefined : "uncertain");
    if (code === "invalid_request") expect(outcome.code).toBe(code);
    expect(await page.evaluate(() => browserHarness.calls.filter(call => call.type === "bridge-error-probe").length)).toBe(1);
  });
}
