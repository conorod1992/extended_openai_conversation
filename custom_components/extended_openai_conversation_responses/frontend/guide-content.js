export const GUIDE_TOPICS = [
  {id:"getting-started", title:"Getting started", summary:"Choose an assistant, confirm its model, then decide which Home Assistant capabilities and data it may use.", terms:"setup first steps", action:{label:"Configure this",page:"assistant",section:"basics"}},
  {id:"models", title:"Choosing a model/provider", summary:"Provider format and model capabilities determine which response controls are available.", terms:"api reasoning service tier", action:{label:"Open model settings",page:"assistant",section:"model-responses"}},
  {id:"continuity", title:"Conversation continuity", summary:"Recent context lets a follow-up continue naturally. Its timeout is separate from retained archive history.", terms:"follow up timeout context", action:{label:"Configure continuity",page:"assistant",section:"conversation"}},
  {id:"memory", title:"What the assistant remembers", summary:"Recent context, expiring details, durable memories, archives, and Knowledge each solve a different problem.", terms:"comparison data privacy", action:{label:"Manage memories",page:"data-memory",section:"memories"}},
  {id:"persistent-memory", title:"Persistent memory", summary:"Reusable facts and preferences remain available until they are removed.", terms:"long term durable facts", action:{label:"Manage memories",page:"data-memory",section:"memories"}},
  {id:"temporary-memory", title:"Temporary memory", summary:"Useful short-term information expires automatically at its recorded time.", terms:"short term expiring", action:{label:"View temporary memory",page:"data-memory",section:"memories"}},
  {id:"archive", title:"Conversation archive", summary:"The archive stores reviewable history. It is independent from the recent context used for follow-ups.", terms:"history retained search", action:{label:"Open conversation history",page:"data-memory",section:"conversations"}},
  {id:"knowledge", title:"Knowledge Library", summary:"Store larger reference material that the assistant can search only when it is useful.", terms:"sources reference retrieval", action:{label:"Open Knowledge Library",page:"data-memory",section:"knowledge"}},
  {id:"functions", title:"Function Tools & Groups", summary:"Functions add actions. Groups can load their detailed instructions only when a task needs them.", terms:"tools on demand yaml", action:{label:"Manage functions",page:"capabilities",section:"functions"}},
  {id:"guest-mode", title:"Guest Mode", summary:"Guest Mode adds integration-enforced restrictions for visitors without mixing guest and personal history.", terms:"visitors privacy exclusions schedule", action:{label:"Configure Guest Mode",page:"capabilities",section:"guest-mode"}},
  {id:"voice", title:"Voice assistants and multiple users", summary:"Voice scope and device mappings decide whose continuity and memory a request may use.", terms:"satellite identity shared household", action:{label:"Configure voice",page:"assistant",section:"voice"}},
  {id:"privacy", title:"Privacy & security", summary:"Use Home Assistant exposure, Guest exclusions, scope policy, and retention together as layered controls.", terms:"access entities controls", action:{label:"Review HA access",page:"capabilities",section:"home-assistant"}},
  {id:"usage", title:"Usage & troubleshooting", summary:"Review token use, recent runs, provider tests, retention, and backups from one maintenance area.", terms:"diagnostics tokens backup", action:{label:"Open diagnostics",page:"usage-maintenance",section:"diagnostics"}},
];

export const MEMORY_COMPARISON = [
  ["Conversation context","Natural follow-ups","Until continuity timeout","Included in the next follow-up"],
  ["Temporary memory","Short-lived useful details","Until its expiry time","Retrieved when relevant"],
  ["Persistent memory","Reusable facts and preferences","Until deleted","Retrieved when relevant"],
  ["Conversation archive","Reviewable past discussions","For configured retention","Searched only when enabled/needed"],
  ["Knowledge Library","Larger reference information","Until a source is deleted","Searched on demand"],
];
