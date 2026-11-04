const MAX_PACK_BYTES=2*1024*1024;

export function prepareRulePackDownload(pack) {
  const json=JSON.stringify(pack);
  const blob=new Blob([json],{type:"application/json"});
  if(blob.size>MAX_PACK_BYTES) throw new Error("Rule pack exceeds the 2 MB safety limit.");
  return {json,blob};
}

