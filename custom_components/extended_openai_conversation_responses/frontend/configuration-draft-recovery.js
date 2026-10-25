import {clone, same} from "./unsaved-state.js";

const MODEL_FIELDS = ["chat_model", "azure_model", "reasoning_effort"];
const sameField = (left, right, key) => Object.hasOwn(left, key) === Object.hasOwn(right, key) && same(left[key], right[key]);
const changedFields = (draft, baseline) => new Set([...new Set([...Object.keys(draft), ...Object.keys(baseline)])]
  .filter(key => !sameField(draft, baseline, key)));

// Each draft is compared with its own authoring baseline. A newer accepted
// snapshot does not turn either panel's unchanged fields into authored edits.
export function planConfigurationRecovery(source, sourceBaseline, target, targetBaseline, {targetOwnsModel = false} = {}) {
  const sourceFields = changedFields(source.draft, sourceBaseline.config);
  const targetFields = changedFields(target.draft, targetBaseline.config);
  const conflicts = [...sourceFields].filter(key => targetFields.has(key) && !sameField(source.draft, target.draft, key));
  if (source.title !== sourceBaseline.title && target.title !== targetBaseline.title && source.title !== target.title) conflicts.push("__title");
  const sourceModelChanged = MODEL_FIELDS.some(key => sourceFields.has(key));
  const targetModelChanged = MODEL_FIELDS.some(key => targetFields.has(key));
  if (sourceModelChanged && (targetModelChanged || targetOwnsModel) && !MODEL_FIELDS.every(key => sameField(source.draft, target.draft, key))) conflicts.push("model_settings");
  return {conflicts, preserveTargetModel:targetOwnsModel || targetModelChanged};
}

// Overlay only the recovered delta. In particular, unchanged model defaults
// from the source must not replace a newer target model's prepared settings.
export function combineRecoveredConfiguration(target, targetBaseline, recovered) {
  const draft = clone(target.draft);
  for (const key of changedFields(recovered.draft, targetBaseline.config)) {
    if (Object.hasOwn(recovered.draft, key)) draft[key] = clone(recovered.draft[key]);
    else delete draft[key];
  }
  return {draft, title:recovered.title !== targetBaseline.title ? recovered.title : target.title};
}

export function recoveredConfigurationChanges(current, baseline) {
  const fields = changedFields(current.draft, baseline.config);
  const config = {}, deleted_fields = [];
  for (const key of fields) {
    if (Object.hasOwn(current.draft, key)) config[key] = clone(current.draft[key]);
    else deleted_fields.push(key);
  }
  return {config, ...(deleted_fields.length ? {deleted_fields} : {}),
    ...(current.title !== baseline.title ? {title:current.title} : {})};
}
