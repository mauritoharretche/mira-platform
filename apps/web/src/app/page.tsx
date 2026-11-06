"use client";

import { useCallback, useEffect, useState } from "react";

type Status = "loading" | "healthy" | "unavailable";

export default function HomePage() {
  const [status, setStatus] = useState<Status>("loading");

  const checkApi = useCallback(async () => {
    setStatus("loading");
    try {
      const response = await fetch("/api/technical/health", { cache: "no-store" });
      const body: unknown = await response.json();
      setStatus(
        response.ok && typeof body === "object" && body !== null && "status" in body && body.status === "healthy"
          ? "healthy"
          : "unavailable",
      );
    } catch {
      setStatus("unavailable");
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void checkApi();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [checkApi]);

  return (
    <main>
      <h1>MIRA Platform</h1>
      <p aria-live="polite">
        {status === "loading" && "Checking API readiness…"}
        {status === "healthy" && "API readiness confirmed."}
        {status === "unavailable" && "API readiness is unavailable."}
      </p>
      <button type="button" onClick={() => void checkApi()} disabled={status === "loading"}>
        Retry
      </button>
    </main>
  );
}
