import {waitForManagementRouteReady} from "../ci/frontend_latency/routes.mjs";
import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

const routes = [
  "assistant/basics", "assistant/conversation", "assistant/model-responses", "assistant/prompt-context",
  "assistant/voice", "assistant/speech", "capabilities/home-assistant", "capabilities/web-skills",
  "data-memory/memory-settings", "usage-maintenance/retention",
];
const panelFor = page => page.locator("extended-openai-management-panel");

function controlValues(panel) {
  return panel.evaluate(host => [...host.shadowRoot.querySelectorAll("[data-config], [data-memory-config]")]
    .filter(control => !control.disabled)
    .map(control => ({
      key:control.dataset.config || control.dataset.memoryConfig,
      kind:control.type || control.tagName.toLowerCase(),
      dataType:control.dataset.type,
      value:control.type === "checkbox" ? control.checked : control.value,
    })));
}

async function changeControl(panel, key) {
  const control = panel.locator(`[data-config="${key}"], [data-memory-config="${key}"]`).first();
  if (!(await control.count())) return null;
  if (!(await control.isVisible())) return null;
  const state = await control.evaluate(element => ({
    tag:element.tagName.toLowerCase(), type:element.type, disabled:element.disabled,
    dataType:element.dataset.type, checked:element.checked,
    min:element.min, max:element.max, value:element.value,
    options:element.tagName === "SELECT" ? [...element.options].filter(option => !option.disabled).map(option => option.value) : [],
  }));
  if (state.disabled) return null;
  let edited;
  if (state.type === "checkbox") {
    edited = !state.checked;
    await control.setChecked(edited);
  } else if (state.tag === "select") {
    const next = state.options.find(value => value !== state.value);
    if (next === undefined) return null;
    edited = next;
    await control.selectOption(next);
  } else if (state.type === "number" || state.type === "range") {
    const min = state.min === "" ? null : Number(state.min), max = state.max === "" ? null : Number(state.max);
    if (state.type === "number") {
      await control.fill("");
      await control.dispatchEvent("change");
    }
    if (min !== null) {
      await control.fill(String(min));
      expect(await control.evaluate(element => element.validity.valid), `${key} minimum should be accepted`).toBe(true);
    }
    if (max !== null) {
      await control.fill(String(max));
      expect(await control.evaluate(element => element.validity.valid), `${key} maximum should be accepted`).toBe(true);
    }
    const current = Number(state.value || 0);
    const target = min !== null && current !== min ? min : max !== null && current !== max ? max : current + 1;
    edited = String(target);
    await control.fill(String(target));
    await control.dispatchEvent("change");
    expect(await control.evaluate(element => element.validity.valid), `${key} should accept its selected numeric value`).toBe(true);
  } else if (state.tag === "textarea") {
    edited = "Nightly control matrix content";
    await control.fill(edited);
  } else {
    edited = `${state.value || ""} nightly`;
    await control.fill(edited);
  }
  return {key, kind:state.type || state.tag, dataType:state.dataType, value:edited};
}

// This oracle parses the values we entered; Save output never supplies it.
function expectedValue(field) {
  if (field.kind === "checkbox") return field.value;
  if (field.dataType === "number" || ["archive_retention_days", "usage_request_retention_days", "usage_run_retention_days"].includes(field.key)) return Number(field.value);
  if (field.key === "skills") return String(field.value).split(/\r?\n/).map(value => value.trim()).filter(Boolean);
  if (/^guest_(readable|controllable)_/.test(field.key)) return String(field.value).split(",").map(value => value.trim()).filter(Boolean);
  return field.value;
}

test("nightly assistant configuration control matrix edits, saves, and reloads every ordinary field", async ({page}) => {
  const errors = trackPageErrors(page);
  const coverage = {};
  for (const route of routes) {
    await page.goto(fixtureUrl(route));
    await waitForManagementRouteReady(page, {name: route, path: route}, 30000);
    const panel = panelFor(page);
    await expect(panel.locator("main")).toBeVisible();
    const initial = await controlValues(panel);
    expect(initial.length, `${route} must expose ordinary configuration controls`).toBeGreaterThan(0);
    const keys = [...new Set(initial.map(item => item.key))].sort((a, b) => {
      const priority = key => ({api_mode:0, memory_retrieval_mode:0, web_search:0}[key] ?? 1);
      const byFeature = priority(a) - priority(b);
      if (byFeature) return byFeature;
      const checkbox = key => initial.find(item => item.key === key)?.kind === "checkbox" ? 1 : 0;
      return checkbox(a) - checkbox(b);
    });
    const changed = [];
    const edits = new Map();
    for (const key of keys) {
      const control = await changeControl(panel, key);
      if (control) { changed.push(key); edits.set(key, control); }
    }
    for (const {key} of await controlValues(panel)) {
      if (keys.includes(key) || changed.includes(key)) continue;
      const control = await changeControl(panel, key);
      if (control) { changed.push(key); edits.set(key, control); }
    }
    expect(changed.length, `${route} should exercise its enabled controls through the UI`).toBeGreaterThan(0);
    await expect(panel.locator("#save-config")).toBeVisible();
    const visibleBeforeSave = await controlValues(panel);
    const expected = [...edits.values()]
      .map(field => ({...field, expected: expectedValue(field)}));
    await panel.locator("#save-config").click();
    await expect(panel.locator("#save-config")).toHaveCount(0);
    const saved = await panel.evaluate(host => ({
      config:structuredClone(host._configData?.config || host._result?.config || {}),
      title:host._configData?.title,
      draftTitle:host._draftTitle,
    }));
    coverage[route] = {keys, changed, dependentDisabled:keys.filter(key => !changed.includes(key)), visibleBeforeSave};
    for (const field of expected) {
      if (field.key === "__title") expect(saved.title).toBe(field.expected);
      else {
        expect(Object.hasOwn(saved.config, field.key), `${route}/${field.key} missing from Save`).toBe(true);
        expect(saved.config[field.key], `${route}/${field.key} Save`).toEqual(field.expected);
      }
    }

    await page.goto(fixtureUrl(route));
    await waitForManagementRouteReady(page, {name: route, path: route}, 30000);
    const reloaded = await controlValues(panelFor(page));
    for (const field of expected) {
      const actual = reloaded.find(item => item.key === field.key);
      expect(actual, `${route}/${field.key} should remain present after fresh load`).toBeTruthy();
      expect(expectedValue(actual), `${route}/${field.key} fresh load`).toEqual(field.expected);
    }
  }
  expect(Object.keys(coverage)).toEqual(routes);
  await expectHarnessClean(page, errors);
});

test("nightly configuration validation maps backend field errors onto the matching control", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/basics"));
  const panel = panelFor(page);
  await panel.locator('[data-config="max_tokens"]').fill("1300");
  await panel.evaluate(host => {
    const original = host._hass.callWS.bind(host._hass);
    host._hass.callWS = async message => {
      if (message.section === "configuration" && message.action === "save") {
        return {...host._configData, valid:false, errors:{max_tokens:"Nightly validation: token limit rejected."}};
      }
      return original(message);
    };
  });
  await panel.locator("#save-config").click();
  await expect(panel.locator('[data-error="max_tokens"]')).toHaveText("Nightly validation: token limit rejected.");
  await expect(panel.locator("#toast")).toContainText("Fix the highlighted configuration errors");
  await expect(panel.locator("#save-config")).toBeVisible();
  await expectHarnessClean(page, errors);
});

test("nightly configuration parent controls reveal and enable their dependent settings", async ({page}) => {
  const errors = trackPageErrors(page);
  const panel = panelFor(page);
  await page.goto(fixtureUrl("capabilities/web-skills"));
  await expect(panel.locator('[data-config="web_search_context"]')).toBeDisabled();
  await panel.locator('[data-config="web_search"]').check();
  await expect(panel.locator('[data-config="web_search_context"]')).toBeEnabled();
  await panel.locator("#save-config").click();
  await expect(panel.locator("#save-config")).toHaveCount(0);

  await page.goto(fixtureUrl("data-memory/memory-settings"));
  await expect(panel.locator('[data-memory-config="memory_embedding_model"]')).toBeDisabled();
  await panel.locator('[data-memory-config="memory_retrieval_mode"]').selectOption("hybrid");
  await expect(panel.locator('[data-memory-config="memory_embedding_model"]')).toBeEnabled();
  await panel.locator("#save-config").click();
  await expect(panel.locator("#save-config")).toHaveCount(0);

  await page.goto(fixtureUrl("assistant/speech"));
  await expect(panel.locator('[data-config="speech_strip_markdown"]')).toBeDisabled();
  await panel.locator('[data-config="speech_processing_enabled"]').check();
  await expect(panel.locator('[data-config="speech_strip_markdown"]')).toBeEnabled();
  await expect(panel.locator('[data-config="speech_strip_urls"]')).toBeEnabled();
  await panel.locator("#save-config").click();
  await expect(panel.locator("#save-config")).toHaveCount(0);

  await page.goto(fixtureUrl("assistant/voice"));
  await expect(panel.locator('[data-config="voice_unmapped_policy"]')).toBeDisabled();
  await panel.locator('[data-config="voice_scope_policy"]').selectOption("device_mapping");
  await expect(panel.locator('[data-config="voice_unmapped_policy"]')).toBeEnabled();
  await expect(panel.locator("#add-voice-mapping")).toBeVisible();
  await panel.locator("#add-voice-mapping").click();
  await expect(panel.locator("[data-voice-mapping-row]")).toHaveCount(1);
  await expectHarnessClean(page, errors);
});
