import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

test("Guest Mode schedule mutations refresh only the selected agent and route", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/guest-mode"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name:"Guest Mode", exact:true})).toBeVisible();

  const result = await panel.evaluate(async (host) => {
    const original = host._hass.callWS;
    const calls = window.browserHarness.calls;
    const initialAgentCalls = calls.filter((call) => call.action === "agents").length;
    const agentId = host._agentId;
    const agent = host._data.agents.find((item) => item.subentry_id === agentId);
    agent.guest_mode = {...(agent.guest_mode || {}), has_home_assistant_exclusions:true};

    let status = {
      state:"inactive",
      currently_active:false,
      scheduled:false,
      indefinite:false,
      active_from:null,
      active_until:null,
    };
    const guestCalls = [];
    host._hass.callWS = async (message) => {
      if (message.section !== "guest_mode") return original(message);
      guestCalls.push(message.action);
      if (message.action === "update") {
        status = {
          state:"active_indefinitely",
          currently_active:true,
          scheduled:false,
          indefinite:true,
          active_from:"2026-09-22T16:00:00+00:00",
          active_until:null,
        };
        return {status:{...status}};
      }
      if (message.action === "disable") {
        status = {
          state:"inactive",
          currently_active:false,
          scheduled:false,
          indefinite:false,
          active_from:null,
          active_until:null,
        };
        return {status:{...status}};
      }
      if (message.action === "get") {
        return {
          ...host._result,
          status:{...status},
          policy:{guest_active:status.currently_active, marker:status.state},
        };
      }
      return original(message);
    };

    await host._updateGuestMode(true);
    const afterUpdate = {
      agent:{...agent.guest_mode},
      routeStatus:{...host._result.status},
      routePolicy:{...host._result.policy},
    };

    host._confirm = async () => true;
    await host._disableGuestMode();
    const afterDisable = {
      agent:{...agent.guest_mode},
      routeStatus:{...host._result.status},
      routePolicy:{...host._result.policy},
    };

    host._hass.callWS = original;
    return {
      initialAgentCalls,
      finalAgentCalls:calls.filter((call) => call.action === "agents").length,
      guestCalls,
      afterUpdate,
      afterDisable,
    };
  });

  expect(result.finalAgentCalls).toBe(result.initialAgentCalls);
  expect(result.guestCalls).toEqual(["update", "get", "disable", "get"]);
  expect(result.afterUpdate.agent).toMatchObject({
    state:"active_indefinitely",
    currently_active:true,
    has_home_assistant_exclusions:true,
  });
  expect(result.afterUpdate.routeStatus.state).toBe("active_indefinitely");
  expect(result.afterUpdate.routePolicy).toMatchObject({guest_active:true, marker:"active_indefinitely"});
  expect(result.afterDisable.agent).toMatchObject({
    state:"inactive",
    currently_active:false,
    has_home_assistant_exclusions:true,
  });
  expect(result.afterDisable.routeStatus.state).toBe("inactive");
  expect(result.afterDisable.routePolicy).toMatchObject({guest_active:false, marker:"inactive"});
  await expectHarnessClean(page, errors);
});

test("late Guest Mode refresh does not replace a newer route", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/guest-mode"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name:"Guest Mode", exact:true})).toBeVisible();

  const result = await panel.evaluate(async (host) => {
    const original = host._hass.callWS;
    let releaseGet;
    host._hass.callWS = async (message) => {
      if (message.section === "guest_mode" && message.action === "update") {
        return {status:{state:"active_indefinitely", currently_active:true, indefinite:true}};
      }
      if (message.section === "guest_mode" && message.action === "get") {
        await new Promise((resolve) => { releaseGet = resolve; });
        return {...host._result, status:{state:"active_indefinitely", currently_active:true, indefinite:true}};
      }
      return original(message);
    };

    const pending = host._updateGuestMode(true);
    while (!releaseGet) await new Promise((resolve) => setTimeout(resolve, 0));
    await host._navigate("guide");
    const guideResult = host._result;
    releaseGet();
    await pending;
    host._hass.callWS = original;
    return {
      view:host._viewKey(),
      sameResult:host._result === guideResult,
      agentState:host._data.agents.find((item) => item.subentry_id === host._agentId)?.guest_mode?.state,
    };
  });

  expect(result).toMatchObject({
    view:"guide",
    sameResult:true,
    agentState:"active_indefinitely",
  });
  await expectHarnessClean(page, errors);
});
