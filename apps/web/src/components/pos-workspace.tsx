"use client";

import { useMemo, useRef, useState, useSyncExternalStore } from "react";
import { useOperator } from "./operations-shell";
import { OperatorError, ReadGeneration } from "@/lib/operations-api";
import { CartLine, manualLine, pos, PosProduct, posFailure, Quote, Sale, SaleSummary, unavailableReason, validQuote, validSale } from "@/lib/pos-api";

const key="mira-pos-pending";
const subscribe=(callback:()=>void)=>{window.addEventListener(key,callback);return()=>window.removeEventListener(key,callback);};
const snapshot=()=>{try{return sessionStorage.getItem(key);}catch{return "STORAGE_UNAVAILABLE";}};
const serverSnapshot=()=>null;
function pendingRecord(value:string|null):{operationId:string;quoteId:string}|null {
  if (!value) return null;
  try { const p=JSON.parse(value);const uuid=/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i;return typeof p.operationId==="string" && uuid.test(p.operationId) && typeof p.quoteId==="string" && uuid.test(p.quoteId)?p:null; } catch {return null;}
}
function savePending(value:{operationId:string;quoteId:string}|null){if(value)sessionStorage.setItem(key,JSON.stringify(value));else sessionStorage.removeItem(key);window.dispatchEvent(new Event(key));}

export function PosWorkspace() {
  const {capabilities}=useOperator();const can=(c:string)=>capabilities.includes(`pos.${c}`);
  const [search,setSearch]=useState("");const [products,setProducts]=useState<PosProduct[]>([]);const [cursor,setCursor]=useState<string|null>(null);
  const [selected,setSelected]=useState<PosProduct|null>(null);const [quantity,setQuantity]=useState("1");const [unit,setUnit]=useState<CartLine["unit"]>("EACH");
  const [draftDirty,setDraftDirty]=useState(false);
  const [resolution,setResolution]=useState("0.001");const [gross,setGross]=useState("");const [tare,setTare]=useState("");
  const [cart,setCart]=useState<{product:PosProduct;line:CartLine}[]>([]);const [quote,setQuote]=useState<Quote|null>(null);const [reviewed,setReviewed]=useState(false);
  const [sale,setSale]=useState<Sale|null>(null);const [busy,setBusy]=useState(false);const writing=useRef(false);const [error,setError]=useState("");const [status,setStatus]=useState("");
  const [history,setHistory]=useState<SaleSummary[]>([]);const [historyCursor,setHistoryCursor]=useState<string|null>(null);
  const [filters,setFilters]=useState({actor_id:"",on_date:"",sale_id:""});const reads=useRef(new ReadGeneration());
  const serialized=useSyncExternalStore(subscribe,snapshot,serverSnapshot);const pending=useMemo(()=>pendingRecord(serialized),[serialized]);
  const locked=busy || Boolean(serialized);
  async function action(work:()=>Promise<void>) {
    if(writing.current)return;writing.current=true;setBusy(true);setError("");setStatus("");
    try {await work();} catch(e){setError(posFailure(e));} finally{writing.current=false;setBusy(false);}
  }
  async function discover(after:string|null=null){
    const token=reads.current.begin();await action(async()=>{const r=await pos.discover(search,after);if(reads.current.accepts(token)){setProducts(r.items);setCursor(r.next_cursor);setStatus("Resultados consultados.");}});
  }
  function edit(item:PosProduct){setDraftDirty(!cart.some(x=>x.product.variant_id===item.variant_id));setSelected(item);setUnit(item.sale_unit??"EACH");setResolution(item.sale_unit==="KG"?"0.000001":"0.001");setGross("");setTare("");setQuantity(cart.find(x=>x.product.variant_id===item.variant_id)?.line.quantity??"1");}
  function invalidateQuote(){setQuote(null);setReviewed(false);setSale(null);}
  function add(){if(!selected||locked)return;setDraftDirty(false);const line=manualLine(selected,quantity,unit,resolution,gross,tare);setCart(old=>[...old.filter(x=>x.product.variant_id!==selected.variant_id),{product:selected,line}]);invalidateQuote();setStatus("Carrito actualizado; stock sin consumir.");}
  function complete(s:Sale){setSale(s);setSelected(null);setDraftDirty(false);setCart([]);setQuote(null);setReviewed(false);savePending(null);setStatus("Consumo confirmado. Pendiente de liquidación; no acredita pago.");}
  async function confirm(){if(!quote||!reviewed||serialized||draftDirty||writing.current)return;
    const operationId=crypto.randomUUID(),quoteId=quote.id;
    await action(async()=>{
      savePending({operationId,quoteId});
      try{const r=await pos.confirm(operationId,quoteId);if(!validSale(r.sale,operationId,quoteId))throw new OperatorError("invalid_response",undefined,true);complete(r.sale);}
      catch(e){if(e instanceof OperatorError&&!e.uncertain){savePending(null);setReviewed(false);if(e.code==="pos_conflict")setQuote(null);}throw e;}
    });
  }
  return <div className="pos-workspace">
    <header className="workspace-heading"><p className="eyebrow">Operación comercial · POS-01</p><h1>Caja de demostración</h1><p>Consumo de inventario aislado. Sin cobro, liquidación ni comprobante fiscal.</p></header>
    {error&&<p role="alert">{error}</p>}{status&&<p role="status">{status}</p>}
    {serialized&&!pending&&<p role="alert">No pudimos leer la identidad pendiente. Conservamos el bloqueo de escritura; solicitá revisión antes de continuar.</p>}
    {pending&&<section className="notice" aria-label="Operación pendiente"><h2>Confirmación pendiente de verificar</h2><p>Resultado incierto. No reenvíes la escritura ni armes otra confirmación. Una consulta sin resultado no demuestra que falló.</p><p>Operación <code>{pending.operationId}</code></p>
      <button disabled={busy||!can("confirm_sale")} onClick={()=>void action(async()=>{const r=await pos.operation(pending.operationId);if(!validSale(r.sale,pending.operationId,pending.quoteId))throw new OperatorError("invalid_response");complete(r.sale);})}>Consultar operación</button></section>}
    <div className="pos-columns"><section className="panel" aria-labelledby="pos-discovery"><h2 id="pos-discovery">Buscar producto</h2>
      {can("discover_products")?<><form onSubmit={e=>{e.preventDefault();void discover();}}><label htmlFor="pos-search">Código de barras exacto, SKU o comienzo del nombre</label><div className="pos-search"><input id="pos-search" value={search} maxLength={255} onChange={e=>{reads.current.invalidate();setSearch(e.target.value);}}/><button disabled={busy}>Buscar</button></div><p className="field-help">El escáner de teclado puede enviar el código y Enter. La búsqueda por nombre usa prefijos.</p></form>
      <ul className="pos-results">{products.map(p=><li key={p.variant_id}><div><strong>{p.product_name} · {p.variant_label}</strong><p className="mono">{p.sku}{p.barcode?` · ${p.barcode}`:""}</p><p>{p.price_amount?`ARS ${p.price_amount} · ${p.price_basis==="PER_KG"?"por kg":"por unidad"}`:"Sin precio"} · Libre elegible: {p.available_quantity} {p.accounting_unit??"sin unidad"}</p>{!p.eligible&&<p>{p.unavailable_reasons.map(r=>unavailableReason[r]??"No elegible").join(" · ")}</p>}</div><button disabled={locked||!p.eligible} onClick={()=>edit(p)}>Elegir {p.sku}</button></li>)}</ul>
      {cursor&&<button disabled={busy} onClick={()=>void discover(cursor)}>Más productos</button>}</>:<p>No tenés permiso de descubrimiento POS.</p>}
      {selected&&<form aria-label="Cantidad para carrito" onSubmit={e=>{e.preventDefault();add();}}><h3>{selected.product_name} · {selected.variant_label}</h3><p>{selected.sku}</p><fieldset disabled={locked}><label htmlFor="pos-quantity">Cantidad</label><input id="pos-quantity" inputMode="decimal" value={quantity} onChange={e=>{setQuantity(e.target.value);setDraftDirty(true);invalidateQuote();}} required/>
        <label htmlFor="pos-unit">Unidad de transacción</label><select id="pos-unit" value={unit} onChange={e=>{setDraftDirty(true);invalidateQuote();setUnit(e.target.value as CartLine["unit"]);setResolution(e.target.value==="KG"?"0.000001":"0.001");}}>{selected.accounting_unit==="EACH"?<option>EACH</option>:<><option>G</option><option>KG</option></>}</select>
        {unit!=="EACH"&&<><p>Medición manual: se registran operador y momento al agregar o actualizar la línea.</p><label htmlFor="pos-resolution">Resolución declarada</label><input id="pos-resolution" inputMode="decimal" value={resolution} onChange={e=>{setResolution(e.target.value);setDraftDirty(true);invalidateQuote();}} required/><label htmlFor="pos-gross">Bruto opcional</label><input id="pos-gross" value={gross} onChange={e=>{setGross(e.target.value);setDraftDirty(true);invalidateQuote();}}/><label htmlFor="pos-tare">Tara opcional (junto con bruto)</label><input id="pos-tare" value={tare} onChange={e=>{setTare(e.target.value);setDraftDirty(true);invalidateQuote();}}/></>}
        <button>Agregar o actualizar línea</button></fieldset></form>}
    </section><section className="panel pos-cart" aria-labelledby="pos-cart-title"><h2 id="pos-cart-title">Carrito · En edición</h2><p>No reserva ni consume existencias.</p>{draftDirty&&<p>Aplicá la línea en edición antes de cotizar.</p>}
      {!cart.length&&<p>Elegí una variante para comenzar.</p>}<ol>{cart.map(({product,line})=><li key={line.variant_id}><strong>{product.product_name} · {product.variant_label}</strong><p>{product.sku} · {line.quantity} {line.unit}</p><div className="actions"><button disabled={locked} onClick={()=>edit(product)}>Editar {product.sku}</button><button disabled={locked} onClick={()=>{setCart(c=>c.filter(x=>x.line.variant_id!==line.variant_id));invalidateQuote();}}>Quitar {product.sku}</button></div></li>)}</ol>
      <div className="actions"><button disabled={locked||!cart.length} onClick={()=>{setCart([]);invalidateQuote();}}>Vaciar carrito</button><button disabled={locked||draftDirty||!cart.length||!can("quote_sale")} onClick={()=>void action(async()=>{setQuote(null);setReviewed(false);const r=await pos.quote(cart.map(x=>x.line));if(!validQuote(r.quote))throw new OperatorError("invalid_response",undefined,true);setQuote(r.quote);setReviewed(false);setStatus("Cotización calculada por el servidor. Revisá importes y revisiones.");})}>Cotizar carrito</button></div>
      {quote&&<section aria-label="Cotización del servidor"><h3>Registrado · Cotización vigente</h3><ul>{quote.lines.map(l=><li key={l.variant_id}>{l.sku} · {l.quantity} {l.unit} · ARS {l.amount_ars}<small>Revisión de precio {l.price_revision_id}</small></li>)}</ul><p className="pos-total">Total ARS {quote.total_ars}</p><p>Vence: {new Date(quote.expires_at).toLocaleString()}</p><label className="pos-review"><input type="checkbox" checked={reviewed} disabled={locked} onChange={e=>setReviewed(e.target.checked)}/>Revisé esta cotización y sus precios</label><div className="actions"><button className="button-primary" disabled={locked||draftDirty||!reviewed||!can("confirm_sale")} onClick={()=>void confirm()}>Confirmar consumo sin liquidar</button><button disabled={locked} onClick={()=>void action(async()=>{await pos.cancel(quote.id);setQuote(null);setReviewed(false);setStatus("Cotización cancelada; no hubo consumo.");})}>Cancelar cotización</button></div></section>}
    </section></div>
    {sale&&<section className="panel" aria-label="Operación registrada"><h2>Consumo registrado · Sin liquidar</h2><p>No es comprobante fiscal ni acredita pago. Sólo datos ficticios.</p><p>Venta <code>{sale.id}</code></p><p>Operador {sale.actor_id} · {new Date(sale.created_at).toLocaleString()}</p><p className="pos-total">ARS {sale.total_ars}</p><ul>{sale.lines.map(l=><li key={l.variant_id}>{l.product_name} · {l.sku} · {l.quantity} {l.unit} · ARS {l.amount_ars}</li>)}</ul><h3>Trazabilidad Inventory</h3><ul>{sale.inventory_trace.allocations.map((a,i)=><li key={i}>{a.lot_code} · {a.location_code} · {a.quantity} {a.unit} · Movimiento <code>{a.stock_movement_id}</code></li>)}</ul></section>}
    {can("list_sales")&&<section className="panel" aria-label="Historial POS"><h2>Historial de consumos</h2><form onSubmit={e=>{e.preventDefault();void action(async()=>{const r=await pos.history(filters,null);setHistory(r.items);setHistoryCursor(r.next_cursor);});}}><div className="pos-history-filters"><label>Operador<input value={filters.actor_id} onChange={e=>setFilters({...filters,actor_id:e.target.value})}/></label><label>Fecha UTC<input type="date" value={filters.on_date} onChange={e=>setFilters({...filters,on_date:e.target.value})}/></label><label>Identidad de venta<input value={filters.sale_id} onChange={e=>setFilters({...filters,sale_id:e.target.value})}/></label></div><button disabled={busy}>Consultar historial</button></form><ul>{history.map(s=><li key={s.id}><p>{new Date(s.created_at).toLocaleString()} · Operador {s.actor_id} · ARS {s.total_ars} · Sin liquidar</p><button disabled={busy||!can("read_sale")} onClick={()=>void action(async()=>{const r=await pos.sale(s.id);if(!validSale(r.sale))throw new OperatorError("invalid_response");setSale(r.sale);})}>Ver consumo {s.id}</button></li>)}</ul>{historyCursor&&<button disabled={busy} onClick={()=>void action(async()=>{const r=await pos.history(filters,historyCursor);setHistory(r.items);setHistoryCursor(r.next_cursor);})}>Más consumos</button>}</section>}
  </div>;
}
