import {fixture, retire} from "./management-ownership-harness.mjs";
import {routeAssetPromise} from "../custom_components/extended_openai_conversation_responses/frontend/management-route.js";
import {readFile} from "node:fs/promises";
if (process.argv[4]) {
  const RealDate = Date, fixed = RealDate.parse(process.argv[4]);
  globalThis.Date = class extends RealDate {
    constructor(...args) {super(...(args.length ? args : [fixed]));}
    static now() {return fixed;}
  };
}
let bundlePanel;
if (process.argv[3] === "bundle") {
  const dist = new URL("../custom_components/extended_openai_conversation_responses/frontend/dist/", import.meta.url);
  const manifest = JSON.parse(await readFile(new URL("manifest.json", dist), "utf8"));
  globalThis.customElements.define = (name, constructor) => {if (name === "extended-openai-management-panel") bundlePanel = constructor;};
  globalThis.document = {querySelector:() => null, getElementsByTagName:() => [], createElement:() => ({relList:{supports:() => true}}), head:{appendChild() {}}};
  await import(new URL(Object.values(manifest).find(entry => entry.isEntry).file, dist));
  if (!bundlePanel) throw new Error("Production management panel did not register");
}
const results=[];
for (const scenario of ["schedule","now-finite","edit-start","edit-end","unchanged","now-indefinite","legacy","legacy-naive"]) {
  const document = globalThis.document;
  delete globalThis.document;
  const {create}=fixture(), panel=create();
  if (document) globalThis.document=document;
  if (bundlePanel) Object.setPrototypeOf(panel, bundlePanel.prototype);
  panel._page="capabilities"; panel._subsection="guest-mode";
  panel._hass.config={time_zone:process.argv[2] || "UTC"};
  if (bundlePanel) {
    panel._loadSectionData=async()=>{};
    await panel._loadSection();
    if (panel._error) throw new Error(panel._error);
  } else await routeAssetPromise("capabilities/guest-mode",panel);
  const status=scenario.startsWith("edit") || scenario==="unchanged" ? {
    active_from:"2027-06-04T12:00:00+00:00", active_until:"2027-06-04T13:00:00+00:00",
    active_from_local:panel._hass.config.time_zone === "America/New_York" ? "2027-06-04T08:00" : "2027-06-04T12:00",
    active_until_local:panel._hass.config.time_zone === "America/New_York" ? "2027-06-04T09:00" : "2027-06-04T13:00",
  } : {};
  panel._result={status};
  const controls=new Map([
    ["#guest-start",{value:scenario==="edit-start" ? status.active_from_local.replace(":00", ":15") : status.active_from_local || "2027-06-04T12:00"}],
    ["#guest-end",{value:scenario==="edit-end" ? status.active_until_local.replace(":00", ":15") : status.active_until_local || "2027-06-04T13:00",checkValidity:()=>true}],
    ["#guest-indefinite",{checked:scenario==="now-indefinite"}],
  ]);
  panel.shadowRoot.querySelector=key=>controls.get(key)||null;
  panel._refreshGuestModeMutation=async()=>{};
  panel._hass.callWS=async message=> {results.push({scenario,message}); return {status:{state:"scheduled"}};};
  if(scenario.startsWith("legacy")) await panel._callCore("guest_mode","update",{active_from:scenario==="legacy" ? "2027-06-04T12:00:00+02:00" : "2027-06-04T12:00",indefinite:true});
  else await panel._updateGuestMode(scenario.startsWith("now-"));
  retire(panel);
}
process.stdout.write(JSON.stringify(results));
