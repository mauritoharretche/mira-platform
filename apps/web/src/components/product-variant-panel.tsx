"use client";

import { FormEvent, useRef, useState } from "react";
import { messageFor, OperatorError, post, Product, Variant } from "@/lib/operations-api";
import { useOperator } from "./operations-shell";

export function ProductVariantPanel({ product, variantId, locked, onSelect, onBusy, onCreated }: {
  product: Product; variantId: string; locked: boolean;
  onSelect: (id: string) => void; onBusy: (busy: boolean) => void; onCreated: (variant: Variant) => void;
}) {
  const session = useOperator();
  const [label, setLabel] = useState("");
  const [sku, setSku] = useState("");
  const [barcode, setBarcode] = useState("");
  const [busy, setBusy] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [error, setError] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const errorRef = useRef<HTMLParagraphElement>(null);
  const pending = useRef(false);
  const canAdd = session.capabilities.includes("catalog.add_variant_to_product");
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (locked || pending.current || uncertain || !canAdd) return;
    pending.current = true; onBusy(true); setBusy(true); setError(""); setConfirmed(false);
    try {
      const result = await post<Variant>(`/catalog/products/${encodeURIComponent(product.id)}/variants`, { label, sku, barcode: barcode || null });
      if (!result || typeof result.id !== "string" || !/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(result.id) ||
          typeof result.label !== "string" || typeof result.sku !== "string" || !(result.barcode === null || typeof result.barcode === "string")) {
        throw new OperatorError("invalid_response", undefined, true);
      }
      onCreated(result); setConfirmed(true); setLabel(""); setSku(""); setBarcode("");
    } catch (failure) {
      setError(messageFor(failure));
      if (failure instanceof OperatorError && failure.uncertain) setUncertain(true);
      window.setTimeout(() => errorRef.current?.focus(), 0);
    } finally { pending.current = false; setBusy(false); onBusy(false); }
  }
  return <section className="panel variant-panel" aria-labelledby="variants-title">
    <div className="section-heading"><div><p className="eyebrow">Presentaciones del producto</p><h2 id="variants-title">Variantes</h2></div></div>
    <label htmlFor="variant">Presentación / SKU</label>
    <select id="variant" value={variantId} onChange={(event) => onSelect(event.target.value)} disabled={locked || busy}>
      {product.variants.map((variant) => <option key={variant.id} value={variant.id}>{variant.label} · {variant.sku}</option>)}
    </select>
    <p className="muted">La selección identifica la variante consultada en inventario. No declara una unidad comercial ni un precio.</p>
    {confirmed && <p role="status" className="success-box">Variante creada y seleccionada.</p>}
    {error && <p role="alert" className="error-box" tabIndex={-1} ref={errorRef}>{error}</p>}
    {uncertain && <p role="status" className="notice">Resultado incierto del alta de variante. Actualizá el detalle para revisar las presentaciones. La lectura no confirma esta escritura. No reenvíes el alta desde esta pantalla.</p>}
    {!canAdd ? <p>Sin permiso para agregar variantes.</p> : <form onSubmit={(event) => void create(event)} aria-busy={busy}>
      <fieldset disabled={locked || busy || uncertain}>
        <legend>Nueva variante</legend>
        <label htmlFor="variant-label">Etiqueta de presentación</label><input id="variant-label" value={label} onChange={(event) => setLabel(event.target.value)} maxLength={255} required />
        <label htmlFor="variant-sku">SKU de la nueva variante</label><input id="variant-sku" value={sku} onChange={(event) => setSku(event.target.value)} maxLength={128} required />
        <label htmlFor="variant-barcode">Código de barras de la nueva variante (opcional)</label><input id="variant-barcode" value={barcode} onChange={(event) => setBarcode(event.target.value)} maxLength={255} />
        <button className="button-primary" type="submit">{busy ? "Creando variante…" : "Agregar variante"}</button>
      </fieldset>
    </form>}
  </section>;
}
