import {expect, test} from "@playwright/test";
import {openColdHaRoute} from "./real-ha-shell-helpers.mjs";

test.skip(!process.env.REAL_HA_FRONTEND_URL, "requires isolated genuine Home Assistant shell");

test("native HA Guest intervals retain finite values through indefinite saves and navigation", async ({context, page}) => {
  const panel = await openColdHaRoute(context, page, "capabilities/guest-mode");
  const values = await panel.evaluate(host => ({
    start:host._dateTimeLocal(new Date(Date.now() + 3_600_000).toISOString()),
    end:host._dateTimeLocal(new Date(Date.now() + 7_200_000).toISOString()),
  }));
  await panel.locator("#guest-start").fill(values.start);
  await panel.locator("#guest-end").fill(values.end);
  await panel.locator("#guest-indefinite").check();
  await panel.getByRole("button", {name:"Usage & Maintenance", exact:true}).click();
  await panel.getByRole("button", {name:"Capabilities", exact:true}).click();
  await panel.getByRole("button", {name:"Guest Mode", exact:true}).click();
  await expect(panel.locator("#guest-start")).toHaveValue(values.start);
  await expect(panel.locator("#guest-end")).toHaveValue(values.end);
  await expect(panel.locator("#guest-indefinite")).toBeChecked();
  await panel.locator("#guest-update").click();
  await expect(panel.locator("#toast")).toContainText("Guest Mode updated");
  await expect(panel.locator("#guest-end")).toHaveValue(values.end);
  await panel.locator("#guest-indefinite").uncheck();
  await panel.locator("#guest-update").click();
  await expect.poll(() => panel.evaluate(async host => {
    const status = (await host._call("guest_mode", "get")).status;
    return {indefinite:status.indefinite, end:host._dateTimeLocal(status.active_until)};
  })).toEqual({indefinite:false, end:values.end});
});

test("native HA validates off-route fields and reveals the error without dropping the draft", async ({context, page}) => {
  const panel = await openColdHaRoute(context, page, "assistant/basics");
  await panel.evaluate(host => {
    const original = host.hass.callWS.bind(host.hass);
    host.hass.callWS = async request => {
      const response = await original(request);
      if (request.section === "configuration" && request.action === "save" && response.valid === false) {
        await new Promise(resolve => { window.releaseNativeValidation = resolve; });
      }
      return response;
    };
  });
  await panel.locator("#config-max_tokens").fill("-1");
  await panel.getByRole("button", {name:"Usage & Maintenance", exact:true}).click();
  await panel.getByRole("button", {name:"Usage data retention", exact:true}).click();
  await panel.locator("#save-config").press("Enter");
  await expect.poll(() => page.evaluate(() => Boolean(window.releaseNativeValidation))).toBe(true);
  await expect.poll(() => panel.evaluate(host => host.shadowRoot.activeElement?.id || "")).toBe("");
  await page.evaluate(() => window.releaseNativeValidation());
  const errors = panel.getByRole("region", {name:"Configuration errors", exact:true});
  await expect(errors).toBeFocused();
  await errors.locator('[data-config-error="max_tokens"]').click();
  await expect(panel.locator("#config-max_tokens")).toBeFocused();
  await expect(panel.locator("#config-max_tokens")).toHaveValue("-1");
  await expect(panel.locator("#config-max_tokens")).toHaveAttribute("aria-invalid", "true");
  await panel.locator("#revert-config").click();
  await expect(panel.locator("#configuration-errors")).toHaveCount(0);
});

test("a disabled group remains disabled through genuine HA editing and dependency rejection", async ({context, page}) => {
  const panel = await openColdHaRoute(context, page, "capabilities/functions");
  await panel.evaluate(async host => {
    await host._call("tools", "save", {tool:{spec:{name:"usability_probe", description:"Disabled group editing probe", parameters:{type:"object", properties:{}}}, function:{type:"template", value_template:"Local test"}, enabled:true}});
    const config = await host._call("configuration", "get");
    await host._call("tools", "save_group", {revision:config.revision, group:{id:"disabled_usability", name:"Disabled usability group", description:"Prepared offline", loading_mode:"on_demand", functions:["usability_probe"], enabled:false}});
    await host._loadSection(true);
  });
  const group = panel.locator('[data-group-id="disabled_usability"].function-group-card');
  await expect(group.locator(".group-enabled")).not.toBeChecked();
  await group.locator(".edit-group").click();
  await panel.locator("#group-name").fill("Edited safely while disabled");
  await panel.locator("#group-save").click();
  await expect(panel.locator("#group-dialog")).not.toBeVisible();
  const saved = await panel.evaluate(async host => {
    const config = await host._call("configuration", "get");
    const group = config.config.function_groups.find(item => item.id === "disabled_usability");
    let rejected = false;
    try { await host._call("tools", "save_group", {revision:config.revision, original_id:group.id, group:{...group, functions:["missing_function"]}}); }
    catch { rejected = true; }
    return {group, rejected, after:(await host._call("configuration", "get")).config};
  });
  expect(saved.group).toMatchObject({enabled:false, name:"Edited safely while disabled", functions:["usability_probe"]});
  expect(saved.rejected).toBe(true);
  expect(saved.after.function_groups.find(item => item.id === "disabled_usability")).toEqual(saved.group);
  expect(saved.after.functions.find(item => item.spec?.name === "usability_probe").enabled).not.toBe(false);
});

test("shared Save cancellation preserves real usage details and confirmed disabling prunes only detail", async ({context, page}) => {
  const panel = await openColdHaRoute(context, page, "usage-maintenance/retention");
  const before = await panel.evaluate(async host => ({runs:await host._call("usage", "runs"), summary:await host._call("usage", "summary")}));
  expect(before.runs.runs.length).toBeGreaterThan(0);
  await panel.locator('[data-retention-config="usage_request_retention_days"]').selectOption("0");
  await panel.locator('[data-retention-config="usage_run_retention_days"]').selectOption("0");
  await panel.getByRole("button", {name:/^Assistant(?:,|$)/}).click();
  await panel.getByRole("button", {name:"Basics", exact:true}).click();
  await panel.locator("#save-config").click();
  await expect(panel.locator("#confirm-message")).toContainText("request details and run details");
  await panel.locator("#confirm-cancel").click();
  const cancelled = await panel.evaluate(async host => ({runs:await host._call("usage", "runs"), draft:host._draft}));
  expect(cancelled.runs.runs.length).toBe(before.runs.runs.length);
  expect(cancelled.draft.usage_request_retention_days).toBe(0);
  await panel.locator("#save-config").click();
  await panel.locator("#confirm-accept").click();
  await expect.poll(() => panel.evaluate(async host => (await host._call("configuration", "get")).config.usage_run_retention_days)).toBe(0);
  await expect.poll(() => panel.evaluate(async host => (await host._call("usage", "runs")).runs.length)).toBe(0);
  const after = await panel.evaluate(async host => host._call("usage", "summary"));
  expect(after.lifetime.total_tokens).toBe(before.summary.lifetime.total_tokens);
});
