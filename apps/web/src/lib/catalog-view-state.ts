// The product-list URL is the only persisted selection. Never accept a return URL.
export type CatalogViewState = Readonly<{ category: string; after: string | null }>;
export type CatalogSearchParams = Readonly<Record<string, string | string[] | undefined>>;

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function canonicalUuid(value: string): string | null {
  return value.length === 36 && UUID.test(value) ? value.toLowerCase() : null;
}

function canonicalCategory(value: string): string | null {
  if (value === "all" || value === "uncategorized") return value;
  return canonicalUuid(value);
}

function queryFor(state: CatalogViewState): string {
  const query = new URLSearchParams();
  if (state.category !== "all") query.set("category", state.category);
  if (state.after !== null) query.set("after", state.after);
  return query.toString();
}

export function catalogHref(state: CatalogViewState): string {
  const category = canonicalCategory(state.category) ?? "all";
  const after = canonicalUuid(state.after ?? "");
  const query = queryFor({ category, after });
  return `/operations/catalog${query ? `?${query}` : ""}`;
}

export function productHref(productId: string, state: CatalogViewState): string {
  const query = catalogHref(state).split("?")[1];
  return `/operations/products/${encodeURIComponent(productId)}${query ? `?${query}` : ""}`;
}

export function parseCatalogViewState(input: CatalogSearchParams): Readonly<{ state: CatalogViewState; canonicalQuery: string; needsRedirect: boolean }> {
  const rawCategory = input.category;
  const category = typeof rawCategory === "string" ? canonicalCategory(rawCategory) : rawCategory === undefined ? "all" : null;
  const rawAfter = input.after;
  const after = category === null || typeof rawAfter !== "string" ? null : canonicalUuid(rawAfter);
  const state: CatalogViewState = { category: category ?? "all", after };
  const canonicalQuery = queryFor(state);
  const supplied = new URLSearchParams();
  for (const [name, values] of Object.entries(input)) {
    if (typeof values === "string") supplied.append(name, values);
    else if (Array.isArray(values)) for (const value of values) supplied.append(name, value);
  }
  return { state, canonicalQuery, needsRedirect: supplied.toString() !== canonicalQuery };
}
