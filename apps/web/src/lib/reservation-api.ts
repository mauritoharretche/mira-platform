import { AccountingUnit, validQuantity, canonicalQuantity } from "./quantities";
import { OperatorError, post, request, validPositiveInteger } from "./operations-api";

export type AvailabilityBalance = { balance_id: string; lot_id: string; lot_code: string; location_id: string;
  location_code: string; expires_on: string | null; physical: string; eligible: boolean; reserved: string; available: string };
export type Availability = { unit?: AccountingUnit | null; variant_id: string; as_of: string; physical: string; allocated: string; unallocated: string;
  eligible: string; reserved: string; unavailable: string; available: string; safety_stock: string; balances: AvailabilityBalance[] };
export type Reservation = { id: string; operation_id: string; source: string; reference: string;
  state: "ACTIVE" | "RELEASED" | "CONSUMED" | "EXPIRED"; effective_state: Reservation["state"];
  created_at: string; expires_at: string; allocations: { id: string; variant_id: string; lot_id: string;
    lot_code: string; location_id: string; location_code: string; quantity: string; unit?: AccountingUnit; stock_movement_id: string | null }[];
  history: { action: string; at: string; actor_id: string }[] };
export type ReservationDraftLine = { unit?: AccountingUnit; variant_id: string; quantity: string; label: string };
const uuid = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i;
const states = ["ACTIVE", "RELEASED", "CONSUMED", "EXPIRED"];
const validTime = (value: unknown): value is string => typeof value === "string" && Number.isFinite(Date.parse(value));
export function validReservation(value: unknown): value is Reservation {
  if (!value || typeof value !== "object") return false;
  const r = value as Reservation;
  return typeof r.id === "string" && uuid.test(r.id) && typeof r.operation_id === "string" && uuid.test(r.operation_id)
    && states.includes(r.state) && states.includes(r.effective_state) && typeof r.source === "string" && typeof r.reference === "string"
    && validTime(r.created_at) && validTime(r.expires_at) && Array.isArray(r.allocations) && r.allocations.length > 0
    && r.allocations.every((a) => a && uuid.test(a.id) && uuid.test(a.variant_id) && uuid.test(a.lot_id) && uuid.test(a.location_id)
      && typeof a.lot_code === "string" && typeof a.location_code === "string" && typeof a.quantity === "string" && validQuantity(a.quantity,a.unit ?? "EACH",true))
    && Array.isArray(r.history) && r.history.every((e) => e && typeof e.action === "string" && validTime(e.at) && typeof e.actor_id === "string");
}
export function validAvailability(value: unknown, variantId: string): value is Availability {
  if (!value || typeof value !== "object") return false;
  const a = value as Availability;
  return a.variant_id === variantId && validTime(a.as_of) && [a.physical,a.allocated,a.unallocated,a.eligible,a.reserved,a.unavailable,a.available,a.safety_stock]
    .every((v) => typeof v === "string" && validQuantity(v,a.unit ?? "EACH")) && Array.isArray(a.balances)
    && a.balances.every((b) => b && uuid.test(b.balance_id) && typeof b.lot_code === "string" && typeof b.location_code === "string"
      && typeof b.eligible === "boolean" && [b.physical,b.reserved,b.available].every((v) => typeof v === "string" && validQuantity(v,a.unit ?? "EACH")));
}
export function validReservationLine(line: ReservationDraftLine) {
  return uuid.test(line.variant_id) && validQuantity(line.quantity,line.unit ?? "EACH",true);
}
async function reservationWrite(path: string, body: object) {
  const result = await post<{ reservation: unknown }>(path,body);
  if (!validReservation(result?.reservation)) throw new OperatorError("invalid_response", undefined, true);
  return result.reservation;
}
export const reservationApi = {
  classify: async (body: {operation_id:string;lot_id:string;location_id:string;quantity:string;expected_stock_version:string|null;evidence:string;unit?:AccountingUnit}) => {
    const result=await post<{classified:string;lot_id:string}>("/inventory/classifications",body);
    if(result?.classified!==canonicalQuantity(body.quantity,body.unit ?? "EACH") || result?.lot_id!==body.lot_id) throw new OperatorError("invalid_response",undefined,true);
    return result;
  },
  eligibility: async (id:string,eligible:boolean,version:string,reason:string) => {
    const result=await post<{location_id:string;eligible:boolean;version:string}>(`/inventory/locations/${id}/eligibility`,{eligible,expected_version:version,reason});
    if(result?.location_id!==id || result?.eligible!==eligible || typeof result?.version!=="string" || !validPositiveInteger(result.version))
      throw new OperatorError("invalid_response",undefined,true);
    return result;
  },
  availability: async (id: string) => {
    const result = await request<unknown>(`/inventory/variants/${id}/availability`);
    if (!validAvailability(result,id)) throw new OperatorError("invalid_response");
    return result;
  },
  list: async (id: string) => {
    const result = await request<{ reservations: Reservation[]; truncated: boolean }>(`/inventory/reservations?variant_id=${id}`);
    if (!Array.isArray(result?.reservations) || !result.reservations.every(validReservation)) throw new OperatorError("invalid_response");
    return result;
  },
  read: async (id: string) => {
    const result = await request<{ reservation: Reservation }>(`/inventory/reservations/${id}`);
    if (!validReservation(result?.reservation)) throw new OperatorError("invalid_response");
    return result.reservation;
  },
  create: (operationId: string, source: string, reference: string, expiresAt: string, lines: ReservationDraftLine[]) =>
    reservationWrite("/inventory/reservations",{ operation_id: operationId,source,reference,expires_at: expiresAt,
      lines: lines.map((line) => ({variant_id:line.variant_id,quantity:line.quantity,unit:line.unit ?? "EACH"})) }),
  transition: (id: string, action: "release" | "expire" | "consume", operationId: string) =>
    reservationWrite(`/inventory/reservations/${id}/${action}`,{operation_id:operationId}),
  operation: (id: string) => request<{ operation_id: string; action: string; result: unknown }>(`/inventory/operations/${id}`),
};
