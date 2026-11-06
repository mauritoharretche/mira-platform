export type ApiHealthResult = {
  status: "healthy" | "unavailable";
  httpStatus: 200 | 503;
};

const TIMEOUT_MS = 3_000;

export async function probeApiReadiness(
  fetchImplementation: typeof fetch = fetch,
  baseUrl = process.env.MIRA_API_BASE_URL ?? "http://127.0.0.1:8000",
): Promise<ApiHealthResult> {
  try {
    const response = await fetchImplementation(`${baseUrl}/api/v1/health/ready/`, {
      cache: "no-store",
      headers: { Accept: "application/json" },
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });

    return response.ok
      ? { status: "healthy", httpStatus: 200 }
      : { status: "unavailable", httpStatus: 503 };
  } catch {
    return { status: "unavailable", httpStatus: 503 };
  }
}
