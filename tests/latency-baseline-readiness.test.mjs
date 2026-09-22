import assert from "node:assert/strict";
import {waitForLatencyRoute} from "../ci/frontend_latency/routes.mjs";

const route = {name:"overview", path:"overview"};

{
  const calls = [];
  const result = await waitForLatencyRoute({
    route,
    baselineMode:false,
    waitReady: async (timeout) => { calls.push(timeout); },
    getState: async () => ({page:"overview", subsection:null}),
  });
  assert.deepEqual(result, {supported:true});
  assert.equal(calls.length, 1);
  assert.ok(calls[0] > 0, "current routes get a bounded readiness wait");
}

{
  const calls = [];
  const result = await waitForLatencyRoute({
    route,
    baselineMode:true,
    probeTimeout:7,
    fullTimeout:70,
    waitReady: async (timeout) => {
      calls.push(timeout);
      if (timeout === 7) throw new Error("probe timeout");
    },
    getState: async () => ({page:"overview", subsection:null}),
  });
  assert.deepEqual(result, {supported:true});
  assert.deepEqual(calls, [7, 70]);
}

{
  const calls = [];
  const result = await waitForLatencyRoute({
    route:{name:"legacy", path:"assistant/model-responses"},
    baselineMode:true,
    probeTimeout:7,
    fullTimeout:70,
    waitReady: async (timeout) => {
      calls.push(timeout);
      throw new Error("probe timeout");
    },
    getState: async () => ({page:"capabilities", subsection:"web-skills"}),
  });
  assert.equal(result.supported, false);
  assert.match(result.unavailable_reason, /Historical route resolved to capabilities\/web-skills/);
  assert.deepEqual(calls, [7]);
}

{
  const calls = [];
  await assert.rejects(
    waitForLatencyRoute({
      route,
      baselineMode:true,
      probeTimeout:7,
      fullTimeout:70,
      waitReady: async (timeout) => {
        calls.push(timeout);
        throw new Error(timeout === 7 ? "probe timeout" : "full timeout");
      },
      getState: async () => ({page:"overview", subsection:null}),
    }),
    /full timeout/,
  );
  assert.deepEqual(calls, [7, 70]);
}
