import {expect, test} from "@playwright/test";

const backendUrl = process.env.REAL_HA_BACKEND_URL;
const controlUrl = process.env.REAL_HA_CONTROL_URL;
const seed = process.env.STRESS_SEED || "0";
test.skip(!backendUrl || !controlUrl, "requires the scheduled/manual genuine HA WebSocket bridge");

const fixture = route => `/tests_browser/real-ha-fixture.html?route=${encodeURIComponent(route)}&backend=${encodeURIComponent(backendUrl)}`;
const panel = page => page.locator("extended-openai-management-panel");
const title = page => panel(page).locator('[data-config="__title"]');

async function control(page, identity = "owner", delay_ms = 0) {
  const response = await page.evaluate(async ({url, identity, delay_ms}) => fetch(url, {
    method: "POST", body: JSON.stringify({identity, delay_ms}),
  }).then(result => result.json()), {url: controlUrl, identity, delay_ms});
  expect(response.identity).toBe(identity);
}

test("dropped save keeps its draft and the authoritative HA state", async ({page}) => {
  await page.goto(fixture("assistant/basics"));
  await expect(title(page)).toBeVisible();
  const original = await title(page).inputValue();
  const draft = `Dropped save ${seed}`;
  await title(page).fill(draft);
  let dropped = 0;
  await page.route(backendUrl, route => {
    const body = route.request().postDataJSON();
    if (dropped === 0 && body?.section === "configuration" && body?.action === "save") {
      dropped++;
      return route.abort("failed");
    }
    return route.continue();
  });
  await panel(page).getByRole("button", {name: "Save changes", exact: true}).click();
  await expect.poll(() => dropped).toBe(1);
  await expect(title(page)).toHaveValue(draft);
  await expect(panel(page).getByText("Unsaved changes", {exact: true})).toBeVisible();

  const fresh = await page.context().newPage();
  try {
    await fresh.goto(fixture("assistant/basics"));
    await expect(title(fresh)).toHaveValue(original);
  } finally { await fresh.close(); }

  await page.unroute(backendUrl);
  await panel(page).getByRole("button", {name: "Save changes", exact: true}).click();
  await expect(panel(page).getByText("Unsaved changes", {exact: true})).toHaveCount(0);
  await page.goto(fixture("assistant/basics"));
  await expect(title(page)).toHaveValue(draft);
});

test("expired transport and owner-to-restricted change never claim a successful save", async ({page}) => {
  await page.goto(fixture("assistant/basics"));
  await expect(title(page)).toBeVisible();
  await title(page).fill(`Protected draft ${seed}`);

  for (const identity of ["expired", "restricted"]) {
    await control(page, identity);
    await panel(page).getByRole("button", {name: "Save changes", exact: true}).click();
    await expect(title(page)).toHaveValue(`Protected draft ${seed}`);
    await expect(panel(page).getByText("Unsaved changes", {exact: true})).toBeVisible();
  }
  await control(page, "owner");
  const fresh = await page.context().newPage();
  try {
    await fresh.goto(fixture("assistant/basics"));
    await expect(title(fresh)).not.toHaveValue(`Protected draft ${seed}`);
  } finally { await fresh.close(); }
  await panel(page).getByRole("button", {name: "Save changes", exact: true}).click();
  await expect(panel(page).getByText("Unsaved changes", {exact: true})).toHaveCount(0);
});

test("slow reads and a dropped read converge on real HA state", async ({page}) => {
  await page.goto(fixture("assistant/basics"));
  await expect(title(page)).toBeVisible();
  await control(page, "owner", 900);
  const start = Date.now();
  await page.goto(fixture("data-memory/memories"));
  await expect(panel(page).getByRole("heading", {name: "Memories", exact: true})).toBeVisible();
  expect(Date.now() - start).toBeGreaterThanOrEqual(800);
  await control(page);

  let dropped = 0;
  await page.route(backendUrl, route => {
    const body = route.request().postDataJSON();
    if (dropped === 0 && body?.section === "memories" && body?.action === "list") {
      dropped++;
      return route.abort("failed");
    }
    return route.continue();
  });
  await page.goto(fixture("data-memory/memories"));
  await expect.poll(() => dropped).toBe(1);
  await page.unroute(backendUrl);
  await page.goto(fixture("data-memory/memories"));
  await expect(panel(page).getByRole("heading", {name: "Memories", exact: true})).toBeVisible();
  await expect(panel(page).locator("#add-memory")).toBeVisible();
});

test("long-lived browser and fresh browser agree after seeded saves", async ({page}) => {
  await page.goto(fixture("assistant/basics"));
  await expect(title(page)).toBeVisible();
  for (let index = 0; index < 12; index++) {
    const value = `Nightly ${seed} revision ${index}`;
    await title(page).fill(value);
    await panel(page).getByRole("button", {name: "Save changes", exact: true}).click();
    await expect(panel(page).getByText("Unsaved changes", {exact: true})).toHaveCount(0);
  }
  const expected = `Nightly ${seed} revision 11`;
  const fresh = await page.context().newPage();
  try {
    await fresh.goto(fixture("assistant/basics"));
    await expect(title(fresh)).toHaveValue(expected);
    await expect(title(page)).toHaveValue(expected);
  } finally { await fresh.close(); }
});
