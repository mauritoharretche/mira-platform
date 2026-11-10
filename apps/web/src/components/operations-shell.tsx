"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
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
  const pathname = usePathname();
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
        <div className="operator-header-inner">
          <Link className="brand" href="/operations/catalog" aria-label="MIRA · Ir al catálogo">MIRA<span>Operaciones</span></Link>
          <nav aria-label="Navegación principal">
            <Link href="/operations/catalog" aria-current={pathname === "/operations/catalog" ? "page" : pathname.startsWith("/operations/products/") ? "location" : undefined}>Catálogo</Link>
            <Link href="/operations" aria-current={pathname === "/operations" ? "page" : undefined}>Nuevo producto</Link>
          </nav>
          <p className="demo-marker">Demo aislada · Datos ficticios</p>
          <button className="button-quiet logout-button" onClick={() => void logout()} disabled={leaving}>Cerrar sesión</button>
        </div>
      </header>
      <main id="workspace" className="operator-page" tabIndex={-1}>{children}</main>
      <footer className="operator-footer">Entorno de demostración local · Datos ficticios</footer>
    </div>
  </OperatorContext>;
}
