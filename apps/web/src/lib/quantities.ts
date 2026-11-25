export type AccountingUnit = "EACH" | "G";
const max=9223372036854775807n;
export function quantityBase(value: string,unit: AccountingUnit,signed=false): bigint | null {
  if((unit!=="EACH"&&unit!=="G")||typeof value!=="string"||value.length>30||!/^(-?)(0|[1-9][0-9]*)(?:\.([0-9]+))?$/.test(value))return null;
  const negative=value.startsWith("-");if(negative&&!signed)return null;
  const [whole,fraction=""]=(negative?value.slice(1):value).split(".");
  if(unit==="EACH"&&fraction.length||unit==="G"&&fraction.length>3)return null;
  const base=unit==="EACH"?BigInt(whole):BigInt(whole)*1000n+BigInt(fraction.padEnd(3,"0"));
  return base>max?null:negative?-base:base;
}
export function validQuantity(value:string,unit:AccountingUnit,positive=false) {
  const base=quantityBase(value,unit);return base!==null&&(!positive||base>0n);
}
export function canonicalQuantity(value:string,unit:AccountingUnit) {
  const base=quantityBase(value,unit);if(base===null)return null;
  return unit==="EACH"?base.toString():`${base/1000n}.${(base%1000n).toString().padStart(3,"0")}`;
}
export function convertMass(value:string,from:"G"|"KG",to:"G"|"KG"): string|null {
  if((from!=="G"&&from!=="KG")||(to!=="G"&&to!=="KG"))return null;
  if(typeof value!=="string"||value.length>30||!/^(0|[1-9][0-9]*)(?:\.[0-9]+)?$/.test(value))return null;
  const [whole,fraction=""]=value.split(".");const scale=from==="G"?3:6;
  if(fraction.length>scale)return null;
  const base=BigInt(whole)*10n**BigInt(scale)+BigInt(fraction.padEnd(scale,"0"));if(base>max)return null;
  const targetScale=to==="G"?3:6;const divisor=10n**BigInt(targetScale);
  return `${base/divisor}.${(base%divisor).toString().padStart(targetScale,"0")}`;
}
