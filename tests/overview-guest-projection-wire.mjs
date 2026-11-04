import {fixture,flushUntil} from "./management-ownership-harness.mjs";
import {renderOverview} from "../custom_components/extended_openai_conversation_responses/frontend/overview-page-impl.js";
let input="";
for await(const chunk of process.stdin) input+=chunk;
const payload=JSON.parse(input), f=fixture(), panel=f.create();
panel._page="overview";panel._subsection="";
const agent=panel._selectedAgent();
payload.primary.agent.entry_id=agent.entry_id;payload.primary.agent.subentry_id=agent.subentry_id;
const calls=[];
let release;
const held=new Promise(resolve=>{release=resolve;});
panel._hass.callWS=async request=>{
  calls.push(request);
  if(request.section!=="overview") throw new Error(`Unexpected section ${request.section}`);
  if(request.action==="primary") return structuredClone(payload.primary);
  if(request.kind==="guest_mode") {await held;return structuredClone(payload.detail);}
  return {kind:request.kind};
};
const warning=()=>renderOverview(panel,panel._selectedAgent()).includes("Guest Mode is active without configured Home Assistant exclusions.");
await panel._loadSectionData(true);
const primary_warning=warning();
release();await flushUntil(()=>panel._eocOverviewDetailState?.remaining.size===0,"all Overview details must settle");
const detail_warning=warning(), flag=panel._selectedAgent().guest_mode.has_home_assistant_exclusions;
const before=calls.length;
await panel._loadSectionData(false);
const cached_warning=warning();
process.stdout.write(JSON.stringify({primary_warning,detail_warning,cached_warning,flag,revisit_requests:calls.length-before}));
panel.isConnected=false;
