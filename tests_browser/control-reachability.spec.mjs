import fs from "node:fs";
import path from "node:path";
import {fileURLToPath} from "node:url";
import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const routeInventory = JSON.parse(
  fs.readFileSync(path.join(ROOT, "tests_stress/frontend_route_inventory.json"), "utf8"),
);
const reachabilityInventory = JSON.parse(
  fs.readFileSync(path.join(ROOT, "tests_stress/frontend_control_reachability_inventory.json"), "utf8"),
);

const CONTROL_SELECTOR = [
  "button",
  'input:not([type="hidden"])',
  "select",
  "textarea",
  "summary",
  "a[href]",
  "ha-selector",
  "ha-yaml-editor",
  '[role="button"]',
  '[role="switch"]',
  '[role="checkbox"]',
  '[role="tab"]',
  '[tabindex]:not([tabindex="-1"])',
  '[contenteditable="true"]',
].join(",");

const ROUTE_EXTRAS = {
  "data-memory/conversations": "&seed_conversations=1",
  "usage-maintenance/usage": "&usage_boundary=1",
};

const SURFACES = {
  "assistant/basics": [
    {
      name: "agent import dialog",
      open: ["#import-agent"],
      ready: "#import-dialog",
      close: "#import-cancel",
    },
  ],
  "assistant/prompt-context": [
    {
      name: "effective request preview",
      open: ["#preview-request"],
      ready: "#prompt-preview-dialog",
      close: "#prompt-preview-close",
    },
  ],
  "capabilities/functions": [
    {
      name: "Function Tool editor",
      open: ["#function-add", "#add-tool"],
      ready: "#tool-dialog",
      close: "#tool-cancel",
    },
    {
      name: "Function Group editor",
      open: ["#function-add", "#add-group"],
      ready: "#group-dialog",
      close: "#group-cancel",
    },
  ],
  "capabilities/request-rules": [
    {
      name: "Request Rule editor",
      open: ["#rule-add, #rule-empty-add"],
      ready: "#rule-dialog",
      afterOpen: ["#rule-add-conditions"],
      close: "#rule-dialog .rule-close",
    },
    {
      name: "Request Rule group manager",
      open: ["#rule-groups-manage"],
      ready: "#rule-groups-dialog",
      close: "#rule-groups-done",
    },
  ],
  "data-memory/memories": [
    {
      name: "Memory editor",
      open: ["#add-memory"],
      ready: "#memory-dialog",
      close: "#memory-dialog .close-editor",
    },
  ],
  "data-memory/knowledge": [
    {
      name: "Knowledge editor",
      open: ["#add-source"],
      ready: "#knowledge-dialog",
      close: "#knowledge-dialog .close-editor",
    },
  ],
};

function identityFor(index, meta) {
  return [
    meta.id && `#${meta.id}`,
    meta.name && `[name="${meta.name}"]`,
    meta.aria && `[aria-label="${meta.aria}"]`,
    meta.role && `[role="${meta.role}"]`,
    meta.text && `${meta.tag}:${meta.text.slice(0, 80)}`,
    `${meta.tag}[${index}]`,
  ].find(Boolean);
}

async function expandDetails(panel) {
  await panel.evaluate((host) => {
    host.shadowRoot.querySelectorAll("details").forEach((details) => {
      details.open = true;
    });
  });
}

async function hiddenReason(control, width) {
  return control.evaluate((element, viewportWidth) => {
    if (element.matches("[hidden], input[type=hidden]") || element.closest("[hidden], .hidden")) {
      return "hidden-contract";
    }
    if (element.closest("dialog:not([open])")) return "closed-dialog";
    if (element.closest("details:not([open])")) return "closed-details";
    const popover = element.closest("[popover]");
    if (popover && !popover.matches(":popover-open")) return "closed-popover";
    if (element.closest(".mobile-nav") && viewportWidth > 760) return "desktop-mobile-nav";
    if (element.closest(".top-nav") && viewportWidth <= 760) return "mobile-desktop-nav";
    if (element.closest(".subsection-nav") && viewportWidth <= 800) return "mobile-subsection-nav";
    if (element.closest(".section-selector") && viewportWidth >= 801) return "desktop-section-selector";
    if (element.matches(".local-handling-review > summary")) return "conditional-review-summary";
    return null;
  }, width);
}

async function auditControls(page, panel, label) {
  await expandDetails(panel);

  const viewport = page.viewportSize();
  expect(viewport, `${label}: viewport is unavailable`).not.toBeNull();

  const overflow = await page.evaluate(() => {
    const host = document.querySelector("extended-openai-management-panel");
    return {
      documentWidth: document.documentElement.scrollWidth,
      bodyWidth: document.body.scrollWidth,
      viewportWidth: window.innerWidth,
      hostWidth: host?.scrollWidth || 0,
      hostClientWidth: host?.clientWidth || 0,
    };
  });
  expect(
    overflow.documentWidth,
    `${label}: document has unintended horizontal overflow`,
  ).toBeLessThanOrEqual(overflow.viewportWidth + 2);
  expect(
    overflow.bodyWidth,
    `${label}: body has unintended horizontal overflow`,
  ).toBeLessThanOrEqual(overflow.viewportWidth + 2);
  if (overflow.hostClientWidth) {
    expect(
      overflow.hostWidth,
      `${label}: management host has unintended horizontal overflow`,
    ).toBeLessThanOrEqual(overflow.hostClientWidth + 2);
  }

  const openDialogs = panel.locator("dialog[open]");
  const scope = await openDialogs.count() ? openDialogs.last() : panel;
  const controls = scope.locator(CONTROL_SELECTOR);
  const count = await controls.count();
  let audited = 0;
  let intentionallyHidden = 0;

  for (let index = 0; index < count; index++) {
    const control = controls.nth(index);
    const meta = await control.evaluate((element) => ({
      tag: element.tagName.toLowerCase(),
      id: element.id || "",
      name: element.getAttribute("name") || "",
      aria: element.getAttribute("aria-label") || "",
      role: element.getAttribute("role") || "",
      text: (element.textContent || element.getAttribute("placeholder") || "").trim(),
    }));
    const identity = identityFor(index, meta);
    const visible = await control.isVisible();
    if (!visible) {
      const reason = await hiddenReason(control, viewport.width);
      expect(
        reason,
        `${label}: ${identity} is CSS-hidden without an explicit conditional/responsive contract`,
       ).not.toBeNull();
      intentionallyHidden++;
      continue;
    }

    await control.scrollIntoViewIfNeeded();
    const box = await control.boundingBox();
    expect(box, `${label}: ${identity} has no rendered box`).not.toBeNull();
    expect(box.width, `${label}: ${identity} has zero width`).toBeGreaterThan(0);
    expect(box.height, `${label}: ${identity} has zero height`).toBeGreaterThan(0);

    expect(box.x, `${label}: ${identity} remains clipped to the left`).toBeGreaterThanOrEqual(-1);
    expect(
      box.x + box.width,
      `${label}: ${identity} remains clipped to the right`,
    ).toBeLessThanOrEqual(viewport.width + 1);

    if (box.height <= viewport.height - 2) {
      expect(box.y, `${label}: ${identity} remains clipped above the viewport`).toBeGreaterThanOrEqual(-1);
      expect(
        box.y + box.height,
        `${label}: ${identity} remains clipped below the viewport`,
      ).toBeLessThanOrEqual(viewport.height + 1);
    } else {
      expect(box.y + box.height, `${label}: ${identity} cannot intersect the viewport`).toBeGreaterThan(0);
      expect(box.y, `${label}: ${identity} cannot intersect the viewport`).toBeLessThan(viewport.height);
    }

    const disabled = await control.isDisabled().catch(() => false);
    const trialExcluded =
      meta.tag.startsWith("ha-")
      || (meta.tag === "input" && await control.getAttribute("type") === "file");
    if (!disabled && !trialExcluded) {
      await control.click({trial: true, timeout: 3_000});
    }
    audited++;
  }

  expect(audited, `${label}: no rendered controls were audited`).toBeGreaterThan(0);
  return {discovered: count, audited, intentionallyHidden};
}

async function openSurface(panel, surface) {
  for (const selector of surface.open) {
    const opener = panel.locator(selector).first();
    await opener.evaluate((element) => {
      const details = element.closest("details");
      if (details) details.open = true;
    });
    await expect(opener, `${surface.name}: opener ${selector}`).toBeVisible();
    await opener.click();
  }
  await expect(panel.locator(surface.ready), `${surface.name}: surface did not open`).toBeVisible();
  for (const selector of surface.afterOpen || []) {
    const control = panel.locator(selector).first();
    await expect(control, `${surface.name}: nested opener ${selector}`).toBeVisible();
    await control.click();
  }
}

async function closeSurface(panel, surface) {
  const close = panel.locator(surface.close).last();
  await expect(close, `${surface.name}: close control`).toBeVisible();
  await close.click();
  await expect(panel.locator(surface.ready), `${surface.name}: surface did not close`).not.toBeVisible();
}

test("all rendered controls remain reachable across shipped routes and viewports", async ({page}, testInfo) => {
  test.setTimeout(360_000);
  const diagnostics = trackPageErrors(page);
  const evidence = [];

  for (const viewport of reachabilityInventory.viewports) {
    await page.setViewportSize({width: viewport.width, height: viewport.height});

    for (const route of Object.keys(routeInventory.routes)) {
      const extra = ROUTE_EXTRAS[route] || "";
      await page.goto(fixtureUrl(route, extra));
      const panel = page.locator("extended-openai-management-panel");
      await expect(panel).toHaveCount(1);
      await expect.poll(
        () => panel.evaluate((host) => host._viewKey?.()),
        {message: `${viewport.name}/${route}: route did not settle`},
      ).toBe(route);

      const base = await auditControls(page, panel, `${viewport.name}/${route}`);
      const surfaces = [];
      for (const surface of SURFACES[route] || []) {
        await openSurface(panel, surface);
        const result = await auditControls(
          page,
          panel,
          `${viewport.name}/${route}/${surface.name}`,
        );
        surfaces.push({name: surface.name, ...result});
        await closeSurface(panel, surface);
      }

      evidence.push({
        viewport: viewport.name,
        width: viewport.width,
        height: viewport.height,
        route,
        ...base,
        surfaces,
      });
    }
  }

  await expectHarnessClean(page, diagnostics);
  await testInfo.attach("control-reachability-evidence", {
    body: JSON.stringify(evidence, null, 2),
    contentType: "application/json",
  });
});