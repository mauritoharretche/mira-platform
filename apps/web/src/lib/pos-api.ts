import { quantityBase } from "./quantities";
import { OperatorError, post, request, validMeasurementTimestamp } from "./operations-api";

export type PosProduct = { variant_id: string; product_name: string; variant_label: string; sku: string; barcode: string | null;
  sale_unit: "EACH" | "G" | "KG" | null; accounting_unit: "EACH" | "G" | null; price_amount: string | null;
  price_basis: "PER_VARIANT" | "PER_KG" | null; available_quantity: string; eligible: boolean; unavailable_reasons: string[] };
export type CartLine = { variant_id: string; quantity: string; unit: "EACH" | "G" | "KG";
  measurement: null | { net: string; unit: "G" | "KG"; source: "manual"; measured_at: string; resolution: string; gross: string | null; tare: string | null } };
export type QuotedLine = PosProduct & CartLine & { amount_ars: string; price_revision_id: string; measurement_version: string; rounding: string };
export type Quote = { id: string; state: "QUOTED" | "CANCELLED" | "COMMITTED" | "EXPIRED"; lines: QuotedLine[]; total_ars: string; currency: "ARS"; created_at: string; expires_at: string };
export type Sale = { id: string; operation_id: string; quote_id: string; state: "COMMITTED_UNSETTLED"; settlement: "UNSETTLED";
  total_ars: string; currency: "ARS"; actor_id: string; created_at: string; lines: QuotedLine[]; reservation_id: string;
  inventory_trace: { state: "CONSUMED"; allocations: { lot_code: string; location_code: string; quantity: string; unit: string; stock_movement_id: string }[] }; notice: string };
export type SaleSummary = Pick<Sale,"id" | "actor_id" | "created_at" | "total_ars" | "state" | "settlement">;
export const pos = {
  discover: async (search: string, after: string | null = null) => {
    const r=await request<{items:PosProduct[];next_cursor:string|null}>(`/pos/products?${new URLSearchParams({ search, ...(after ? {after} : {}) })}`);
    if(!validDiscovery(r))throw new OperatorError("invalid_response");return r;
  },
  quote: (lines: CartLine[]) => post<{quote:Quote}>("/pos/quotes",{lines}),
  confirm: (operationId:string,quoteId:string) => post<{sale:Sale}>("/pos/sales",{operation_id:operationId,quote_id:quoteId}),
  operation: (operationId:string) => request<{sale:Sale}>(`/pos/operations/${operationId}`),
  cancel: (id:string) => post<{quote:Quote}>(`/pos/quotes/${id}`,{action:"cancel"}),
  history: async (filters: {actor_id:string;on_date:string;sale_id:string},after:string|null) => {
    const r=await request<{items:SaleSummary[];next_cursor:string|null}>(`/pos/sales?${new URLSearchParams({...Object.fromEntries(Object.entries(filters).filter(([,v])=>v)),...(after?{after}:{})})}`);
    if(!r || !Array.isArray(r.items) || r.items.length>50 || !(r.next_cursor===null || typeof r.next_cursor==="string"&&uuid.test(r.next_cursor))
      || !r.items.every(s=>s && typeof s.actor_id==="string" && s.actor_id.length<=128 && uuid.test(s.id) && validMeasurementTimestamp(s.created_at) && typeof s.total_ars==="string" && money.test(s.total_ars) && s.state==="COMMITTED_UNSETTLED" && s.settlement==="UNSETTLED"))throw new OperatorError("invalid_response");
    return r;
  },
  sale: (id:string) => request<{sale:Sale}>(`/pos/sales/${id}`),
};
const uuid=/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i;
const money=/^(?:0|[1-9][0-9]{0,11})\.[0-9]{2}$/;
export function validDiscovery(r:{items:PosProduct[];next_cursor:string|null}):boolean {
  return Boolean(r && Array.isArray(r.items) && r.items.length<=50 && (r.next_cursor===null || typeof r.next_cursor==="string"&&uuid.test(r.next_cursor))
    && r.items.every(p=>p && typeof p.variant_id==="string" && uuid.test(p.variant_id) && typeof p.product_name==="string" && p.product_name.length<=255
      && typeof p.variant_label==="string" && p.variant_label.length<=255 && typeof p.sku==="string" && p.sku.length<=128
      && (p.barcode===null || typeof p.barcode==="string") && (p.sale_unit===null || ["EACH","G","KG"].includes(p.sale_unit))
      && (p.accounting_unit===null || ["EACH","G"].includes(p.accounting_unit)) && typeof p.available_quantity==="string"
      && quantityBase(p.available_quantity,p.accounting_unit??"EACH")!==null && (p.price_amount===null || typeof p.price_amount==="string"&&money.test(p.price_amount))
      && (p.price_basis===null || ["PER_VARIANT","PER_KG"].includes(p.price_basis)) && typeof p.eligible==="boolean"
      && Array.isArray(p.unavailable_reasons) && p.unavailable_reasons.length<=16 && p.unavailable_reasons.every(v=>typeof v==="string"&&Object.hasOwn(unavailableReason,v))));
}
function validLine(l: QuotedLine): boolean {
  if (!l || !uuid.test(l.variant_id) || !uuid.test(l.price_revision_id) || typeof l.amount_ars!=="string" || !money.test(l.amount_ars) || typeof l.price_amount!=="string" || !money.test(l.price_amount)
    || typeof l.product_name!=="string" || !l.product_name || l.product_name.length>255 || typeof l.variant_label!=="string" || !l.variant_label
    || typeof l.sku!=="string" || !l.sku || l.sku.length>128 || !["EACH","G","KG"].includes(l.unit)
    || l.rounding!=="HALF_EVEN_CENTS_PER_LINE") return false;
  const accounting=l.unit==="EACH"?"EACH":"G";
  const normalized=l.unit==="KG" ? kgToGrams(l.quantity) : l.quantity;
  const base=normalized===null?null:quantityBase(normalized,accounting);
  if (base===null || base<=0n || l.accounting_unit!==accounting) return false;
  if (l.unit==="EACH") return l.price_basis==="PER_VARIANT" && l.measurement===null;
  return l.price_basis==="PER_KG" && l.measurement?.source==="manual" && l.measurement.unit===l.unit
    && l.measurement.net===l.quantity && validMeasurementTimestamp(l.measurement.measured_at);
}
function kgToGrams(s:string):string|null {
  if (typeof s!=="string" || !/^(?:0|[1-9][0-9]{0,12})(?:\.[0-9]{1,6})?$/.test(s))return null;
  const [whole,fraction=""]=s.split(".");const base=BigInt(whole)*1000000n+BigInt(fraction.padEnd(6,"0"));
  return `${base/1000n}.${String(base%1000n).padStart(3,"0")}`;
}
function validLines(lines:QuotedLine[],total:string):boolean {
  return Array.isArray(lines)&&lines.length>0&&lines.length<=30 && new Set(lines.map(l=>l?.variant_id)).size===lines.length
    && typeof total==="string" && money.test(total) && lines.every(validLine) && lines.reduce((sum,l)=>sum+BigInt(l.amount_ars.replace(".","")),0n)===BigInt(total.replace(".",""));
}
export function validQuote(q: Quote): boolean {
  return Boolean(q && uuid.test(q.id) && q.currency==="ARS" && q.state==="QUOTED" && validLines(q.lines,q.total_ars)
    && validMeasurementTimestamp(q.created_at) && validMeasurementTimestamp(q.expires_at));
}
export function validSale(s: Sale, operationId?:string, quoteId?:string): boolean {
  return Boolean(s && uuid.test(s.id) && uuid.test(s.operation_id) && uuid.test(s.quote_id) && uuid.test(s.reservation_id) && s.currency==="ARS" && s.state==="COMMITTED_UNSETTLED"
    && s.settlement==="UNSETTLED" && typeof s.actor_id==="string" && Boolean(s.actor_id.trim()) && s.actor_id.length<=128 && validLines(s.lines,s.total_ars) && validMeasurementTimestamp(s.created_at)
    && s.inventory_trace?.state==="CONSUMED" && Array.isArray(s.inventory_trace.allocations) && s.inventory_trace.allocations.length>0
    && s.inventory_trace.allocations.every(a=>a && uuid.test(a.stock_movement_id) && typeof a.lot_code==="string" && typeof a.location_code==="string" && typeof a.quantity==="string" && (a.unit==="EACH"||a.unit==="G") && (quantityBase(a.quantity,a.unit)??0n)>0n)
    && (!operationId || s.operation_id===operationId) && (!quoteId || s.quote_id===quoteId));
}
export function posFailure(error:unknown) {
  if (error instanceof OperatorError && error.code==="pos_conflict") return "La cotización o disponibilidad cambió. Conservamos el carrito: consultá y revisá una nueva cotización antes de confirmar.";
  if (error instanceof OperatorError && error.uncertain) return "Resultado incierto. No vuelvas a confirmar: consultá la identidad de operación. Una ausencia no demuestra que falló.";
  if (error instanceof OperatorError && error.code==="access_denied") return "No tenés autorización para esta operación.";
  return "No pudimos completar la operación. Revisá los datos y consultá el estado antes de continuar.";
}
export function manualLine(product:PosProduct, quantity:string, unit:CartLine["unit"], resolution:string, gross:string, tare:string):CartLine {
  return {variant_id:product.variant_id,quantity,unit,measurement:unit==="EACH"?null:{net:quantity,unit,source:"manual",measured_at:new Date().toISOString(),resolution,gross:gross||null,tare:tare||null}};
}
export const unavailableReason:Record<string,string>={product_inactive:"Producto inactivo",variant_inactive:"Variante inactiva",category_missing:"Sin categoría",price_missing:"Sin precio",measurement_unknown:"Unidad comercial desconocida",stock_uninitialized:"Sin unidad contable registrada",commercial_unit_mismatch:"Precio, medición y unidad contable incompatibles",no_eligible_stock:"Sin stock elegible libre"};
