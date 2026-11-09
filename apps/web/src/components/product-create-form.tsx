"use client";

import { FormEvent, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { messageFor, OperatorError, post, Product } from "@/lib/operations-api";
import { useOperator } from "./operations-shell";

export function ProductCreateForm() {
  const router = useRouter();
  const session = useOperator();
  const [pending, setPending] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [error, setError] = useState("");
  const [fields, setFields] = useState<Record<string, string[]>>({});
  const errorRef = useRef<HTMLParagraphElement>(null);
  const allowed = session.capabilities.includes("catalog.create_product_with_initial_variant");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending || uncertain || !allowed) return;
    const form = new FormData(event.currentTarget);
    const values = Object.fromEntries(["product_name", "variant_label", "sku", "barcode"].map((name) => [name, String(form.get(name) ?? "")]));
    const invalid = Object.fromEntries(["product_name", "variant_label", "sku"].filter((name) => !values[name].trim()).map((name) => [name, ["Completá este campo."]]));
    setFields(invalid);
    setError("");
    if (Object.keys(invalid).length) { setError("Completá los campos obligatorios."); return; }
    setPending(true);
    try {
      const product = await post<Product>("/catalog/products", values);
      router.push(`/operations/products/${encodeURIComponent(product.id)}`);
    } catch (failure) {
      setError(messageFor(failure));
      if (failure instanceof OperatorError) { setFields(failure.fields ?? {}); setUncertain(failure.uncertain); }
      window.setTimeout(() => errorRef.current?.focus(), 0);
    } finally { setPending(false); }
  }

  return <section className="panel" aria-labelledby="create-title">
    <h2 id="create-title">Crear producto</h2><p className="muted">Primero la identidad del producto y su presentación. Después, su recepción inicial.</p>
    {!allowed && <p className="notice" role="alert">Tu cuenta no tiene permiso para crear productos.</p>}
    {error && <p className="error-box" role="alert" tabIndex={-1} ref={errorRef}>{error}</p>}
    {uncertain && <p className="notice">No recibimos el UUID y no podemos reconciliar esta creación desde aquí. Conservá el SKU y solicitá verificación antes de volver a enviar.</p>}
    <form onSubmit={(event) => void submit(event)} aria-busy={pending}>
      <fieldset disabled={!allowed || pending || uncertain}>
        {([
          ["product_name", "Nombre del producto", 255, true],
          ["variant_label", "Presentación", 255, true],
          ["sku", "SKU", 128, true],
          ["barcode", "Código de barras (opcional)", 255, false],
        ] as const).map(([name, label, maxLength, required]) => <div key={name} className="field">
          <label htmlFor={name}>{label}</label><input id={name} name={name} maxLength={maxLength} required={required} autoComplete="off" aria-invalid={!!fields[name]} aria-describedby={fields[name] ? `${name}-error` : undefined} />
          {fields[name] && <p id={`${name}-error`} className="field-error">Revisá este campo: {fields[name].join(" ")}</p>}
        </div>)}
        <button className="button-primary" disabled={!allowed || pending || uncertain}>{pending ? "Creando…" : "Crear producto y presentación"}</button>
      </fieldset>
    </form>
  </section>;
}
