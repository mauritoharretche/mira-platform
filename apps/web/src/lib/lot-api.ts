import { post, request, validPositiveInteger } from "./operations-api";

export type Location = { id: string; code: string; name: string; active: boolean; commercial_eligible: boolean; version: string };
export type Lot = { id: string; code: string; expires_on: string | null; status: "ACTIVE" | "BLOCKED" | "QUARANTINE";
  expired: boolean; version: string; quantity: string; locations: { id: string; code: string; quantity: string }[] };
export type LotState = { on_hand: string; unallocated: string; lots: Lot[]; fefo: string[]; truncated: boolean };

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
      quantity, reason, expected_version: lot.version }),
  correct: (lot: Lot, locationId: string, delta: string, stockVersion: string, reason: string) =>
    post(`/inventory/lots/${lot.id}/correction`, { location_id: locationId, delta, reason,
      expected_lot_version: lot.version, expected_stock_version: stockVersion }),
};

export function validSignedQuantity(value: string) {
  const digits = value.startsWith("-") ? value.slice(1) : value;
  return validPositiveInteger(digits) && value !== "0";
}
