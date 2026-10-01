import {expect, test} from "@playwright/test";
import {openColdHaRoute, replaceNativeYaml} from "./real-ha-shell-helpers.mjs";

const feature = process.env.REAL_HA_GOLDEN_FEATURE;
const phase = process.env.REAL_HA_GOLDEN_PHASE;
test.skip(!process.env.REAL_HA_FRONTEND_URL || !feature, "requires genuine HA golden acceptance");

const toolYaml = revision => `spec:\n  name: golden_shell_tool\n  description: Golden shell runtime tool\n  parameters:\n    type: object\n    properties: {}\nfunction:\n  type: template\n  value_template: golden-tool-${revision}\n`;

test("cold native shell authoring reaches runtime and availability boundaries", async ({context, page}) => {
  test.setTimeout(90000);
  const panel = await openColdHaRoute(context, page, feature === "tool" ? "capabilities/functions" : "data-memory/memories");
  // Two assistants are present on a completely fresh direct-route entry.
  await expect(panel.locator("#agent option")).toHaveCount(2);
  const primary = await panel.locator("#agent").inputValue();
  const choices = await panel.locator("#agent option").evaluateAll(nodes => nodes.map(node => node.value));
  expect(choices).toHaveLength(2);
  if (phase === "create") {
    await panel.locator("#agent").selectOption(choices.find(value => value !== primary));
    await expect(panel.locator("#agent")).not.toHaveValue(primary);
    await panel.locator("#agent").selectOption(primary);
    await expect(panel.locator("#agent")).toHaveValue(primary);
  }
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  if (feature === "tool") {
    await expect(panel.locator("#add-tool")).toBeVisible();
    const card = panel.locator('[data-tool-key="golden_shell_tool"]');
    if (phase === "disable") {
      await expect(card).toBeVisible();
      await card.locator(".tool-enabled").uncheck();
      await expect(card.locator(".tool-enabled")).toBeEnabled();
      await expect(card.locator(".tool-enabled")).not.toBeChecked();
    } else {
      if (phase === "create") await panel.locator("#add-tool").click();
      else await card.locator(".edit-tool").click();
      const editor = panel.locator("#tool-yaml-native");
      await replaceNativeYaml(page, editor, toolYaml(phase === "create" ? "v1" : "v2"));
      await panel.locator("#tool-validate").click();
      await expect(panel.locator("#tool-error")).toHaveClass(/valid/);
      await panel.locator("#tool-save").click();
      await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", false);
      await expect(card).toContainText("Golden shell runtime tool");
    }
  } else {
    if (phase === "create") {
      await panel.locator("#add-memory").click();
      await panel.locator("#memory-content").fill("The golden observatory token is cobalt-golden-memory.");
      await panel.locator("#memory-category").fill("observatory");
      await panel.locator("#memory-save").click();
      await expect(panel.locator("#memory-dialog")).toHaveJSProperty("open", false);
      await panel.evaluate(host => host._navigate("data-memory", "knowledge"));
      await panel.locator("#add-source").click();
      await panel.locator("#knowledge-title").fill("Golden observatory handbook");
      await panel.locator("#knowledge-content").fill("The golden observatory calibration is cobalt-golden-knowledge.");
      await panel.locator("#knowledge-save").click();
      await expect(panel.locator("#knowledge-dialog")).toHaveJSProperty("open", false);
      await panel.locator("#knowledge-enabled-toggle").check();
      await expect(panel.locator("#knowledge-enabled-toggle")).toBeEnabled();
    } else {
      await expect(panel.locator(".memory-list")).toContainText("cobalt-golden-memory");
      await panel.evaluate(host => host._navigate("data-memory", "memory-settings"));
      await panel.locator("#config-memory_mode").selectOption("off");
      await panel.getByRole("button", {name: "Save changes", exact: true}).click();
      await expect(panel.locator(".save-bar")).toHaveCount(0);
      await panel.evaluate(host => host._navigate("data-memory", "knowledge"));
      await expect(panel.locator(".list-card")).toContainText("Golden observatory handbook");
      await panel.locator("#knowledge-enabled-toggle").uncheck();
      await expect(panel.locator("#knowledge-enabled-toggle")).toBeEnabled();
      await expect(panel.locator("#knowledge-enabled-toggle")).not.toBeChecked();
    }
  }
  expect(errors).toEqual([]);
});
