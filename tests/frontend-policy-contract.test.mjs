import assert from "node:assert/strict";
import {readdirSync, readFileSync} from "node:fs";
import {join} from "node:path";
import {fileURLToPath} from "node:url";

import {NAVIGATION} from "../custom_components/extended_openai_conversation_responses/frontend/frontend-navigation.js";
import {SETTINGS_INDEX} from "../custom_components/extended_openai_conversation_responses/frontend/management-settings-index.js";
import {searchProjectedSettings} from "../custom_components/extended_openai_conversation_responses/frontend/management-navigation-search.js";
import {CONFIG_OWNER_BY_KEY} from "../custom_components/extended_openai_conversation_responses/frontend/management-config-owners.js";
import {SETTING_LOOKUP} from "../custom_components/extended_openai_conversation_responses/frontend/management-setting-lookup.js";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
const FRONTEND = join(ROOT, "custom_components/extended_openai_conversation_responses/frontend");
const routeInventory = JSON.parse(readFileSync(join(ROOT, "tests_stress/frontend_route_inventory.json"), "utf8"));

function sourceFiles(folder) {
  const files = [];
  for (const entry of readdirSync(folder, {withFileTypes:true})) {
    if (entry.name === "dist") continue;
    const path = join(folder, entry.name);
    if (entry.isDirectory()) files.push(...sourceFiles(path));
    else if (entry.isFile() && /\.(?:js|css)$/.test(entry.name)) files.push(path);
  }
  return files.sort();
}

function navigationRoutes() {
  return NAVIGATION.flatMap((page) =>
    page.sections?.length
      ? page.sections.map((section) => `${page.id}/${section.id}`)
      : [page.id],
  ).sort();
}

// Route inventory is the independent source used by broad browser/nightly
// coverage. New navigation must be represented there before this can pass.
assert.deepEqual(navigationRoutes(), Object.keys(routeInventory.routes).sort());

const indexedRoutes = new Set(Object.keys(routeInventory.routes));
const seenEntries = new Set();
const seenConfigKeys = new Set();
for (const item of SETTINGS_INDEX) {
  const route = `${item.page}/${item.section}`.replace(/\/(?:null|undefined)$/, "");
  assert(indexedRoutes.has(route), `Settings index points to an unknown route: ${route} (${item.label})`);
  assert(String(item.label || "").trim(), `Settings index entry on ${route} has no label`);
  assert(String(item.description || "").trim(), `Settings index entry "${item.label}" has no description`);
  assert(String(item.terms || "").trim(), `Settings index entry "${item.label}" has no search terms`);
  const identity = `${route}|${item.label}`;
  assert(!seenEntries.has(identity), `Duplicate settings index entry: ${identity}`);
  seenEntries.add(identity);

  const search = searchProjectedSettings(item.label);
  assert(search.includes(item), `Exact user-facing label cannot find "${item.label}"`);

  if (!item.configKey) continue;
  assert(!seenConfigKeys.has(item.configKey), `Duplicate settings-index owner for ${item.configKey}`);
  seenConfigKeys.add(item.configKey);
  assert.equal(CONFIG_OWNER_BY_KEY[item.configKey], route, `Generated owner drift for ${item.configKey}`);
  assert.equal(SETTING_LOOKUP[item.configKey]?.label, item.label, `Generated lookup drift for ${item.configKey}`);
  assert(
    String(SETTING_LOOKUP[item.configKey]?.aliases || "").includes(item.configKey.toLowerCase()),
    `Generated lookup aliases omit ${item.configKey}`,
  );
}
assert.deepEqual(new Set(Object.keys(CONFIG_OWNER_BY_KEY)), seenConfigKeys);
assert.deepEqual(new Set(Object.keys(SETTING_LOOKUP)), seenConfigKeys);

// Shared surfaces are intentionally a small vocabulary. This is a review
// tripwire rather than a claim that new patterns are forbidden: a contributor
// can add an explicitly reviewed surface here when the existing patterns are
// genuinely unsuitable.
const APPROVED_SURFACES = new Set([
  "always-card",
  "backup-panel",
  "backup-surface",
  "card",
  "config-card",
  "config-surface",
  "content-card",
  "dashboard-card",
  "eoc-overview-snapshot-card",
  "eoc-model-data-panel",
  "feature-status-card",
  "function-group-card",
  "guide-quick-card",
  "list-card",
  "request-rule-card",
  "rule-card",
  "retention-surface",
  "rule-preview-panel",
  "supporting-panel",
  "transfer-panel",
  "tool-card",
  "tools-surface",
  "usage-diagnostic-panel",
  "usage-range-card",
  "usage-request-card",
  "voice-default-card",
  "voice-mappings-card",
  "voice-policy-card",
]);

const foundSurfaces = new Set();
for (const path of sourceFiles(FRONTEND)) {
  const source = readFileSync(path, "utf8");
  for (const match of source.matchAll(/class(?:Name)?\s*=\s*(?:"([^"]*)"|'([^']*)'|`([^`]*)`)/g)) {
    const value = (match[1] || match[2] || match[3] || "").replace(/\$\{[^}]*\}/g, " ");
    for (const token of value.split(/\s+/).filter(Boolean)) {
      if ((token === "card" || /-(?:card|panel|surface)$/.test(token)) && !token.startsWith("cm-")) {
        foundSurfaces.add(token);
      }
    }
  }
  for (const match of source.matchAll(/\.([A-Za-z_][\w-]*(?:-(?:card|panel|surface)|card))\b/g)) {
    if (!match[1].startsWith("cm-")) foundSurfaces.add(match[1]);
  }
}
assert.deepEqual(
  [...foundSurfaces].filter((name) => !APPROVED_SURFACES.has(name)).sort(),
  [],
  "New *-card/*-panel/*-surface pattern requires explicit frontend-policy review",
);

// Hard-coded colours are deliberately constrained. The allowlist contains the
// shared semantic fallbacks, neutral disabled fallback, shadows/backdrops, and
// two historical false-positive selector/ID fragments (#add and #039). A new
// literal requires an intentional review instead of silently becoming a
// feature-specific colour.
const APPROVED_COLOUR_LITERALS = new Set([
  "#000", "#0002", "#0003", "#039", "#0f9d58", "#9e9e9e", "#add",
  "#b26a00", "#db4437", "#f9ab00", "#ff9800", "#fff",
  "rgba(0,0,0,.035)", "rgba(0,0,0,.04)", "rgba(0,0,0,.06)",
  "rgba(0,0,0,.07)", "rgba(0,0,0,.14)", "rgba(0,0,0,.16)",
  "rgba(0,0,0,.2)", "rgba(0,0,0,.25)", "rgba(0,0,0,.28)",
  "rgba(0,0,0,.3)", "rgba(0,0,0,.35)", "rgba(0,0,0,.45)",
  "rgba(0,0,0,.5)", "rgba(127,127,127,.35)",
]);
const colourViolations = [];
for (const path of sourceFiles(FRONTEND)) {
  const source = readFileSync(path, "utf8");
  for (const match of source.matchAll(/#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|hsla?\([^)]*\)/g)) {
    const literal = match[0].toLowerCase().replace(/\s+/g, "");
    if (!APPROVED_COLOUR_LITERALS.has(literal)) {
      colourViolations.push(`${path.replace(ROOT + "/", "")}: ${match[0]}`);
    }
  }
}
assert.deepEqual(colourViolations, [], "New hard-coded frontend colour requires semantic/theme review");

console.log("Frontend policy static contracts passed.");
