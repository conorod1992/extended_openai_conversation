export const SETTINGS_POLISH_STYLE = `
    [data-eoc-guide-layout]{grid-template-columns:minmax(0,1fr)!important}
    [data-eoc-guide-layout]>*{min-width:0;max-width:100%}
    .guide-quick-start,
    .guide-quick-tasks,
    .guide-quick-card,
    .guide-search,
    .guide-groups,
    .guide-group,
    .guide-group-heading,
    .guide-topics,
    .guide-topic{min-width:0;max-width:100%}
    .guide-quick-tasks{grid-template-columns:repeat(auto-fit,minmax(min(260px,100%),1fr))!important}
    .guide-quick-card{width:100%;overflow-wrap:anywhere}
    .comparison-table{min-width:0;max-width:100%;overflow-x:auto}
    .comparison-table table{max-width:none}
    [data-field="conversation_continuity"]{align-content:start}
    #config-conversation_continuity{height:42px;min-height:42px}
    .eoc-model-data-panel{display:grid;gap:14px;margin-top:30px;padding-top:26px;border-top:1px solid var(--divider-color)}
    .eoc-model-data-heading{display:grid;gap:6px}
    .eoc-model-data-heading h3{margin:0;color:var(--primary-text-color);font-size:16px}
    .eoc-model-data-heading p{max-width:860px;margin:0;color:var(--secondary-text-color);font-size:13px;line-height:1.5}
    .eoc-model-data-actions{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(240px,100%),1fr));gap:12px}
    .eoc-model-data-action{display:flex;min-width:0;flex-direction:column;align-items:flex-start;gap:7px;padding:14px;border:1px solid var(--divider-color);border-radius:10px;background:color-mix(in srgb,var(--secondary-background-color) 32%,var(--card-background-color))}
    .eoc-model-data-action[hidden]{display:none}
    .eoc-model-data-action strong{color:var(--primary-text-color);font-size:13px;line-height:1.35}
    .eoc-model-data-action p{flex:1;margin:0;color:var(--secondary-text-color);font-size:12px;line-height:1.45}
    .eoc-model-data-action button{width:100%;margin-top:4px}
    .eoc-model-data-status{display:grid;gap:3px;margin:0;padding:10px 12px;border-radius:9px;background:var(--secondary-background-color);font-size:12px;line-height:1.45}
    .eoc-model-data-status strong{color:var(--primary-text-color)}
    .eoc-model-data-status [data-model-data-status]{margin:0}
    @media(max-width:900px){.eoc-model-data-actions{grid-template-columns:1fr}}
  `;
