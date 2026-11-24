"use client";

import { FormEvent, useRef, useState } from "react";
import { useOperator } from "./operations-shell";
import { OperatorError, request, Stock } from "@/lib/operations-api";
import { procurement, VariantReference } from "@/lib/procurement-api";
import { Location, Lot, lots } from "@/lib/lot-api";
import { Availability, Reservation, ReservationDraftLine, reservationApi, validReservationLine } from "@/lib/reservation-api";

export function CommercialInventoryPanel({onChanged}: {onChanged?: () => Promise<void>} = {}) {
  const { capabilities } = useOperator();
  const can = (name: string) => capabilities.includes(`inventory.${name}`);
  const [query,setQuery] = useState("");
  const [variants,setVariants] = useState<VariantReference[]>([]);
  const [variantId,setVariantId] = useState("");
  const [availability,setAvailability] = useState<Availability | null>(null);
  const [reservations,setReservations] = useState<Reservation[]>([]);
  const [selected,setSelected] = useState<Reservation | null>(null);
  const [draft,setDraft] = useState<ReservationDraftLine[]>([]);
  const [quantity,setQuantity] = useState("1");
  const [source,setSource] = useState("fictional-operator");
  const [reference,setReference] = useState("");
  const [expires,setExpires] = useState("");
  const [operationId,setOperationId] = useState("");
  const [blocked,setBlocked] = useState<"conflict" | "uncertain" | null>(null);
  const [reviewed,setReviewed] = useState(false);
  const [busy,setBusy] = useState(false);
  const [error,setError] = useState("");
  const [notice,setNotice] = useState("");
  const [locations,setLocations] = useState<Location[]>([]);
  const [lotOptions,setLotOptions] = useState<Lot[]>([]);
  const [lotId,setLotId] = useState("");
  const [locationId,setLocationId] = useState("");
  const [evidence,setEvidence] = useState("");
  const [stockVersion,setStockVersion] = useState<string | null>(null);
  const generation=useRef(0);
  const inFlight=useRef(false);

  async function refresh(id=variantId) {
    if (!id) return;
    const token=++generation.current;
    const [a,r] = await Promise.all([
      can("read_availability") ? reservationApi.availability(id) : Promise.resolve(null),
      can("read_reservation") ? reservationApi.list(id) : Promise.resolve({reservations:[],truncated:false}),
    ]);
    if (token===generation.current) {
      setAvailability(a);setReservations(r.reservations);setReviewed(true);
      if(r.truncated) setError("El listado de reservas está limitado; consultá una reserva por su identidad para ver su detalle.");
    }
  }
  function readFailure() { setError("No pudimos completar la consulta. Podés volver a consultar; una lectura no confirma una escritura incierta."); }
  async function write(call:(id:string)=>Promise<unknown>, success:string) {
    if(inFlight.current || blocked) return;
    inFlight.current=true;setBusy(true);setError("");setNotice("");setReviewed(false);
    const id=crypto.randomUUID();setOperationId(id);
    try {
      const result=await call(id);
      if(result && typeof result==="object" && "effective_state" in result) {
        const reservation=result as Reservation;setSelected(reservation);
        setNotice(`Operación registrada. Reserva: ${reservation.effective_state}.`);
      } else setNotice(success);
      try { await refresh(); await onChanged?.(); } catch { readFailure(); }
    } catch(failure) {
      const uncertain=failure instanceof OperatorError && failure.uncertain;
      setBlocked(uncertain?"uncertain":"conflict");
      setError(uncertain ? "Resultado incierto. Conservamos la identidad de operación; consultala antes de decidir. No hay reenvíos automáticos."
        : "La operación fue rechazada. Conservamos el borrador. Consultá el estado vigente antes de decidir otra escritura.");
    } finally {inFlight.current=false;setBusy(false);}
  }
  async function recover() {
    try {
      const result=await reservationApi.operation(operationId);
      setNotice(`Operación encontrada: ${result.action}. Su resultado quedó registrado. Consultá el estado actual antes de una nueva decisión.`);
      await refresh();
    } catch {setError("Operación no confirmada. Su ausencia no demuestra que haya fallado; no reenvíes la escritura.");}
  }
  async function search(event:FormEvent) {
    event.preventDefault();
    try {setVariants((await procurement.variants(query)).variants);}catch {readFailure();}
  }
  async function classificationOptions() {
    const token=generation.current;
    try {
      const [state,places,stock]=await Promise.all([lots.state(variantId),lots.locations(),
        request<{stock:Stock|null}>(`/inventory/variants/${variantId}/stock`)]);
      if(token===generation.current) {
        setLotOptions(state.lots);setLocations(places.locations);setStockVersion(stock.stock?.version ?? null);
      }
    } catch {readFailure();}
  }
  const selectedVariant=variants.find((v)=>v.id===variantId);
  const label=selectedVariant?`${selectedVariant.product_name} · ${selectedVariant.label} · SKU ${selectedVariant.sku}`:variantId;
  const hasRead=can("read_availability")||can("read_reservation");
  if(!hasRead) return <section className="panel"><h2>Disponibilidad y reservas</h2><p>Tu cuenta no tiene permisos de consulta comercial.</p></section>;
  return <section className="panel commercial-inventory" aria-labelledby="commercial-title">
    <p className="eyebrow">Inventario comercial · piloto</p><h2 id="commercial-title">Disponibilidad y reservas</h2>
    <p>Disponibilidad física elegible para reservar. No acredita activación comercial, precio, venta ni cobro.</p>
    {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
    {capabilities.includes("catalog.read_product")&&<form onSubmit={(event)=>void search(event)}>
      <label htmlFor="commercial-query">Buscar variante para reserva</label><input id="commercial-query" value={query} onChange={(event)=>setQuery(event.target.value)}/>
      <button>Buscar variantes comerciales</button></form>}
    {capabilities.includes("catalog.read_product") ? <><label htmlFor="commercial-variant">Variante comercial</label><select id="commercial-variant" value={variantId} disabled={busy}
      onChange={(event)=>{generation.current++;setVariantId(event.target.value);setAvailability(null);setReservations([]);setSelected(null);setReviewed(false);setLotOptions([]);setLotId("");setLocationId("");setStockVersion(null);
        if(event.target.value)void refresh(event.target.value).catch(readFailure);}}>
      <option value="">Seleccioná una variante</option>{variants.map((v)=><option key={v.id} value={v.id}>{v.product_name} · {v.label} · SKU {v.sku}</option>)}</select></>
      : <><label htmlFor="commercial-variant-id">UUID de variante comercial</label><input id="commercial-variant-id" value={variantId} disabled={busy}
          onChange={(event)=>{generation.current++;setVariantId(event.target.value);setAvailability(null);setReservations([]);setSelected(null);setReviewed(false);}} />
          <p className="caption">Sin permiso de búsqueda de catálogo. Consultá únicamente una identidad de variante conocida.</p></>}
    <p className="selected-variant-identity">{label}</p>
    {variantId&&<button type="button" disabled={busy||!validReservationLine({variant_id:variantId,quantity:"1",label})} onClick={()=>void refresh().catch(readFailure)}>Consultar disponibilidad vigente</button>}
    {availability&&<><dl className="commercial-summary">
      {[["Físico",availability.physical],["Asignado a lotes",availability.allocated],["Sin asignar",availability.unallocated],
        ["Elegible",availability.eligible],["Reservado",availability.reserved],["No disponible",availability.unavailable],["Disponible para reservar",availability.available]]
        .map(([name,value])=><div key={name}><dt>{name}</dt><dd>{value} EACH</dd></div>)}
    </dl><p className="caption">Stock de seguridad explícito: {availability.safety_stock} EACH. Corte de consulta: {availability.as_of}. Los vencimientos de reservas se aplican aunque no haya limpieza programada.</p>
      <div className="table-wrap"><table><caption>Disponibilidad por lote y ubicación</caption><thead><tr><th>Lote</th><th>Ubicación</th><th>Físico</th><th>Elegible</th><th>Reservado</th><th>Disponible</th></tr></thead>
        <tbody>{availability.balances.map((b)=><tr key={b.balance_id}><td data-label="Lote">{b.lot_code}</td><td data-label="Ubicación">{b.location_code}</td><td data-label="Físico">{b.physical}</td><td data-label="Elegible">{b.eligible?"Sí":"No"}</td><td data-label="Reservado">{b.reserved}</td><td data-label="Disponible">{b.available}</td></tr>)}</tbody></table></div></>}
    {blocked&&<div className="workspace-edit-block"><p role="alert">Escrituras pausadas. Identidad: <code>{operationId}</code></p>
      {blocked==="uncertain"&&can("read_reservation")&&<button type="button" onClick={()=>void recover()}>Consultar resultado de operación</button>}
      {blocked==="conflict"&&reviewed&&<button type="button" onClick={()=>{setBlocked(null);setReviewed(false);}}>Editar sobre el estado consultado</button>}</div>}
    {can("reserve_stock")&&<form onSubmit={(event)=>{event.preventDefault();if(!draft.length||draft.some((l)=>!validReservationLine(l))||!expires)return;
      void write((id)=>reservationApi.create(id,source,reference,new Date(expires).toISOString(),draft),"Reserva registrada.");}}>
      <h3>Nueva reserva · borrador</h3><label htmlFor="reserve-quantity">Cantidad a reservar EACH</label><input id="reserve-quantity" value={quantity} inputMode="numeric" onChange={(event)=>setQuantity(event.target.value)}/>
      <button type="button" disabled={busy||!variantId||!validReservationLine({variant_id:variantId,quantity,label})}
        onClick={()=>setDraft((old)=>[...old.filter((line)=>line.variant_id!==variantId),{variant_id:variantId,quantity,label}])}>Agregar variante a reserva</button>
      <ul>{draft.map((line)=><li key={line.variant_id}>{line.label} · {line.quantity} EACH <button type="button" disabled={busy} onClick={()=>setDraft((old)=>old.filter((l)=>l.variant_id!==line.variant_id))}>Quitar de reserva</button></li>)}</ul>
      <label htmlFor="reserve-source">Origen de reserva</label><input id="reserve-source" value={source} maxLength={40} required onChange={(event)=>setSource(event.target.value)}/>
      <label htmlFor="reserve-reference">Referencia ficticia</label><input id="reserve-reference" value={reference} maxLength={120} required onChange={(event)=>setReference(event.target.value)}/>
      <label htmlFor="reserve-expiry">Vencimiento de reserva (hora local)</label><input id="reserve-expiry" type="datetime-local" value={expires} required onChange={(event)=>setExpires(event.target.value)}/>
      <button className="button-primary" disabled={busy||Boolean(blocked)||!draft.length}>Crear reserva</button></form>}
    {can("read_reservation")&&<><h3>Reservas de esta variante</h3><ul>{reservations.map((r)=><li key={r.id}><button type="button" disabled={busy}
      onClick={()=>{const token=generation.current;void reservationApi.read(r.id).then((result)=>{if(token===generation.current)setSelected(result);}).catch(readFailure);}}>{r.reference} · {r.effective_state} · vence {r.expires_at}</button></li>)}</ul>
      {selected&&<article className="workspace-record-block" aria-labelledby="reservation-detail"><h3 id="reservation-detail">Detalle de reserva</h3>
        <p className="identifier">{selected.id}</p><p>Estado efectivo: <strong>{selected.effective_state}</strong> · origen {selected.source} · {selected.reference}</p>
        <p>Creada {selected.created_at} · vence {selected.expires_at}</p><ul>{selected.allocations.map((a)=><li key={a.id}>{a.lot_code} · {a.location_code}: {a.quantity} EACH{a.stock_movement_id&&<> · movimiento {a.stock_movement_id}</>}</li>)}</ul>
        <ol>{selected.history.map((e,i)=><li key={i}>{e.action} · {e.at}</li>)}</ol>
        {([ ["release","release_reservation","Liberar reserva"],["expire","expire_reservation","Expirar reserva"],["consume","consume_reservation","Consumir reserva"] ] as const).map(([action,capability,text])=>
          can(capability)&&<button key={action} type="button" disabled={busy||Boolean(blocked)||selected.effective_state!=="ACTIVE"&&action!=="expire"}
            onClick={()=>void write((id)=>reservationApi.transition(selected.id,action,id),"Operación registrada.")}>{text}</button>)}
      </article>}</>}
    {can("classify_stock")&&can("read_stock_lots")&&can("read_stock")&&variantId&&<form onSubmit={(event)=>{event.preventDefault();
      void write((id)=>reservationApi.classify({operation_id:id,lot_id:lotId,location_id:locationId,quantity,expected_stock_version:stockVersion,evidence}),"Conteo verificado clasificado sin aumentar el stock físico.");}}>
      <h3>Conciliar stock sin asignación</h3><p>Verificá físicamente cantidad, etiqueta de lote y ubicación. No inventes proveedor ni recepción histórica.</p>
      <button type="button" disabled={busy} onClick={()=>void classificationOptions()}>Consultar datos para conciliación</button>
      <label htmlFor="classify-lot">Lote verificado</label><select id="classify-lot" value={lotId} required onChange={(event)=>setLotId(event.target.value)}><option value="">Seleccioná lote</option>{lotOptions.map((l)=><option key={l.id} value={l.id}>{l.code}</option>)}</select>
      <label htmlFor="classify-location">Ubicación verificada</label><select id="classify-location" value={locationId} required onChange={(event)=>setLocationId(event.target.value)}><option value="">Seleccioná ubicación</option>{locations.map((l)=><option key={l.id} value={l.id}>{l.code}</option>)}</select>
      <label htmlFor="classify-quantity">Cantidad verificada EACH</label><input id="classify-quantity" value={quantity} inputMode="numeric" required onChange={(event)=>setQuantity(event.target.value)}/>
      <label htmlFor="classify-evidence">Evidencia de verificación física</label><input id="classify-evidence" value={evidence} maxLength={255} required onChange={(event)=>setEvidence(event.target.value)}/>
      <button disabled={busy||Boolean(blocked)||!stockVersion}>Clasificar stock verificado</button></form>}
  </section>;
}
