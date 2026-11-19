import { afterEach, describe, expect, it, vi } from "vitest";
import { clearOperatorState, formatPriceARS, messageFor, normalizeMeasurementAmount, normalizePriceInput, OperationPermitCoordinator, OperatorError, request, signIn, validMeasurementSpec, validPositiveInteger } from "./operations-api";

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

describe("variant price presentation", () => {
  it("normalizes exact decimal strings without floating point conversion", () => {
    expect(normalizePriceInput("12,3")).toBe("12.30");
    expect(normalizePriceInput("0.01")).toBe("0.01");
    expect(normalizePriceInput("999999999999.99")).toBe("999999999999.99");
    for (const value of ["0", "-1", "1e2", "1.001", "1,2.3", "1000000000000", "１.００"])
      expect(normalizePriceInput(value)).toBeNull();
    expect(formatPriceARS("123456789.05")).toBe("ARS 123.456.789,05");
  });
  it("preserves a 409 as an explicit price conflict", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ error: { code: "price_conflict" } }, { status: 409 }));
    await expect(request("/catalog/variants/id/price", { method: "POST" }, fetcher))
      .rejects.toMatchObject({ code: "price_conflict", uncertain: false });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});

describe("variant measurement presentation", () => {
  const variantId = "00000000-0000-4000-8000-000000000001";
  const spec = { spec_id: "00000000-0000-4000-8000-000000000002", variant_id: variantId,
    version: "9007199254740993", sale_unit: "EACH", net_content_amount: "250.000000", net_content_unit: "G",
    created_at: "2026-11-18T12:00:00+00:00", updated_at: "2026-11-18T12:00:00+00:00" };

  it("normalizes exact positive decimals using strings only", () => {
    expect(normalizeMeasurementAmount("0.000001")).toBe("0.000001");
    expect(normalizeMeasurementAmount("250.0")).toBe("250.000000");
    expect(normalizeMeasurementAmount("999999999999.999999")).toBe("999999999999.999999");
    for (const value of ["0", "0.000000", "-1", "1e3", "1.0000001", "1000000000000", " 1", "１.０"])
      expect(normalizeMeasurementAmount(value)).toBeNull();
  });

  it("validates identity, version, units, paired net content and timestamps", () => {
    expect(validMeasurementSpec(spec, variantId)).toBe(true);
    expect(validMeasurementSpec({ ...spec, sale_unit: null, net_content_amount: null, net_content_unit: null }, variantId)).toBe(true);
    for (const value of [{ ...spec, version: 9007199254740993 }, { ...spec, version: "01" },
      { ...spec, variant_id: "00000000-0000-4000-8000-000000000099" },
      { ...spec, sale_unit: "TON" }, { ...spec, net_content_amount: null },
      { ...spec, net_content_unit: "EACH" }, { ...spec, net_content_amount: "1e3" },
      { ...spec, created_at: "2026-02-30T12:00:00+00:00" }])
      expect(validMeasurementSpec(value, variantId)).toBe(false);
  });

  it("keeps conflicts explicit, treats lost writes as uncertain and does not retry", async () => {
    const conflict = vi.fn().mockResolvedValue(Response.json({ error: { code: "measurement_conflict" } }, { status: 409 }));
    await expect(request("/catalog/variants/id/measurement-spec", { method: "POST" }, conflict))
      .rejects.toMatchObject({ code: "measurement_conflict", uncertain: false });
    expect(conflict).toHaveBeenCalledTimes(1);
    const lost = vi.fn().mockRejectedValue(new TypeError("fictional network loss"));
    await expect(request("/catalog/variants/id/measurement-spec", { method: "POST" }, lost))
      .rejects.toMatchObject({ uncertain: true });
    expect(lost).toHaveBeenCalledTimes(1);
    expect(messageFor(new OperatorError("measurement_conflict"))).toContain("versión de medición cambió");
  });

  it("uses the existing permit and read-generation barriers", async () => {
    const { ReadGeneration } = await import("./operations-api");
    const permits = new OperationPermitCoordinator();
    const token = permits.acquire("measurement", variantId);
    expect(token).not.toBeNull();
    expect(permits.acquire("stock", variantId)).toBeNull();
    const stream = new ReadGeneration();
    const old = stream.begin();
    stream.invalidate();
    expect(stream.accepts(old)).toBe(false);
    expect(permits.release(token!)).toBe(true);
    expect(permits.busy).toBe(false);
  });
});
