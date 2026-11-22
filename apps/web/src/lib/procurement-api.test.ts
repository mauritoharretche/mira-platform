import { describe, expect, it } from "vitest";
import { OperatorError } from "./operations-api";
import { procurementFailure, validCost, validOrderLine, validReceiptLine } from "./procurement-api";

const id = "12345678-1234-1234-1234-123456789abc";

describe("procurement operator input", () => {
  it("keeps ARS exact and rejects floats, exponent notation and malformed quantities", () => {
    expect(validCost("0.00")).toBe(true);
    expect(validCost("12.30")).toBe(true);
    expect(["12.3", "12,30", "1e3", "01.00", "12.300", "-1.00"].every((value) => !validCost(value))).toBe(true);
    expect(validOrderLine({ variant_id: id, quantity: "5", unit: "EACH", unit_cost_ars: "12.30" })).toBe(true);
    expect(validOrderLine({ variant_id: id, quantity: "5.5", unit: "EACH", unit_cost_ars: "12.30" })).toBe(false);
  });

  it("requires explicit differences and keeps receipt quantities within each delivery", () => {
    const line = { order_line_id: id, delivered_quantity: "5", accepted_quantity: "4", difference_reason: "Fictional shortage", expected_stock_version: "2" };
    expect(validReceiptLine(line)).toBe(true);
    expect(validReceiptLine({ ...line, accepted_quantity: "6" })).toBe(false);
    expect(validReceiptLine({ ...line, difference_reason: null })).toBe(false);
    expect(validReceiptLine({ ...line, accepted_quantity: "5", difference_reason: null })).toBe(true);
    expect(validReceiptLine({ ...line, delivered_quantity: "5.5" })).toBe(false);
  });

  it("does not present an uncertain write as a confirmed result", () => {
    expect(procurementFailure(new OperatorError("network_error", undefined, true))).toContain("incierto");
    expect(procurementFailure(new OperatorError("procurement_conflict"))).toContain("Conservamos el borrador");
  });
});
