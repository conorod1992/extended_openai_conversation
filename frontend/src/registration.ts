type CustomElementConstructorLike = CustomElementConstructor;

export function defineCustomElement(
  name: string,
  constructor: CustomElementConstructorLike,
  registry: CustomElementRegistry | undefined =
    typeof customElements === "undefined" ? undefined : customElements,
): boolean {
  if (!registry || registry.get(name)) return false;
  registry.define(name, constructor);
  return true;
}
