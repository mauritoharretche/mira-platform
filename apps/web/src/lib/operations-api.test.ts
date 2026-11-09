import { afterEach, describe, expect, it, vi } from "vitest";
import { clearOperatorState, messageFor, OperatorError, request, signIn, validPositiveInteger } from "./operations-api";

afterEach(() => { clearOperatorState(); vi.unstubAllGlobals(); });

describe("operator transport", () => {
  it("preserves exact integer boundaries without Number coercion", () => {
    expect(validPositiveInteger("9223372036854775807")).toBe(true);
    expect(validPositiveInteger("9007199254740993")).toBe(true);
    for (const value of ["9223372036854775808", "01", "0", "-1", "1.0", " 1", "１"]) expect(validPositiveInteger(value)).toBe(false);
  });
  it("never retries a POST when its response is lost", async () => {
    const fetcher = vi.fn().mockRejectedValue(new TypeError("internal network detail"));
    await expect(request("/catalog/products", { method: "POST" }, fetcher)).rejects.toMatchObject({ code: "network_error", uncertain: true });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(messageFor(new OperatorError("network_error", undefined, true))).toContain("Resultado incierto");
  });
  it("uses same-origin credentials, no cache and a bounded timeout", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ stock: null }));
    await expect(request("/inventory/variants/example/stock", {}, fetcher)).resolves.toEqual({ stock: null });
    expect(fetcher).toHaveBeenCalledWith("/api/v1/operator/inventory/variants/example/stock", expect.objectContaining({ credentials: "same-origin", cache: "no-store", signal: expect.any(AbortSignal) }));
  });
  it("refreshes the CSRF token from successful native login and never exposes exceptions", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ csrf_token: "fictional-before" })).mockResolvedValueOnce(Response.json({ csrf_token: "fictional-after" }));
    vi.stubGlobal("fetch", fetcher);
    await signIn("fictional-operator", "fictional-password");
    expect(fetcher.mock.calls[1][1].headers["X-CSRFToken"]).toBe("fictional-before");
    expect(messageFor(new Error("private traceback"))).not.toContain("private traceback");
  });
  it("preserves explicit conflicts instead of converting them into success", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ error: { code: "stock_conflict" } }, { status: 409 }));
    await expect(request("/inventory/variants/example/initial-receipt", { method: "POST" }, fetcher)).rejects.toMatchObject({ code: "stock_conflict", uncertain: false });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});

describe("catalog read ownership", () => {
  it("discards an older filter response even if its fetch ignores abort", async () => {
    const { ReadGeneration } = await import("./operations-api");
    const reads = new ReadGeneration();
    let resolveOld!: (value: string) => void;
    const oldResult = new Promise<string>((resolve) => { resolveOld = resolve; });
    const oldToken = reads.begin();
    let visible = "";
    const oldRead = oldResult.then((result) => { if (reads.accepts(oldToken)) visible = result; });
    reads.invalidate(); // synchronous filter change, before the next effect
    const newToken = reads.begin();
    if (reads.accepts(newToken)) visible = "new category";
    resolveOld("old category");
    await oldRead;
    expect(visible).toBe("new category");
  });
  it("invalidates in-flight product reads after writes and unmount", async () => {
    const { ReadGeneration } = await import("./operations-api");
    const reads = new ReadGeneration();
    const beforeWrite = reads.begin();
    reads.invalidate();
    expect(reads.accepts(beforeWrite)).toBe(false);
    const afterWrite = reads.begin();
    reads.invalidate();
    expect(reads.accepts(afterWrite)).toBe(false);
  });
  it("resets pagination by constructing a fresh filter query", async () => {
    const { catalogQuery } = await import("./operations-api");
    expect(catalogQuery("uncategorized", "old-cursor")).toContain("after=old-cursor");
    expect(catalogQuery("all")).toBe("/catalog/products?category=all");
    expect(catalogQuery("a&after=spoofed")).toBe("/catalog/products?category=a%26after%3Dspoofed");
  });
  it("category write uncertainty and duplicate errors do not authorize replay", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ error: { code: "category_conflict" } }, { status: 409 }));
    await expect(request("/catalog/categories", { method: "POST" }, fetcher)).rejects.toMatchObject({ code: "category_conflict", uncertain: false });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(messageFor(new OperatorError("category_conflict"))).toContain("Ya existe");
    const failed = vi.fn().mockRejectedValue(new TypeError("network"));
    await expect(request("/catalog/products/id/category", { method: "POST" }, failed)).rejects.toMatchObject({ uncertain: true });
    expect(failed).toHaveBeenCalledTimes(1);
  });
});
