import {expect, test} from "@playwright/test";
import {openColdHaRoute} from "./real-ha-shell-helpers.mjs";

test.skip(!process.env.REAL_HA_FRONTEND_URL || !process.env.REAL_HA_FRONTEND_AUTH,
  "requires genuine HA frontend and authenticated management");

const payloads = [
  '<img src=x onerror="(window.__eoaiHostileExecuted=1,localStorage.setItem("eoai-hostile-executed","1"))">',
  '<svg onload="(window.__eoaiHostileExecuted=2,localStorage.setItem("eoai-hostile-executed","1"))"></svg>',
  '<a href="javascript:(window.__eoaiHostileExecuted=3,localStorage.setItem("eoai-hostile-executed","1"))">do not follow</a>',
  '"><script>(window.__eoaiHostileExecuted=4,localStorage.setItem("eoai-hostile-executed","1"))</script>'
];

test("hostile managed records stay inert in real HA frontend and after reload", async ({context, page}) => {
  test.setTimeout(120000);
  const errors = [];
  page.on("pageerror", e => errors.push(e.message));
  const panel = await openColdHaRoute(context, page, "data-memory/knowledge");
  await page.evaluate(() => {window.__eoaiHostileExecuted = 0; localStorage.removeItem("eoai-hostile-executed");});
  try {
    for (const [index, value] of payloads.entries()) {
      await panel.locator("#add-source").click();
      await panel.locator("#knowledge-title").fill(`Hostile reference ${index}`);
      await panel.locator("#knowledge-content").fill(value);
      await panel.locator("#knowledge-save").click();
      await expect(panel.locator("#knowledge-dialog")).not.toBeVisible();
    }
    await expect(panel.locator(".list-card").filter({hasText:"Hostile reference 0"})).toBeVisible();
    await panel.evaluate(host => host._loadSection(true));
    await page.reload();
    await expect(panel.locator("#agent")).toBeEnabled();
    await expect(panel.locator(".list-card").filter({hasText:"Hostile reference 0"})).toBeVisible();

    await panel.evaluate(host => host._navigate("data-memory", "memories"));
    await panel.locator("#add-memory").click();
    await panel.locator("#memory-content").fill(payloads[0]);
    await panel.locator("#memory-category").fill("adversarial");
    await panel.locator("#memory-save").click();
    await expect(panel.locator("#memory-dialog")).not.toBeVisible();
    await expect(panel.locator(".memory-list")).toContainText(payloads[0]);

    const state = await page.evaluate(() => ({
      executed: window.__eoaiHostileExecuted || 0,
      persistedExecution: localStorage.getItem("eoai-hostile-executed"),
      untrustedImage: [...document.querySelectorAll("extended-openai-management-panel")].some(
        panel => [...(panel.shadowRoot?.querySelectorAll("img,svg,script") || [])]
          .some(element => (element.outerHTML || "").includes("__eoaiHostileExecuted"))
      ),
      dangerousLinks: [...document.querySelectorAll("extended-openai-management-panel")].some(
        panel => [...(panel.shadowRoot?.querySelectorAll('a[href^="javascript:"]') || [])]
          .some(link => (link.outerHTML || "").includes("__eoaiHostileExecuted"))
      )
    }));
    expect(state.executed).toBe(0);
    expect(state.persistedExecution).toBeNull();
    expect(state.untrustedImage).toBe(false);
    expect(state.dangerousLinks).toBe(false);
    expect(errors).toEqual([]);
  } finally {
    // An isolated HA test fixture is thrown away, so never run client-side cleanup
    // that could obscure evidence when assertions fail.
  }
});
