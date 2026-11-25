import { describe,it,expect } from "vitest";
import { OperatorError } from "./operations-api";
import { manualLine, PosProduct, posFailure, Quote, Sale, validQuote, validSale, validDiscovery } from "./pos-api";
const id="10000000-0000-4000-8000-000000000001";
const line={variant_id:id,product_name:"Fictional grain",variant_label:"Pack",sku:"FIC-1",barcode:null,quantity:"1",unit:"EACH",accounting_unit:"EACH",measurement:null,amount_ars:"100.00",price_amount:"100.00",price_basis:"PER_VARIANT",price_revision_id:id,rounding:"HALF_EVEN_CENTS_PER_LINE"};
const q={id,state:"QUOTED",currency:"ARS",created_at:"2026-11-25T12:00:00Z",expires_at:"2026-11-25T12:05:00Z",total_ars:"100.00",lines:[line]} as Quote;
const sale={id,actor_id:"fictional-cashier",operation_id:id,quote_id:id,reservation_id:id,state:"COMMITTED_UNSETTLED",settlement:"UNSETTLED",total_ars:"100.00",currency:"ARS",created_at:q.created_at,lines:q.lines,inventory_trace:{state:"CONSUMED",allocations:[{stock_movement_id:id,lot_code:"FIC-L",location_code:"FIC-S",quantity:"1",unit:"EACH"}]}} as Sale;
describe("POS authoritative payload and uncertain boundary",()=>{
 it("accepts bounded exact authoritative quote and unsettled result",()=>{expect(validQuote(q)).toBe(true);expect(validSale(sale,id,id)).toBe(true);});
 it("rejects invalid timestamps and false settlement",()=>{expect(validQuote({...q,expires_at:"invalid"})).toBe(false);expect(validSale({...sale,created_at:"invalid"})).toBe(false);expect(validSale({...sale,settlement:"PAID"} as unknown as Sale)).toBe(false);});
 it("rejects manipulated totals, overprecision and duplicate lines",()=>{expect(validQuote({...q,total_ars:"99.00"})).toBe(false);expect(validQuote({...q,total_ars:"100.001"})).toBe(false);expect(validQuote({...q,lines:[...q.lines,...q.lines],total_ars:"200.00"})).toBe(false);});
 it("rejects mismatched operation and missing inventory attribution",()=>{expect(validSale(sale,"different",id)).toBe(false);expect(validSale({...sale,inventory_trace:{...sale.inventory_trace,allocations:[]}})).toBe(false);});
 it("preserves manual exact strings without float conversion or fabricated device metadata",()=>{const m=manualLine({variant_id:id} as PosProduct,"0.245001","KG","0.000001","","");expect(m.quantity).toBe("0.245001");expect(m.measurement?.net).toBe("0.245001");expect(m.measurement?.source).toBe("manual");expect(m.measurement?.gross).toBeNull();});
 it("mass DTO validates milligram precision and explicit measurement",()=>{const m=manualLine({variant_id:id} as PosProduct,"0.245001","KG","0.000001","","");const mq={...q,total_ars:"24.50",lines:[{...line,...m,accounting_unit:"G",price_basis:"PER_KG",amount_ars:"24.50"}]} as Quote;expect(validQuote(mq)).toBe(true);expect(validQuote({...mq,lines:[{...mq.lines[0],quantity:"0.0000001"}]})).toBe(false);});
 it("conflict requires review; uncertain results prohibit resubmission",()=>{expect(posFailure(new OperatorError("pos_conflict"))).toContain("revisá una nueva cotización");expect(posFailure(new OperatorError("network_error",undefined,true))).toContain("No vuelvas a confirmar");});
});

it("rejects malformed rendered actor/allocation fields without false confirmation",()=>{
 expect(validSale({...sale,actor_id:{private:"invalid"}} as unknown as Sale)).toBe(false);
 expect(validSale({...sale,inventory_trace:{...sale.inventory_trace,allocations:[null]}} as unknown as Sale)).toBe(false);
 expect(validSale({...sale,inventory_trace:{...sale.inventory_trace,allocations:[{...sale.inventory_trace.allocations[0],quantity:{bad:true}}]}} as unknown as Sale)).toBe(false);
});
it("malformed read results are rejected before rendering",()=>{
 expect(validDiscovery({items:[{product_name:{bad:true}} as unknown as PosProduct],next_cursor:null})).toBe(false);
 expect(validDiscovery({items:[],next_cursor:null})).toBe(true);
});
