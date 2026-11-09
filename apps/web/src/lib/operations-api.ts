export const API = "/api/v1/operator";
export const MAX_INTEGER = "9223372036854775807";

export type Session = { authenticated: boolean; capabilities: string[] };
export type ProductSummary = { id: string; name: string; category_id: string | null; is_active: boolean };
export type Product = ProductSummary & { variants: { id: string; label: string; sku: string; barcode: string | null }[] };
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
export type Movement = { movement_id: string; sequence: string; kind: "RECEIPT" | "ADJUSTMENT"; unit: "EACH"; quantity_before: string; delta: string; quantity_after: string; reason: string | null; recorded_at: string };
export type History = { movements: Movement[]; next_cursor: string | null };

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
