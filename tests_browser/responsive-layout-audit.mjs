// Conservative responsive integrity checks; no screenshot baseline and no layout
// assumptions about deliberate mobile navigation substitutions.
export async function auditResponsive(page, selectors, {baseline = null} = {}) {
  return page.evaluate(({selectors, baseline}) => {
    const panel = document.querySelector("extended-openai-management-panel");
    const root = panel?.shadowRoot || document;
    const describe = selector => {
      const element = root.querySelector(selector);
      if (!element) return {selector, present:false};
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      const rendered = element.getClientRects().length > 0 &&
        style.display !== "none" && style.visibility !== "hidden" &&
        Number(style.opacity) > 0 && rect.width >= 4 && rect.height >= 4;
      const horizontalEscape = rect.right < -2 || rect.left > innerWidth + 2;
      return {selector, present:true, rendered, horizontalEscape,
        rect:{x:rect.x,y:rect.y,width:rect.width,height:rect.height}};
    };
    const current = selectors.map(describe);
    const failures = [];
    for (const item of current) {
      const reference = baseline?.find(other => other.selector === item.selector);
      if (!reference?.rendered) continue;
      if (!item.present || !item.rendered) failures.push({kind:"lost-visible-control", selector:item.selector});
      else if (item.horizontalEscape) failures.push({kind:"offscreen-control", selector:item.selector});
    }
    // Only audit independent directly adjacent controls in the same parent.
    // Descendants, decoration, overlay portals and intentional nested boxes are
    // excluded; a meaningful intersection cannot be explained by border rounding.
    const controls = [...root.querySelectorAll(
      'button, input:not([type="hidden"]), select, textarea, [role="button"], [role="switch"]'
    )].filter(element => {
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== "none" && style.visibility !== "hidden" &&
        Number(style.opacity) > 0 && rect.width >= 8 && rect.height >= 8 &&
        !element.closest('dialog:not([open]), details:not([open]), [hidden]');
    });
    for (let i = 0; i < controls.length; i++) {
      const a = controls[i], ra = a.getBoundingClientRect();
      if (!a.parentElement) continue;
      for (let j = i + 1; j < controls.length; j++) {
        const b = controls[j];
        if (a.parentElement !== b.parentElement) continue;
        const rb = b.getBoundingClientRect();
        const w = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left);
        const h = Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top);
        if (w >= 8 && h >= 8 && w * h >= 100)
          failures.push({kind:"sibling-control-overlap",
            first:a.id || a.outerHTML.slice(0,70),
            second:b.id || b.outerHTML.slice(0,70), width:w, height:h});
      }
    }
    return {viewport:{width:innerWidth,height:innerHeight}, controls:current, failures};
  }, {selectors,baseline});
}
