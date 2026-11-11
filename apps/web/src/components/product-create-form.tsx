"use client";

import { FormEvent, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { messageFor, OperatorError, post, Product } from "@/lib/operations-api";
import { useOperator } from "./operations-shell";

export function ProductCreateForm() {
  const router = useRouter();
  const session = useOperator();
  const [pending, setPending] = useState(false);
  const [complete, setComplete] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [error, setError] = useState("");
  const [fields, setFields] = useState<Record<string, string[]>>({});
  const errorRef = useRef<HTMLParagraphElement>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const submitting = useRef(false);
  const allowed = session.capabilities.includes("catalog.create_product_with_initial_variant");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting.current || pending || complete || uncertain || !allowed) return;
    const form = new FormData(event.currentTarget);
    const values = Object.fromEntries(["product_name", "variant_label", "sku", "barcode"].map((name) => [name, String(form.get(name) ?? "")]));
    const invalid = Object.fromEntries(["product_name", "variant_label", "sku"].filter((name) => !values[name].trim()).map((name) => [name, ["Completá este campo."]]));
    setFields(invalid);
    setError("");
    if (Object.keys(invalid).length) {
      setError("Completá los campos obligatorios.");
      window.setTimeout(() => formRef.current?.querySelector<HTMLInputElement>("[aria-invalid='true']")?.focus(), 0);
      return;
    }
    submitting.current = true; setPending(true);
    try {
      const product = await post<Product>("/catalog/products", values);
      if (!product || typeof product.id !== "string" || !/^[0-9a-f-]{36}$/i.test(product.id)) throw new OperatorError("invalid_response", undefined, true);
      setComplete(true);
      router.push(`/operations/products/${encodeURIComponent(product.id)}`);
    } catch (failure) {
      setError(messageFor(failure));
      if (failure instanceof OperatorError) { setFields(failure.fields ?? {}); setUncertain(failure.uncertain); }
      window.setTimeout(() => errorRef.current?.focus(), 0);
    } finally { submitting.current = false; setPending(false); }
  }

  return <section className="panel create-product-panel" aria-labelledby="create-title">
    <p className="eyebrow">Alta de catálogo</p><h2 id="create-title">Crear producto</h2><p className="muted">Registrá la identidad y una primera presentación. Luego podrás consultar su ficha y registrar stock inicial.</p>
    {!allowed && <p className="notice" role="alert">Tu cuenta no tiene permiso para crear productos.</p>}
    {error && <p className="error-box" role="alert" tabIndex={-1} ref={errorRef}>{error}</p>}
    {uncertain && <p className="notice">No recibimos el UUID y no podemos reconciliar esta creación desde aquí. Conservá el SKU y solicitá verificación antes de volver a enviar.</p>}
    {complete && <p className="success-box" role="status">Producto creado. Abriendo su ficha…</p>}
    <form ref={formRef} noValidate onSubmit={(event) => void submit(event)} aria-busy={pending}>
      <fieldset disabled={!allowed || pending || uncertain || complete} className="create-fields">
        {([
          ["product_name", "Nombre del producto", 255, true],
          ["variant_label", "Presentación", 255, true],
          ["sku", "SKU", 128, true],
          ["barcode", "Código de barras (opcional)", 255, false],
        ] as const).map(([name, label, maxLength, required]) => <div key={name} className="field">
          <label htmlFor={name}>{label}</label><input id={name} name={name} maxLength={maxLength} required={required} autoComplete="off" aria-invalid={!!fields[name]} aria-describedby={fields[name] ? `${name}-error` : undefined} onInput={() => { if (fields[name]) setFields((old) => { const updated = { ...old }; delete updated[name]; return updated; }); }} />
          {fields[name] && <p id={`${name}-error`} className="field-error">Revisá este campo: {fields[name].join(" ")}</p>}
        </div>)}
        <button className="button-primary" disabled={!allowed || pending || uncertain || complete}>{pending ? "Creando…" : "Crear producto y presentación"}</button>
      </fieldset>
    </form>
  </section>;
}
