// Shared Unicode-aware Function search for editors, groups and Local Handling.
const functionSearchTokenCache = new WeakMap();

export function cachedFunctionSearchTokens(node, searchableText) {
  const value = String(searchableText || "");
  const cached = functionSearchTokenCache.get(node);
  if (cached?.value === value) return cached.tokens;
  const tokens = searchTokens(value);
  functionSearchTokenCache.set(node, {value, tokens});
  return tokens;
}

export function matchesFunctionSearch(query, searchableText, minimumPrefixLength = 3) {
  return matchesFunctionSearchTokens(searchTokens(query), searchTokens(searchableText), minimumPrefixLength);
}

export function searchTokens(value) {
  const raw = String(value || "");
  const tokens = raw.normalize("NFKD")
    .replace(/\p{M}/gu, "")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim().split(/\s+/).filter(Boolean)
    .map((token) => {
      if (token.length > 4 && token.endsWith("ies")) return `${token.slice(0, -3)}y`;
      if (token.length > 4 && /(ches|shes|sses|xes|zes)$/.test(token)) return token.slice(0, -2);
      if (token.length > 3 && token.endsWith("s") && !token.endsWith("ss")) return token.slice(0, -1);
      return token;
    });
  return tokens.length || !raw.trim() ? tokens : [raw.trim().normalize("NFKC").toLowerCase()];
}

export function matchesFunctionSearchTokens(queryTokens, textTokens, minimumPrefixLength = 3) {
  if (!queryTokens.length) return true;
  return queryTokens.every((queryToken) => textTokens.some((textToken) =>
    textToken === queryToken || (
      Math.min(textToken.length, queryToken.length) >= minimumPrefixLength
      && (textToken.startsWith(queryToken) || queryToken.startsWith(textToken))
    )
  ));
}
