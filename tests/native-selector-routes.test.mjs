import assert from "node:assert/strict";
import {setImmediate} from "node:timers/promises";

function shell() {
  const state={loads:0,fail:false,registered:false};
  let release;
  const gate=new Promise(resolve=>{release=resolve;});
  globalThis.document={defaultView:{customElements:{get:name=>name==="ha-selector"&&state.registered}},createElement(name){
    if(name==="partial-panel-resolver") return {getRoutes:()=>({routes:{config:{load:async()=>{state.loads++;await gate;}}}})};
    if(name==="ha-panel-config") return {routerOptions:{routes:{automation:{load:async()=>{if(state.fail)throw new Error("selector module failed");state.registered=true;}}}}};
    throw new Error(`Unexpected native dependency: ${name}`);
  }};
  return {state,release};
}
const frontend=new URL("../custom_components/extended_openai_conversation_responses/frontend/management-route.js",import.meta.url);
const first=shell(), routes=await import(`${frontend}?native=success`);
const views=["capabilities/request-rules","capabilities/guest-mode"];
const pending=Promise.all(views.map(view=>routes.routeAssetPromise(view)));
await setImmediate();
assert.equal(first.state.loads,1);
for(const view of views) assert.equal(routes.routeFeaturesReady(view),false,"Never publish an editor with an unregistered selector");
first.release(); await pending;
for(const view of views) assert.equal(routes.routeFeaturesReady(view),true);
await routes.routeAssetPromise(views[0]); assert.equal(first.state.loads,1);
const retry=shell(), other=await import(`${frontend}?native=failure`);
retry.state.fail=true;
const rejected=other.routeAssetPromise(views[0]); retry.release();
await assert.rejects(rejected,/selector module failed/);
assert.equal(other.routeFeaturesReady(views[0]),false);
retry.state.fail=false;
await other.routeAssetPromise(views[0]);
assert.equal(other.routeFeaturesReady(views[0]),true);
assert.equal(retry.state.loads,2,"Failure must leave the existing route retry usable");
console.log("Native editor admission, shared loading, warm visits and failed-load retry passed");
