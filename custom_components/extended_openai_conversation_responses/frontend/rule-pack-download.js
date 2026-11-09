const MAX_PACK_BYTES=2*1024*1024;

export function prepareRulePackDownload(result) {
  if(typeof result?.json!=="string" || !result.json.length) throw new Error("Server-generated Rule Pack text is unavailable. Reload Management after updating the integration.");
  const json=result.json;
  const blob=new Blob([json],{type:"application/json"});
  if(blob.size>MAX_PACK_BYTES) throw new Error("Rule pack exceeds the 2 MB safety limit.");
  // Parse only review metadata. Never serialize this object: JavaScript may
  // round valid integer values in action payloads. The artifact stays opaque.
  const document=JSON.parse(json);
  if(document?.format!=="extended_openai_request_rule_pack" || !Array.isArray(document.rules) || !Array.isArray(document.groups) || typeof document.security?.review_required!=="boolean") throw new Error("Rule Pack export is incomplete. Reload Management and try again.");
  return {json,blob,security:document.security,count:document.rules.length};
}
