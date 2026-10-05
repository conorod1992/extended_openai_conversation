import {expect} from "@playwright/test";

// Hold only the WebSocket dependency. Production ownership and rendering run intact.
export async function installIntentGate(page, section, actions) {
  await page.evaluate(({section, actions}) => {
    const original = browserHarness.hass.callWS.bind(browserHarness.hass);
    window.intentGate = {pending: [], schedule: [], restore: () => { browserHarness.hass.callWS = original; }};
    browserHarness.hass.callWS = request => {
      if (request.section !== section || !actions.includes(request.action)) return original(request);
      const index = intentGate.pending.length;
      intentGate.schedule.push({event: "requested", index, request});
      return new Promise((resolve, reject) => intentGate.pending.push({request, settle: async (failure, marker, payload) => {
        intentGate.schedule.push({event: "settled", index, failure, marker});
        if (failure) { reject(new Error(marker)); await new Promise(requestAnimationFrame); return; }
        const response = payload || await original(request);
        response.intentMarker = marker;
        resolve(response);
        await new Promise(requestAnimationFrame);
      }}));
    };
  }, {section, actions});
}

export async function waitForIntents(page, count) {
  await expect.poll(() => page.evaluate(() => intentGate.pending.length)).toBe(count);
}

export async function settleIntent(page, index, failure, marker, payload = null) {
  await page.evaluate(({index, failure, marker, payload}) => intentGate.pending[index].settle(failure, marker, payload), {index, failure, marker, payload});
}

export async function assertLatestIntent(page, failure, marker, observe) {
  await expect.poll(observe).toEqual(failure ? {error: marker} : {marker});
  const schedule = await page.evaluate(() => intentGate.schedule);
  expect(schedule.filter(row => row.event === "settled").map(row => row.index)).toEqual([1, 0]);
  return schedule;
}
