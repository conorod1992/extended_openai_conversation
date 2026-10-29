import {expect, test} from "@playwright/test";
import {browserToolYaml, expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

// Model HA's two distinct Lit updates: setValue changes YAML first; the child
// CodeMirror document follows later. Neither setter completion nor the outer
// update alone proves that the editable document contains the intended text.
async function installDelayedEditor(page) {
  await page.addInitScript(() => {
    if (!sessionStorage.getItem("readiness-initialized")) {
      localStorage.clear();
      sessionStorage.setItem("readiness-initialized", "true");
    }
    window.nativeHydrations = [];
    customElements.define("ha-yaml-editor", class extends HTMLElement {
      constructor() {
        super();
        this.attachShadow({mode:"open"});
        const code = document.createElement("ha-code-editor");
        const surface = document.createElement("textarea");
        code.attachShadow({mode:"open"}).append(surface);
        code.codemirror = {state:{doc:{toString:() => surface.value}}};
        this.shadowRoot.append(code);
        surface.addEventListener("input", () => {
          this._yaml = surface.value;
          this.dispatchEvent(new CustomEvent("value-changed", {detail:{isValid:true}}));
        });
      }
      get yaml() { return this._yaml || ""; }
      setValue() {
        this._yaml = this.getRootNode().querySelector("#tool-yaml").value;
        const yaml = this._yaml;
        const code = this.shadowRoot.querySelector("ha-code-editor");
        let outer, inner;
        this.updateComplete = new Promise(resolve => { outer = resolve; });
        code.updateComplete = new Promise(resolve => { inner = resolve; });
        window.nativeHydrations.push({
          yaml,
          outer:() => outer(true),
          inner:() => {
            code.shadowRoot.querySelector("textarea").value = yaml;
            this._yaml = yaml;
            this.dispatchEvent(new CustomEvent("value-changed", {detail:{isValid:true}}));
            inner(true);
          },
        });
      }
      focus() { this.shadowRoot.querySelector("ha-code-editor").shadowRoot.querySelector("textarea").focus(); }
    });
  });
}

async function releaseHydration(page) {
  await page.evaluate(() => {
    for (const pending of window.nativeHydrations) { pending.outer(); pending.inner(); }
  });
}

const assertHydrating = async panel => {
  await expect(panel.locator("#tool-save")).toBeDisabled({timeout:500});
  await expect(panel.locator("#tool-dialog")).toHaveAttribute("aria-busy", "true");
};

const assertNativeDocument = async native => {
  const state = await native.evaluate(element => ({yaml:element.yaml,
    document:element.shadowRoot.querySelector("ha-code-editor").codemirror.state.doc.toString()}));
  expect(state.yaml.length).toBeGreaterThan(0);
  expect(state.document).toBe(state.yaml);
};

for (const action of ["add", "edit"]) {
  test(`${action}: readiness includes the native document and immediate editing persists exactly`, async ({page}) => {
    const errors = trackPageErrors(page);
    await installDelayedEditor(page);
    await page.goto(fixtureUrl("capabilities/functions"));
    const panel = page.locator("extended-openai-management-panel");
    if (action === "add") {
      await panel.locator("#function-add").click();
      await panel.locator("#add-tool").click();
    } else {
      await panel.locator('[data-tool-key="baseline_tool"]').evaluate(card => { card.closest("details").open = true; });
      await panel.locator('[data-tool-key="baseline_tool"] .edit-tool').click();
    }
    await expect.poll(() => page.evaluate(() => window.nativeHydrations.some(item => item.yaml.includes("spec:")))).toBe(true);
    const dialog = panel.locator("#tool-dialog");
    const save = panel.locator("#tool-save");
    await assertHydrating(panel);
    await page.evaluate(() => window.nativeHydrations.forEach(item => item.outer()));
    await expect(save).toBeDisabled();
    await releaseHydration(page);
    await expect(dialog).toHaveAttribute("aria-busy", "false");
    await assertNativeDocument(panel.locator("#tool-yaml-native"));
    const surface = panel.locator("#tool-yaml-native").locator("textarea");
    await expect(surface).toHaveValue(/spec:/);
    await surface.click();
    await surface.press("ControlOrMeta+A");
    const yaml = browserToolYaml(`Immediate ${action} after ready`)
      .replace("name: browser_tool", `name: ${action === "edit" ? "baseline_tool" : "browser_tool"}`);
    await page.keyboard.insertText(yaml);
    await expect(panel.locator("#tool-yaml")).toHaveValue(yaml);
    await save.click();
    await expect(dialog).not.toHaveJSProperty("open", true);
    const name = action === "edit" ? "baseline_tool" : "browser_tool";
    expect(await page.evaluate(name => window.browserHarness.getState().toolYamls[name], name)).toBe(yaml);
    await page.goto(fixtureUrl("capabilities/functions"));
    await expect(panel.locator(`[data-tool-key="${name}"]`)).toContainText(`Immediate ${action} after ready`);
    await expectHarnessClean(page, errors);
  });
}

for (const stalled of ["native document", "validation reply"]) {
test(`stalled ${stalled} falls back and its late events cannot overwrite the saved draft`, async ({page}) => {
  const errors = trackPageErrors(page);
  await installDelayedEditor(page);
  await page.goto(fixtureUrl("capabilities/functions"));
  const panel = page.locator("extended-openai-management-panel");
  if (stalled === "validation reply") await panel.evaluate(host => {
    const call = host._call.bind(host);
    host._call = async (section, action, args) => {
      const result = await call(section, action, args);
      if (section === "tools" && action === "validate_yaml") {
        window.validationHeld = true;
        await new Promise(resolve => { window.releaseValidation = resolve; });
      }
      return result;
    };
  });
  await panel.locator("#function-add").click();
  await panel.locator("#add-tool").click();
  await expect.poll(() => page.evaluate(stalled => stalled === "validation reply" ? window.validationHeld === true
    : window.nativeHydrations.some(item => item.yaml.includes("spec:")), stalled)).toBe(true);
  await expect(panel.locator("#tool-save")).toBeEnabled({timeout:10000});
  const yaml = browserToolYaml("Fallback after stalled native document");
  await panel.locator("#tool-yaml").fill(yaml);
  await page.evaluate(() => window.releaseValidation?.());
  await releaseHydration(page);
  await expect(panel.locator("#tool-yaml-native")).toBeHidden();
  await expect(panel.locator("#tool-yaml")).toHaveValue(yaml);
  await panel.locator("#tool-save").click();
  await expect(panel.locator("#tool-dialog")).not.toHaveJSProperty("open", true);
  expect(await page.evaluate(() => window.browserHarness.getState().toolYamls.browser_tool)).toBe(yaml);
  await expectHarnessClean(page, errors);
});
}

for (const mutation of ["early dialog gate", "outer update only"]) {
  test(`readiness oracle detects ${mutation}`, async ({page}) => {
    test.skip(process.env.SHIPPED_BUNDLE === "1", "Source mutation; the normal regression also runs against the shipped bundle");
    const target = mutation === "early dialog gate" ? "agent-config-tools-base.js" : "agent-config-native-yaml.js";
    await page.route(`**/${target}`, async route => {
      const response = await route.fetch();
      const source = (await response.text()).replace(/\r\n/g, "\n");
      const original = mutation === "early dialog gate" ? "await editor.setYaml(response.yaml);"
        : "await code?.updateComplete;";
      expect(source).toContain(original);
      let body = source.replace(original, mutation === "early dialog gate" ? "editor.setYaml(response.yaml);" : "");
      if (mutation === "outer update only") {
        const check = "code?.codemirror?.state.doc.toString() !== yaml";
        expect(body).toContain(check);
        body = body.replace(check, "false");
      }
      await route.fulfill({response, body});
    });
    await installDelayedEditor(page);
    await page.goto(fixtureUrl("capabilities/functions"));
    const panel = page.locator("extended-openai-management-panel");
    await panel.locator("#function-add").click();
    await panel.locator("#add-tool").click();
    await expect.poll(() => page.evaluate(() => window.nativeHydrations.some(item => item.yaml.includes("spec:")))).toBe(true);
    if (mutation === "early dialog gate") {
      await expect(assertHydrating(panel)).rejects.toThrow();
    } else {
      await page.evaluate(() => window.nativeHydrations.forEach(item => item.outer()));
      await expect(panel.locator("#tool-save")).toBeEnabled();
      await expect(assertNativeDocument(panel.locator("#tool-yaml-native"))).rejects.toThrow();
    }
    await releaseHydration(page);
  });
}
