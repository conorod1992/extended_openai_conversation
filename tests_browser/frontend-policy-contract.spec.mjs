import fs from "node:fs";
import path from "node:path";
import {fileURLToPath} from "node:url";
import {expect, test} from "@playwright/test";

import {SETTINGS_INDEX} from "../custom_components/extended_openai_conversation_responses/frontend/management-settings-index.js";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const routeInventory = JSON.parse(
  fs.readFileSync(path.join(ROOT, "tests_stress/frontend_route_inventory.json"), "utf8"),
);
const ROUTES = Object.keys(routeInventory.routes);
const ROUTE_EXTRAS = {
  "data-memory/conversations": "&seed_conversations=1",
  "usage-maintenance/usage": "&usage_boundary=1",
};

function indexedKeys(route) {
  const [page, section] = route.split("/");
  return new Set(
    SETTINGS_INDEX
      .filter((item) => item.page === page && item.section === section && item.configKey)
      .map((item) => item.configKey),
  );
}

async function semanticDistances(locator, property) {
  return locator.evaluateAll((nodes, propertyName) => {
    const visible = (element) => {
      const style = getComputedStyle(element);
      return style.display !== "none" && style.visibility !== "hidden"
        && element.getClientRects().length > 0;
    };
    const colour = (value) => {
      const canvas = document.createElement("canvas");
      canvas.width = canvas.height = 1;
      const drawing = canvas.getContext("2d");
      drawing.clearRect(0, 0, 1, 1);
      drawing.fillStyle = "#000";
      drawing.fillStyle = value || "#000";
      drawing.fillRect(0, 0, 1, 1);
      return [...drawing.getImageData(0, 0, 1, 1).data].slice(0, 3);
    };
    const distance = (left, right) => Math.hypot(
      ...left.map((value, index) => value - right[index]),
    );
    return nodes.filter(visible).map((element) => {
      const style = getComputedStyle(element);
      const actual = colour(style[propertyName]);
      const token = (name, fallback) => colour(style.getPropertyValue(name).trim() || fallback);
      return {
        text: (element.textContent || element.getAttribute("aria-label") || element.id || element.className).trim().slice(0, 120),
        neutral: Math.min(
          distance(actual, token("--secondary-text-color", "#777")),
          distance(actual, token("--divider-color", "#bbb")),
        ),
        primary: distance(actual, token("--primary-color", "#03a9f4")),
        warning: distance(actual, token("--warning-color", "#f9ab00")),
        success: distance(actual, token("--success-color", "#0f9d58")),
        error: distance(actual, token("--error-color", "#db4437")),
      };
    });
  }, property);
}

function expectClosest(records, expected, alternatives, label) {
  for (const record of records) {
    for (const alternative of alternatives) {
      expect.soft(
        record[expected],
        `${label}: ${record.text || "(unnamed)"} should be closer to ${expected} than ${alternative}`,
      ).toBeLessThan(record[alternative]);
    }
  }
}

test("all management routes satisfy deterministic frontend policy contracts", async ({page}) => {
  test.setTimeout(180000);
  const diagnostics = trackPageErrors(page);

  for (const route of ROUTES) {
    await page.goto(fixtureUrl(route, ROUTE_EXTRAS[route] || ""));
    const panel = page.locator("extended-openai-management-panel");

    await expect.soft(panel.locator("h1:visible"), `${route}: exactly one visible routed H1`).toHaveCount(1);
    const product = panel.locator(".page-heading .product-title");
    await expect.soft(product, `${route}: product identity remains visible`).toHaveText("Extended OpenAI");
    expect.soft(
      await product.evaluate((element) => /^H[1-6]$/.test(element.tagName)),
      `${route}: product identity must not consume a semantic heading level`,
    ).toBe(false);

    const outline = await panel.locator("h1:visible,h2:visible,h3:visible,h4:visible").evaluateAll(
      (nodes) => nodes.map((node) => ({
        level: Number(node.tagName.slice(1)),
        text: (node.textContent || "").trim().slice(0, 120),
      })),
    );
    expect.soft(outline.length, `${route}: visible heading outline`).toBeGreaterThan(0);
    expect.soft(outline[0].level, `${route}: first visible heading is H1`).toBe(1);
    expect.soft(outline.filter((heading) => heading.level === 1), `${route}: one H1`).toHaveLength(1);
    for (let index = 1; index < outline.length; index++) {
      expect.soft(
        outline[index].level,
        `${route}: heading "${outline[index].text}" skips a level after "${outline[index - 1].text}"`,
      ).toBeLessThanOrEqual(outline[index - 1].level + 1);
    }

    const indexed = indexedKeys(route);
    const renderedFields = await panel.locator("[data-setting][data-field]").evaluateAll((nodes) =>
      [...new Set(nodes.map((node) => node.dataset.field).filter(Boolean))],
    );
    for (const field of renderedFields) {
      expect.soft(indexed.has(field), `${route}: rendered setting "${field}" is missing from global settings search`).toBe(true);
    }

    const unexplainedDisabled = await panel.locator(
      "[data-setting] input,[data-setting] select,[data-setting] textarea,[data-setting] ha-selector,[data-setting] ha-user-picker,[data-setting] ha-entity-picker",
    ).evaluateAll((nodes) => {
      const visible = (element) => {
        const style = getComputedStyle(element);
        return style.display !== "none" && style.visibility !== "hidden"
          && element.getClientRects().length > 0;
      };
      const text = (element) => (element?.textContent || "").trim();
      return nodes.filter((control) => control.disabled && visible(control)).filter((control) => {
        if (control.closest('[aria-busy="true"]')) return false;
        if (control.getAttribute("title")?.trim()) return false;
        const described = (control.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean)
          .some((id) => text(control.getRootNode().getElementById?.(id)));
        if (described) return false;
        const setting = control.closest("[data-setting]");
        if (setting?.querySelector("small,.help,.capability-note,.voice-picker-note,.voice-picker-warning")) return false;
        const dependent = control.closest(".dependent.is-disabled");
        if (dependent) {
          let previous = dependent.previousElementSibling;
          while (previous && previous.hasAttribute("hidden")) previous = previous.previousElementSibling;
          if (previous && text(previous)) return false;
        }
        return true;
      }).map((control) => ({
        id: control.id,
        field: control.closest("[data-field]")?.dataset.field || "",
        label: control.getAttribute("aria-label") || control.closest("label")?.textContent?.trim() || "",
      }));
    });
    expect.soft(unexplainedDisabled, `${route}: visible disabled controls need an explanation or visible prerequisite`).toEqual([]);

    const actionIssues = await panel.locator(
      ".dialog-actions,.section-actions,.rule-heading-actions,.backup-actions,.tools-actions,.card-heading,.section-heading",
    ).evaluateAll((contexts) => {
      const visible = (element) => {
        const style = getComputedStyle(element);
        return style.display !== "none" && style.visibility !== "hidden"
          && element.getClientRects().length > 0;
      };
      const opaqueBackground = (button) => {
        const value = getComputedStyle(button).backgroundColor;
        if (value === "transparent") return false;
        const match = value.match(/rgba?\(([^)]+)\)/);
        if (!match) return true;
        const parts = match[1].split(",").map((item) => Number.parseFloat(item.trim()));
        return parts.length < 4 || parts[3] > 0.05;
      };
      return contexts.filter(visible).map((context) => {
        const buttons = [...context.querySelectorAll("button")].filter(visible);
        const primary = buttons.filter((button) => !button.classList.contains("danger") && opaqueBackground(button));
        return {
          label: (context.querySelector("h2,h3,strong")?.textContent || context.className || "action context").trim(),
          primary: primary.map((button) => (button.textContent || button.getAttribute("aria-label") || "").trim()),
        };
      }).filter((item) => item.primary.length > 1);
    });
    expect.soft(actionIssues, `${route}: one competing primary action per local context`).toEqual([]);

    expectClosest(
      await semanticDistances(panel.locator(".notice.warning"), "borderLeftColor"),
      "warning", ["neutral", "success", "error"], `${route} warning notice`,
    );
    expectClosest(
      await semanticDistances(panel.locator(".notice.success,.notice.on"), "borderLeftColor"),
      "success", ["neutral", "warning", "error"], `${route} success notice`,
    );
    expectClosest(
      await semanticDistances(panel.locator(".notice.error"), "borderLeftColor"),
      "error", ["neutral", "warning", "success"], `${route} error notice`,
    );
    expectClosest(
      await semanticDistances(panel.locator(".notice:not(.warning):not(.success):not(.on):not(.error)"), "borderLeftColor"),
      "neutral", ["warning", "success", "error"], `${route} informational notice`,
    );
    expectClosest(
      await semanticDistances(panel.locator(".disabled-badge,.neutral-badge"), "color"),
      "neutral", ["success", "error"], `${route} disabled/inactive badge`,
    );
    expectClosest(
      await semanticDistances(panel.locator("button.danger"), "backgroundColor"),
      "error", ["primary"], `${route} danger action`,
    );
    await page.waitForLoadState("networkidle");
  }

  await expectHarnessClean(page, diagnostics);
});
