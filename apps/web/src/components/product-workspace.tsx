"use client";

import { useEffect, useRef, useState } from "react";
import { messageFor, Product, ReadGeneration, request } from "@/lib/operations-api";
import { useOperator } from "./operations-shell";
import { ProductClassificationPanel } from "./product-classification-panel";
import { StockPanel } from "./stock-panel";

export function ProductWorkspace({ productId }: { productId: string }) {
  const session = useOperator();
  const [product, setProduct] = useState<Product | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const reads = useRef(new ReadGeneration());
  const canRead = session.capabilities.includes("catalog.read_product");
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
  if (!canRead) return <section className="panel"><h1>Acceso restringido</h1><p role="alert">Tu cuenta no tiene permiso para consultar productos.</p></section>;
  const current = product?.id === productId ? product : null;
  return <>
    <div className="page-heading product-heading"><div><p className="eyebrow">Ficha de producto</p><h1>{current?.name ?? "Consultando producto…"}</h1></div>
      <button className="button-quiet" onClick={() => { reads.current.invalidate(); setError(""); setAttempt((value) => value + 1); }}>Actualizar detalle</button>
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
      <StockPanel key={productId} product={current} />
    </>}
  </>;
}
