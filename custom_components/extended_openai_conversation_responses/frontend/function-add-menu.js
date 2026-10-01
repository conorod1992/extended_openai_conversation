export function functionAddMenu() {
  return `<style>
    .function-add-menu{position:fixed;inset:auto;margin:0;padding:6px;border:1px solid var(--divider-color);border-radius:var(--eoc-radius,12px);background:var(--card-background-color);color:var(--primary-text-color);box-shadow:0 4px 16px #0003;width:320px;max-width:calc(100vw - 32px);max-height:calc(100vh - 32px);overflow:auto}
    .function-add-menu button{display:grid;gap:4px;width:100%;text-align:left;white-space:normal;background:transparent;color:var(--primary-text-color);padding:12px;border:0;box-shadow:none}
    .function-add-menu button:hover,.function-add-menu button:focus-visible{background:var(--secondary-background-color)}
    .function-add-menu small{color:var(--secondary-text-color);font-weight:normal;line-height:1.4}
  </style><div class="actions"><button type="button" id="function-add" popovertarget="function-add-menu" aria-haspopup="menu" aria-expanded="false" aria-controls="function-add-menu">+ Add ▾</button>
    <div id="function-add-menu" class="function-add-menu" popover role="menu" aria-label="Add tool or group">
      <button type="button" id="add-tool" role="menuitem" tabindex="-1"><strong>Function Tool</strong><small>Create a custom tool with your own configuration and instructions.</small></button>
      <button type="button" id="add-ha-tools" role="menuitem" tabindex="-1"><strong>Home Assistant tool</strong><small>Use a tool provided by Home Assistant or another integration.</small></button>
      <button type="button" id="add-group" role="menuitem" tabindex="-1"><strong>Group</strong><small>Organise related tools together.</small></button>
    </div></div>`;
}

export function bindFunctionAddMenu(host) {
  const trigger = host.querySelector("#function-add"), menu = host.querySelector("#function-add-menu");
  const items = [...menu.querySelectorAll('[role="menuitem"]')];
  let lastItem = false;
  trigger.addEventListener("keydown", event => {
    if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
    event.preventDefault();
    lastItem = event.key === "ArrowUp";
    menu.showPopover();
  });
  menu.addEventListener("beforetoggle", event => {
    const open = event.newState === "open";
    trigger.setAttribute("aria-expanded", String(open));
    if (open) {
      const rect = trigger.getBoundingClientRect();
      menu.style.left = `${Math.max(16, Math.min(rect.right - 320, window.innerWidth - 336))}px`;
      // Measure before painting so a narrow screen never shows a clipped menu.
      menu.style.visibility = "hidden";
      menu.style.display = "block";
      const height = menu.getBoundingClientRect().height;
      menu.style.removeProperty("display");
      menu.style.removeProperty("visibility");
      menu.style.top = `${rect.bottom + 8 + height <= window.innerHeight - 16 ? rect.bottom + 8 : Math.max(16, rect.top - height - 8)}px`;
    }
  });
  menu.addEventListener("toggle", event => {
    if (event.newState === "open") {
      items[lastItem ? items.length - 1 : 0].focus();
      lastItem = false;
    }
  });
  menu.addEventListener("keydown", event => {
    const index = items.indexOf(event.target.closest("button"));
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1
        : (index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
      items[next].focus();
    } else if (event.key === "Escape" || event.key === "Tab") {
      menu.hidePopover();
      trigger.focus();
      if (event.key === "Escape") event.preventDefault();
    }
  });
  menu.addEventListener("click", event => {
    if (event.target.closest("button")) { menu.hidePopover(); trigger.focus(); }
  });
}
