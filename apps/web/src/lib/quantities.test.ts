import {describe,expect,it} from "vitest";
import {canonicalQuantity,convertMass,quantityBase,validQuantity} from "./quantities";
import {validReservationLine} from "./reservation-api";
import {validStock} from "./operations-api";

describe("exact measured quantities",()=>{
  it("converts G and KG through integer milligrams without rounding",()=>{
    expect(convertMass("0.245001","KG","G")).toBe("245.001");
    expect(convertMass("0.001","G","KG")).toBe("0.000001");
    expect(canonicalQuantity("245.1","G")).toBe("245.100");
    expect(quantityBase("9223372036854775.807","G")).toBe(9223372036854775807n);
    expect(quantityBase("-0.001","G",true)).toBe(-1n);
  });
  it("rejects invalid scales, units, overflow and implicit coercion",()=>{
    for(const value of ["1.0001","NaN","1e3","01.0","9223372036854775.808"])
      expect(validQuantity(value,"G")).toBe(false);
    expect(validQuantity("1.0","EACH")).toBe(false);
    expect(validQuantity("1","KG" as "G")).toBe(false);
    expect(validQuantity("0","G",true)).toBe(false);
    expect(convertMass("0.0000001","KG","G")).toBeNull();
  });
  it("keeps units explicit in stock and reservation contracts",()=>{
    const id="00000000-0000-0000-0000-000000000001";
    expect(validReservationLine({variant_id:id,label:"Fictional",unit:"G",quantity:"245.001"})).toBe(true);
    expect(validReservationLine({variant_id:id,label:"Fictional",quantity:"245.001"})).toBe(false);
    expect(validStock({position_id:id,created_at:"2026-11-24T00:00:00Z",updated_at:"2026-11-24T00:00:00Z",variant_id:id,unit:"G",on_hand:"245.001",version:"1"},id)).toBe(true);
    expect(validStock({position_id:id,created_at:"2026-11-24T00:00:00Z",updated_at:"2026-11-24T00:00:00Z",variant_id:id,unit:"G",on_hand:"245.0001",version:"1"},id)).toBe(false);
  });
});
