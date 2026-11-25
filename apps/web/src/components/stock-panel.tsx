"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { History, messageFor, Movement, OperatorError, post, Product, request, Stock, validPositiveInteger, validStock } from "@/lib/operations-api";
import { validQuantity, canonicalQuantity } from "@/lib/quantities";
import { useOperator } from "./operations-shell";
import type { OperationPermit, StockAdjustmentState } from "./product-workspace";

type Observation = { variantId: string; stock: Stock | null | undefined; history: History | null; consistent: boolean };

export function StockPanel({ product, variantId, locked, outcome: currentOutcome, onOutcome, adjustment, onAdjustment, acquire, release }: {
  product: Product; variantId: string; locked: boolean; outcome: "confirmed" | "uncertain" | "conflict" | null;
  onOutcome: (kind: "confirmed" | "uncertain" | "conflict") => void;
  adjustment: StockAdjustmentState; onAdjustment: (state: StockAdjustmentState) => void;
  acquire: (operation: OperationPermit["operation"]) => OperationPermit | null;
  release: (permit: OperationPermit) => void;
}) {
  const session = useOperator();
  const [observation, setObservation] = useState<Observation | null>(null);
  const [error, setError] = useState("");
  const [readError, setReadError] = useState("");
  const [busy, setBusy] = useState(false);
  const [more, setMore] = useState<Movement[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [unit, setUnit] = useState("");
  const [quantity, setQuantity] = useState("");
  const [reviewing, setReviewing] = useState(false);
  const generation = useRef(0);
  const errorRef = useRef<HTMLParagraphElement>(null);
  const canReadProduct = session.capabilities.includes("catalog.read_product");
  const canStock = session.capabilities.includes("inventory.read_stock");
  const canHistory = session.capabilities.includes("inventory.read_stock_movements");
  const canReceive = session.capabilities.includes("inventory.receive_stock");
  const canAdjust = session.capabilities.includes("inventory.adjust_stock");

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
    setReadError("");
  }, [canStock, canHistory]);

  useEffect(() => {
    if (!variantId) return;
    const controller = new AbortController();
    load(variantId, controller.signal).catch((failure) => { if (!controller.signal.aborted) setReadError(messageFor(failure)); });
    return () => { controller.abort(); generation.current += 1; };
  }, [variantId, load]);

  async function receive(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (locked || busy || !canReceive || currentOutcome !== null) return;
    if ((unit !== "EACH" && unit !== "G") || !validQuantity(quantity, unit, true)) { setError("Seleccioná EACH o G e ingresá una cantidad exacta positiva dentro del límite técnico."); return; }
    const permit = acquire("stock");
    if (!permit) return;
    setBusy(true); setError(""); setReadError("");
    try {
      try {
        await post(`/inventory/variants/${encodeURIComponent(variantId)}/initial-receipt`, { quantity, unit, expected_version: null });
      } catch (failure) {
        setError(messageFor(failure));
        if (failure instanceof OperatorError && (failure.code === "stock_conflict" || failure.uncertain)) {
          onOutcome(failure.uncertain ? "uncertain" : "conflict");
          setObservation(null); setMore([]); setCursor(null);
          try { await load(variantId); } catch (readFailure) { setReadError(messageFor(readFailure)); }
        }
        window.setTimeout(() => errorRef.current?.focus(), 0);
        return;
      }
      // A confirmed POST remains confirmed even when the following GET fails.
      onOutcome("confirmed");
      setObservation(null); setMore([]); setCursor(null);
      setQuantity("");
      try { await load(variantId); } catch (failure) { setReadError(messageFor(failure)); }
    } finally { setBusy(false); release(permit); }
  }

  function reviewAdjustment() {
    if (!validQuantity(adjustment.quantity, observation?.stock?.unit ?? "EACH") || !adjustment.reason.trim() || adjustment.reason.trim().length > 255 ||
        !validPositiveInteger(adjustment.expectedVersion ?? "")) {
      setError("Ingresá una cantidad exacta no negativa en la unidad registrada, un motivo y una versión de stock vigente.");
      return;
    }
    setError("");
    setReviewing(true);
  }

  async function submitAdjustment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const version = adjustment.expectedVersion;
    if (!reviewing || locked || busy || !canAdjust || adjustment.outcome === "conflict" || adjustment.outcome === "uncertain" ||
        !validQuantity(adjustment.quantity, observation?.stock?.unit ?? "EACH") || !adjustment.reason.trim() || !version || !validPositiveInteger(version)) return;
    const permit = acquire("stock");
    if (!permit) return;
    generation.current += 1; // A GET started before this write cannot overwrite its result.
    setBusy(true); setReviewing(false); setError(""); setReadError("");
    try {
      try {
        const result = await post<{ stock: Stock }>(`/inventory/variants/${encodeURIComponent(variantId)}/adjustments`, {
          new_on_hand: adjustment.quantity, unit: observation?.stock?.unit ?? "EACH", expected_version: version, reason: adjustment.reason.trim(),
        });
        if (!validStock(result?.stock, variantId) || result.stock.on_hand !== canonicalQuantity(adjustment.quantity, result.stock.unit) ||
            (result.stock.version !== version && BigInt(result.stock.version) !== BigInt(version) + 1n)) {
          throw new OperatorError("invalid_response", undefined, true);
        }
        onAdjustment({ quantity: "", reason: "", expectedVersion: result.stock.version,
          outcome: result.stock.version === version ? "noop" : "confirmed", priorUncertain: adjustment.priorUncertain });
      } catch (failure) {
        setError(messageFor(failure));
        const conflict = failure instanceof OperatorError && (failure.code === "stock_version_conflict" || failure.code === "stock_not_initialized");
        const uncertain = failure instanceof OperatorError ? failure.uncertain : true;
        if (conflict || uncertain) onAdjustment({ ...adjustment, outcome: conflict ? "conflict" : "uncertain",
          priorUncertain: adjustment.priorUncertain || uncertain });
        window.setTimeout(() => errorRef.current?.focus(), 0);
      }
      setObservation(null); setMore([]); setCursor(null);
      try { await load(variantId); } catch (failure) { setReadError(messageFor(failure)); }
    } finally { setBusy(false); release(permit); }
  }

  async function nextPage() {
    if (!cursor || busy) return;
    const permit = acquire("history");
    if (!permit) return;
    setBusy(true);
    const current = generation.current;
    try {
      const result = await request<History>(`/inventory/variants/${encodeURIComponent(variantId)}/movements?before_sequence=${cursor}`);
      if (current !== generation.current) return;
      setMore((previous) => [...previous, ...result.movements]); setCursor(result.next_cursor);
    } catch (failure) { setReadError(messageFor(failure)); } finally { setBusy(false); release(permit); }
  }

  const current = observation?.variantId === variantId ? observation : null;
  const variant = product?.variants.find((item) => item.id === variantId);
  if (!canReadProduct) return <section className="panel"><h1>Acceso restringido</h1><p role="alert">Tu cuenta no tiene permiso para consultar productos.</p></section>;
  return <>
    {error && <p className="error-box" role="alert" tabIndex={-1} ref={errorRef}>{error}</p>}
    {currentOutcome === "confirmed" && <p className="success-box" role="status">Recepción inicial registrada. No la repitas. Consultá el saldo y su movimiento cuando la lectura esté disponible.</p>}
    {currentOutcome === "uncertain" && <p className="notice" role="status">El resultado de la recepción es incierto. Consultá el saldo y los movimientos, pero esa consulta no confirma quién realizó el cambio. No repitas la recepción.</p>}
    {adjustment.outcome === "confirmed" && <p className="success-box" role="status">Ajuste físico confirmado. Consultá el saldo y el movimiento; no repitas esa operación.</p>}
    {adjustment.outcome === "noop" && <p className="success-box" role="status">Recuento sin cambios. No se creó un movimiento ni un registro Audit nuevo.</p>}
    {adjustment.outcome === "conflict" && <p className="notice" role="alert">El ajuste físico tiene un conflicto pendiente. El borrador y la versión original se conservan. Consultar el saldo no habilita otra escritura.</p>}
    {adjustment.outcome === "uncertain" && <p className="notice" role="alert">El resultado del ajuste físico es incierto. Consultar saldo y movimientos no confirma quién lo realizó. No reenvíes esta operación.</p>}
    {adjustment.priorUncertain && adjustment.outcome !== "uncertain" && <p className="notice" role="status">Un ajuste anterior tuvo resultado incierto. La consulta posterior no atribuye esa escritura.</p>}
    {readError && <p className="notice" role="alert">No pudimos actualizar la consulta: {readError} {currentOutcome === "confirmed" ? "La recepción sigue confirmada; no la repitas." : "Podés volver a consultar sin enviar otra recepción."}</p>}
    {product && <>
      <section className="panel identity-panel"><div><p className="eyebrow">Presentación</p><h2>Identidad de inventario</h2><p className="muted">Stock de esta variante; no implica disponibilidad para vender.</p></div>
        <div><p className="presentation-meta">SKU <strong>{variant?.sku}</strong>{variant?.barcode && <> · Código <strong>{variant.barcode}</strong></>}</p><details className="technical-details"><summary>Identificador de variante</summary><p className="identifier">{variant?.id}</p></details></div>
      </section>
      <div className="workspace-grid stock-grid"><section className="panel" aria-labelledby="stock-title"><p className="eyebrow">Saldo actual</p><h2 id="stock-title">Existencia física</h2>
        {!canStock ? <p role="alert">Sin permiso para consultar stock.</p> : !current ? <p role="status">{readError ? "Consulta de stock pendiente de actualización." : "Consultando stock…"}</p> : current.stock === null ? <><p className="empty-state">Sin recepción inicial</p><p className="muted">Esta variante aún no tiene una posición de stock.</p></> : <><p className="stock-count">{current.stock?.on_hand} <span>{current.stock?.unit}</span></p><p className="caption">Saldo físico; no calcula disponibilidad para venta.</p><details className="technical-details"><summary>Versión de stock</summary><p className="identifier">{current.stock?.version}</p></details></>}
        <button className="button-quiet" disabled={!variantId || busy || locked} onClick={() => { setReadError(""); void load(variantId).catch((failure) => setReadError(messageFor(failure))); }}>Actualizar consulta</button>
      </section><section className="panel" aria-labelledby="receipt-title"><p className="eyebrow">Operación</p><h2 id="receipt-title">Recepción inicial</h2>
        {!canReceive ? <p role="alert">Sin permiso para recibir stock.</p> : !canStock ? <p>Necesitás consultar el estado antes de iniciar una recepción.</p> : currentOutcome ? <p className="muted">{currentOutcome === "confirmed" ? "Esta recepción fue confirmada. Actualizá la consulta si aún no ves el saldo." : "Revisá el estado antes de cualquier nueva operación. Esta pantalla no reenvía la recepción."}</p> : current?.stock === null ? <form onSubmit={(event) => void receive(event)} aria-busy={busy}>
          <label htmlFor="inventory-unit">Unidad de inventario</label><select id="inventory-unit" value={unit} required onChange={(event) => setUnit(event.target.value)} disabled={busy || locked}><option value="">Seleccioná la unidad</option><option value="EACH">EACH — conteo de esta variante</option><option value="G">G — masa, precisión 0,001 g</option></select>
          <label htmlFor="quantity">Cantidad recibida</label><input id="quantity" value={quantity} onChange={(event) => setQuantity(event.target.value)} inputMode={unit === "G" ? "decimal" : "numeric"} maxLength={30} required disabled={busy || locked} />
          <p className="caption">Cantidad positiva exacta. EACH entero o G con hasta tres decimales; la unidad queda fija desde la primera recepción.</p><button className="button-primary" disabled={busy || locked}>{busy ? "Registrando…" : "Registrar recepción inicial"}</button>
        </form> : <p className="muted">{current?.stock ? "La recepción inicial ya está registrada. Las recepciones posteriores quedan fuera de esta pantalla." : "Esperando la consulta de stock."}</p>}
      </section></div>
      <section className="panel movement-panel" aria-labelledby="adjustment-title"><p className="eyebrow">Recuento</p><h2 id="adjustment-title">Ajustar existencia física</h2>
        <p className="muted">Registrá el conteo real de esta variante en su unidad contable. Este ajuste no registra ventas ni disponibilidad para vender.</p>
        {!canAdjust ? <p>Sin permiso para ajustar stock.</p> : !canStock ? <p>Necesitás permiso de lectura para verificar el saldo antes de ajustar.</p> : current?.stock === null ? <p>Primero debe registrarse la recepción inicial.</p> : <>
          {adjustment.outcome === "conflict" && <>
            <p>Versión del borrador: {adjustment.expectedVersion ?? "sin versión"}. Versión consultada: {current?.stock?.version ?? "pendiente"}.</p>
            <button type="button" className="button-quiet" disabled={locked || busy || !current?.stock || !current.consistent} onClick={() => {
              if (!current?.stock) return;
              onAdjustment({ ...adjustment, expectedVersion: current.stock.version, outcome: null }); setReviewing(false); setError("");
            }}>Editar sobre esta versión</button>
          </>}
          {adjustment.outcome === "uncertain" && <>
            <p>El borrador se conserva para conciliación. Revisá el saldo y los movimientos antes de decidir otra operación.</p>
            <button type="button" className="button-quiet" disabled={locked || busy || !canHistory || !current?.stock || !current.history || !current.consistent} onClick={() => {
              if (!current?.stock || !current.history || !current.consistent) return;
              onAdjustment({ quantity: "", reason: "", expectedVersion: current.stock.version, outcome: null, priorUncertain: true });
              setReviewing(false); setError("");
            }}>Revisé saldo y movimientos; iniciar un ajuste nuevo</button>
          </>}
          {adjustment.outcome !== "conflict" && adjustment.outcome !== "uncertain" && <form onSubmit={(event) => void submitAdjustment(event)} aria-busy={busy}>
            <label htmlFor="adjustment-quantity">Cantidad física contada ({current?.stock?.unit ?? "EACH"})</label>
            <input id="adjustment-quantity" value={adjustment.quantity} inputMode={current?.stock?.unit === "G" ? "decimal" : "numeric"} maxLength={30} required disabled={busy || locked || !current?.stock}
              onChange={(event) => { onAdjustment({ ...adjustment, quantity: event.target.value, expectedVersion: adjustment.expectedVersion ?? current?.stock?.version ?? null, outcome: null }); setReviewing(false); }} />
            <label htmlFor="adjustment-reason">Motivo del ajuste</label>
            <input id="adjustment-reason" value={adjustment.reason} maxLength={255} required disabled={busy || locked || !current?.stock}
              onChange={(event) => { onAdjustment({ ...adjustment, reason: event.target.value, expectedVersion: adjustment.expectedVersion ?? current?.stock?.version ?? null, outcome: null }); setReviewing(false); }} />
            <p className="caption">Versión esperada: {adjustment.expectedVersion ?? current?.stock?.version ?? "pendiente"}. El motivo quedará asociado al movimiento.</p>
            {!reviewing ? <button type="button" className="button-quiet" disabled={busy || locked || !current?.stock} onClick={reviewAdjustment}>Revisar ajuste</button> : <>
              <p role="status">Vas a registrar {adjustment.quantity} {current?.stock?.unit ?? "EACH"} como existencia física para {variant?.sku}, con motivo: {adjustment.reason.trim()}.</p>
              <button className="button-primary" disabled={busy || locked}>Confirmar ajuste físico</button>
            </>}
          </form>}
        </>}
      </section>
      <section className="panel movement-panel" aria-labelledby="history-title"><p className="eyebrow">Trazabilidad</p><h2 id="history-title">Historial de movimientos</h2>
        {!canHistory ? <p role="alert">Sin permiso para consultar movimientos.</p> : !current ? <p role="status">{readError ? "Consulta de movimientos pendiente de actualización." : "Consultando movimientos…"}</p> : <>
          {!current.consistent && <p className="notice" role="status">El stock cambió durante la consulta. Actualizá para comparar saldo y movimientos.</p>}
          {!current.history?.movements.length ? <p className="empty-state">Todavía no hay movimientos.</p> : <div className="table-wrap"><table><caption className="visually-hidden">Movimientos en orden de secuencia descendente. Fechas en UTC.</caption><thead><tr><th>Secuencia</th><th>Movimiento</th><th>Antes</th><th>Cambio</th><th>Después</th><th>Fecha UTC</th></tr></thead><tbody>
            {[...(current.history?.movements ?? []), ...more].map((movement) => <tr key={movement.movement_id}><td data-label="Secuencia">{movement.sequence}</td><td data-label="Movimiento">{movement.kind === "RECEIPT" ? "Recepción" : movement.kind === "CONSUMPTION" ? "Consumo de reserva" : movement.kind === "COUNT" ? "Conteo físico" : movement.kind === "WRITE_OFF" ? "Merma" : "Ajuste"}</td><td data-label="Antes">{movement.quantity_before}</td><td data-label="Cambio">{movement.delta}</td><td data-label="Después">{movement.quantity_after} {movement.unit}</td><td data-label="Fecha UTC">{new Intl.DateTimeFormat("es-AR", { dateStyle: "short", timeStyle: "short", timeZone: "UTC" }).format(new Date(movement.recorded_at))}</td></tr>)}
          </tbody></table></div>}
          {cursor && <button disabled={busy || locked} onClick={() => void nextPage()}>Ver movimientos anteriores</button>}
        </>}
      </section>
    </>}
  </>;
}
