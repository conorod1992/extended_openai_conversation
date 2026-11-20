import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

test("Diagnostics renders the selected agent and a real result from its management action", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("usage-maintenance/diagnostics"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("[data-eoc-main]")).toContainText("Jarvis");
  await panel.locator("#test-agent").click();
  await expect(panel.locator("#test-result .diagnostic-summary.passed")).toBeVisible();
  await expect(panel.locator("#test-result .diagnostic-check")).toContainText("Model access");
  expect(await page.evaluate(() => window.browserHarness.calls.filter(call =>
    call.section === "diagnostics" && call.action === "test_agent").length)).toBe(1);
  await expectHarnessClean(page, errors);
});

async function holdDiagnostics(panel) {
  await panel.evaluate(host => {
    const hass = host.hass;
    const call = hass.callWS.bind(hass);
    const run = host._testAgent.bind(host);
    const requests = [];
    const runs = [];
    window.browserHarness.diagnostics = {requests, runs};
    host._testAgent = () => { runs.push(run()); };
    hass.callWS = (message) => {
      if (message.section !== "diagnostics" || message.action !== "test_agent") return call(message);
      return new Promise((resolve, reject) => requests.push({message, resolve, reject}));
    };
  });
}

async function startDiagnostic(panel, index, agentId) {
  await panel.locator("#test-agent").click();
  expect(await panel.evaluate((_host, index) =>
    window.browserHarness.diagnostics.requests[index].message.subentry_id, index)).toBe(agentId);
}

async function settleDiagnostic(panel, index, {rejected = false, authentication = false} = {}) {
  await panel.evaluate(async (_host, {index, rejected, authentication}) => {
    const {requests, runs} = window.browserHarness.diagnostics;
    const request = requests[index];
    if (rejected) request.reject(new Error("Previous request failed"));
    else request.resolve({
      status: authentication ? "failed" : "passed",
      authentication_rejected: authentication,
      checks: [{name: `Result ${index}`, status: authentication ? "failed" : "passed"}],
    });
    await runs[index];
  }, {index, rejected, authentication});
}

for (const transition of ["assistant switch", "assistant round trip", "disconnect", "account round trip"]) {
  test(`Diagnostics discards results after a retired ${transition}`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl("usage-maintenance/diagnostics"));
    const panel = page.locator("extended-openai-management-panel");
    await expect(panel.locator("#eoc-change-api-key")).toBeVisible();
    if (transition === "account round trip") {
      await panel.evaluate(host => { host.hass = {...host.hass, user: {id: "original-user"}}; });
    }
    await holdDiagnostics(panel);
    await panel.evaluate(host => {
      host._data.agents.push({...host._data.agents[0], entry_id: "entry-2", subentry_id: "agent-2", title: "Friday"});
      host._render();
    });

    await startDiagnostic(panel, 0, "agent-1");
    const picker = panel.locator("#agent");
    if (transition === "assistant switch" || transition === "assistant round trip") {
      await picker.selectOption("agent-2");
      await expect(panel.locator("main")).toContainText("Friday");
      if (transition === "assistant round trip") {
        await picker.selectOption("agent-1");
        await expect(panel.locator("main")).toContainText("Jarvis");
      }
    } else if (transition === "disconnect") {
      await panel.evaluate(host => {
        const parent = host.parentElement;
        host.remove();
        parent.append(host);
      });
    } else {
      await panel.evaluate(host => {
        const original = host.hass;
        host.hass = {...original, user: {...original.user, id: "different-user"}};
        host.hass = original;
      });
    }

    await expect(panel.locator("#test-agent")).toBeEnabled();
    await startDiagnostic(panel, 1, transition === "assistant switch" ? "agent-2" : "agent-1");
    await settleDiagnostic(panel, 0, {authentication: true});
    await expect(panel.locator("#test-agent")).toBeDisabled();
    await expect(panel.locator("#test-result")).toContainText("Running diagnostic checks");
    await expect(panel.locator("#eoc-auth-recovery")).toHaveCount(0);
    await settleDiagnostic(panel, 1, {authentication: true});
    await expect(panel.locator("#test-result")).toContainText("Result 1");
    await expect(panel.locator("#eoc-auth-recovery")).toBeVisible();
    await expect(panel.locator("#test-agent")).toBeEnabled();
    await expectHarnessClean(page, errors);
  });
}

test("A late diagnostics transport error cannot clear the newer credential warning", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("usage-maintenance/diagnostics"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#eoc-change-api-key")).toBeVisible();
  await holdDiagnostics(panel);
  await startDiagnostic(panel, 0, "agent-1");
  await panel.evaluate(async host => {
    await host._navigate("guide");
    await host._navigate("usage-maintenance", "diagnostics");
  });
  await startDiagnostic(panel, 1, "agent-1");
  await settleDiagnostic(panel, 1, {authentication: true});
  await settleDiagnostic(panel, 0, {rejected: true});
  await expect(panel.locator("#test-result")).toContainText("Result 1");
  await expect(panel.locator("#eoc-auth-recovery")).toBeVisible();
  await expectHarnessClean(page, errors);
});

test("An acknowledged credential update retires an older diagnostic", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("usage-maintenance/diagnostics"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#eoc-change-api-key")).toBeVisible();
  await holdDiagnostics(panel);
  await startDiagnostic(panel, 0, "agent-1");
  await panel.locator("#eoc-change-api-key").click();
  await panel.locator("#eoc-new-api-key").fill("fixture-credential");
  await panel.locator("#eoc-api-key-save").click();
  await expect(panel.locator("#test-result")).toContainText("Credential updated or validated");
  await settleDiagnostic(panel, 0, {authentication: true});
  await expect(panel.locator("#test-result")).toContainText("Credential updated or validated");
  await expect(panel.locator("#eoc-auth-recovery")).toHaveCount(0);
  await expect(panel.locator("#test-agent")).toBeEnabled();
  await expectHarnessClean(page, errors);
});
