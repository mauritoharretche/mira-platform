import { OperatorError, post, request, validNonnegativeInteger, validPositiveInteger } from "./operations-api";

export type Supplier = { id: string; name: string; active: boolean; version: string };
export type OrderLine = { id: string; variant_id: string; variant_label: string; variant_sku: string; product_name: string;
  unit: "EACH"; quantity: string; received_quantity: string; pending_quantity: string; unit_cost_ars: string };
export type Order = { id: string; supplier_id: string; supplier_name: string; status: "DRAFT" | "CONFIRMED" | "PARTIAL" | "COMPLETE";
  version: string; lines: OrderLine[]; total_cost_ars: string; history?: { action: string; version: string; occurred_at: string }[];
  receipts?: Receipt[] };
export type Receipt = { id: string; order_id: string; operation_id: string; recorded_at: string;
  lines: { order_line_id: string; delivered_quantity: string; accepted_quantity: string;
    difference_reason: string | null; stock_movement_id: string | null;
    allocations: { lot_id: string; location_id: string; quantity: string }[] | null }[] };
export type VariantReference = { id: string; label: string; sku: string; product_name: string };
export type DraftLine = { variant_id: string; quantity: string; unit: "EACH"; unit_cost_ars: string };
export type ReceiptDraftLine = { order_line_id: string; delivered_quantity: string; accepted_quantity: string;
  difference_reason: string | null; expected_stock_version: string | null; lot_id?: string; location_id?: string };

const uuid = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i;
const money = /^(?:0|[1-9][0-9]{0,11})\.[0-9]{2}$/;
export function validCost(value: string) { return money.test(value); }
export function validOrderLine(value: DraftLine) {
  return uuid.test(value.variant_id) && value.unit === "EACH" && validPositiveInteger(value.quantity) && validCost(value.unit_cost_ars);
}
export function validReceiptLine(value: ReceiptDraftLine) {
  return uuid.test(value.order_line_id) && validPositiveInteger(value.delivered_quantity)
    && validNonnegativeInteger(value.accepted_quantity)
    && BigInt(value.accepted_quantity) <= BigInt(value.delivered_quantity)
    && (value.accepted_quantity === value.delivered_quantity ? value.difference_reason === null
      : Boolean(value.difference_reason?.trim()))
    && (value.accepted_quantity === "0" ? value.expected_stock_version === null
      : value.expected_stock_version === null || validPositiveInteger(value.expected_stock_version))
    && ((!value.lot_id && !value.location_id) ||
      Boolean(value.lot_id && value.location_id && value.accepted_quantity !== "0"));
}
export function procurementFailure(error: unknown) {
  if (error instanceof OperatorError && error.code === "procurement_conflict")
    return "El registro cambió. Conservamos el borrador: consultá el estado vigente antes de decidir cómo continuar.";
  if (error instanceof OperatorError && error.uncertain)
    return "Resultado incierto. La operación pudo haberse guardado. Consultá su identidad antes de volver a escribir.";
  return error instanceof OperatorError && error.code === "validation_error"
    ? "Revisá los datos de la operación." : "No pudimos completar la operación. Consultá el estado antes de volver a escribir.";
}

export const procurement = {
  suppliers: (search = "") => request<{ suppliers: Supplier[]; truncated: boolean }>(`/procurement/suppliers?search=${encodeURIComponent(search)}`),
  supplier: (id: string) => request<{ supplier: Supplier }>(`/procurement/suppliers/${id}`),
  createSupplier: (name: string) => post<{ supplier: Supplier }>("/procurement/suppliers", { name }),
  updateSupplier: (supplier: Supplier, name: string, active: boolean) =>
    post<{ supplier: Supplier }>(`/procurement/suppliers/${supplier.id}`, { name, active, expected_version: supplier.version }),
  orders: () => request<{ orders: Order[]; truncated: boolean }>("/procurement/orders"),
  order: (id: string) => request<{ order: Order }>(`/procurement/orders/${id}`),
  createOrder: (supplierId: string, lines: DraftLine[]) =>
    post<{ order: Order }>("/procurement/orders", { supplier_id: supplierId, lines }),
  updateOrder: (order: Order, supplierId: string, lines: DraftLine[]) =>
    post<{ order: Order }>(`/procurement/orders/${order.id}`, { supplier_id: supplierId, lines, expected_version: order.version }),
  confirm: (order: Order) => post<{ order: Order }>(`/procurement/orders/${order.id}/confirm`, { expected_version: order.version }),
  receive: (order: Order, operationId: string, lines: ReceiptDraftLine[]) =>
    post<{ order: Order; receipt: Receipt }>(`/procurement/orders/${order.id}/receipts`, {
      operation_id: operationId, expected_version: order.version, lines: lines.map((line) => ({
        order_line_id: line.order_line_id, delivered_quantity: line.delivered_quantity,
        accepted_quantity: line.accepted_quantity, difference_reason: line.difference_reason,
        expected_stock_version: line.expected_stock_version,
        ...(line.lot_id && line.location_id ? { allocations: [{ lot_id: line.lot_id,
          location_id: line.location_id, quantity: line.accepted_quantity }] } : {}),
      })),
    }),
  receiptByOperation: (operationId: string) =>
    request<{ receipt: Receipt }>(`/procurement/receipts/operations/${operationId}`),
  variants: (q: string) => request<{ variants: VariantReference[]; truncated: boolean }>(`/catalog/variants/search?q=${encodeURIComponent(q)}`),
};
