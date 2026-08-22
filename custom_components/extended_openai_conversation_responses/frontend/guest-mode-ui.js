export const GUEST_EXCLUSION_KEYS = [
  "guest_excluded_labels", "guest_excluded_areas", "guest_excluded_domains", "guest_excluded_entities",
  "guest_control_excluded_labels", "guest_control_excluded_areas", "guest_control_excluded_domains", "guest_control_excluded_entities",
];

export function freshGuestPolicyDraft(config = {}) {
  const draft = JSON.parse(JSON.stringify(config));
  [...GUEST_EXCLUSION_KEYS, "guest_knowledge_source_ids", "guest_allowed_function_names", "guest_allowed_group_ids"]
    .forEach((key) => { draft[key] = []; });
  return Object.assign(draft, {
    guest_mode_enabled: true,
    guest_separate_control_restrictions: false,
    guest_knowledge_policy: "off",
    guest_function_policy: "off",
    guest_shared_memory_policy: "off",
  });
}
