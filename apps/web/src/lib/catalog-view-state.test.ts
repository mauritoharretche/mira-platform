import { describe, expect, it } from "vitest";
import { canonicalUuid, catalogHref, parseCatalogViewState, productHref } from "./catalog-view-state";

const ID = "12345678-1234-1234-1234-123456789abc";
const UPPER = ID.toUpperCase();

describe("catalog view state", () => {
  it("defaults to the first unfiltered page without redirecting repeatedly", () => {
    const parsed = parseCatalogViewState({});
    expect(parsed).toEqual({ state: { category: "all", after: null }, canonicalQuery: "", needsRedirect: false });
    expect(catalogHref(parsed.state)).toBe("/operations/catalog");
  });

  it("uses deterministic category then cursor order and canonical UUIDs", () => {
    const parsed = parseCatalogViewState({ after: UPPER, category: UPPER });
    expect(parsed).toEqual({ state: { category: ID, after: ID }, canonicalQuery: `category=${ID}&after=${ID}`, needsRedirect: true });
    expect(parseCatalogViewState({ category: ID, after: ID }).needsRedirect).toBe(false);
    expect(catalogHref(parsed.state)).toBe(`/operations/catalog?category=${ID}&after=${ID}`);
    expect(productHref(ID, parsed.state)).toBe(`/operations/products/${ID}?category=${ID}&after=${ID}`);
  });

  it("omits the explicit all value while preserving its valid cursor", () => {
    expect(parseCatalogViewState({ category: "all", after: ID })).toEqual({
      state: { category: "all", after: ID }, canonicalQuery: `after=${ID}`, needsRedirect: true,
    });
    expect(parseCatalogViewState({ category: "uncategorized" }).needsRedirect).toBe(false);
  });

  it("rejects malformed and oversized values without trimming or rounding", () => {
    for (const value of ["", " ", `${ID}0`, ` ${ID}`, "not-a-uuid", "/operations", "ALL"]) {
      expect(canonicalUuid(value)).toBe(null);
      expect(parseCatalogViewState({ category: value, after: ID }).state).toEqual({ category: "all", after: null });
      expect(parseCatalogViewState({ category: ID, after: value }).state).toEqual({ category: ID, after: null });
    }
  });

  it("rejects duplicate recognized parameters and removes unknown parameters", () => {
    expect(parseCatalogViewState({ category: [ID, ID], after: ID }).state).toEqual({ category: "all", after: null });
    expect(parseCatalogViewState({ category: ID, after: [ID, ID] }).state).toEqual({ category: ID, after: null });
    expect(parseCatalogViewState({ category: ID, returnUrl: "https://example.invalid/path" })).toEqual({
      state: { category: ID, after: null }, canonicalQuery: `category=${ID}`, needsRedirect: true,
    });
  });

  it("keeps return links on fixed local paths even for hostile product IDs", () => {
    expect(productHref("https://example.invalid/?token=fictional-value", { category: "all", after: null }))
      .toBe("/operations/products/https%3A%2F%2Fexample.invalid%2F%3Ftoken%3Dfictional-value");
    expect(catalogHref({ category: "https://example.invalid", after: null })).toBe("/operations/catalog");
  });
});
