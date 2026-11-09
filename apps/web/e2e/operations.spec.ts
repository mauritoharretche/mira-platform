import { expect, Page, test, TestInfo } from "@playwright/test";
import { existsSync } from "node:fs";
import path from "node:path";

const root = "/api/v1/operator";
const password = process.env.MIRA_E2E_PASSWORD!;
const artifacts = process.env.MIRA_E2E_ARTIFACT_DIR!;
const username = (info: TestInfo) => `fictional-${info.project.name}-${info.title}`;

async function login(page: Page, info: TestInfo) {
  await page.goto("/login");
  await page.getByLabel("Usuario", { exact: true }).fill(username(info));
  await page.keyboard.press("Tab");
  await expect(page.getByLabel("Contraseña", { exact: true })).toBeFocused();
  await page.getByLabel("Contraseña", { exact: true }).fill(password);
  if (info.title === "journey") await page.screenshot({ path: path.join(artifacts, `login-${info.project.name}.png`), fullPage: true });
  await page.getByLabel("Contraseña", { exact: true }).press("Enter");
  await expect(page).toHaveURL(/\/operations$/);
  await expect(page.getByRole("heading", { name: "Prepará tu primera recepción" })).toBeVisible();
  if (info.title === "journey") await page.screenshot({ path: path.join(artifacts, `creation-${info.project.name}.png`), fullPage: true });
}

async function create(page: Page, info: TestInfo) {
  const sku = `FICTIONAL-${info.project.name}-${info.title}`;
  await page.getByLabel("Nombre del producto", { exact: true }).fill("Producto de demostración");
  await page.getByLabel("Presentación", { exact: true }).fill("Presentación inicial");
  await page.getByLabel("SKU", { exact: true }).fill(sku);
  await page.getByLabel("Código de barras (opcional)").fill("");
  await page.getByRole("button", { name: "Crear producto y presentación" }).click();
  await expect(page).toHaveURL(/\/operations\/products\/[0-9a-f-]{36}$/);
  await expect(page.getByText("Sin recepción inicial", { exact: true })).toBeVisible();
  return page.url().split("/").at(-1)!;
}

async function receipt(page: Page) {
  await page.getByLabel("Unidad de inventario", { exact: true }).selectOption("EACH");
  await page.getByLabel("Cantidad recibida", { exact: true }).fill("12");
  await page.getByRole("button", { name: "Registrar recepción inicial", exact: true }).click();
}

async function control(action: "revoke" | "expire", info: TestInfo) {
  const account = username(info);
  process.stdout.write(`\nMIRA_BROWSER_CONTROL=${JSON.stringify({ action, username: account })}\n`);
  await expect.poll(() => existsSync(path.join(artifacts, `${action}-${account}.ack`))).toBe(true);
}

test("journey", async ({ page }, info) => {
  await login(page, info);
  await create(page, info);
  await receipt(page);
  await expect(page.getByRole("status")).toContainText("Recepción inicial registrada");
  await expect(page.locator(".stock-count")).toHaveText("12 EACH");
  await expect(page.getByRole("cell", { name: /^(Movimiento )?Recepción$/ })).toBeVisible();
  await page.reload();
  await expect(page.locator(".stock-count")).toHaveText("12 EACH");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: path.join(artifacts, `journey-${info.project.name}.png`), fullPage: true });
  await page.getByRole("button", { name: "Cerrar sesión" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.goto("/operations");
  await expect(page).toHaveURL(/\/login\?expired=1$/);
});

test("csrf", async ({ page }, info) => {
  await page.goto("/login");
  const result = await page.evaluate(async ({ root, username, password }) => {
    const before = await (await fetch(`${root}/auth/csrf`)).json();
    const body = new URLSearchParams({ username, password });
    const unprotected = await fetch(`${root}/auth/login`, { method: "POST", body });
    const login = await fetch(`${root}/auth/login`, { method: "POST", body, headers: { "X-CSRFToken": before.csrf_token } });
    const after = await login.json();
    const oldLogout = await fetch(`${root}/auth/logout`, { method: "POST", headers: { "X-CSRFToken": before.csrf_token } });
    return { unprotected: unprotected.status, login: login.status, rotated: before.csrf_token !== after.csrf_token, oldLogout: oldLogout.status, cache: login.headers.get("cache-control") };
  }, { root, username: username(info), password });
  expect(result).toEqual({ unprotected: 403, login: 200, rotated: true, oldLogout: 403, cache: "no-store" });
  for (const source of await page.locator("script[src]").evaluateAll((scripts) => scripts.map((script) => (script as HTMLScriptElement).src))) {
    const asset = await page.request.get(source);
    expect(await asset.text()).not.toContain(process.env.MIRA_API_BASE_URL!);
  }
  expect(await page.evaluate(() => Object.keys(localStorage).length + Object.keys(sessionStorage).length)).toBe(0);
  const cookies = await page.context().cookies();
  for (const name of ["mira_operator_session", "mira_operator_csrf"]) {
    expect(cookies.find((cookie) => cookie.name === name)).toMatchObject({ httpOnly: true, secure: false, sameSite: "Lax", path: "/api/v1/operator/", domain: "localhost" });
  }
  const session = cookies.find((cookie) => cookie.name === "mira_operator_session")!;
  expect(session.expires - Date.now() / 1000).toBeGreaterThan(28_700);
  expect(session.expires - Date.now() / 1000).toBeLessThanOrEqual(28_800);
  const badOrigin = await page.request.post(`${root}/auth/login`, {
    form: { username: username(info), password }, headers: { Origin: "https://untrusted.example", "X-CSRFToken": "fictional-invalid-token" },
  });
  expect(badOrigin.status()).toBe(403);
  await page.context().clearCookies();
  const spoof = await page.request.get(`${root}/catalog/products/00000000-0000-0000-0000-000000000000`, {
    headers: { "X-User-Id": "1", "X-Forwarded-User": "1", "X-Forwarded-Host": "untrusted.example", Authorization: "Basic ZmFrZTpmYWtl" },
  });
  expect(spoof.status()).toBe(403);
});

test("denied", async ({ page }, info) => {
  await login(page, info);
  await expect(page.getByRole("button", { name: "Crear producto y presentación" })).toBeDisabled();
  await page.getByLabel("UUID del producto").fill("00000000-0000-0000-0000-000000000000");
  await page.getByRole("button", { name: "Abrir producto" }).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText("no tiene permiso");
});

test("revoked", async ({ page }, info) => {
  await login(page, info);
  await control("revoke", info);
  await page.getByLabel("Nombre del producto", { exact: true }).fill("Producto ficticio rechazado");
  await page.getByLabel("Presentación", { exact: true }).fill("Presentación");
  await page.getByLabel("SKU", { exact: true }).fill(`FICTIONAL-${info.project.name}-revoked`);
  await page.getByRole("button", { name: "Crear producto y presentación" }).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText("no tiene permiso");
  await expect(page).toHaveURL(/\/operations$/);
});

test("duplicate", async ({ page }, info) => {
  await login(page, info);
  const product = await create(page, info);
  const initialized = await page.evaluate(async ({ root, product }) => {
    const token = await (await fetch(`${root}/auth/csrf`)).json();
    const detail = await (await fetch(`${root}/catalog/products/${product}`)).json();
    return (await fetch(`${root}/inventory/variants/${detail.variants[0].id}/initial-receipt`, {
      method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": token.csrf_token },
      body: JSON.stringify({ quantity: "12", unit: "EACH", expected_version: null }),
    })).status;
  }, { root, product });
  expect(initialized).toBe(201);
  await receipt(page); // UI still holds its earlier, uninitialized observation
  await expect(page.getByRole("main").getByRole("alert")).toContainText("stock ya fue inicializado");
  await expect(page.locator(".stock-count")).toHaveText("12 EACH");
  await expect(page.locator(".success-box")).toHaveCount(0);
});

test("lost-creation", async ({ page }, info) => {
  await login(page, info);
  let posts = 0;
  await page.route("**/api/v1/operator/catalog/products", async (route) => {
    posts += 1;
    const response = await route.fetch();
    expect(response.status()).toBe(201);
    await route.abort("failed");
  });
  await page.getByLabel("Nombre del producto", { exact: true }).fill("Producto ficticio con respuesta perdida");
  await page.getByLabel("Presentación", { exact: true }).fill("Presentación inicial");
  await page.getByLabel("SKU", { exact: true }).fill(`FICTIONAL-${info.project.name}-lost-creation`);
  await page.getByRole("button", { name: "Crear producto y presentación" }).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText("Resultado incierto");
  await expect(page.getByRole("button", { name: "Crear producto y presentación" })).toBeDisabled();
  await expect(page.getByText(/No recibimos el UUID/)).toBeVisible();
  expect(posts).toBe(1);
});

test("lost-receipt", async ({ page }, info) => {
  await login(page, info);
  await create(page, info);
  let posts = 0;
  await page.route("**/initial-receipt", async (route) => {
    posts += 1;
    const response = await route.fetch();
    expect(response.status()).toBe(201);
    await route.abort("failed");
  });
  await receipt(page);
  await expect(page.getByRole("main").getByRole("alert")).toContainText("Resultado incierto");
  await expect(page.locator(".stock-count")).toHaveText("12 EACH");
  await expect(page.locator(".success-box")).toHaveCount(0);
  expect(posts).toBe(1);
});

test("expired", async ({ page }, info) => {
  await login(page, info);
  await create(page, info);
  await control("expire", info);
  await page.getByRole("button", { name: "Actualizar consulta" }).click();
  await expect(page).toHaveURL(/\/login\?expired=1$/);
  await expect(page.getByRole("heading", { name: "Tu espacio de operaciones" })).toBeVisible();
  await expect(page.locator(".stock-count")).toHaveCount(0);
});
