"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { messageFor, Product, ReadGeneration, request, Variant } from "@/lib/operations-api";
import { useOperator } from "./operations-shell";
import { ProductClassificationPanel } from "./product-classification-panel";
import { ProductVariantPanel } from "./product-variant-panel";
import { StockPanel } from "./stock-panel";

export function ProductWorkspace({ productId, returnHref }: { productId: string; returnHref: string }) {
  const session = useOperator();
  const [product, setProduct] = useState<Product | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [selected, setSelected] = useState("");
  const [locked, setLocked] = useState(false);
  const lock = useRef(false);
  const [stockOutcomes, setStockOutcomes] = useState<Record<string, "confirmed" | "uncertain" | "conflict">>({});
  const setBusy = (value: boolean) => { lock.current = value; setLocked(value); };
  const reads = useRef(new ReadGeneration());
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
  return <>
    {back}
    <div className="page-heading product-heading"><div><p className="eyebrow">Ficha de producto</p><h1 id="product-title" tabIndex={-1}>{current?.name ?? "Consultando producto…"}</h1></div>
      <button className="button-quiet" disabled={locked} onClick={() => { if (lock.current) return; reads.current.invalidate(); setError(""); setAttempt((value) => value + 1); }}>Actualizar detalle</button>
    </div>
    <details className="technical-details"><summary>Identificador técnico del producto</summary><p className="identifier">{productId}</p></details>
    {error && <p role="alert" className="error-box">{error}</p>}
    {!current && !error && <p role="status">Consultando producto…</p>}
    {current && <>
      <ProductClassificationPanel key={productId} product={current} onChanged={(change) => {
        // Invalidate a concurrent older GET before applying the command result.
        reads.current.invalidate();
        setProduct((previous) => previous?.id === change.id ? { ...previous, ...change } : previous);
      }} />
      <ProductVariantPanel key={productId} product={current} variantId={variantId} locked={locked}
        onBusy={setBusy} onSelect={(id) => { if (!lock.current) setSelected(id); }}
        onCreated={(variant: Variant) => {
          reads.current.invalidate();
          setProduct((previous) => previous?.id === productId ? { ...previous,
            variants: [...previous.variants.filter((item) => item.id !== variant.id), variant].sort((a, b) => a.id < b.id ? -1 : a.id > b.id ? 1 : 0),
          } : previous);
          setSelected(variant.id);
        }} />
      {Object.entries(stockOutcomes).filter(([id, outcome]) => id !== variantId && outcome !== "confirmed").map(([id]) =>
        <p key={id} role="status" className="notice">Una recepción de {current.variants.find((item) => item.id === id)?.sku ?? "otra variante"} requiere revisión. Cambiar de selección no confirma ni reenvía esa escritura.</p>)}
      <StockPanel key={`${productId}:${variantId}`} product={current} variantId={variantId} locked={locked}
        outcome={stockOutcomes[variantId] ?? null} onBusy={setBusy}
        onOutcome={(kind) => setStockOutcomes((previous) => ({ ...previous, [variantId]: kind }))} />
    </>}
  </>;
}
