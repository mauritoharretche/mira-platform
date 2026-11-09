import { expect, Page, test, TestInfo } from "@playwright/test";
import { existsSync } from "node:fs";
import path from "node:path";

const root = "/api/v1/operator";
const password = process.env.MIRA_E2E_PASSWORD!;
const artifacts = process.env.MIRA_E2E_ARTIFACT_DIR!;
const account = (info: TestInfo) => `fictional-${info.project.name}-${info.title}`;
const fixtureName = (info: TestInfo, scenario: string) => `Fictional ${info.project.name} ${scenario}`;
const categoryName = (info: TestInfo, suffix: string) => `Fictional ${info.project.name} Navigation ${suffix}`;

async function login(page: Page, info: TestInfo) {
  await page.goto("/login");
  await page.getByLabel("Usuario", { exact: true }).fill(account(info));
  await page.getByLabel("Contraseña", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Iniciar sesión", exact: true }).click();
  await expect(page).toHaveURL(/\/operations$/);
}
async function catalog(page: Page) {
  await page.getByRole("navigation").getByRole("link", { name: "Catálogo", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Organizá tus productos" })).toBeVisible();
}
async function filter(page: Page, label: string) {
  await expect(page.locator("#catalog-filter option", { hasText: label })).toHaveCount(1);
  await page.getByLabel("Filtrar productos").selectOption({ label });
}
async function fixture(page: Page, info: TestInfo, scenario: string) {
  await catalog(page);
  await filter(page, scenario === "catalog-readonly" || scenario === "catalog-revoked" ? categoryName(info, "B") : "Sin categoría");
  await page.getByRole("link", { name: fixtureName(info, scenario), exact: true }).click();
  await expect(page.getByRole("heading", { name: "Clasificación y estado" })).toBeVisible();
}
async function createCategory(page: Page, name: string) {
  await page.getByLabel("Nombre de categoría", { exact: true }).fill(name);
  await page.getByRole("button", { name: "Crear categoría", exact: true }).click();
}

test("catalog-journey", async ({ page }, info) => {
  await login(page, info);
  await page.getByLabel("Nombre del producto", { exact: true }).fill(`Fictional ${info.project.name} browser catalog`);
  await page.getByLabel("Presentación", { exact: true }).fill("Fictional presentation");
  await page.getByLabel("SKU", { exact: true }).fill(`CATALOG-${info.project.name}-journey`);
  await page.getByRole("button", { name: "Crear producto y presentación" }).click();
  await expect(page).toHaveURL(/\/operations\/products\/[0-9a-f-]{36}$/);
  await expect(page.getByRole("button", { name: "Activar producto", exact: true })).toBeDisabled();
  await catalog(page);
  const name = `Fictional ${info.project.name} browser category`;
  await createCategory(page, ` ${name} `);
  await expect(page.getByRole("status").filter({ hasText: "Categoría creada" })).toContainText("Categoría creada");
  await filter(page, "Sin categoría");
  await expect(page.getByRole("link", { name: `Fictional ${info.project.name} browser catalog`, exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: path.join(artifacts, `catalog-navigation-${info.project.name}.png`), fullPage: true });
  await page.getByRole("link", { name: `Fictional ${info.project.name} browser catalog`, exact: true }).click();
  await page.getByLabel("Categoría del producto", { exact: true }).selectOption({ label: name });
  await page.getByRole("button", { name: "Guardar categoría", exact: true }).click();
  await expect(page.locator(".classification-panel").getByRole("status")).toContainText("Clasificación guardada");
  await page.getByRole("button", { name: "Activar producto", exact: true }).click();
  await expect(page.getByRole("button", { name: "Desactivar producto", exact: true })).toBeEnabled();
  await expect(page.locator("#product-category option[value='']")).toBeDisabled();
  await page.getByLabel("Categoría del producto").selectOption({ label: categoryName(info, "B") });
  await page.getByRole("button", { name: "Guardar categoría", exact: true }).click();
  await expect(page.locator(".classification-panel").getByRole("status")).toContainText("Clasificación guardada");
  await page.getByRole("button", { name: "Desactivar producto", exact: true }).click();
  await expect(page.getByRole("button", { name: "Activar producto", exact: true })).toBeEnabled();
  await page.getByLabel("Categoría del producto").selectOption("");
  await page.getByRole("button", { name: "Guardar categoría", exact: true }).click();
  await expect(page.locator(".classification-panel").getByRole("status")).toContainText("Clasificación guardada");
  await page.getByRole("button", { name: "Guardar categoría", exact: true }).click(); // authorized no-op
  await expect(page.getByText("Activo no significa publicado, disponible ni vendible.", { exact: false })).toBeVisible();
  await expect(page.locator(".stock-count")).toHaveCount(0); // no Inventory mutation
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: path.join(artifacts, `catalog-${info.project.name}.png`), fullPage: true });
});

test("catalog-readonly", async ({ page }, info) => {
  await login(page, info);
  await fixture(page, info, "catalog-readonly");
  await expect(page.getByText("Sin permiso para cambiar la categoría.")).toBeVisible();
  await expect(page.getByText("Sin permiso para cambiar el estado administrativo.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Guardar categoría" })).toHaveCount(0);
  const denied = await page.evaluate(async (root) => {
    const csrf = await (await fetch(`${root}/auth/csrf`)).json();
    return (await fetch(`${root}/catalog/categories`, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf.csrf_token }, body: JSON.stringify({ name: "Should not persist" }) })).status;
  }, root);
  expect(denied).toBe(403);
});

test("catalog-revoked", async ({ page }, info) => {
  await login(page, info);
  await fixture(page, info, "catalog-revoked");
  process.stdout.write(`\nMIRA_BROWSER_CONTROL=${JSON.stringify({ action: "revoke", username: account(info) })}\n`);
  await expect.poll(() => existsSync(path.join(artifacts, `revoke-${account(info)}.ack`))).toBe(true);
  await page.getByRole("button", { name: "Activar producto", exact: true }).click();
  await expect(page.locator(".classification-panel .error-box")).toContainText("no tiene permiso");
});

test("catalog-stale", async ({ page }, info) => {
  await login(page, info);
  await catalog(page);
  await filter(page, categoryName(info, "A"));
  await expect(page.locator(".catalog-list li")).toHaveCount(50);
  await page.getByRole("button", { name: "Más productos", exact: true }).click();
  await expect(page.locator(".catalog-list li")).toHaveCount(2);
  const resetRequest = page.waitForRequest((request) => request.url().includes("/catalog/products?category=uncategorized"));
  await filter(page, "Sin categoría");
  expect(new URL((await resetRequest).url()).searchParams.has("after")).toBe(false);
  let release!: () => void;
  const wait = new Promise<void>((resolve) => { release = resolve; });
  let started!: () => void;
  const pending = new Promise<void>((resolve) => { started = resolve; });
  let held = false;
  await page.route("**/catalog/products?*", async (route) => {
    if (!held && new URL(route.request().url()).searchParams.get("category") !== "uncategorized") {
      held = true;
      const response = await route.fetch();
      started();
      await wait;
      await route.fulfill({ response }).catch(() => { /* selection aborts the obsolete GET */ });
    } else await route.continue();
  });
  await filter(page, categoryName(info, "A"));
  await pending;
  await filter(page, categoryName(info, "B"));
  await expect(page.getByRole("link", { name: fixtureName(info, "catalog-readonly"), exact: true })).toBeVisible();
  release();
  await expect(page.getByRole("button", { name: "Más productos", exact: true })).toHaveCount(0);
  await expect(page.locator(".catalog-list")).not.toContainText("Page");
});

test("catalog-lost-category", async ({ page }, info) => {
  await login(page, info);
  await catalog(page);
  let posts = 0;
  await page.route("**/catalog/categories", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    posts += 1;
    expect((await route.fetch()).status()).toBe(201);
    await route.abort("failed");
  });
  await createCategory(page, `Fictional ${info.project.name} lost category`);
  await expect(page.getByRole("main").getByRole("alert")).toContainText("Resultado incierto");
  await expect(page.getByRole("button", { name: "Crear categoría", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Actualizar categorías", exact: true }).click();
  await expect(page.locator("#catalog-filter option", { hasText: `Fictional ${info.project.name} lost category` })).toHaveCount(1);
  expect(posts).toBe(1);
});

test("catalog-lost-change", async ({ page }, info) => {
  await login(page, info);
  await fixture(page, info, "catalog-lost-change");
  let assignments = 0;
  await page.route("**/catalog/products/*/category", async (route) => {
    assignments += 1;
    expect((await route.fetch()).status()).toBe(200);
    await route.abort("failed");
  });
  await page.getByLabel("Categoría del producto").selectOption({ label: categoryName(info, "B") });
  await page.getByRole("button", { name: "Guardar categoría", exact: true }).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText("Resultado incierto");
  await page.getByRole("button", { name: "Actualizar detalle", exact: true }).click();
  await expect(page.locator(".classification-panel strong")).toHaveText(categoryName(info, "B"));
  await expect(page.getByRole("main").getByRole("alert")).toContainText("Resultado incierto");
  expect(assignments).toBe(1);
  await page.reload(); // explicit fresh observation, no replay of the lost command
  await expect(page.getByRole("button", { name: "Activar producto", exact: true })).toBeEnabled();
  let activations = 0;
  await page.route("**/catalog/products/*/active", async (route) => {
    activations += 1;
    expect((await route.fetch()).status()).toBe(200);
    await route.abort("failed");
  });
  await page.getByRole("button", { name: "Activar producto", exact: true }).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText("Resultado incierto");
  await page.getByRole("button", { name: "Actualizar detalle", exact: true }).click();
  await expect(page.getByRole("button", { name: "Desactivar producto", exact: true })).toBeDisabled();
  await expect(page.locator(".success-box")).toHaveCount(0);
  expect(activations).toBe(1);
});
