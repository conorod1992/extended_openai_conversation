import {expect, test} from "@playwright/test";
import {openDataCollection} from "./data-collection-helpers.mjs";
import {trackPageErrors, expectHarnessClean, fixtureUrl} from "./browser-helpers.mjs";

for (const bundled of [false, true]) for (const kind of ["knowledge", "persistent"]) {
  for (const failure of ["close", "cache", "render"]) {
    test(`${kind}: acknowledged create survives ${failure} failure (${bundled ? "bundle" : "source"})`, async ({page}) => {
      const errors = trackPageErrors(page);
      const panel = await openDataCollection(page, kind, 1, bundled);
      const editor = kind === "knowledge" ? "knowledge" : "memory";
      await panel.locator(kind === "knowledge" ? "#add-source" : "#add-memory").click();
      if (kind === "knowledge") await panel.locator("#knowledge-title").fill("Committed source");
      await panel.locator(`#${editor}-content`).fill("Committed A");
      await panel.evaluate((host, {failure, editor}) => {
        const target = failure === "close" ? host.shadowRoot.querySelector(`#${editor}-dialog`) : host;
        const key = {close:"close", cache:"_invalidateAfterMutation", render:"_render"}[failure];
        const original = target[key];
        target[key] = () => { throw Error(`injected ${failure} failure`); };
        window.restoreAcknowledgementPresentation = () => { target[key] = original; };
      }, {failure, editor});
      await panel.locator(`#${editor}-save`).click();
      await expect(panel.locator("#toast")).toContainText(/(?:saved|added), but .*could not be refreshed/i);
      const state = () => page.evaluate(kind => ({
        records:kind === "knowledge" ? dataCollectionBackend.state.sources : dataCollectionBackend.state.memories,
        writes:dataCollectionBackend.calls.filter(call => ["create", "add", "update"].includes(call.action)),
      }), kind);
      expect((await state()).records).toHaveLength(2);
      expect((await state()).writes).toHaveLength(1);
      await page.evaluate(() => restoreAcknowledgementPresentation());
      if (failure === "close") {
        await panel.locator(`#${editor}-content`).fill("Newer B");
        await panel.locator(`#${editor}-save`).click();
        await expect(panel.locator(`#${editor}-dialog`)).toHaveJSProperty("open", false);
        const saved = await state();
        expect(saved.records).toHaveLength(2);
        expect(saved.records.some(record => record.content === "Newer B")).toBe(true);
        expect(saved.writes.map(call => call.action)).toEqual([kind === "knowledge" ? "create" : "add", "update"]);
      }
      await expectHarnessClean(page, errors);
    });
  }

  test(`${kind}: incomplete reply remains uncertain after a real write (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const errors = trackPageErrors(page);
    const panel = await openDataCollection(page, kind, 1, bundled);
    const editor = kind === "knowledge" ? "knowledge" : "memory";
    await panel.evaluate((host, kind) => {
      const original = host.hass.callWS.bind(host.hass);
      host.hass.callWS = async request => {
        const result = await original(request);
        return request.section === (kind === "knowledge" ? "knowledge" : "memories") && request.action === (kind === "knowledge" ? "create" : "add") ? {} : result;
      };
    }, kind);
    await panel.locator(kind === "knowledge" ? "#add-source" : "#add-memory").click();
    if (kind === "knowledge") await panel.locator("#knowledge-title").fill("Uncertain source");
    await panel.locator(`#${editor}-content`).fill("Actually committed");
    await panel.locator(`#${editor}-save`).click();
    await expect(panel.locator(`#${editor}-error`)).toContainText("outcome is unknown");
    expect(await page.evaluate(kind => (kind === "knowledge" ? dataCollectionBackend.state.sources : dataCollectionBackend.state.memories).length, kind)).toBe(2);
    expect(await page.evaluate(() => dataCollectionBackend.calls.filter(call => ["create", "add"].includes(call.action)).length)).toBe(1);
    await expectHarnessClean(page, errors);
  });
}

for (const bundled of [false, true]) test(`malformed Knowledge reply stays uncertain through cache failure (${bundled ? "bundle" : "source"})`, async ({page}) => {
  const errors = trackPageErrors(page);
  const panel = await openDataCollection(page, "knowledge", 1, bundled);
  await panel.evaluate(host => {
    const send = host.hass.callWS.bind(host.hass);
    host.hass.callWS = async request => {
      const result = await send(request);
      return request.section === "knowledge" && request.action === "create" ? null : result;
    };
    host._invalidateAfterMutation = () => { throw Error("cache publication failed"); };
  });
  await panel.locator("#add-source").click();
  await panel.locator("#knowledge-title").fill("Uncertain committed source");
  await panel.locator("#knowledge-content").fill("Committed despite a malformed reply");
  await panel.locator("#knowledge-save").click();
  await expect(panel.locator("#knowledge-error")).toContainText("outcome is unknown");
  const actual = await page.evaluate(() => ({records:dataCollectionBackend.state.sources, writes:dataCollectionBackend.calls.filter(call => call.action === "create")}));
  expect(actual.records).toHaveLength(2);
  expect(actual.records.some(source => source.content === "Committed despite a malformed reply")).toBe(true);
  expect(actual.writes).toHaveLength(1);
  expect(await panel.evaluate(host => host._editingSource?.source_id)).toBeUndefined();
  await expectHarnessClean(page, errors);
});

for (const bundled of [false, true]) for (const failure of ["cache", "lost"]) {
  test(`duplicate acknowledgement survives request ${failure} failure (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl("assistant/basics", bundled ? "&bundle=1" : ""));
    const panel = page.locator("extended-openai-management-panel");
    await expect(panel.locator('[data-config="max_tokens"]')).toBeVisible();
    await panel.evaluate((host, failure) => {
      const callWS = host.hass.callWS.bind(host.hass);
      window.createdAgents = [];
      window.duplicateSource = host._configData;
      host.hass.callWS = async request => {
        if (request.section !== "configuration" || request.action !== "duplicate") return callWS(request);
        const created = {subentry_id:`copy-${createdAgents.length + 1}`, title:"Committed copy"};
        createdAgents.push(created);
        if (failure === "lost") throw Error("connection lost after create");
        return created;
      };
      if (failure === "cache") host._invalidateAfterMutation = () => { throw Error("post-commit cache failure"); };
      host._loadAgents = async () => {};
    }, failure);
    await panel.locator(".agent-actions-menu > summary").click();
    await panel.locator("#duplicate-agent").click();
    await expect(panel.locator("#toast")).toContainText(failure === "cache"
      ? /Created Committed copy, but .*post-commit cache failure/
      : /Unable to duplicate agent: .*outcome is unknown/);
    expect(await page.evaluate(() => createdAgents.length)).toBe(1);
    expect(await panel.evaluate(host => host._configData === duplicateSource)).toBe(true);
    expect(await panel.evaluate(host => host._eocRequestDiagnostics.at(-1).status)).toBe(failure === "lost" ? "uncertain" : "fulfilled");
    await expect(panel.locator("#duplicate-agent")).toBeEnabled();
    await expectHarnessClean(page, errors);
  });
}

for (const bundled of [false, true]) for (const action of ["delete", "clear"]) for (const transition of ["none", "agent", "authentication round trip"]) {
  test(`temporary ${action} confirmation retains its owner: ${transition} (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const errors = trackPageErrors(page);
    const panel = await openDataCollection(page, "temporary", 1, bundled);
    await panel.evaluate((host, transition) => {
      window.confirmationCompleted = false;
      host._confirm = async () => {
        if (transition === "agent") {
          host._data.agents.push({entry_id:"another-entry", subentry_id:"another-agent"});
          host._agentId = "another-agent";
        } else if (transition === "authentication round trip") {
          const original = host.hass;
          host.hass = {...original, user:{...original.user, id:"intervening-user"}};
          host.hass = original;
        }
        confirmationCompleted = true;
        return true;
      };
    }, transition);
    await panel.locator(action === "delete" ? ".delete-temporary" : "#clear-temporary").first().click();
    await expect.poll(() => page.evaluate(() => confirmationCompleted)).toBe(true);
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const outcome = await page.evaluate(action => ({
      writes:dataCollectionBackend.calls.filter(call => call.action === `temporary_${action}`),
      remaining:dataCollectionBackend.state.temporary.length,
    }), action);
    expect(outcome.writes).toHaveLength(transition === "none" ? 1 : 0);
    expect(outcome.remaining).toBe(transition === "none" ? action === "delete" ? 11 : 0 : 12);
    await expectHarnessClean(page, errors);
  });
}

for (const bundled of [false, true]) for (const replacement of [false, true, "pending", "authentication", "authentication acknowledged"]) test(`generic Knowledge error replacement=${replacement} blocks the next create until authoritative reopening (${bundled ? "bundle" : "source"})`, async ({page}) => {
  const panel = await openDataCollection(page, "knowledge", 1, bundled);
  await panel.evaluate((host, replacement) => {
    host.hass = {...host.hass, user:{...host.hass.user, id:"knowledge-owner"}};
    const send = host.hass.callWS.bind(host.hass);
    host.hass.callWS = async request => {
      const result = await send(request);
      if (request.section === "knowledge" && request.action === "create") {
        if (["pending", "authentication", "authentication acknowledged"].includes(replacement) && !window.releaseUnknownKnowledge) await new Promise(resolve => {window.releaseUnknownKnowledge = resolve;});
        if (replacement === "authentication acknowledged") return result;
        throw {code:"unknown_error", message:"Unknown error"};
      }
      return result;
    };
  }, replacement);
  await panel.locator("#add-source").click();
  await panel.locator("#knowledge-title").fill("Ambiguous committed source");
  await panel.locator("#knowledge-content").fill("Already stored");
  await panel.locator("#knowledge-save").click();
  if (["pending", "authentication", "authentication acknowledged"].includes(replacement)) await expect.poll(() => page.evaluate(() => typeof window.releaseUnknownKnowledge)).toBe("function");
  else await expect(panel.locator("#knowledge-error")).toContainText("outcome is unknown");
  if (replacement) {
    if (String(replacement).startsWith("authentication")) await panel.evaluate(host => {
      const original = host.hass;
      host.hass = {...original, user:{...original.user, id:"intervening-owner"}};
      host.hass = original;
    });
    await panel.evaluate(host => {
      const parent = host.parentElement, hass = host.hass;
      host.remove();
      const next = document.createElement("extended-openai-management-panel");
      parent.append(next); next.hass = hass;
    });
    if (["pending", "authentication", "authentication acknowledged"].includes(replacement)) await page.evaluate(() => releaseUnknownKnowledge());
    await expect(panel.locator("#knowledge-content")).toHaveValue("Already stored");
  }
  await panel.evaluate(host => host._saveKnowledge());
  expect(await page.evaluate(() => dataCollectionBackend.calls.filter(call => call.action === "create").length)).toBe(1);
  await expect(panel.locator("#knowledge-save")).toBeDisabled();
  // A presentation reset cannot confer authority to repeat this editor intent.
  await panel.evaluate(host => {
    host.shadowRoot.querySelector("#knowledge-save").disabled = false;
    return host._saveKnowledge();
  });
  expect(await page.evaluate(() => dataCollectionBackend.calls.filter(call => call.action === "create").length)).toBe(1);
  const sources = await page.evaluate(() => dataCollectionBackend.state.sources);
  expect(sources.filter(source => source.title === "Ambiguous committed source")).toHaveLength(1);
  const saved = sources.find(source => source.title === "Ambiguous committed source");
  await panel.evaluate(async (host, id) => {
    host.shadowRoot.querySelector("#knowledge-dialog").close();
    await host._openKnowledge(id);
  }, saved.source_id);
  await expect(panel.locator("#knowledge-save")).toBeEnabled();
  await panel.locator("#knowledge-content").fill("Deliberate update after recovery");
  await panel.locator("#knowledge-save").click();
  expect(await page.evaluate(() => dataCollectionBackend.state.sources.filter(source => source.title === "Ambiguous committed source").length)).toBe(1);
  expect(await page.evaluate(() => dataCollectionBackend.state.sources.find(source => source.title === "Ambiguous committed source").content)).toBe("Deliberate update after recovery");
});

for (const bundled of [false, true]) test(`Knowledge partial acknowledgement cannot authorize an unguarded update (${bundled ? "bundle" : "source"})`, async ({page}) => {
  const panel = await openDataCollection(page, "knowledge", 1, bundled);
  await panel.evaluate(host => {
    const send = host.hass.callWS.bind(host.hass);
    host.hass.callWS = async request => {
      const result = await send(request);
      if (request.section === "knowledge" && request.action === "create") {
        await new Promise(resolve => {window.releasePartialSource = resolve;});
        delete result.source.updated_at;
      }
      return result;
    };
  });
  await panel.locator("#add-source").click();
  await panel.locator("#knowledge-title").fill("Partial source");
  await panel.locator("#knowledge-content").fill("Submitted content");
  await panel.locator("#knowledge-save").click();
  await expect.poll(() => page.evaluate(() => typeof releasePartialSource)).toBe("function");
  await panel.locator("#knowledge-content").fill("Later draft");
  await page.evaluate(() => {
    const source = dataCollectionBackend.state.sources.find(source => source.title === "Partial source");
    source.content = "Concurrent committed content";
    source.updated_at = "2026-10-29T20:00:00Z";
    releasePartialSource();
  });
  await expect.poll(() => panel.evaluate(host => host._eocAgentMutations)).toBe(0);
  // The next user action must not combine a known identity with no revision.
  await panel.evaluate(host => host._saveKnowledge());
  expect(await page.evaluate(() => dataCollectionBackend.state.sources.find(source => source.title === "Partial source").content)).toBe("Concurrent committed content");
  expect(await page.evaluate(() => dataCollectionBackend.calls.filter(call => call.action === "update").length)).toBe(0);
  await expect(panel.locator("#knowledge-error")).toContainText("outcome is unknown");
});

for (const bundled of [false, true]) test(`Knowledge reads retire with their authentication lifetime (${bundled ? "bundle" : "source"})`, async ({page}) => {
  const panel = await openDataCollection(page, "knowledge", 1, bundled);
  await panel.evaluate(host => {
    host.hass = {...host.hass, user:{...host.hass.user, id:"original-reader"}};
    const send = host.hass.callWS.bind(host.hass);
    let first = true;
    window.knowledgeUpdateDispatches = 0;
    host.hass.callWS = async request => {
      if (request.section === "knowledge" && request.action === "update") knowledgeUpdateDispatches++;
      if (request.section === "knowledge" && request.action === "update"
          && request.expected_revision !== dataCollectionBackend.state.sources[0].updated_at) {
        throw {code:"invalid_request", message:"Source changed before save"};
      }
      const result = await send(request);
      if (first && request.section === "knowledge" && request.action === "get") {
        first = false;
        await new Promise(resolve => {window.releaseKnowledgeRead = resolve;});
      }
      if (request.action === "get") window.knowledgeReadReturned = true;
      return result;
    };
  });
  await panel.locator(".source-edit-button").first().click();
  await expect.poll(() => page.evaluate(() => typeof releaseKnowledgeRead)).toBe("function");
  await panel.evaluate(host => {
    const original = host.hass;
    host.hass = {...original, user:{...original.user, id:"intervening-reader"}};
    host.hass = original;
    dataCollectionBackend.state.sources[0].content = "New authoritative content";
    dataCollectionBackend.state.sources[0].updated_at = "2026-10-29T22:00:00Z";
    releaseKnowledgeRead();
  });
  await expect.poll(() => page.evaluate(() => window.knowledgeReadReturned)).toBe(true);
  await panel.evaluate(host => host._saveKnowledge());
  expect(await page.evaluate(() => knowledgeUpdateDispatches)).toBe(0);
  expect(await page.evaluate(() => dataCollectionBackend.state.sources[0].content)).toBe("New authoritative content");
  await expect(panel.locator("#knowledge-content")).not.toHaveValue("Reference text 0");
  await expect(panel.locator("#knowledge-save")).toBeDisabled();
  await panel.evaluate(async host => {
    host.shadowRoot.querySelector("#knowledge-dialog").close();
    await host._openKnowledge("source-0");
  });
  await expect(panel.locator("#knowledge-content")).toHaveValue("New authoritative content");
  await panel.locator("#knowledge-content").fill("Deliberate edit after fresh read");
  await panel.locator("#knowledge-save").click();
  expect(await page.evaluate(() => dataCollectionBackend.state.sources[0].content)).toBe("Deliberate edit after fresh read");
});
