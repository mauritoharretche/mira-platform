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
