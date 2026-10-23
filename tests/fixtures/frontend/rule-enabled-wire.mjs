import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const chunks=[];for await(const chunk of process.stdin)chunks.push(chunk);
const result=JSON.parse(Buffer.concat(chunks).toString('utf8'));
const rule=result.rules[0];
const node={dataset:{ruleKey:rule.id},querySelector:()=>null,querySelectorAll:()=>[],matches:()=>true,nextElementSibling:null};
const handlers={};
const list={dataset:{},children:[node],firstElementChild:node,querySelector:()=>null,querySelectorAll:selector=>selector==='[data-rule-key]'?[node]:[],addEventListener:(event,handler)=>{handlers[event]=handler;}};
const root={querySelector:selector=>selector==='.rule-list'?list:null,querySelectorAll:()=>[],addEventListener:()=>{}};
let sent;
const panel={shadowRoot:root,_result:result,_call:async(section,action,payload)=>{assert.equal(section,'request_rules');assert.equal(action,'update');sent=payload;return {};},_toast:()=>{}};
const module=await import(pathToFileURL(process.argv[2]).href);
const bindRequestRulesCore=module.bindRequestRulesCore ?? module.r;
bindRequestRulesCore(panel);
await handlers.change({target:{dataset:{id:rule.id},checked:false,disabled:false,matches:selector=>selector==='.rule-enabled'}});
assert.ok(sent);
process.stdout.write(JSON.stringify(sent));


