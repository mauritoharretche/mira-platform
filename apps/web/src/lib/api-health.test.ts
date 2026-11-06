import { describe, expect, it, vi } from "vitest";

import { probeApiReadiness } from "./api-health";

describe("probeApiReadiness", () => {
  it("returns healthy for an upstream success", async () => {
    const fetchImplementation = vi.fn().mockResolvedValue(new Response(null, { status: 200 }));

    await expect(probeApiReadiness(fetchImplementation, "http://api.test")).resolves.toEqual({
      status: "healthy",
      httpStatus: 200,
    });
  });

  it("maps an upstream 503 to an unavailable response", async () => {
    const fetchImplementation = vi.fn().mockResolvedValue(new Response(null, { status: 503 }));

    await expect(probeApiReadiness(fetchImplementation, "http://api.test")).resolves.toEqual({
      status: "unavailable",
      httpStatus: 503,
    });
  });

  it("maps an abort timeout to an unavailable response", async () => {
    const fetchImplementation = vi.fn().mockRejectedValue(new DOMException("timeout", "AbortError"));

    await expect(probeApiReadiness(fetchImplementation, "http://api.test")).resolves.toEqual({
      status: "unavailable",
      httpStatus: 503,
    });
  });

  it("maps a network error to an unavailable response", async () => {
    const fetchImplementation = vi.fn().mockRejectedValue(new TypeError("network failure"));

    await expect(probeApiReadiness(fetchImplementation, "http://api.test")).resolves.toEqual({
      status: "unavailable",
      httpStatus: 503,
    });
  });
});
