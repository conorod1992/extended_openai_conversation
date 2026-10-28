import assert from "node:assert/strict";
import test from "node:test";
import {readFile} from "node:fs/promises";
import {copyTextToClipboard} from "../custom_components/extended_openai_conversation_responses/frontend/clipboard.js";
async function fallbackWitness(copy, outcome) {
  let copied, removed=false, focused=false, appended=false;
  const textarea={style:{},setAttribute(){},select(){if(outcome==="selection failure")throw Error("selection");},remove(){removed=true;}};
  const dialog={appendChild(node){assert.equal(node,textarea);appended=true;}};
  const active={closest:()=>dialog,focus(){focused=true;}};
  const doc={activeElement:active,body:{appendChild(){assert.fail("fallback must stay inside the modal");}},createElement:()=>textarea,execCommand(command){copied=command;return outcome==="success";}};
  const operation=copy("private text",{clipboard:{writeText:async()=>{throw Error("denied");}}},doc);
  if(outcome==="success")await operation;else await assert.rejects(operation);
  assert.equal(appended,true);assert.equal(removed,true);assert.equal(focused,true);
  if(outcome!=="selection failure")assert.equal(copied,"copy");
}
for (const outcome of ["success", "denied", "selection failure"]) test(`fallback cleanup and modal focus: ${outcome}`, () => fallbackWitness(copyTextToClipboard, outcome));
test("modern API keeps its receiver and avoids DOM fallback",async()=>{
  const clipboard={async writeText(value){assert.equal(this,clipboard);assert.equal(value,"hello");}};
  await copyTextToClipboard("hello",{clipboard},{createElement(){assert.fail("unexpected fallback");}});
});

for (const removed of ["textarea.remove();", "active.focus({preventScroll:true});"]) test(`sensitivity: fallback requires ${removed}`, async()=>{
  const source=await readFile(new URL("../custom_components/extended_openai_conversation_responses/frontend/clipboard.js",import.meta.url),"utf8");
  assert.ok(source.includes(removed));
  const mutant=await import(`data:text/javascript;base64,${Buffer.from(source.replace(removed,"void 0;")).toString("base64")}`);
  await assert.rejects(()=>fallbackWitness(mutant.copyTextToClipboard,"success"),assert.AssertionError);
});
