"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { formatPriceARS, messageFor, normalizePriceInput, OperatorError, post, Price, PriceHistory, ReadGeneration, request } from "@/lib/operations-api";
import { useOperator } from "./operations-shell";
import type { OperationPermit, PendingPriceConflict } from "./product-workspace";

function validRecordedAt(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?(Z|[+-](\d{2}):(\d{2}))$/.exec(value);
  if (!match) return false;
  const [, yearText, monthText, dayText, hourText, minuteText, secondText, offset, offsetHourText, offsetMinuteText] = match;
  const year = Number(yearText), month = Number(monthText), day = Number(dayText);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (year < 1 || month < 1 || month > 12 || day < 1 || day > days[month - 1] ||
      Number(hourText) > 23 || Number(minuteText) > 59 || Number(secondText) > 59) return false;
  if (offset !== "Z" && (Number(offsetHourText) > 14 || Number(offsetMinuteText) > 59 ||
      Number(offsetHourText) === 14 && Number(offsetMinuteText) !== 0)) return false;
  return Number.isFinite(Date.parse(value));
}

function validPrice(value: unknown, variantId: string): value is Price {
  if (!value || typeof value !== "object") return false;
  const price = value as Partial<Price>;
  return typeof price.revision_id === "string" && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(price.revision_id)
    && price.variant_id === variantId && typeof price.sequence === "string" && /^[1-9][0-9]{0,18}$/.test(price.sequence)
    && typeof price.amount === "string" && /^(?:0|[1-9][0-9]{0,11})\.[0-9]{2}$/.test(price.amount)
    && price.currency === "ARS" && price.price_basis === "PER_VARIANT" && validRecordedAt(price.recorded_at);
}

export function ProductPricePanel({ variantId, locked, uncertain, onUncertain, pendingConflict, onConflict, onConflictResolved, acquire, release }: {
  variantId: string; locked: boolean; uncertain: boolean; onUncertain: () => void;
  pendingConflict: PendingPriceConflict | null;
  onConflict: (value: PendingPriceConflict) => void;
  onConflictResolved: () => void;
  acquire: (operation: OperationPermit["operation"]) => OperationPermit | null;
  release: (permit: OperationPermit) => void;
}) {
  const { capabilities } = useOperator();
  const canRead = capabilities.includes("catalog.read_variant_price");
  const canWrite = capabilities.includes("catalog.set_variant_price");
  const canHistory = capabilities.includes("catalog.read_variant_price_history");
  const [startedWithConflict] = useState(pendingConflict !== null);
  const [current, setCurrent] = useState<Price | null | undefined>(undefined);
  const [baseRevision, setBaseRevision] = useState<string | null>(pendingConflict?.baseRevision ?? null);
  const [draft, setDraft] = useState(pendingConflict?.draft ?? "");
  const [history, setHistory] = useState<Price[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loadingHistory, setLoadingHistory] = useState(canHistory);
  const [readError, setReadError] = useState("");
  const [historyError, setHistoryError] = useState("");
  const [writeError, setWriteError] = useState(pendingConflict ? messageFor(new OperatorError("price_conflict")) : "");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [conflict, setConflict] = useState(startedWithConflict);
  const [reviewed, setReviewed] = useState(false);
  const priceReads = useRef(new ReadGeneration());
  const historyReads = useRef(new ReadGeneration());
  const alive = useRef(false);
  const errorRef = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    const priceStream = priceReads.current;
    const historyStream = historyReads.current;
    alive.current = true;
    return () => { alive.current = false; priceStream.invalidate(); historyStream.invalidate(); };
  }, []);

  const loadCurrent = useCallback(async (signal?: AbortSignal, initial = false): Promise<boolean> => {
    const stream = priceReads.current;
    const token = stream.begin();
    const active = () => !signal?.aborted && stream.accepts(token) && alive.current;
    try {
      const result = await request<{ price: Price | null }>(`/catalog/variants/${encodeURIComponent(variantId)}/price`, { signal });
      if (!active()) return false;
      if (!result || !(result.price === null || validPrice(result.price, variantId))) throw new OperatorError("invalid_response");
      setCurrent(result.price);
      if (initial && !startedWithConflict) { setBaseRevision(result.price?.revision_id ?? null); setDraft(result.price?.amount ?? ""); }
      setReadError("");
      return true;
    } catch (failure) {
      if (active()) setReadError(messageFor(failure));
      return false;
    }
  }, [variantId, startedWithConflict]);

  const loadHistory = useCallback(async (after?: string, signal?: AbortSignal): Promise<void> => {
    const stream = historyReads.current;
    const token = stream.begin();
    const active = () => !signal?.aborted && stream.accepts(token) && alive.current;
    setLoadingHistory(true);
    try {
      const query = after ? `?before_sequence=${encodeURIComponent(after)}` : "";
      const result = await request<PriceHistory>(`/catalog/variants/${encodeURIComponent(variantId)}/price/revisions${query}`, { signal });
      if (!active()) return;
      if (!result || !Array.isArray(result.revisions) || !result.revisions.every((item) => validPrice(item, variantId)) ||
          !(result.next_cursor === null || typeof result.next_cursor === "string" && /^[1-9][0-9]{0,18}$/.test(result.next_cursor))) {
        throw new OperatorError("invalid_response");
      }
      setHistory((previous) => after ? [...previous, ...result.revisions.filter((item) => !previous.some((old) => old.revision_id === item.revision_id))] : result.revisions);
      setCursor(result.next_cursor); setHistoryError("");
    } catch (failure) {
      if (active()) setHistoryError(messageFor(failure));
    } finally { if (active()) setLoadingHistory(false); }
  }, [variantId]);

  useEffect(() => {
    if (!canRead || !variantId) return;
    const controller = new AbortController();
    const stream = priceReads.current;
    void Promise.resolve().then(() => { if (!controller.signal.aborted) return loadCurrent(controller.signal, true); });
    return () => { controller.abort(); stream.invalidate(); };
  }, [canRead, variantId, loadCurrent]);
  useEffect(() => {
    if (!canHistory || !variantId) return;
    const controller = new AbortController();
    const stream = historyReads.current;
    void Promise.resolve().then(() => { if (!controller.signal.aborted) return loadHistory(undefined, controller.signal); });
    return () => { controller.abort(); stream.invalidate(); };
  }, [canHistory, variantId, loadHistory]);

  const needsReview = current !== undefined && baseRevision !== (current?.revision_id ?? null);
  async function refresh() {
    setReadError("");
    if (await loadCurrent()) setReviewed(true);
  }
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canRead || !canWrite || current === undefined || locked || busy || uncertain || conflict || needsReview) return;
    const amount = normalizePriceInput(draft);
    if (!amount) { setWriteError("Ingresá un importe ARS positivo, sin separador de miles y con hasta dos decimales."); return; }
    const permit = acquire("price");
    if (!permit) return;
    priceReads.current.invalidate(); historyReads.current.invalidate();
    setLoadingHistory(false);
    setBusy(true); setWriteError(""); setNotice(""); setHistoryError("");
    try {
      const response = await post<{ price: Price }>(`/catalog/variants/${encodeURIComponent(variantId)}/price`,
        { amount, currency: "ARS", expected_revision_id: baseRevision });
      if (!response || !validPrice(response.price, variantId)) throw new OperatorError("invalid_response", undefined, true);
      if (!alive.current) return;
      setCurrent(response.price); setBaseRevision(response.price.revision_id); setDraft(response.price.amount);
      setConflict(false); setReviewed(false); setNotice("Precio confirmado para esta variante.");
      if (canHistory) {
        setHistory([]); setCursor(null);
        await loadHistory();
      }
    } catch (failure) {
      if (!alive.current) return;
      setWriteError(messageFor(failure));
      if (failure instanceof OperatorError && failure.code === "price_conflict") {
        onConflict({ draft, baseRevision });
        setConflict(true); setReviewed(false);
      }
      if (failure instanceof OperatorError && failure.uncertain) onUncertain();
      window.setTimeout(() => errorRef.current?.focus(), 0);
    } finally { if (alive.current) setBusy(false); release(permit); }
  }

  return <section className="panel price-panel" aria-labelledby="price-title">
    <div className="section-heading"><div><p className="eyebrow">Valor administrativo</p><h2 id="price-title">Precio de la variante</h2></div></div>
    <p className="muted">ARS por variante (PER_VARIANT). No expresa precio por peso, impuestos ni disponibilidad para vender.</p>
    {!canRead ? <p>Sin permiso para consultar precios.</p> : <>
      {current === undefined ? !readError && <p role="status">Consultando precio…</p> : current === null ? <p className="empty-state">Sin precio asignado</p> : <>
        <p className="stock-count">{formatPriceARS(current.amount)}</p>
        <p className="caption">Revisión {current.sequence}. Registrada: {new Intl.DateTimeFormat("es-AR", { dateStyle: "short", timeStyle: "short", timeZone: "UTC" }).format(new Date(current.recorded_at))} UTC.</p>
      </>}
      {readError && <p role="alert" className="notice">{readError}</p>}
      <button type="button" className="button-quiet" disabled={busy || locked} onClick={() => void refresh()}>Consultar precio vigente</button>
      {uncertain && <p role="status" className="notice">Resultado incierto para esta variante. La consulta permite revisar el estado, pero no confirma quién realizó la escritura. No reenvíes el precio desde este panel.</p>}
      {(conflict || needsReview) && <div className="notice" role="status"><p>La revisión vigente cambió. Tu importe escrito se conserva. Consultá el precio actual y decidí si querés editar sobre esa revisión.</p>
        {reviewed && <button type="button" disabled={locked || uncertain} onClick={() => { setBaseRevision(current?.revision_id ?? null); setConflict(false); setReviewed(false); setWriteError(""); onConflictResolved(); }}>Editar sobre esta revisión</button>}
      </div>}
      {writeError && <p role="alert" className="error-box" tabIndex={-1} ref={errorRef}>{writeError}</p>}
      {notice && <p role="status" className="success-box">{notice}</p>}
      {!canWrite ? <p>Sin permiso para asignar o actualizar precios.</p> : <form onSubmit={(event) => void save(event)} aria-busy={busy}>
        <fieldset disabled={busy || locked || uncertain || current === undefined || conflict || needsReview}>
          <legend>{current === null ? "Asignar primer precio" : "Actualizar precio"}</legend>
          <label htmlFor="variant-price-amount">Importe ARS por variante</label>
          <input id="variant-price-amount" type="text" inputMode="decimal" value={draft} onChange={(event) => setDraft(event.target.value)} maxLength={32} required />
          <p className="caption">Sin separador de miles. Usá coma o punto para los centavos.</p>
          <button className="button-primary" type="submit">{busy ? "Guardando precio…" : "Guardar precio"}</button>
        </fieldset>
      </form>}
    </>}
    <div className="price-history"><h3>Historial de revisiones</h3>
      {!canHistory ? <p>Sin permiso para consultar revisiones anteriores.</p> : <>
        <button type="button" className="button-quiet" disabled={locked || loadingHistory} onClick={() => void loadHistory()}>Consultar historial</button>
        {historyError && <p role="alert" className="notice">{historyError}</p>}
        {loadingHistory && <p role="status">Consultando revisiones…</p>}
        {!loadingHistory && history.length === 0 && !historyError && <p className="empty-state">Todavía no hay revisiones.</p>}
        {history.length > 0 && <div className="table-wrap"><table><caption className="visually-hidden">Revisiones de precio en orden descendente. Fechas en UTC.</caption>
          <thead><tr><th>Revisión</th><th>Importe</th><th>Registro UTC</th></tr></thead>
          <tbody>{history.map((item) => <tr key={item.revision_id}><td data-label="Revisión">{item.sequence}</td><td data-label="Importe">{formatPriceARS(item.amount)}</td><td data-label="Registro UTC">{new Intl.DateTimeFormat("es-AR", { dateStyle: "short", timeStyle: "short", timeZone: "UTC" }).format(new Date(item.recorded_at))}</td></tr>)}</tbody>
        </table></div>}
        {cursor && <button type="button" disabled={locked || loadingHistory} onClick={() => void loadHistory(cursor)}>Ver revisiones anteriores</button>}
      </>}
    </div>
  </section>;
}
