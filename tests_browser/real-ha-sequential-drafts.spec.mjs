import {expect, test} from "@playwright/test";

const backendUrl = process.env.REAL_HA_BACKEND_URL;
const operation = process.env.AUDIT_OPERATION;
test.skip(!backendUrl || !operation, "requires actual Home Assistant bridge and audit operation");
const url = route => `/tests_browser/real-ha-fixture.html?route=${encodeURIComponent(route)}&backend=${encodeURIComponent(backendUrl)}&bundle=1`;

for (const mutate of [process.env.AUDIT_MUTATE === "1"]) {
  test(`shared assistant draft survives ${operation}, mutation=${mutate}`, async ({page}) => {
    await page.goto(url("assistant/prompt-context"));
    const panel = page.locator("extended-openai-management-panel");
    const draft = `Sequential ${operation} draft ${mutate}`;
    await panel.locator('[data-config="prompt"]').fill(draft);
    await panel.getByRole("button", {name:"Capabilities",exact:true}).click();
    await panel.getByRole("button", {name:operation === "guest" ? "Guest Mode" : "Functions",exact:true}).click();
    console.log(JSON.stringify({operation, mutate, precondition:await panel.evaluate(async host=>({
      view:host._viewKey(), legacyPolicy:host._result?.legacy_policy,
      guestVersion:host._result?.config?.guest_policy_version,
      frontendRepairCount:host._result?.function_repair?.invalid_tools?.length,
      backendRepairCount:(await host._call("configuration","get")).function_repair?.invalid_tools?.length,
    }))}));
    if (mutate && operation === "guest") {
      await panel.locator("#guest-web-search").check();
      await panel.locator("#save-page").click();
      await expect(panel.locator("#save-page")).toHaveCount(0);
    }
    if (mutate && operation === "repair") {
      await expect(panel.locator(".tool-card-invalid")).toHaveCount(1);
      const repair = await panel.evaluate(host => host._call("function_repair", "get"));
      const replacement = structuredClone(repair.invalid_tools[0].tool);
      replacement.spec.name = "audit_repaired";
      delete replacement.spec.parameters.description;
      await panel.locator(".edit-invalid-tool").click();
      await panel.locator("#tool-yaml").fill(JSON.stringify(replacement, null, 2));
      await panel.locator("#tool-save").click();
      await expect(panel.locator(".tool-card-invalid")).toHaveCount(0);
    }
    await panel.getByRole("button", {name:/^Assistant(, has unsaved changes)?$/}).click();
    await panel.getByRole("button", {name:/^Prompt.*context/}).click();
    const evidence = await panel.evaluate(async host => {
      const persisted = await host._call("configuration", "get");
      return {
        rendered: host.shadowRoot.querySelector('[data-config="prompt"]').value,
        draft: host._draft?.prompt,
        dirty: host._configDirty,
        baselineRevision: host._configData?.revision,
        persistedRevision: persisted.revision,
        persistedPrompt: persisted.config.prompt,
        persistedGuestWebSearch: persisted.config.guest_web_search,
        persistedTools: persisted.config.functions.map(tool => tool.spec.name),
      };
    });
    console.log(JSON.stringify({operation, mutate, expectedDraft:draft, ...evidence}));
    await expect(panel.locator('[data-config="prompt"]')).toHaveValue(draft);
    await panel.locator("#save-config").click();
    await expect.poll(() => panel.evaluate(async host => (await host._call("configuration", "get")).config.prompt)).toBe(draft);
    if (mutate && operation === "guest") {
      expect(await panel.evaluate(async host => (await host._call("configuration", "get")).config.guest_web_search)).toBe(true);
    }
  });
}
