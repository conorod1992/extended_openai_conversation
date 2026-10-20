// HA's automation editor registers the native user and entity pickers lazily.
// Share the load within a document, without carrying readiness across documents.
const documentLoads = new WeakMap();

export async function ensureNativePicker(name, documentRef = globalThis.document) {
  const registry = documentRef?.defaultView?.customElements || globalThis.customElements;
  if (registry?.get(name)) return;
  if (!documentRef) throw new Error(`Home Assistant ${name} could not be loaded.`);
  let loading = documentLoads.get(documentRef);
  if (!loading) {
    loading = (async () => {
      const resolver = documentRef.createElement("partial-panel-resolver");
      const routes = (resolver.getRoutes || resolver._getRoutes)?.call(resolver, [
        {component_name: "config", url_path: "config"},
      ]);
      await routes?.routes?.config?.load?.();
      const config = documentRef.createElement("ha-panel-config");
      await config.routerOptions?.routes?.automation?.load?.();
    })().finally(() => documentLoads.delete(documentRef));
    documentLoads.set(documentRef, loading);
  }
  await loading;
  if (!registry?.get(name)) throw new Error(`Home Assistant ${name} could not be loaded.`);
}
