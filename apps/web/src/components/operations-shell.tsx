"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useState } from "react";
import { clearOperatorState, messageFor, request, Session, signOut } from "@/lib/operations-api";

const OperatorContext = createContext<Session | null>(null);

export function useOperator() {
  const session = useContext(OperatorContext);
  if (!session) throw new Error("Operator context is unavailable");
  return session;
}

export function OperationsShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [session, setSession] = useState<Session | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [leaving, setLeaving] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    const ended = () => {
      clearOperatorState();
      setSession(null);
      router.replace("/login?expired=1");
    };
    window.addEventListener("mira-session-ended", ended);
    request<Session>("/auth/session", { signal: controller.signal }).then((result) => {
      if (controller.signal.aborted) return;
      if (!result.authenticated) return ended();
      setSession(result);
      setError("");
    }).catch((failure) => { if (!controller.signal.aborted) setError(messageFor(failure)); });
    return () => { controller.abort(); window.removeEventListener("mira-session-ended", ended); };
  }, [router, attempt]);

  async function logout() {
    setLeaving(true);
    setSession(null);
    try { await signOut(); router.replace("/login"); } catch {
      clearOperatorState();
      router.replace("/login?logout_uncertain=1");
    }
  }

  if (!session || leaving) return <main className="operator-page"><p role="status">{leaving ? "Cerrando sesión…" : "Verificando sesión…"}</p>{error && <><p role="alert">{error}</p><button onClick={() => setAttempt((value) => value + 1)}>Reintentar consulta</button></>}</main>;
  return <OperatorContext value={session}>
    <div className="operator-app">
      <a className="skip-link" href="#workspace">Ir al contenido</a>
      <header className="operator-header">
        <Link className="brand" href="/operations">MIRA<span>Operaciones</span></Link>
        <nav aria-label="Navegación principal"><Link href="/operations">Nuevo producto</Link><button className="button-quiet" onClick={() => void logout()} disabled={leaving}>Cerrar sesión</button></nav>
      </header>
      <main id="workspace" className="operator-page">{children}</main>
      <footer className="operator-footer">Entorno de demostración local · Datos ficticios</footer>
    </div>
  </OperatorContext>;
}
