export const API = "/api/v1/operator";
export const MAX_INTEGER = "9223372036854775807";

export type Session = { authenticated: boolean; capabilities: string[] };
export type ProductSummary = { id: string; name: string; category_id: string | null; is_active: boolean };
export type Variant = { id: string; label: string; sku: string; barcode: string | null };
export type Product = ProductSummary & { variants: Variant[] };
export type Category = { id: string; name: string };
export type ProductPage = { products: ProductSummary[]; next_cursor: string | null };
export type CategoryPage = { categories: Category[]; next_cursor: string | null };

// One owner per read stream. Invalidate synchronously when its selection changes.
export class ReadGeneration {
  private current = 0;
  begin() { return ++this.current; }
  accepts(token: number) { return token === this.current; }
  invalidate() { this.current += 1; }
}

export function catalogQuery(category: string, after: string | null = null) {
  const query = new URLSearchParams({ category });
  if (after !== null) query.set("after", after);
  return `/catalog/products?${query}`;
}
export type Stock = { position_id: string; variant_id: string; unit: "EACH"; on_hand: string; version: string; created_at: string; updated_at: string };
export type Movement = { movement_id: string; sequence: string; kind: "RECEIPT" | "ADJUSTMENT" | "CONSUMPTION"; unit: "EACH"; quantity_before: string; delta: string; quantity_after: string; reason: string | null; recorded_at: string };
export type History = { movements: Movement[]; next_cursor: string | null };
export type Price = { revision_id: string; variant_id: string; sequence: string; amount: string; currency: "ARS"; price_basis: "PER_VARIANT"; recorded_at: string };
export type PriceHistory = { revisions: Price[]; next_cursor: string | null };
export type MeasurementUnit = "EACH" | "G" | "KG";
export type MeasurementSpec = { spec_id: string; variant_id: string; version: string;
  sale_unit: MeasurementUnit | null; net_content_amount: string | null;
  net_content_unit: "G" | "KG" | null; created_at: string; updated_at: string };

export type OperationPermit = { readonly id: symbol;
  readonly operation: "classification" | "variant" | "stock" | "history" | "price" | "measurement";
  readonly productId: string };

export class OperationPermitCoordinator {
  private active: OperationPermit | null = null;
  get busy() { return this.active !== null; }
  acquire(operation: OperationPermit["operation"], productId: string): OperationPermit | null {
    if (this.active) return null;
    const permit = { id: Symbol(operation), operation, productId };
    this.active = permit;
    return permit;
  }
  release(permit: OperationPermit): boolean {
    if (this.active !== permit) return false;
    this.active = null;
    return true;
  }
}

export function normalizeMeasurementAmount(value: string): string | null {
  if (!/^(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,6})?$/.test(value)) return null;
  const [whole, fraction = ""] = value.split(".");
  if (whole === "0" && (!fraction || /^0+$/.test(fraction))) return null;
  return `${whole}.${fraction.padEnd(6, "0")}`;
}

function validMeasurementTimestamp(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?(Z|[+-](\d{2}):(\d{2}))$/.exec(value);
  if (!match) return false;
  const year = Number(match[1]), month = Number(match[2]), day = Number(match[3]);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return year > 0 && month >= 1 && month <= 12 && day >= 1 && day <= days[month - 1]
    && Number(match[4]) <= 23 && Number(match[5]) <= 59 && Number(match[6]) <= 59
    && (match[7] === "Z" || Number(match[8]) <= 14 && Number(match[9]) <= 59 &&
        !(Number(match[8]) === 14 && Number(match[9]) !== 0))
    && Number.isFinite(Date.parse(value));
}

export function validMeasurementSpec(value: unknown, variantId: string): value is MeasurementSpec {
  if (!value || typeof value !== "object") return false;
  const spec = value as Partial<MeasurementSpec>;
  const uuid = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i;
  return typeof spec.spec_id === "string" && uuid.test(spec.spec_id)
    && spec.variant_id === variantId && typeof spec.version === "string" && validPositiveInteger(spec.version)
    && (spec.sale_unit === null || spec.sale_unit === "EACH" || spec.sale_unit === "G" || spec.sale_unit === "KG")
    && (spec.net_content_amount === null && spec.net_content_unit === null ||
        typeof spec.net_content_amount === "string" && normalizeMeasurementAmount(spec.net_content_amount) === spec.net_content_amount &&
        (spec.net_content_unit === "G" || spec.net_content_unit === "KG"))
    && validMeasurementTimestamp(spec.created_at) && validMeasurementTimestamp(spec.updated_at);
}

export function normalizePriceInput(value: string): string | null {
  const normalized = value.trim().replace(",", ".");
  if (!/^(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,2})?$/.test(normalized)) return null;
  const [whole, fraction = ""] = normalized.split(".");
  if (whole === "0" && (!fraction || /^0+$/.test(fraction))) return null;
  return `${whole}.${fraction.padEnd(2, "0")}`;
}

export function formatPriceARS(amount: string): string {
  if (!/^(?:0|[1-9][0-9]{0,11})\.[0-9]{2}$/.test(amount)) return "Importe no disponible";
  const [whole, fraction] = amount.split(".");
  return `ARS ${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ".")},${fraction}`;
}

export class OperatorError extends Error {
  constructor(public code: string, public fields?: Record<string, string[]>, public uncertain = false) {
    super(code);
  }
}

let csrfToken: string | undefined;

export function clearOperatorState() {
  csrfToken = undefined;
}

export function validPositiveInteger(value: string) {
  return /^[1-9][0-9]{0,18}$/.test(value) && (value.length < MAX_INTEGER.length || value <= MAX_INTEGER);
}

export function validNonnegativeInteger(value: string) {
  return value === "0" || validPositiveInteger(value);
}

export function validStock(value: unknown, variantId: string): value is Stock {
  if (!value || typeof value !== "object") return false;
  const stock = value as Partial<Stock>;
  return typeof stock.position_id === "string" && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(stock.position_id)
    && stock.variant_id === variantId && stock.unit === "EACH"
    && typeof stock.on_hand === "string" && validNonnegativeInteger(stock.on_hand)
    && typeof stock.version === "string" && validPositiveInteger(stock.version)
    && validMeasurementTimestamp(stock.created_at) && validMeasurementTimestamp(stock.updated_at);
}

export function messageFor(error: unknown): string {
  if (!(error instanceof OperatorError)) return "No pudimos completar la consulta. Intentá nuevamente.";
  if (error.uncertain) return "Resultado incierto: la operación pudo haberse guardado. No vuelvas a enviarla sin verificar.";
  return ({
    invalid_credentials: "No pudimos iniciar sesión con esas credenciales.",
    login_throttled: "Se alcanzó el límite de intentos. Esperá antes de volver a ingresar.",
    authentication_required: "La sesión terminó. Volvé a iniciar sesión.",
    access_denied: "Tu cuenta no tiene permiso para esta operación.",
    csrf_failed: "La verificación de seguridad venció. Recargá la página antes de continuar.",
    identifier_conflict: "El SKU o código de barras ya está registrado. La creación no se realizó.",
    category_conflict: "Ya existe una categoría con ese nombre. Consultá las categorías antes de continuar.",
    stock_conflict: "El stock ya fue inicializado. Consultá el saldo y los movimientos antes de continuar.",
    stock_version_conflict: "La versión del stock cambió. Conservamos tu borrador; consultá el saldo y decidí si querés editar sobre la versión vigente.",
    stock_not_initialized: "Esta variante aún no tiene una recepción inicial. Consultá el saldo antes de ajustar.",
    price_conflict: "La revisión de precio cambió. Consultá el precio vigente antes de decidir una nueva edición.",
    measurement_conflict: "La versión de medición cambió. Consultá la declaración vigente antes de decidir una nueva edición.",
    not_found: "No encontramos el registro solicitado o el entorno operativo no está habilitado.",
    validation_error: "Revisá los datos indicados antes de continuar.",
    unavailable: "El servicio no está disponible. No reenvíes una escritura sin verificar su resultado.",
  } as Record<string, string>)[error.code] ?? "No pudimos completar la operación.";
}

export async function request<T>(path: string, options: RequestInit = {}, fetcher: typeof fetch = fetch): Promise<T> {
  const write = options.method === "POST";
  let response: Response;
  try {
    response = await fetcher(`${API}${path}`, {
      ...options, credentials: "same-origin", cache: "no-store",
      signal: options.signal ?? AbortSignal.timeout(10_000),
      headers: { Accept: "application/json", ...options.headers },
    });
  } catch {
    throw new OperatorError("network_error", undefined, write);
  }
  let body: unknown;
  try { body = await response.json(); } catch {
    throw new OperatorError("invalid_response", undefined, write);
  }
  if (!response.ok) {
    const error = typeof body === "object" && body !== null && "error" in body ? body.error : null;
    const code = typeof error === "object" && error !== null && "code" in error && typeof error.code === "string" ? error.code : "invalid_response";
    const fields = typeof error === "object" && error !== null && "fields" in error && typeof error.fields === "object" && error.fields !== null ? error.fields as Record<string, string[]> : undefined;
    if (code === "authentication_required") {
      clearOperatorState();
      if (typeof window !== "undefined") window.dispatchEvent(new Event("mira-session-ended"));
    }
    throw new OperatorError(code, fields, write && response.status >= 500);
  }
  return body as T;
}

async function token() {
  if (!csrfToken) csrfToken = (await request<{ csrf_token: string }>("/auth/csrf")).csrf_token;
  return csrfToken;
}

export async function signIn(username: string, password: string) {
  const result = await request<{ csrf_token: string }>("/auth/login", {
    method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded", "X-CSRFToken": await token() },
    body: new URLSearchParams({ username, password }).toString(),
  });
  csrfToken = result.csrf_token;
}

export async function signOut() {
  await request("/auth/logout", { method: "POST", headers: { "X-CSRFToken": await token() } });
  clearOperatorState();
}

export async function post<T>(path: string, payload: object): Promise<T> {
  return request<T>(path, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": await token() }, body: JSON.stringify(payload) });
}
