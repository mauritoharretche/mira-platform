"use client";
import { useRef, useState } from "react";
import { useOperator } from "./operations-shell";
import { countApi, CountSession } from "@/lib/count-api";
import { Location, LotState } from "@/lib/lot-api";
import { OperatorError, request, Stock, validStock } from "@/lib/operations-api";
import { validQuantity } from "@/lib/quantities";

export function PhysicalInventoryPanel({variantId,locations,state,onChanged}:{variantId:string;locations:Location[];state:LotState|null;onChanged?:()=>Promise<void>}) {
  const {capabilities}=useOperator();const can=(name:string)=>capabilities.includes(`inventory.${name}`);
  const [locationId,setLocationId]=useState("");const [scope,setScope]=useState<{id:string;label:string}[]>([]);
  const [counts,setCounts]=useState<CountSession[]>([]);const [count,setCount]=useState<CountSession|null>(null);
  const [blind,setBlind]=useState(true);const [observations,setObservations]=useState<Record<string,string>>({});
  const [reason,setReason]=useState("");const [evidence,setEvidence]=useState("");
  const [lossBalance,setLossBalance]=useState("");const [lossQuantity,setLossQuantity]=useState("");
  const [lossReason,setLossReason]=useState("DAMAGE");const [stock,setStock]=useState<{variantId:string;value:Stock}|null>(null);
  const [busy,setBusy]=useState(false);const [blocked,setBlocked]=useState(false);const [operation,setOperation]=useState("");
  const [notice,setNotice]=useState("");const [error,setError]=useState("");const flight=useRef(false);const readGeneration=useRef(0);const [uncertain,setUncertain]=useState(false);const [reviewed,setReviewed]=useState(false);
  const balances=(state?.lots??[]).flatMap((lot)=>lot.locations.filter((place)=>place.balance_id).map((place)=>({id:place.balance_id!,locationId:place.id,label:`${lot.code} · ${place.code} · ${place.quantity} ${state?.unit ?? "EACH"}`})));
  async function write(action:(id:string)=>Promise<unknown>,success:string){
    if(flight.current||blocked)return;flight.current=true;readGeneration.current+=1;setReviewed(false);setBusy(true);setError("");setNotice("");const id=crypto.randomUUID();setOperation(id);
    try {
      const result=await action(id);
      if(result&&typeof result==="object"&&"lines" in result)setCount(result as CountSession);
      setNotice(success);
      try{await onChanged?.();}catch{setError("La escritura fue registrada; la actualización de la vista quedó pendiente.");}
    }catch(failure){setBlocked(true);setUncertain(failure instanceof OperatorError&&failure.uncertain);setError(failure instanceof OperatorError&&failure.uncertain?"Resultado incierto. No repitas la escritura; consultá su identidad de operación.":"La operación fue rechazada. Conservamos los datos; revisá snapshots, unidades y reservas antes de decidir.");}
    finally{setBusy(false);flight.current=false;}
  }
  async function readCount(id:string){if(flight.current)return;const generation=++readGeneration.current;try{const result=await countApi.read(id);if(generation===readGeneration.current){setCount(result);setReviewed(true);}}catch{if(generation===readGeneration.current)setError("No pudimos consultar el conteo.");}}
  if(!can("read_physical_inventory"))return null;
  return <section className="panel physical-inventory" aria-labelledby="physical-count-title"><h2 id="physical-count-title">Conteos físicos y mermas</h2>
    {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
    {blocked&&<div className="notice"><p>Escrituras pausadas · operación <code>{operation}</code>. Una consulta no autoriza repetir una escritura incierta.</p>
      <button type="button" onClick={()=>void countApi.operation(operation).then((r)=>setNotice(`Operación encontrada: ${r.action}. Revisá su resultado y la trazabilidad.`)).catch(()=>setError("Operación no confirmada; la ausencia no demuestra un fallo."))}>Consultar operación física</button></div>}
    {blocked&&!uncertain&&reviewed&&<button type="button" onClick={()=>{setBlocked(false);setReviewed(false);}}>Continuar tras revisar el rechazo</button>}
    <button type="button" onClick={()=>void countApi.list().then((r)=>{setCounts(r.counts);if(r.truncated)setError("Listado de conteos limitado a 50 sesiones.");}).catch(()=>setError("No pudimos listar los conteos."))}>Consultar sesiones de conteo</button>
    <ul>{counts.map((c)=><li key={c.id}><button type="button" onClick={()=>void readCount(c.id)}>Conteo {c.id} · {c.state}</button></li>)}</ul>
    {can("create_physical_count")&&<form onSubmit={(event)=>{event.preventDefault();void write((id)=>countApi.create(id,locationId,scope.map((s)=>s.id),blind),"Sesión de conteo abierta.");}}>
      <h3>Abrir conteo · alcance explícito</h3><label htmlFor="count-location">Ubicación de conteo</label><select id="count-location" value={locationId} required disabled={busy} onChange={(event)=>{setLocationId(event.target.value);setScope([]);}}><option value="">Seleccioná ubicación</option>{locations.map((l)=><option key={l.id} value={l.id}>{l.code}</option>)}</select>
      <p>Seleccioná los lotes de la variante actual. Podés sumar otras variantes de la misma ubicación antes de abrir el conteo. Máximo 100 saldos.</p>
      {balances.filter((b)=>b.locationId===locationId).map((b)=><label className="proc-check" key={b.id}><input type="checkbox" checked={scope.some((s)=>s.id===b.id)} disabled={busy} onChange={(event)=>setScope((old)=>event.target.checked?[...old,{id:b.id,label:b.label}]:old.filter((s)=>s.id!==b.id))}/>{b.label}</label>)}
      <ul>{scope.map((s)=><li key={s.id}>{s.label}</li>)}</ul>
      <label className="proc-check"><input type="checkbox" checked={blind} disabled={busy} onChange={(event)=>setBlind(event.target.checked)}/>Ingreso ciego de observaciones (no precargar cantidades esperadas)</label>
      <button disabled={busy||blocked||!scope.length||scope.length>100}>Abrir conteo físico</button></form>}
    {count&&<article className="workspace-record-block"><h3>Detalle de conteo</h3><p className="identifier">Sesión {count.id} · {count.state}</p>
      <p>Responsable {count.responsible_actor_id} · {count.blind?"Ingreso ciego":"Conteo con referencia"}</p>
      <button type="button" disabled={busy} onClick={()=>void readCount(count.id)}>Consultar detalle de conteo</button>
      {count.state==="OPEN"&&can("record_physical_count")&&<><label htmlFor="count-reason">Motivo de observación</label><input id="count-reason" value={reason} maxLength={255} onChange={(event)=>setReason(event.target.value)}/>
        <label htmlFor="count-evidence">Evidencia de conteo</label><input id="count-evidence" value={evidence} maxLength={255} onChange={(event)=>setEvidence(event.target.value)}/></>}
      {count.lines.map((line)=><div className="workspace-edit-block" key={line.id}><h4>{line.lot_code} · {line.unit}</h4><p id={`count-line-identity-${line.id}`} className="identifier">Variante {line.variant_id} · Saldo {line.balance_id}</p>
        {line.expected!==undefined&&<p>Esperado: {line.expected} {line.unit} · Discrepancia: {line.discrepancy ?? "sin observar"} {line.unit}</p>}
        <p>Observado registrado: {line.observed ?? "pendiente"} {line.unit}</p>
        {count.state==="OPEN"&&can("record_physical_count")&&<form onSubmit={(event)=>{event.preventDefault();void write((id)=>countApi.observe(id,count,line,observations[line.id]??"",reason,evidence),"Observación registrada.");}}>
          <label htmlFor={`observed-${line.id}`}>Observado {line.lot_code} ({line.unit})</label><input id={`observed-${line.id}`} aria-describedby={`count-line-identity-${line.id}`} value={observations[line.id]??""} inputMode="decimal" required onChange={(event)=>setObservations((old)=>({...old,[line.id]:event.target.value}))}/>
          <button disabled={busy||blocked||!reason.trim()||!evidence.trim()||!validQuantity(observations[line.id]??"",line.unit)}>Registrar observación</button></form>}</div>)}
      {can("review_physical_count")&&count.state==="OPEN"&&<button type="button" disabled={busy||blocked} onClick={()=>void write((id)=>countApi.transition(id,count,"review"),"Conteo revisado. Consultá el detalle para revisar discrepancias.")}>Revisar conteo</button>}
      {can("complete_physical_count")&&count.state==="REVIEWED"&&<button type="button" disabled={busy||blocked} onClick={()=>void write((id)=>countApi.transition(id,count,"complete"),"Conteo completado; discrepancias conciliadas con Audit.")}>Completar conteo físico</button>}
    </article>}
    {can("write_off_stock")&&state&&variantId&&<form onSubmit={(event)=>{event.preventDefault();if(!stock||stock.variantId!==variantId||!balances.some((b)=>b.id===lossBalance))return;void write((id)=>countApi.loss({operation_id:id,balance_id:lossBalance,unit:stock.value.unit,quantity:lossQuantity,expected_stock_version:stock.value.version,reason_code:lossReason,evidence}),"Merma física registrada con movimiento y Audit.");}}>
      <h3>Registrar merma física</h3><p>Bloquear o vencer un lote no elimina su stock. La merma registra su salida física, sin valoración monetaria.</p>
      <button type="button" disabled={busy} onClick={()=>void request<{stock:Stock|null}>(`/inventory/variants/${variantId}/stock`).then((r)=>{if(!validStock(r.stock,variantId))throw new OperatorError("invalid_response");setStock({variantId,value:r.stock});setReviewed(true);}).catch(()=>setError("No pudimos consultar la versión física."))}>Consultar versión para merma</button>
      <label htmlFor="loss-balance">Lote y ubicación de merma</label><select id="loss-balance" value={lossBalance} required onChange={(event)=>setLossBalance(event.target.value)}><option value="">Seleccioná saldo físico</option>{balances.map((b)=><option key={b.id} value={b.id}>{b.label}</option>)}</select>
      <label htmlFor="loss-quantity">Cantidad de merma ({state.unit ?? "EACH"})</label><input id="loss-quantity" value={lossQuantity} required inputMode="decimal" onChange={(event)=>setLossQuantity(event.target.value)}/>
      <label htmlFor="loss-code">Motivo de merma</label><select id="loss-code" value={lossReason} onChange={(event)=>setLossReason(event.target.value)}><option value="EXPIRATION">Vencimiento</option><option value="DAMAGE">Daño</option><option value="QUALITY_REJECTION">Rechazo de calidad</option><option value="MEASUREMENT_DISCREPANCY">Diferencia de medición</option><option value="INTERNAL_CONSUMPTION">Consumo interno</option><option value="OTHER">Otro justificado</option></select>
      <label htmlFor="loss-evidence">Evidencia de merma</label><input id="loss-evidence" value={evidence} required maxLength={255} onChange={(event)=>setEvidence(event.target.value)}/>
      <button disabled={busy||blocked||!stock||stock.variantId!==variantId||!balances.some((b)=>b.id===lossBalance)||!lossBalance||!evidence.trim()||!validQuantity(lossQuantity,state.unit??"EACH",true)}>Registrar merma</button></form>}
  </section>;
}
