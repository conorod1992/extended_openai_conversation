import assert from "node:assert/strict";
import test from "node:test";
import {
  bindUsageDiagnostics,
  renderUsagePage,
  usageWindowBounds,
} from "../custom_components/extended_openai_conversation_responses/frontend/usage-chart.js";

const instant = Date.parse("2025-12-31T12:30:00Z");
// HA has entered the next calendar year while UTC and the browser remain in 2025.
const today = "2026-01-01";
const oldDate = "2025-07-04";
const sparseHistory = [{date:oldDate, total_tokens:9876, input_tokens:9000,
  output_tokens:876, api_request_count:12, run_count:9,
  provider_breakdown:{"Older provider":9876}, model_breakdown:{"Older model":9876}}];
const escape = value => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;");

function createPanel(window, state) {
  const range = {value:window, disabled:false};
  const calls = [];
  const metrics = new Map();
  const panel = {
    _agentId:"assistant", _usageHistoryWindow:window,
    _hass:{config:{time_zone:"Pacific/Kiritimati"}},
    _selectedAgent:() => ({entry_id:"entry", subentry_id:"assistant"}),
    _viewKey:() => "usage-maintenance/usage",
    _e:escape, _empty:escape,
    _metric(label, value, detail) {
      metrics.set(label, value);
      return `<article><span>${escape(label)}</span><strong>${escape(value)}</strong><small>${escape(detail)}</small></article>`;
    },
    _result:{
      days:{days:structuredClone(sparseHistory)}, runs:{runs:[]},
      ...(state === "summary unavailable" ? {} : {summary:{today:{date:oldDate}}}),
      ...(state === "summary refreshing" ? {loading:{summary:true}}
        : {load_errors:[{key:"summary", label:"Usage summary", message:"Summary read failed"}]}),
    },
    shadowRoot:{querySelector:selector => selector === "#usage-window" ? range : null,
      querySelectorAll:() => []},
    _call:async (section, action, extra) => {
      calls.push({section, action, ...extra});
      return {days:[]};
    },
    _render() { this.markup = renderUsagePage(this, this._result); },
    _toast(message) { assert.fail(message); },
  };
  return {panel, range, calls, metrics};
}

for (const window of ["7", "30", "90", "year"]) {
  for (const state of ["summary unavailable", "summary refresh failed", "summary refreshing"]) {
    test(`${window} uses HA's current calendar with sparse older rows and ${state}`, async context => {
      context.mock.timers.enable({apis:["Date"], now:instant});
      const {panel, range, calls, metrics} = createPanel(window, state);
      const bounds = usageWindowBounds(window, today);
      panel._render();
      assert.match(panel.markup, new RegExp(`${bounds.startDate} to ${today}`));
      assert.equal(metrics.get(bounds.label), 0, "old activity cannot be counted in a current relative period");
      assert.equal(metrics.get("Provider requests"), 0);
      assert.doesNotMatch(panel.markup, /Older provider|Older model|class="chart-column"/);

      // Exercise the actual filter handler and transfer, rather than a pure bounds helper.
      panel._usageHistoryWindow = "all";
      bindUsageDiagnostics(panel);
      await range.onchange({target:range});
      assert.deepEqual(calls, [{section:"usage", action:"daily",
        entry_id:"entry", subentry_id:"assistant",
        start_date:bounds.startDate, end_date:today}]);
      assert.equal(panel._usageHistoryWindow, window);
      assert.equal(metrics.get(bounds.label), 0);
      assert.match(panel.markup, new RegExp(`${bounds.startDate} to ${today}`));
    });
  }
}
