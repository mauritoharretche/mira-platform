import { OperatorError, post, request, validMeasurementTimestamp } from "./operations-api";
import { AccountingUnit, validQuantity, quantityBase, canonicalQuantity } from "./quantities";
export type CountLine={id:string;balance_id:string;lot_code:string;variant_id:string;unit:AccountingUnit;observed:string|null;expected?:string;discrepancy?:string|null;reason:string|null;evidence:string|null};
export type CountSession={id:string;location_id:string;state:"OPEN"|"REVIEWED"|"COMPLETED";blind:boolean;responsible_actor_id:string;lines:CountLine[];history:{action:string;at:string;actor_id:string}[]};
const uuid=/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i;
const id=(value:unknown):value is string=>typeof value==="string"&&uuid.test(value);
export function validCount(value:unknown):value is CountSession {
  if(!value||typeof value!=="object")return false;
  const c=value as CountSession;
  return id(c.id)&&id(c.location_id)&&typeof c.blind==="boolean"&&typeof c.responsible_actor_id==="string"
    &&["OPEN","REVIEWED","COMPLETED"].includes(c.state)&&Array.isArray(c.lines)&&c.lines.length>0&&c.lines.length<=100
    &&new Set(c.lines.map((line)=>line?.id)).size===c.lines.length
    &&c.lines.every((line)=>line&&id(line.id)&&id(line.balance_id)&&id(line.variant_id)&&typeof line.lot_code==="string"
      &&(line.unit==="G"||line.unit==="EACH")&&(line.observed===null||validQuantity(line.observed,line.unit))
      &&(line.expected===undefined||validQuantity(line.expected,line.unit))
      &&(line.discrepancy===undefined||line.discrepancy===null||quantityBase(line.discrepancy,line.unit,true)!==null)
      &&(line.reason===null||typeof line.reason==="string")&&(line.evidence===null||typeof line.evidence==="string"))
    &&Array.isArray(c.history)&&c.history.length<=200&&c.history.every((event)=>event&&typeof event.action==="string"&&typeof event.actor_id==="string"&&validMeasurementTimestamp(event.at));
}
async function countWrite(path:string,body:object,expectedId?:string){
  const result=await post<{count:unknown}>(path,body);
  if(!validCount(result?.count)||(expectedId!==undefined&&result.count.id!==expectedId))throw new OperatorError("invalid_response",undefined,true);
  return result.count;
}
export const countApi={
  list:async()=>{const result=await request<{counts:unknown[];truncated:boolean}>("/inventory/counts");if(!result||!Array.isArray(result.counts)||result.counts.length>50||!result.counts.every(validCount)||typeof result.truncated!=="boolean")throw new OperatorError("invalid_response");return {counts:result.counts as CountSession[],truncated:result.truncated};},
  read:async(id:string)=>{const r=await request<{count:unknown}>(`/inventory/counts/${id}`);if(!validCount(r?.count)||r.count.id!==id)throw new OperatorError("invalid_response");return r.count;},
  create:(operation_id:string,location_id:string,balance_ids:string[],blind:boolean)=>countWrite("/inventory/counts",{operation_id,location_id,balance_ids,blind}),
  observe:(operation_id:string,count:CountSession,line:CountLine,observed:string,reason:string,evidence:string)=>countWrite(`/inventory/counts/${count.id}/lines/${line.id}`,{operation_id,observed,unit:line.unit,reason,evidence},count.id),
  transition:(operation_id:string,count:CountSession,action:"review"|"complete")=>countWrite(`/inventory/counts/${count.id}/${action}`,{operation_id},count.id),
  loss:async(body:{operation_id:string;balance_id:string;unit:AccountingUnit;quantity:string;expected_stock_version:string;reason_code:string;evidence:string})=>{
    const result=await post<{loss_id:string;stock_movement_id:string;quantity:string;unit:AccountingUnit}>("/inventory/losses",body);
    if(!result||!id(result.loss_id)||!id(result.stock_movement_id)||result.unit!==body.unit||result.quantity!==canonicalQuantity(body.quantity,body.unit)||!validQuantity(result.quantity,body.unit,true))throw new OperatorError("invalid_response",undefined,true);
    return result;
  },
  operation:(id:string)=>request<{operation_id:string;action:string;result:unknown}>(`/inventory/physical-operations/${id}`),
};
