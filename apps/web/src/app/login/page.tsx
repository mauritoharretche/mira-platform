"use client";

import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useRef, useState } from "react";
import { messageFor, signIn } from "@/lib/operations-api";

export default function LoginPage() {
  const router = useRouter();
  const errorRef = useRef<HTMLParagraphElement>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  useEffect(() => {
    const query = new URLSearchParams(window.location.search);
    const text = query.has("logout_uncertain") ? "No pudimos confirmar el cierre en el servidor. No dejes una sesión abierta en un equipo compartido." : query.has("expired") ? "La sesión terminó. Ingresá nuevamente." : "";
    const timer = window.setTimeout(() => setNotice(text), 0);
    return () => window.clearTimeout(timer);
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    setPending(true);
    setError("");
    try {
      await signIn(String(data.get("username") ?? ""), String(data.get("password") ?? ""));
      form.reset();
      router.replace("/operations");
    } catch (failure) {
      setError(messageFor(failure));
      window.setTimeout(() => errorRef.current?.focus(), 0);
    } finally { setPending(false); }
  }

  return <main className="login-page">
    <section className="login-card" aria-labelledby="login-title">
      <p className="eyebrow">MIRA · Acceso interno</p>
      <h1 id="login-title">Tu espacio de operaciones</h1>
      <p className="muted">Ingresá con tu cuenta habilitada para administrar productos y su recepción inicial.</p>
      {notice && <p className="notice" role="status">{notice}</p>}
      {error && <p className="error-box" role="alert" tabIndex={-1} ref={errorRef}>{error}</p>}
      <form onSubmit={(event) => void submit(event)} aria-busy={pending}>
        <label htmlFor="username">Usuario</label>
        <input id="username" name="username" autoComplete="username" maxLength={150} required disabled={pending} />
        <label htmlFor="password">Contraseña</label>
        <input id="password" name="password" type="password" autoComplete="current-password" maxLength={1024} required disabled={pending} />
        <button className="button-primary" disabled={pending}>{pending ? "Ingresando…" : "Iniciar sesión"}</button>
      </form>
      <p className="caption">Demostración local. El acceso no concede permisos de operación por sí solo.</p>
    </section>
  </main>;
}
