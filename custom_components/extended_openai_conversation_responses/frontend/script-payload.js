// Inspect authored HA service payloads without rendering templates.
const isMapping = (value) => value !== null && typeof value === "object" && !Array.isArray(value);

export function isFunctionServiceAction(action) {
  const name = action?.action ?? action?.service ?? action?.service_template;
  return typeof name === "string" && name.toLowerCase() === "extended_openai_conversation_responses.call_function";
}

export function effectiveServicePayload(action) {
  const payload = {};
  for (const field of ["data", "data_template"]) {
    if (!Object.hasOwn(action || {}, field)) continue;
    if (!isMapping(action[field])) return null;
    Object.assign(payload, action[field]);
  }
  return payload;
}

export function setEffectiveServiceField(action, key, value) {
  if (value === undefined) {
    // Explicit removal must not uncover a shadowed value in the other map.
    for (const field of ["data", "data_template"]) {
      if (isMapping(action[field])) delete action[field][key];
    }
    return;
  }
  const field = ["data_template", "data"].find((name) => isMapping(action[name]) && Object.hasOwn(action[name], key))
    || (isMapping(action.data_template) ? "data_template" : "data");
  action[field] = {...action[field], [key]:value};
}
