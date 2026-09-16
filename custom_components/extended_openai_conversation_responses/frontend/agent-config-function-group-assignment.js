import * as base from "./agent-config-native-yaml.js";

export * from "./agent-config-native-yaml.js";

const ASSIGNMENT_STYLE = `
  .function-group-assignment-control {
    display: inline-flex;
    align-items: center;
    max-width: min(260px, 100%);
    border: 1px solid var(--divider-color, rgba(127, 127, 127, .35));
    border-radius: 999px;
    background: var(--card-background-color, transparent);
    color: var(--primary-text-color);
    transition: border-color 120ms ease, background 120ms ease;
  }
  .function-group-assignment-control:hover,
  .function-group-assignment-control:focus-within {
    border-color: var(--primary-color);
    background: var(--secondary-background-color, transparent);
  }
  .function-group-assignment-control.is-disabled-group {
    opacity: .72;
  }
  .function-group-assignment-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding-inline-start: 10px;
    color: var(--secondary-text-color);
    font-size: 14px;
    line-height: 1;
  }
  .function-group-assignment {
    min-width: 0;
    max-width: 220px;
    border: 0;
    outline: 0;
    background: transparent;
    color: inherit;
    font: inherit;
    font-size: 13px;
    font-weight: 500;
    padding: 6px 10px 6px 6px;
    cursor: pointer;
  }
  .function-group-assignment:disabled {
    cursor: progress;
  }
  @media (max-width: 700px) {
    .function-group-assignment-control {
      max-width: 100%;
    }
    .function-group-assignment {
      max-width: 180px;
    }
  }
`;

function currentGroupForTool(groups, name) {
  return (groups || []).find((group) => (group.functions || []).includes(name)) || null;
}

function assignmentOptions(panel, groups, currentId) {
  const options = [
    `<option value="" ${currentId ? "" : "selected"}>Available on every request</option>`,
    ...(groups || []).map((group) => {
      const disabled = group.enabled === false ? " (disabled)" : "";
      return `<option value="${panel._e(group.id)}" ${group.id === currentId ? "selected" : ""}>${panel._e(group.name)}${disabled}</option>`;
    }),
  ];
  return options.join("");
}

export function decorateFunctionGroupAssignments(panel, html) {
  if (typeof document === "undefined" || typeof document.createElement !== "function") return html;
  const template = document.createElement("template");
  template.innerHTML = html;
  const config = panel?._draft || panel?._result?.config || {};
  const tools = config.functions || [];
  const groups = config.function_groups || [];

  for (const card of template.content.querySelectorAll(".tool-card[data-tool-index]")) {
    const index = Number(card.dataset.toolIndex);
    const tool = tools[index];
    const name = tool?.spec?.name;
    if (!name || card.querySelector(".function-group-assignment")) continue;
    const current = currentGroupForTool(groups, name);
    const actions = card.querySelector(".tool-card-actions, .actions");
    if (!actions) continue;

    const label = document.createElement("label");
    label.className = `function-group-assignment-control${current?.enabled === false ? " is-disabled-group" : ""}`;
    label.title = current?.enabled === false
      ? `Function group: ${current.name}. This group is currently disabled.`
      : `Function group: ${current?.name || "Available on every request"}`;
    label.innerHTML = `<span class="sr-only">Function group for ${panel._e(name)}</span><span class="function-group-assignment-icon" aria-hidden="true">▸</span><select class="function-group-assignment" data-index="${index}" data-original-group="${panel._e(current?.id || "")}" aria-label="Function group for ${panel._e(name)}">${assignmentOptions(panel, groups, current?.id || "")}</select>`;
    actions.insertAdjacentElement("afterbegin", label);
  }

  const style = document.createElement("style");
  style.dataset.functionGroupAssignment = "";
  style.textContent = ASSIGNMENT_STYLE;
  template.content.prepend(style);
  return template.innerHTML;
}

async function assignToolToGroup(panel, select) {
  const config = panel?._draft || panel?._result?.config || {};
  const tools = config.functions || [];
  const groups = config.function_groups || [];
  const index = Number(select.dataset.index);
  const tool = tools[index];
  const name = tool?.spec?.name;
  if (!name) return;

  const current = currentGroupForTool(groups, name);
  const targetId = select.value;
  if ((current?.id || "") === targetId) return;
  select.disabled = true;

  try {
    let response;
    if (targetId) {
      const target = groups.find((group) => group.id === targetId);
      if (!target) throw new Error("The selected Function Group no longer exists");
      response = await panel._call("tools", "save_group", {
        group: {
          ...target,
          functions: [...new Set([...(target.functions || []).filter((item) => item !== name), name])],
        },
        original_id: target.id,
      });
      base.synchronizePersistedFunctions(panel, response);
      panel._toast(`${name} moved to ${target.name}`);
    } else {
      if (!current) return;
      response = await panel._call("tools", "save_group", {
        group: {
          ...current,
          functions: (current.functions || []).filter((item) => item !== name),
        },
        original_id: current.id,
      });
      base.synchronizePersistedFunctions(panel, response);
      panel._toast(`${name} is now available on every request`);
    }
    panel._render();
  } catch (err) {
    select.value = current?.id || "";
    select.disabled = false;
    panel._toast(`Unable to change Function Group: ${err.message || String(err)}`, true);
  }
}

export function renderTools(panel) {
  return decorateFunctionGroupAssignments(panel, base.renderTools(panel));
}

export function bindTools(panel) {
  const result = base.bindTools(panel);
  panel?.shadowRoot?.querySelectorAll(".function-group-assignment").forEach((select) => {
    select.addEventListener("change", () => void assignToolToGroup(panel, select));
  });
  return result;
}
