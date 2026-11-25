import { describe, expect, it } from "vitest";
import { validAvailability, validReservation, validReservationLine } from "./reservation-api";

const id="11111111-1111-4111-8111-111111111111";
describe("commercial inventory trust boundary",()=>{
  it("rejects money-like, boolean, fractional, zero and overflowing EACH quantities",()=>{
    for(const value of ["0","01","1.0","-1","9223372036854775808"])
      expect(validReservationLine({variant_id:id,quantity:value,label:"Fictional"})).toBe(false);
    expect(validReservationLine({variant_id:id,quantity:"1",label:"Fictional"})).toBe(true);
  });
  it("rejects malformed reservation success DTOs before confirming a write",()=>{
    expect(validReservation({id,operation_id:id,state:"CONSUMED"})).toBe(false);
    expect(validReservation(null)).toBe(false);
    expect(validReservation({id,operation_id:id,source:"Fictional",reference:"Fictional",state:"ACTIVE",effective_state:"ACTIVE",
      created_at:"invalid",expires_at:"invalid",allocations:[],history:[]})).toBe(false);
  });
  it("rejects an availability snapshot for another variant or numeric JSON count",()=>{
    const valid={variant_id:id,as_of:"2026-11-24T12:00:00Z",physical:"2",allocated:"2",unallocated:"0",eligible:"2",
      reserved:"1",unavailable:"1",available:"1",safety_stock:"0",balances:[]};
    expect(validAvailability(valid,id)).toBe(true);
    expect(validAvailability({...valid,variant_id:"other"},id)).toBe(false);
    expect(validAvailability({...valid,available:1},id)).toBe(false);
  });
});
