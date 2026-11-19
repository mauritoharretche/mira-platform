"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { MeasurementSpec, MeasurementUnit, messageFor, normalizeMeasurementAmount, OperatorError, post, ReadGeneration, request, validMeasurementSpec } from "@/lib/operations-api";
import { useOperator } from "./operations-shell";
import type { MeasurementDraft, OperationPermit, PendingMeasurementConflict } from "./product-workspace";

const emptyDraft: MeasurementDraft = { saleUnit: "", amount: "", netUnit: "", baseVersion: null };

export function ProductMeasurementPanel({ variantId, locked, savedDraft, pendingConflict, uncertain,
  onDraft, onConfirmed, onConflict, onConflictResolved, onUncertain, acquire, release }: {
  variantId: string; locked: boolean; savedDraft: MeasurementDraft | null;
  pendingConflict: PendingMeasurementConflict | null; uncertain: boolean;
  onDraft: (draft: MeasurementDraft) => void; onConfirmed: () => void;
  onConflict: (value: PendingMeasurementConflict) => void; onConflictResolved: () => void;
  onUncertain: () => void;
  acquire: (operation: OperationPermit["operation"]) => OperationPermit | null;
  release: (permit: OperationPermit) => void;
}) {
  const { capabilities } = useOperator();
  const canRead = capabilities.includes("catalog.read_variant_measurement_spec");
  const canWrite = capabilities.includes("catalog.set_variant_measurement_spec");
  const initialDraft = pendingConflict?.draft ?? savedDraft ?? emptyDraft;
  const [draft, setDraft] = useState<MeasurementDraft>(initialDraft);
  const [current, setCurrent] = useState<MeasurementSpec | null | undefined>(undefined);
  const [conflict, setConflict] = useState(pendingConflict !== null);
  const [reviewed, setReviewed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [readError, setReadError] = useState("");
  const [writeError, setWriteError] = useState(pendingConflict ? messageFor(new OperatorError("measurement_conflict")) : "");
  const [notice, setNotice] = useState("");
  const reads = useRef(new ReadGeneration());
  const alive = useRef(false);
  const errorRef = useRef<HTMLParagraphElement>(null);
  const initiallyEdited = useRef(savedDraft !== null || pendingConflict !== null);

  useEffect(() => {
    const stream = reads.current;
    alive.current = true;
    return () => { alive.current = false; stream.invalidate(); };
  }, []);

  const load = useCallback(async (signal?: AbortSignal, initial = false): Promise<boolean> => {
    const stream = reads.current;
    const token = stream.begin();
    const active = () => !signal?.aborted && stream.accepts(token) && alive.current;
    try {
      const response = await request<{ spec: MeasurementSpec | null }>(`/catalog/variants/${encodeURIComponent(variantId)}/measurement-spec`, { signal });
      if (!active()) return false;
      if (!response || !(response.spec === null || validMeasurementSpec(response.spec, variantId))) throw new OperatorError("invalid_response");
      setCurrent(response.spec); setReadError("");
      if (initial && !initiallyEdited.current) {
        setDraft({ saleUnit: response.spec?.sale_unit ?? "", amount: response.spec?.net_content_amount ?? "",
          netUnit: response.spec?.net_content_unit ?? "", baseVersion: response.spec?.version ?? null });
      }
      return true;
    } catch (error) {
      if (active()) setReadError(messageFor(error));
      return false;
    }
  }, [variantId]);

  useEffect(() => {
    if (!canRead || !variantId) return;
    const controller = new AbortController();
    const stream = reads.current;
    void Promise.resolve().then(() => { if (!controller.signal.aborted) return load(controller.signal, true); });
    return () => { controller.abort(); stream.invalidate(); };
  }, [canRead, variantId, load]);

  function edit(next: MeasurementDraft) { setDraft(next); onDraft(next); }
  const needsReview = current !== undefined && draft.baseVersion !== (current?.version ?? null);

  async function refresh() {
    if (busy || locked) return;
    setReadError("");
    if (await load()) setReviewed(true);
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canRead || !canWrite || current === undefined || busy || locked || uncertain || conflict || needsReview) return;
    const amount = draft.amount === "" ? null : normalizeMeasurementAmount(draft.amount);
    if (draft.amount !== "" && amount === null || (amount === null) !== (draft.netUnit === "")) {
      setWriteError("Indicá un contenido positivo exacto de hasta seis decimales y su unidad G o KG, o dejá ambos vacíos.");
      return;
    }
    const permit = acquire("measurement");
    if (!permit) return;
    reads.current.invalidate();
    setBusy(true); setWriteError(""); setNotice("");
    try {
      const response = await post<{ spec: MeasurementSpec | null }>(`/catalog/variants/${encodeURIComponent(variantId)}/measurement-spec`, {
        sale_unit: draft.saleUnit || null, net_content_amount: amount, net_content_unit: draft.netUnit || null,
        expected_version: draft.baseVersion,
      });
      if (!response || !(response.spec === null && draft.saleUnit === "" && amount === null ||
          validMeasurementSpec(response.spec, variantId))) throw new OperatorError("invalid_response", undefined, true);
      if (!alive.current) return;
      setCurrent(response.spec);
      setDraft((previous) => ({ ...previous, baseVersion: response.spec?.version ?? null }));
      onConfirmed(); setConflict(false); setReviewed(false);
      setNotice("Declaración de medición confirmada para esta variante.");
    } catch (error) {
      if (!alive.current) return;
      setWriteError(messageFor(error));
      if (error instanceof OperatorError && error.code === "measurement_conflict") {
        onConflict({ draft }); setConflict(true); setReviewed(false);
      }
      if (error instanceof OperatorError && error.uncertain) onUncertain();
      window.setTimeout(() => errorRef.current?.focus(), 0);
    } finally { if (alive.current) setBusy(false); release(permit); }
  }

  return <section className="panel measurement-panel" aria-labelledby="measurement-title">
    <div className="section-heading"><div><p className="eyebrow">Presentación comercial</p><h2 id="measurement-title">Medición de la variante</h2></div></div>
    <p className="muted">Declaración administrativa. No convierte unidades, cambia el precio ni modifica el stock EACH.</p>
    {!canRead ? <p>Sin permiso para consultar mediciones. La edición web requiere lectura para conocer la versión vigente.</p> : <>
      {current === undefined ? !readError && <p role="status">Consultando medición…</p> : current === null ?
        <p className="empty-state">Sin especificación declarada</p> : <>
          <p className="presentation-meta">Unidad comercial: <strong>{current.sale_unit ?? "Desconocida"}</strong>. Contenido neto: <strong>{current.net_content_amount === null ? "Desconocido" : `${current.net_content_amount} ${current.net_content_unit}`}</strong>.</p>
          <p className="caption">Versión {current.version}{current.sale_unit === null && current.net_content_amount === null ? " · Declaración existente con campos desconocidos" : ""}</p>
        </>}
      {readError && <p role="alert" className="notice">{readError}</p>}
      <button type="button" className="button-quiet" disabled={busy || locked} onClick={() => void refresh()}>Consultar medición vigente</button>
      {uncertain && <p role="status" className="notice">Resultado incierto para esta variante. Consultar la declaración no confirma quién realizó la escritura. No la reenvíes desde este panel.</p>}
      {(conflict || needsReview) && <div className="notice" role="status"><p>La versión vigente cambió. Tu borrador se conserva. Consultá la declaración actual y decidí si querés editar sobre esta versión.</p>
        {reviewed && <button type="button" disabled={locked || uncertain} onClick={() => {
          const next = { ...draft, baseVersion: current?.version ?? null };
          edit(next); setConflict(false); setReviewed(false); setWriteError(""); onConflictResolved();
        }}>Editar sobre esta versión</button>}
      </div>}
      {writeError && <p role="alert" className="error-box" tabIndex={-1} ref={errorRef}>{writeError}</p>}
      {notice && <p role="status" className="success-box">{notice}</p>}
      {!canWrite ? <p>Sin permiso para editar mediciones.</p> : <form onSubmit={(event) => void save(event)} aria-busy={busy}>
        <fieldset disabled={busy || locked || uncertain || current === undefined || conflict || needsReview}>
          <legend>Editar declaración</legend>
          <label htmlFor="measurement-sale-unit">Unidad comercial</label>
          <select id="measurement-sale-unit" value={draft.saleUnit} onChange={(event) => edit({ ...draft, saleUnit: event.target.value as MeasurementUnit | "" })}>
            <option value="">Desconocida</option><option value="EACH">EACH</option><option value="G">G</option><option value="KG">KG</option>
          </select>
          <label htmlFor="measurement-net-amount">Contenido neto</label>
          <input id="measurement-net-amount" type="text" inputMode="decimal" value={draft.amount} onChange={(event) => edit({ ...draft, amount: event.target.value })} placeholder="Desconocido" />
          <label htmlFor="measurement-net-unit">Unidad de contenido</label>
          <select id="measurement-net-unit" value={draft.netUnit} onChange={(event) => edit({ ...draft, netUnit: event.target.value as "G" | "KG" | "" })}>
            <option value="">Desconocida</option><option value="G">G</option><option value="KG">KG</option>
          </select>
          <p className="caption">Usá punto decimal, sin conversión automática. Dejá contenido y unidad vacíos para declararlos desconocidos.</p>
          <button className="button-primary" type="submit">{busy ? "Guardando medición…" : "Guardar medición"}</button>
        </fieldset>
      </form>}
    </>}
  </section>;
}
