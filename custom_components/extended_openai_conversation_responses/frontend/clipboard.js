// @ts-check
/** @param {string} text @param {Pick<Navigator, "clipboard"> | undefined} clipboardNavigator @param {Document | undefined} clipboardDocument */
export async function copyTextToClipboard(text, clipboardNavigator = globalThis.navigator, clipboardDocument = globalThis.document) {
  const writeText = clipboardNavigator?.clipboard?.writeText;
  if (typeof writeText === "function") {
    try {
      await writeText.call(clipboardNavigator.clipboard, text);
      return;
    } catch (err) {
      if (!clipboardDocument) throw err;
    }
  }
  if (!clipboardDocument?.body || typeof clipboardDocument.createElement !== "function" || typeof clipboardDocument.execCommand !== "function") {
    throw new Error("Clipboard access is unavailable in this browser context");
  }
  let active = clipboardDocument.activeElement;
  while (active?.shadowRoot?.activeElement) active = active.shadowRoot.activeElement;
  const container = active?.closest("dialog[open]") || clipboardDocument.body;
  const textarea = clipboardDocument.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  try {
    container.appendChild(textarea);
    textarea.select();
    if (!clipboardDocument.execCommand("copy")) throw new Error("Browser copy command failed");
  } finally {
    textarea.remove();
    if (active && "focus" in active && typeof active.focus === "function") {
      try { active.focus({preventScroll:true}); } catch { /* Focus restoration is best effort after copy. */ }
    }
  }
}


