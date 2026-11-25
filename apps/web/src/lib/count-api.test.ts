import {describe,expect,it} from "vitest";
import {validCount} from "./count-api";
const uuid="00000000-0000-4000-8000-000000000001";
const count={id:uuid,location_id:uuid,responsible_actor_id:"fictional-operator",state:"OPEN",blind:true,
  lines:[{id:uuid,balance_id:uuid,variant_id:uuid,lot_code:"FICTIONAL-LOT",unit:"G",observed:null,reason:null,evidence:null}],
  history:[{action:"count.created",actor_id:"fictional-operator",at:"2026-11-24T12:00:00Z"}]};
describe("physical count response boundary",()=>{
  it("accepts a blind snapshot without inventing expected quantities",()=>{
    expect(validCount(count)).toBe(true);
    expect(validCount({...count,lines:[{...count.lines[0],observed:"245.001",expected:"245.501",discrepancy:"-0.500"}]})).toBe(true);
  });
  it("rejects malformed, ambiguous and oversized response states before rendering",()=>{
    for(const bad of [{...count,state:"UNKNOWN"},{...count,history:null},{...count,blind:"true"},
      {...count,lines:[{...count.lines[0],unit:"KG"}]},{...count,lines:[{...count.lines[0],observed:"1.0001"}]},
      {...count,lines:[count.lines[0],count.lines[0]]},{...count,lines:Array(101).fill(count.lines[0])},
      {...count,history:[{...count.history[0],at:"not-a-date"}]}])expect(validCount(bad)).toBe(false);
  });
});
