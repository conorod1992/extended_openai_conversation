import test from "node:test";
import assert from "node:assert/strict";
import {downloadFullBackup, downloadSetupExport} from "../custom_components/extended_openai_conversation_responses/frontend/backup-transfer-ui.js";
for (const aba of [false,true]) for (const setup of [false,true]) test(`private export retires across account transition setup=${setup} aba=${aba}`,async()=>{
  let release; const held=new Promise(resolve=>{release=resolve;});
  const calls=[], saved=[];
  const connection={user:{id:"owner"},callWS:async request=>{
    calls.push(request);
    if(request.action==="setup_export")return held;
    if(request.action==="export_start")return {session_id:"original",chunk_count:1,size:1,filename:"private.zip"};
    if(request.action==="export_chunk")return held;
    return {cancelled:true};
  }};
  const panel={hass:connection,_hass:connection,_eocAuthenticationGeneration:0,_selectedAgent:()=>({entry_id:"entry",subentry_id:"agent"}),_call:()=>held};
  const running=setup?downloadSetupExport(panel,(...args)=>saved.push(args)):downloadFullBackup(panel,(...args)=>saved.push(args));
  while(!setup&&!calls.some(call=>call.action==="export_chunk"))await Promise.resolve();
  connection.user.id="other";panel._eocAuthenticationGeneration++;
  if(aba){connection.user.id="owner";panel._eocAuthenticationGeneration++;}
  release(setup?{json:'{"secret":"private"}',filename:"private.json"}:{session_id:"original",index:0,offset:0,bytes:1,data:"YQ=="});
  if(setup)assert.equal((await running).cancelled,true);
  else {await assert.rejects(running,/authentication changed/);assert.equal(calls.at(-1).action,"export_cancel");assert.equal(calls.at(-1).data.session_id,"original");}
  assert.equal(saved.length,0);
});
test("same-user agent navigation preserves explicitly requested backup",async()=>{
 const saved=[],calls=[];let selected="original";
 const connection={user:{id:"owner"},callWS:async request=>{calls.push(request);if(request.action==="export_start"){selected="new";return {session_id:"s",chunk_count:1,size:1,filename:"p.zip"};}if(request.action==="export_chunk")return {session_id:"s",index:0,offset:0,bytes:1,data:"YQ=="};return {};}};
 const panel={hass:connection,_hass:connection,_selectedAgent:()=>({entry_id:"e",subentry_id:selected})};
 await downloadFullBackup(panel,(blob)=>saved.push(blob));assert.equal(saved.length,1);assert.ok(calls.every(c=>c.subentry_id==="original"));
});

test("retired debug Copy cannot publish its pending trace",async()=>{
 globalThis.HTMLElement=class {};globalThis.customElements={get:()=>true};
 const {ExtendedOpenAIDebugPanel}=await import("../custom_components/extended_openai_conversation_responses/frontend/debug-panel.js");
 const panel=Object.create(ExtendedOpenAIDebugPanel.prototype);let release;
 panel._revision=1;panel._hass={};panel._agent={};panel.isConnected=true;
 panel._getRun=()=>new Promise(resolve=>{release=resolve;});
 const copied=[];panel._copyText=text=>copied.push(text);panel._toast=()=>{};panel.hasAttribute=()=>false;
 const pending=panel._copyRun("private-run");panel._revision++;
 release({copy_text:"private trace"});await pending;assert.deepEqual(copied,[]);
});

test("retired clipboard write cannot fall back to a later DOM copy",async()=>{
 const {copyTextToClipboard}=await import("../custom_components/extended_openai_conversation_responses/frontend/clipboard.js");
 let reject, current=true, fallback=false;
 const navigator={clipboard:{writeText:()=>new Promise((_,no)=>{reject=no;})}};
 const document={body:{},createElement:()=>{fallback=true;},execCommand:()=>true};
 const pending=copyTextToClipboard("private",navigator,document,()=>current);
 current=false;reject(Error("clipboard refused"));await assert.rejects(pending,/ownership changed/);assert.equal(fallback,false);
});
