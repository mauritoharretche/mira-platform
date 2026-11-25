import { AccountingUnit, quantityBase } from "./quantities";
import { post, request } from "./operations-api";

export type Location = { id: string; code: string; name: string; active: boolean; commercial_eligible: boolean; version: string };
export type Lot = { id: string; code: string; expires_on: string | null; status: "ACTIVE" | "BLOCKED" | "QUARANTINE";
  unit?: AccountingUnit | null; expired: boolean; version: string; quantity: string; locations: { id: string; balance_id?: string; code: string; quantity: string }[] };
export type LotState = { unit?: AccountingUnit | null; on_hand: string; unallocated: string; lots: Lot[]; fefo: string[]; truncated: boolean };

export const lots = {
  locations: () => request<{ locations: Location[]; truncated: boolean }>("/inventory/locations"),
  createLocation: (code: string, name: string) => post<{ location: Location }>("/inventory/locations", { code, name }),
  state: (variantId: string) => request<LotState>(`/inventory/variants/${encodeURIComponent(variantId)}/lots`),
  createLot: (variantId: string, code: string, expiresOn: string | null) =>
    post<{ lot: { id: string } }>(`/inventory/variants/${encodeURIComponent(variantId)}/lots`, { code, expires_on: expiresOn }),
  status: (lot: Lot, status: Lot["status"], reason: string) =>
    post(`/inventory/lots/${lot.id}/status`, { status, reason, expected_version: lot.version }),
  transfer: (lot: Lot, sourceId: string, destinationId: string, quantity: string, reason: string) =>
    post(`/inventory/lots/${lot.id}/transfer`, { source_id: sourceId, destination_id: destinationId,
      quantity, unit: lot.unit ?? "EACH", reason, expected_version: lot.version }),
  correct: (lot: Lot, locationId: string, delta: string, stockVersion: string, reason: string) =>
    post(`/inventory/lots/${lot.id}/correction`, { location_id: locationId, delta, unit: lot.unit ?? "EACH", reason,
      expected_lot_version: lot.version, expected_stock_version: stockVersion }),
};

export function validSignedQuantity(value: string, unit: AccountingUnit = "EACH") {
  const parsed=quantityBase(value,unit,true);return parsed!==null && parsed!==0n;
}
