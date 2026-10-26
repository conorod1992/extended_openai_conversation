import assert from "node:assert/strict";
import test from "node:test";
import {setDialogBaseline, dialogState} from "../custom_components/extended_openai_conversation_responses/frontend/management-dialogs.js";
import {openDialogBaseline, confirmDialogClose, recoverDisconnectedEditor} from "../custom_components/extended_openai_conversation_responses/frontend/management-state-safety.js";

function editor(kind, connected = true, saved = "Saved content") {
  const content={id:`${kind}-content`,type:"textarea",tagName:"TEXTAREA",value:saved,dispatchEvent(){}};
  const dialog={id:`${kind}-dialog`,open:true,querySelectorAll:()=>[content],close(){this.open=false;}};
  const copies=[];
  const panel={isConnected:connected,hass:{user:{id:"owner"}},_agentId:"agent",_scopeId:"home",_memoryKind:"persistent",_data:{is_admin:true},
    _viewKey:()=>`data-memory/${kind === "memory" ? "memories" : "knowledge"}`,_selectedAgent:()=>({entry_id:"entry"}),
    shadowRoot:{querySelector:selector=>selector === `#${dialog.id}` ? dialog : selector === "dialog[open]" && dialog.open ? dialog : null,append:copy=>copies.push(copy)},
    _memoryValues:()=>({content:content.value,category:"general"}),
    _knowledgeValues:()=>({content:content.value,title:"Title",enabled:true}),
    _confirm:async()=>false};
  const read=()=>panel[kind === "memory" ? "_memoryValues" : "_knowledgeValues"]();
  setDialogBaseline(panel,dialog.id,read());
  panel[kind === "memory" ? "_openMemory" : "_openKnowledge"]=async()=>{
    dialog.open=true;content.value=saved;setDialogBaseline(panel,dialog.id,read());
  };
  return {panel,dialog,content,read,copies};
}

for(const kind of ["memory","knowledge"]) {
  test(`${kind}: typed baseline owns dirty close and its acknowledged reversion`, async () => {
    const {panel,dialog,content,read}=editor(kind);let prompts=0;
    panel._confirm=async()=>{prompts++;return false;};
    const baseline=panel._eocDialogBaselines.get(dialog.id);
    openDialogBaseline(panel,dialog);
    assert.equal(panel._eocDialogBaselines.get(dialog.id),baseline,"raw form capture cannot replace typed values");
    setDialogBaseline(panel,kind === "memory" ? "knowledge-dialog" : "memory-dialog",{content:"Other editor"});
    assert.equal(await confirmDialogClose(panel,dialog),true);assert.equal(prompts,0);
    assert.equal(panel._eocDialogBaselines.has(dialog.id),false);
    dialog.open=true;setDialogBaseline(panel,dialog.id,read());
    content.value="Submitted A";const submitted=read();content.value="Newer B";
    setDialogBaseline(panel,dialog.id,submitted);
    assert.equal(panel._eocDialogBaselines.get(dialog.id),submitted,"fresh typed values need no additional clone");
    assert.equal(await confirmDialogClose(panel,dialog),false);assert.equal(prompts,1);
    content.value="Saved content";
    assert.equal(await confirmDialogClose(panel,dialog),false,"the pre-save value remains an authored reversion");
    content.value="Submitted A";
    assert.equal(await confirmDialogClose(panel,dialog),true);assert.equal(prompts,2);
  });

  test(`${kind}: same typed source baseline restores raw input and preserves dirty close`, async () => {
    const source=editor(kind,false),target=editor(kind);
    source.content.value="Retained raw edit";target.dialog.open=false;
    const raw=dialogState(source.dialog);
    await recoverDisconnectedEditor(target.panel,source.panel);
    assert.equal(source.dialog.open,false);assert.equal(target.dialog.open,true);
    assert.deepEqual(dialogState(target.dialog),raw);
    assert.equal(await confirmDialogClose(target.panel,target.dialog),false);
    assert.equal(target.content.value,"Retained raw edit");
  });

  test(`${kind}: changed typed baseline keeps the source available for explicit review`, async () => {
    const source=editor(kind,false),target=editor(kind,true,"New saved content");
    source.content.value="Retained raw edit";target.dialog.open=false;
    const previous=globalThis.document;
    globalThis.document={createElement:()=>({dataset:{},setAttribute(){},addEventListener(){},append(){},showModal(){this.open=true;}})};
    try {
      await recoverDisconnectedEditor(target.panel,source.panel);
      assert.equal(source.dialog.open,true);assert.equal(source.content.value,"Retained raw edit");
      assert.equal(target.dialog.open,false);assert.equal(target.copies.length,1);
    } finally {globalThis.document=previous;}
  });
}

test("Knowledge loading has no certified dirty baseline", async () => {
  const {panel,dialog,content}=editor("knowledge");let prompts=0;
  panel._confirm=async()=>{prompts++;return false;};
  setDialogBaseline(panel,dialog.id,null);content.value="Loading placeholder";
  assert.equal(await confirmDialogClose(panel,dialog),true);assert.equal(prompts,0);
});
