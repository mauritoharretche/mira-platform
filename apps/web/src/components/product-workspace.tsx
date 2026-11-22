"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { messageFor, OperationPermitCoordinator, type OperationPermit, Product, ReadGeneration, request, Variant } from "@/lib/operations-api";
import { useOperator } from "./operations-shell";
import { ProductClassificationPanel } from "./product-classification-panel";
import { ProductVariantPanel } from "./product-variant-panel";
import { ProductPricePanel } from "./product-price-panel";
import { ProductMeasurementPanel } from "./product-measurement-panel";
import { StockPanel } from "./stock-panel";

export { OperationPermitCoordinator };
export type { OperationPermit };

export type PendingPriceConflict = { draft: string; baseRevision: string | null };
export type MeasurementDraft = { saleUnit: "" | "EACH" | "G" | "KG"; amount: string;
  netUnit: "" | "G" | "KG"; baseVersion: string | null };
export type PendingMeasurementConflict = { draft: MeasurementDraft };
export type StockAdjustmentState = { quantity: string; reason: string; expectedVersion: string | null;
  outcome: "conflict" | "uncertain" | "confirmed" | "noop" | null; priorUncertain: boolean };

export function ProductWorkspace({ productId, returnHref }: { productId: string; returnHref: string }) {
  const session = useOperator();
  const [product, setProduct] = useState<Product | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [selected, setSelected] = useState("");
  const [locked, setLocked] = useState(false);
  const permits = useRef(new OperationPermitCoordinator());
  const [stockOutcomes, setStockOutcomes] = useState<Record<string, "confirmed" | "uncertain" | "conflict">>({});
  const [stockAdjustments, setStockAdjustments] = useState<Record<string, StockAdjustmentState>>({});
  const [priceUncertain, setPriceUncertain] = useState<Record<string, boolean>>({});
  const [priceConflicts, setPriceConflicts] = useState<Record<string, PendingPriceConflict>>({});
  const [measurementDrafts, setMeasurementDrafts] = useState<Record<string, MeasurementDraft>>({});
  const [measurementConflicts, setMeasurementConflicts] = useState<Record<string, PendingMeasurementConflict>>({});
  const [measurementUncertain, setMeasurementUncertain] = useState<Record<string, boolean>>({});
  const reads = useRef(new ReadGeneration());
  const acquire = (operation: OperationPermit["operation"]): OperationPermit | null => {
    const permit = permits.current.acquire(operation, productId);
    if (!permit) return null;
    if (operation === "classification" || operation === "variant") reads.current.invalidate();
    setLocked(true);
    return permit;
  };
  const release = (permit: OperationPermit) => {
    if (!permits.current.release(permit)) return;
    setLocked(false);
  };
  const canRead = session.capabilities.includes("catalog.read_product");
  useEffect(() => {
    if (window.location.hash === "#product-title") document.getElementById("product-title")?.focus();
  }, [productId]); // Explicit navigation only; detail refresh does not move focus.
  useEffect(() => {
    if (!canRead) return;
    const controller = new AbortController();
    const stream = reads.current;
    const token = stream.begin();
    request<Product>(`/catalog/products/${encodeURIComponent(productId)}`, { signal: controller.signal }).then((value) => {
      if (!controller.signal.aborted && stream.accepts(token) && value.id === productId) { setProduct(value); setError(""); }
    }).catch((failure) => { if (!controller.signal.aborted && stream.accepts(token)) setError(messageFor(failure)); });
    return () => { controller.abort(); stream.invalidate(); };
  }, [productId, attempt, canRead]);
  const back = <Link className="back-to-catalog" href={`${returnHref}#catalog-products-title`}>Volver al catálogo</Link>;
  if (!canRead) return <>{back}<section className="panel"><h1 id="product-title" tabIndex={-1}>Acceso restringido</h1><p role="alert">Tu cuenta no tiene permiso para consultar productos.</p></section></>;
  const current = product?.id === productId ? product : null;
  const variantId = current?.variants.some((item) => item.id === selected) ? selected : current?.variants[0]?.id ?? "";
  return <div className="product-workspace">
    {back}
    <div className="page-heading product-heading"><div><p className="eyebrow">Ficha de producto</p><h1 id="product-title" tabIndex={-1}>{current?.name ?? "Consultando producto…"}</h1></div>
      <button className="button-quiet" disabled={locked} onClick={() => { if (permits.current.busy) return; reads.current.invalidate(); setError(""); setAttempt((value) => value + 1); }}>Actualizar detalle</button>
    </div>
    <details className="technical-details"><summary>Identificador técnico del producto</summary><p className="identifier">{productId}</p></details>
    {error && <p role="alert" className="error-box">{error}</p>}
    {!current && !error && <p role="status">Consultando producto…</p>}
    {current && <>
      <ProductClassificationPanel key={`classification:${productId}`} product={current} locked={locked} acquire={acquire} release={release} onChanged={(change) => {
        // Invalidate a concurrent older GET before applying the command result.
        reads.current.invalidate();
        setProduct((previous) => previous?.id === change.id ? { ...previous, ...change } : previous);
      }} />
      <ProductVariantPanel key={`variants:${productId}`} product={current} variantId={variantId} locked={locked}
        acquire={acquire} release={release} onSelect={(id) => { if (!permits.current.busy) setSelected(id); }}
        onCreated={(variant: Variant) => {
          reads.current.invalidate();
          setProduct((previous) => previous?.id === productId ? { ...previous,
            variants: [...previous.variants.filter((item) => item.id !== variant.id), variant].sort((a, b) => a.id < b.id ? -1 : a.id > b.id ? 1 : 0),
          } : previous);
          setSelected(variant.id);
        }} />
      <section className="variant-attention" aria-label="Operaciones pendientes en otras variantes">
        {Object.entries(priceConflicts).filter(([id]) => id !== variantId).map(([id]) =>
          <p key={`price-conflict:${id}`} role="status" className="notice">El precio de {current.variants.find((item) => item.id === id)?.sku ?? "otra variante"} tiene un conflicto pendiente. El borrador se conserva; volvé a esa variante para consultar la revisión y decidir cómo continuar.</p>)}
        {Object.entries(priceUncertain).filter(([id, uncertain]) => id !== variantId && uncertain).map(([id]) =>
          <p key={`price-warning:${id}`} role="status" className="notice">El precio de {current.variants.find((item) => item.id === id)?.sku ?? "otra variante"} requiere revisión. Cambiar de variante no confirma ni reenvía esa escritura.</p>)}
        {Object.entries(measurementConflicts).filter(([id]) => id !== variantId).map(([id]) =>
          <p key={`measurement-conflict:${id}`} role="status" className="notice">La medición de {current.variants.find((item) => item.id === id)?.sku ?? "otra variante"} tiene un conflicto pendiente. El borrador se conserva; volvé a esa variante para consultar la versión y decidir cómo continuar.</p>)}
        {Object.entries(measurementUncertain).filter(([id, uncertain]) => id !== variantId && uncertain).map(([id]) =>
          <p key={`measurement-warning:${id}`} role="status" className="notice">La medición de {current.variants.find((item) => item.id === id)?.sku ?? "otra variante"} requiere revisión. Cambiar de variante no confirma ni reenvía esa escritura.</p>)}
        {Object.entries(stockOutcomes).filter(([id, outcome]) => id !== variantId && outcome !== "confirmed").map(([id, outcome]) =>
          <p key={`stock-warning:${id}`} role="status" className="notice">Una recepción de {current.variants.find((item) => item.id === id)?.sku ?? "otra variante"} {outcome === "conflict" ? "tiene un conflicto y" : "tiene un resultado incierto y"} requiere revisión. Cambiar de selección no confirma ni reenvía esa escritura.</p>)}
        {Object.entries(stockAdjustments).filter(([id, state]) => id !== variantId && (state.outcome === "conflict" || state.outcome === "uncertain" || state.priorUncertain)).map(([id, state]) =>
          <p key={`adjustment-warning:${id}`} role="status" className="notice">Un ajuste físico de {current.variants.find((item) => item.id === id)?.sku ?? "otra variante"} {state.outcome === "conflict" ? "tiene un conflicto pendiente" : "tiene un resultado incierto pendiente de conciliación"}. Cambiar de selección no confirma ni reenvía esa escritura.</p>)}
      </section>
      {!variantId && <p role="status" className="panel empty-state">No hay una variante disponible para consultar precio, medición o inventario.</p>}
      {variantId && <>
      <ProductPricePanel key={`price:${productId}:${variantId}`} variantId={variantId} locked={locked}
        uncertain={priceUncertain[variantId] ?? false} onUncertain={() => setPriceUncertain((previous) => ({ ...previous, [variantId]: true }))}
        pendingConflict={priceConflicts[variantId] ?? null}
        onConflict={(value) => setPriceConflicts((previous) => ({ ...previous, [variantId]: value }))}
        onConflictResolved={() => setPriceConflicts((previous) => {
          if (!(variantId in previous)) return previous;
          const updated = { ...previous };
          delete updated[variantId];
          return updated;
        })}
        acquire={acquire} release={release} />
      <ProductMeasurementPanel key={`measurement:${productId}:${variantId}`} variantId={variantId} locked={locked}
        savedDraft={measurementDrafts[variantId] ?? null} onDraft={(draft) => setMeasurementDrafts((previous) => ({ ...previous, [variantId]: draft }))}
        onConfirmed={() => setMeasurementDrafts((previous) => { const next = { ...previous }; delete next[variantId]; return next; })}
        pendingConflict={measurementConflicts[variantId] ?? null}
        onConflict={(value) => setMeasurementConflicts((previous) => ({ ...previous, [variantId]: value }))}
        onConflictResolved={() => setMeasurementConflicts((previous) => { const next = { ...previous }; delete next[variantId]; return next; })}
        uncertain={measurementUncertain[variantId] ?? false}
        onUncertain={() => setMeasurementUncertain((previous) => ({ ...previous, [variantId]: true }))}
        acquire={acquire} release={release} />
      <StockPanel key={`stock:${productId}:${variantId}`} product={current} variantId={variantId} locked={locked}
        outcome={stockOutcomes[variantId] ?? null} acquire={acquire} release={release}
        onOutcome={(kind) => setStockOutcomes((previous) => ({ ...previous, [variantId]: kind }))}
        adjustment={stockAdjustments[variantId] ?? { quantity: "", reason: "", expectedVersion: null, outcome: null, priorUncertain: false }}
        onAdjustment={(next) => setStockAdjustments((previous) => ({ ...previous, [variantId]: next }))} />
      </>}
    </>}
  </div>;
}
