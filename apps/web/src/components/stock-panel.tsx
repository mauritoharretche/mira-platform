"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { History, messageFor, Movement, OperatorError, post, Product, request, Stock, validPositiveInteger } from "@/lib/operations-api";
import { useOperator } from "./operations-shell";

type Observation = { variantId: string; stock: Stock | null | undefined; history: History | null; consistent: boolean };

export function StockPanel({ productId }: { productId: string }) {
  const session = useOperator();
  const [product, setProduct] = useState<Product | null>(null);
  const [variantId, setVariantId] = useState("");
  const [observation, setObservation] = useState<Observation | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [more, setMore] = useState<Movement[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [unit, setUnit] = useState("");
  const [quantity, setQuantity] = useState("");
  const generation = useRef(0);
  const errorRef = useRef<HTMLParagraphElement>(null);
  const canReadProduct = session.capabilities.includes("catalog.read_product");
  const canStock = session.capabilities.includes("inventory.read_stock");
  const canHistory = session.capabilities.includes("inventory.read_stock_movements");
  const canReceive = session.capabilities.includes("inventory.receive_stock");

  useEffect(() => {
    if (!canReadProduct) return;
    const controller = new AbortController();
    request<Product>(`/catalog/products/${encodeURIComponent(productId)}`, { signal: controller.signal }).then((result) => {
      if (!controller.signal.aborted) { setProduct(result); setVariantId(result.variants[0]?.id ?? ""); }
    }).catch((failure) => { if (!controller.signal.aborted) setError(messageFor(failure)); });
    return () => controller.abort();
  }, [productId, canReadProduct]);

  const load = useCallback(async (id: string, signal?: AbortSignal) => {
    const current = ++generation.current;
    const path = `/inventory/variants/${encodeURIComponent(id)}`;
    const read = () => Promise.all([
      canStock ? request<{ stock: Stock | null }>(`${path}/stock`, { signal }) : Promise.resolve(undefined),
      canHistory ? request<History>(`${path}/movements`, { signal }) : Promise.resolve(null),
    ]);
    let [stock, history] = await read();
    const matches = () => !canStock || !canHistory || (stock?.stock?.version ?? null) === (history?.movements[0]?.sequence ?? null);
    if (!matches()) [stock, history] = await read(); // one bounded read reconciliation, never a write retry
    if (signal?.aborted || generation.current !== current) return;
    setObservation({ variantId: id, stock: stock?.stock, history, consistent: matches() });
    setMore([]);
    setCursor(history?.next_cursor ?? null);
  }, [canStock, canHistory]);

  useEffect(() => {
    if (!variantId) return;
    const controller = new AbortController();
    load(variantId, controller.signal).catch((failure) => { if (!controller.signal.aborted) setError(messageFor(failure)); });
    return () => { controller.abort(); generation.current += 1; };
  }, [variantId, load]);

  async function receive(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !canReceive) return;
    if (unit !== "EACH" || !validPositiveInteger(quantity)) { setError("Seleccioná EACH e ingresá una cantidad entera positiva dentro del límite técnico."); return; }
    setBusy(true); setError(""); setNotice("");
    try {
      await post(`/inventory/variants/${encodeURIComponent(variantId)}/initial-receipt`, { quantity, unit, expected_version: null });
      await load(variantId);
      setNotice("Recepción inicial registrada. Consultá el saldo y su movimiento.");
    } catch (failure) {
      setError(messageFor(failure));
      if (failure instanceof OperatorError && (failure.code === "stock_conflict" || failure.uncertain)) {
        try { await load(variantId); } catch { /* retain the uncertain/conflict outcome, never claim success */ }
      }
      window.setTimeout(() => errorRef.current?.focus(), 0);
    } finally { setBusy(false); }
  }

  async function nextPage() {
    if (!cursor || busy) return;
    setBusy(true);
    const current = generation.current;
    try {
      const result = await request<History>(`/inventory/variants/${encodeURIComponent(variantId)}/movements?before_sequence=${cursor}`);
      if (current !== generation.current) return;
      setMore((previous) => [...previous, ...result.movements]); setCursor(result.next_cursor);
    } catch (failure) { setError(messageFor(failure)); } finally { setBusy(false); }
  }

  const current = observation?.variantId === variantId ? observation : null;
  const variant = product?.variants.find((item) => item.id === variantId);
  if (!canReadProduct) return <section className="panel"><h1>Acceso restringido</h1><p role="alert">Tu cuenta no tiene permiso para consultar productos.</p></section>;
  return <>
    <div className="page-heading"><p className="eyebrow">Producto y recepción</p><h1>{product?.name ?? "Consultando producto…"}</h1><p className="identifier">ID: {productId}</p></div>
    {error && <p className="error-box" role="alert" tabIndex={-1} ref={errorRef}>{error}</p>}
    {notice && <p className="success-box" role="status">{notice}</p>}
    {product && <>
      <section className="panel identity-panel"><div><span className="badge">{product.is_active ? "Activo administrativamente" : "Inactivo administrativamente"}</span><p className="caption">Registrar existencia no publica ni habilita ventas.</p></div>
        <div><label htmlFor="variant">Presentación / SKU</label><select id="variant" value={variantId} onChange={(event) => { setVariantId(event.target.value); setError(""); setNotice(""); setUnit(""); setQuantity(""); }} disabled={busy}>
          {product.variants.map((item) => <option key={item.id} value={item.id}>{item.label} · {item.sku}</option>)}
        </select><p className="identifier">SKU: {variant?.sku}</p><p className="identifier">Variante: {variant?.id}</p>{variant?.barcode && <p>Código: {variant.barcode}</p>}</div>
      </section>
      <div className="workspace-grid"><section className="panel" aria-labelledby="stock-title"><h2 id="stock-title">Existencia física</h2>
        {!canStock ? <p role="alert">Sin permiso para consultar stock.</p> : !current ? <p role="status">Consultando stock…</p> : current.stock === null ? <><p className="empty-state">Sin recepción inicial</p><p className="muted">Esta variante aún no tiene una posición de stock.</p></> : <><p className="stock-count">{current.stock?.on_hand} <span>EACH</span></p><p className="caption">Versión {current.stock?.version} · Saldo físico; no calcula disponibilidad para venta.</p></>}
        <button className="button-quiet" disabled={!variantId || busy} onClick={() => { setError(""); void load(variantId).catch((failure) => setError(messageFor(failure))); }}>Actualizar consulta</button>
      </section><section className="panel" aria-labelledby="receipt-title"><h2 id="receipt-title">Recepción inicial</h2>
        {!canReceive ? <p role="alert">Sin permiso para recibir stock.</p> : !canStock ? <p>Necesitás consultar el estado antes de iniciar una recepción.</p> : current?.stock === null ? <form onSubmit={(event) => void receive(event)} aria-busy={busy}>
          <label htmlFor="inventory-unit">Unidad de inventario</label><select id="inventory-unit" value={unit} required onChange={(event) => setUnit(event.target.value)} disabled={busy}><option value="">Seleccioná la unidad</option><option value="EACH">EACH — conteo de esta variante</option></select>
          <label htmlFor="quantity">Cantidad recibida</label><input id="quantity" value={quantity} onChange={(event) => setQuantity(event.target.value)} inputMode="numeric" maxLength={19} required disabled={busy} />
          <p className="caption">Conteo entero positivo. La unidad se declara explícitamente.</p><button className="button-primary" disabled={busy}>{busy ? "Registrando…" : "Registrar recepción inicial"}</button>
        </form> : <p className="muted">{current?.stock ? "La recepción inicial ya está registrada. Las recepciones posteriores quedan fuera de esta pantalla." : "Esperando la consulta de stock."}</p>}
      </section></div>
      <section className="panel movement-panel" aria-labelledby="history-title"><h2 id="history-title">Historial de movimientos</h2>
        {!canHistory ? <p role="alert">Sin permiso para consultar movimientos.</p> : !current ? <p role="status">Consultando movimientos…</p> : <>
          {!current.consistent && <p className="notice" role="status">El stock cambió durante la consulta. Actualizá para comparar saldo y movimientos.</p>}
          {!current.history?.movements.length ? <p className="empty-state">Todavía no hay movimientos.</p> : <div className="table-wrap"><table><caption className="visually-hidden">Movimientos en orden de secuencia descendente. Fechas en UTC.</caption><thead><tr><th>Secuencia</th><th>Movimiento</th><th>Antes</th><th>Cambio</th><th>Después</th><th>Fecha UTC</th></tr></thead><tbody>
            {[...(current.history?.movements ?? []), ...more].map((movement) => <tr key={movement.movement_id}><td data-label="Secuencia">{movement.sequence}</td><td data-label="Movimiento">{movement.kind === "RECEIPT" ? "Recepción" : "Ajuste"}</td><td data-label="Antes">{movement.quantity_before}</td><td data-label="Cambio">{movement.delta}</td><td data-label="Después">{movement.quantity_after} EACH</td><td data-label="Fecha UTC">{new Intl.DateTimeFormat("es-AR", { dateStyle: "short", timeStyle: "short", timeZone: "UTC" }).format(new Date(movement.recorded_at))}</td></tr>)}
          </tbody></table></div>}
          {cursor && <button disabled={busy} onClick={() => void nextPage()}>Ver movimientos anteriores</button>}
        </>}
      </section>
    </>}
  </>;
}
