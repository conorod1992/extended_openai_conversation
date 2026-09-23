export function knowledgeSourceAvailabilityBadge(source) {
  const enabled = source?.enabled !== false;
  return `<span class="${enabled ? "availability-badge" : "disabled-badge"} knowledge-source-availability-badge">${enabled ? "Available" : "Unavailable"}</span>`;
}
