"use client";

import { validQuantity } from "@/lib/quantities";
import { FormEvent, useEffect, useRef, useState } from "react";
import { useOperator } from "./operations-shell";
import { OperatorError, request, Stock } from "@/lib/operations-api";
import { procurement, VariantReference } from "@/lib/procurement-api";
import { Location, Lot, LotState, lots, validSignedQuantity } from "@/lib/lot-api";
import { PhysicalInventoryPanel } from "./physical-inventory-panel";
import { CommercialInventoryPanel } from "./commercial-inventory-panel";
import { reservationApi } from "@/lib/reservation-api";

const cap = {
  read: "inventory.read_stock_lots", location: "inventory.manage_stock_locations",
  lot: "inventory.manage_stock_lots", transfer: "inventory.transfer_stock_lots",
  correct: "inventory.correct_stock_lots",
};

function failureText(error: unknown) {
  if (error instanceof OperatorError && error.uncertain)
    return "Resultado incierto. No repitas la escritura; consultá el estado y decidí cómo continuar.";
  if (error instanceof OperatorError && error.code === "lot_conflict")
    return "El lote o stock cambió. Conservamos el formulario; consultá el estado vigente antes de decidir.";
  return "No pudimos completar la operación. Revisá los datos y el estado vigente.";
}

export function StockLotWorkspace() {
  const { capabilities } = useOperator();
  const can = (value: string) => capabilities.includes(value);
  const [variants, setVariants] = useState<VariantReference[]>([]);
  const [query, setQuery] = useState("");
  const [variantId, setVariantId] = useState("");
  const [locations, setLocations] = useState<Location[]>([]);
  const [state, setState] = useState<LotState | null>(null);
  const [stock, setStock] = useState<Stock | null>(null);
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [lotCode, setLotCode] = useState("");
  const [expiry, setExpiry] = useState("");
  const [selectedLot, setSelectedLot] = useState("");
  const [locationId, setLocationId] = useState("");
  const [destinationId, setDestinationId] = useState("");
  const [quantity, setQuantity] = useState("");
  const [reason, setReason] = useState("");
  const [lotStatus, setLotStatus] = useState<Lot["status"]>("ACTIVE");
  const [eligibilityReason,setEligibilityReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [blockKind, setBlockKind] = useState<"conflict" | "uncertain" | null>(null);
  const [blockedVariantId, setBlockedVariantId] = useState("");
  const [reviewed, setReviewed] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const generation = useRef(0);
  const lot = state?.lots.find((item) => item.id === selectedLot);
  const blocked = blockKind !== null;

  useEffect(() => {
    if (!can(cap.read)) return;
    void lots.locations().then((result) => setLocations(result.locations)).catch((failure) => setError(failureText(failure)));
  }, [capabilities]); // eslint-disable-line react-hooks/exhaustive-deps

  async function refresh(id = variantId) {
    if (!id) return;
    const token = ++generation.current;
    const [next, currentStock] = await Promise.all([
      lots.state(id),
      can("inventory.read_stock") ? request<{ stock: Stock | null }>(`/inventory/variants/${id}/stock`) : Promise.resolve({ stock: null }),
    ]);
    if (token === generation.current) {
      setState(next); setStock(currentStock.stock);
      if (id === blockedVariantId) setReviewed(true);
    }
  }

  async function write(action: () => Promise<unknown>, success: string) {
    if (busy || blocked) return;
    setBusy(true); setError(""); setNotice("");
    try {
      await action();
      setNotice(success);
    } catch (failure) {
      if (failure instanceof OperatorError && (failure.uncertain || failure.code === "lot_conflict")) {
        setBlockKind(failure.uncertain ? "uncertain" : "conflict"); setBlockedVariantId(variantId); setReviewed(false);
      }
      setError(failureText(failure));
      setBusy(false);
      return;
    }
    try {
      if (variantId) await refresh();
      setLocations((await lots.locations()).locations);
    } catch {
      setError("La escritura fue registrada, pero no pudimos actualizar la vista. Consultá el estado vigente.");
      setBlockKind("uncertain"); setBlockedVariantId(variantId); setReviewed(false);
    } finally { setBusy(false); }
  }

  async function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError("");
    try { setVariants((await procurement.variants(query)).variants); }
    catch (failure) { setError(failureText(failure)); }
  }

  if (!can(cap.read)) return <div className="lot-workspace"><h1>Inventario</h1><CommercialInventoryPanel /><PhysicalInventoryPanel variantId="" locations={[]} state={null} /><section className="panel"><h2>Inventario por lotes</h2><p>Tu cuenta no tiene permiso de lectura de lotes.</p></section></div>;
  return <div className="lot-workspace">
    <header className="page-heading"><p className="eyebrow">Inventario · operación piloto</p><h1>Inventario por lotes</h1>
      <p>Saldo físico en unidades contables explícitas. La secuencia FEFO orienta la revisión del operador; no indica disponibilidad ni vendibilidad.</p></header>
    <CommercialInventoryPanel onChanged={async()=>{if(variantId)await refresh();}} />
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {blockKind === "uncertain" && <p role="alert">Escrituras pausadas: la operación pudo haberse guardado. Consultá el estado y verificá su trazabilidad antes de volver a escribir. Una lectura no confirma una escritura incierta.</p>}
    {blockKind === "conflict" && <div role="alert"><p>La revisión cambió. Conservamos los campos ingresados. Consultá el estado vigente antes de decidir.</p>
      {reviewed && blockedVariantId === variantId && <button type="button" onClick={() => { setBlockKind(null); setReviewed(false); }}>Editar sobre la revisión consultada</button>}</div>}
    <section className="panel"><h2>Variante</h2>
      <form onSubmit={(event) => void search(event)}><label htmlFor="lot-variant-query">Buscar variante por nombre o SKU</label>
        <div className="lot-inline"><input id="lot-variant-query" value={query} onChange={(event) => setQuery(event.target.value)} />
          <button>Buscar variantes</button></div></form>
      <label htmlFor="lot-variant">Variante seleccionada</label><select id="lot-variant" value={variantId} onChange={(event) => {
        generation.current += 1; setVariantId(event.target.value); setState(null); setStock(null); setSelectedLot("");
        if (event.target.value) void refresh(event.target.value).catch((failure) => setError(failureText(failure)));
      }}><option value="">Seleccioná una variante</option>{variants.map((item) =>
        <option key={item.id} value={item.id}>{item.product_name} · {item.label} · SKU {item.sku}</option>)}</select>
      {variantId && <button type="button" onClick={() => void refresh().catch((failure) => setError(failureText(failure)))}>Consultar estado vigente</button>}
    </section>

    {state && <section className="panel" aria-labelledby="lot-summary"><h2 id="lot-summary">Existencia registrada</h2>
      <p><strong>{state.on_hand} {state.unit ?? "EACH"}</strong> en StockPosition · <strong>{state.unallocated} {state.unit ?? "EACH"} sin asignación</strong> a lote o ubicación.</p>
      <p className="muted">El stock previo y las recepciones sin asignación conservan su origen no especificado. No se les atribuye lote retroactivamente.</p>
      {state.lots.some((item) => item.expired || item.status !== "ACTIVE") &&
        <p role="status">Alertas físicas: {state.lots.filter((item) => item.expired).length} vencidos · {state.lots.filter((item) => item.status === "BLOCKED").length} bloqueados · {state.lots.filter((item) => item.status === "QUARANTINE").length} en cuarentena.</p>}
      <h3>Orden FEFO de lotes elegibles</h3><ol>{state.fefo.map((id) => {
        const item = state.lots.find((candidate) => candidate.id === id)!;
        return <li key={id}>{item.code} · vence {item.expires_on ?? "sin vencimiento"} · {item.quantity} {item.unit ?? "EACH"}</li>;
      })}</ol>{!state.fefo.length && <p>No hay lotes elegibles para sugerencia FEFO.</p>}
      {state.truncated && <p role="alert">Listado limitado. Revisá los lotes restantes antes de decidir.</p>}
      <h3>Lotes y ubicaciones</h3><div className="table-wrap"><table><thead><tr><th>Lote</th><th>Estado</th><th>Vence</th><th>Cantidad</th><th>Ubicaciones</th></tr></thead>
        <tbody>{state.lots.map((item) => <tr key={item.id}><td data-label="Lote">{item.code}</td><td data-label="Estado">{item.expired ? "VENCIDO" : item.status}</td>
          <td data-label="Vence">{item.expires_on ?? "No declarado"}</td><td data-label="EACH">{item.quantity} {item.unit ?? "EACH"}</td>
          <td data-label="Ubicaciones">{item.locations.map((entry) => `${entry.code}: ${entry.quantity}`).join(" · ") || "Sin asignación"}</td></tr>)}</tbody></table></div>
    </section>}

    <section className="panel"><h2>Ubicaciones</h2><ul>{locations.map((item) => <li key={item.id}>{item.code} · {item.name}
      <span> · elegibilidad comercial: {item.commercial_eligible ? "habilitada" : "no habilitada"}</span>
      {can("inventory.set_location_eligibility")&&<button type="button" disabled={busy||blocked||!eligibilityReason.trim()}
        onClick={()=>void write(()=>reservationApi.eligibility(item.id,!item.commercial_eligible,item.version,eligibilityReason),"Elegibilidad de ubicación registrada.")}>{item.commercial_eligible?"Deshabilitar":"Habilitar"} elegibilidad de {item.code}</button>}
    </li>)}</ul>
      {can("inventory.set_location_eligibility")&&<><label htmlFor="eligibility-reason">Motivo de elegibilidad de ubicación</label><input id="eligibility-reason" value={eligibilityReason} maxLength={255} onChange={(event)=>setEligibilityReason(event.target.value)}/></>}
      {can(cap.location) && <form onSubmit={(event) => { event.preventDefault(); void write(() => lots.createLocation(code, name), "Ubicación registrada."); }}>
        <label htmlFor="location-code">Código de ubicación</label><input id="location-code" value={code} maxLength={40} onChange={(event) => setCode(event.target.value)} required />
        <label htmlFor="location-name">Nombre de ubicación</label><input id="location-name" value={name} maxLength={120} onChange={(event) => setName(event.target.value)} required />
        <button className="button-primary" disabled={busy || blocked}>Crear ubicación</button></form>}
    </section>

    {variantId && can(cap.lot) && <section className="panel"><h2>Registrar lote</h2>
      <form onSubmit={(event) => { event.preventDefault(); void write(() => lots.createLot(variantId, lotCode, expiry || null), "Lote registrado."); }}>
        <label htmlFor="new-lot-code">Código de lote</label><input id="new-lot-code" value={lotCode} maxLength={80} onChange={(event) => setLotCode(event.target.value)} required />
        <label htmlFor="new-lot-expiry">Vencimiento, si corresponde</label><input id="new-lot-expiry" type="date" value={expiry} onChange={(event) => setExpiry(event.target.value)} />
        <button className="button-primary" disabled={busy || blocked}>Crear lote</button></form></section>}

    <PhysicalInventoryPanel variantId={variantId} locations={locations} state={state} onChanged={async()=>{if(variantId)await refresh();}} />
    {state && <section className="panel"><h2>Movimiento o corrección</h2>
      <label htmlFor="active-lot">Lote</label><select id="active-lot" value={selectedLot} onChange={(event) => setSelectedLot(event.target.value)}>
        <option value="">Seleccioná un lote</option>{state.lots.map((item) => <option key={item.id} value={item.id}>{item.code} · revisión {item.version}</option>)}</select>
      {lot && <><label htmlFor="lot-reason">Motivo documentado</label><input id="lot-reason" value={reason} maxLength={255} onChange={(event) => setReason(event.target.value)} />
        <label htmlFor="lot-location">Ubicación origen o a corregir</label><select id="lot-location" value={locationId} onChange={(event) => setLocationId(event.target.value)}>
          <option value="">Seleccioná ubicación</option>{locations.map((item) => <option key={item.id} value={item.id}>{item.code}</option>)}</select>
        <label htmlFor="lot-quantity">Cantidad {lot.unit ?? "EACH"} (signo para corrección)</label><input id="lot-quantity" value={quantity} inputMode="numeric" onChange={(event) => setQuantity(event.target.value)} />
        {can(cap.lot) && <><label htmlFor="lot-status">Estado del lote</label><select id="lot-status" value={lotStatus} onChange={(event) => setLotStatus(event.target.value as Lot["status"])}>
          <option value="ACTIVE">Activo</option><option value="BLOCKED">Bloqueado</option><option value="QUARANTINE">Cuarentena</option></select>
          <button type="button" disabled={busy || blocked || !reason.trim()} onClick={() => void write(() => lots.status(lot, lotStatus, reason), "Estado del lote registrado.")}>Guardar estado</button></>}
        {can(cap.transfer) && <><label htmlFor="lot-destination">Ubicación destino</label><select id="lot-destination" value={destinationId} onChange={(event) => setDestinationId(event.target.value)}>
          <option value="">Seleccioná ubicación</option>{locations.map((item) => <option key={item.id} value={item.id}>{item.code}</option>)}</select>
          <button type="button" disabled={busy || blocked || !reason.trim() || !locationId || !destinationId || !validQuantity(quantity, lot.unit ?? "EACH", true)}
            onClick={() => void write(() => lots.transfer(lot, locationId, destinationId, quantity, reason), "Traslado registrado.")}>Trasladar lote</button></>}
        {can(cap.correct) && <button type="button" disabled={busy || blocked || !stock || !reason.trim() || !locationId || !validSignedQuantity(quantity, lot.unit ?? "EACH")}
          onClick={() => void write(() => lots.correct(lot, locationId, quantity, stock!.version, reason), "Corrección auditada registrada.")}>Corregir cantidad</button>}
      </>}
    </section>}
  </div>;
}
