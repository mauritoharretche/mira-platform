import { expect, Page, test, TestInfo } from "@playwright/test";
import { existsSync, writeFileSync } from "node:fs";
import path from "node:path";

const root = "/api/v1/operator";
const password = process.env.MIRA_E2E_PASSWORD!;
const artifacts = process.env.MIRA_E2E_ARTIFACT_DIR!;
const account = (info: TestInfo) => `fictional-${info.project.name}-${info.title}`;
const fixtureName = (info: TestInfo, scenario: string) => `Fictional ${info.project.name} ${scenario}`;
const categoryName = (info: TestInfo, suffix: string) => `Fictional ${info.project.name} Navigation ${suffix}`;


// Captures belong to the authorized native runner; never capture filled login fields.
async function captureCatalogViewports(page: Page, info: TestInfo) {
  if (info.project.name !== "desktop") return;
  const measurements = [];
  // From a 1280px desktop, 200% and 400% browser zoom expose 640px and 320px CSS viewports.
  for (const [width, height] of [[320, 844], [390, 844], [640, 844], [768, 1024], [1024, 768], [1280, 800], [1440, 900]]) {
    await page.setViewportSize({ width, height });
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: path.join(artifacts, `catalog-${width}.png`), fullPage: false });
    measurements.push(await page.evaluate(() => {
      const items = Array.from(document.querySelectorAll(".catalog-list li"));
      return { width: innerWidth, height: innerHeight, pageWidth: document.documentElement.scrollWidth,
        fullyVisibleRows: items.filter((item) => { const r = item.getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight; }).length,
        firstProductTop: items[0]?.getBoundingClientRect().top ?? null,
        firstProductBottom: items[0]?.getBoundingClientRect().bottom ?? null,
        categoryCreationTop: document.querySelector(".category-create")?.getBoundingClientRect().top ?? null,
        presentation: items[0] ? getComputedStyle(items[0]).display : null,
        targets: ["#catalog-filter", "nav a[aria-current]", ".logout-button", ".catalog-product-name"].map((selector) => {
          const element = document.querySelector(selector)!;
          const box = element.getBoundingClientRect();
          return { selector, width: box.width, height: box.height };
        }),
        colors: (() => {
          const panel = getComputedStyle(document.querySelector(".catalog-products")!);
          const category = getComputedStyle(document.querySelector(".catalog-product-category")!);
          const select = getComputedStyle(document.querySelector("select")!);
          const activeLink = getComputedStyle(document.querySelector("nav a[aria-current]")!);
          const badge = getComputedStyle(document.querySelector(".catalog-list .badge")!);
          return { ink: getComputedStyle(document.body).color, canvas: getComputedStyle(document.documentElement).backgroundColor,
            surface: panel.backgroundColor, muted: category.color, controlBorder: select.borderTopColor,
            controlBackground: select.backgroundColor, activeText: activeLink.color, activeBackground: activeLink.backgroundColor,
            badgeText: badge.color, badgeBackground: badge.backgroundColor,
            focus: getComputedStyle(document.documentElement).getPropertyValue("--focus").trim() };
        })(),
      };
    }));
  }
  writeFileSync(path.join(artifacts, "catalog-responsive.json"), JSON.stringify(measurements, null, 2));
  for (const result of measurements) {
    expect(result.pageWidth).toBeLessThanOrEqual(result.width);
    for (const target of result.targets) { expect(target.width).toBeGreaterThanOrEqual(44); expect(target.height).toBeGreaterThanOrEqual(44); }
  }
  expect(measurements.find((result) => result.width === 1280)!.fullyVisibleRows).toBeGreaterThanOrEqual(10);
  const mobile = measurements.find((result) => result.width === 390)!;
  expect(mobile.fullyVisibleRows).toBeGreaterThanOrEqual(1);
  expect(mobile.firstProductBottom!).toBeLessThanOrEqual(844);
  expect(mobile.firstProductTop!).toBeLessThan(mobile.categoryCreationTop!);
  expect(mobile.presentation).toBe("flex");
  await page.setViewportSize({ width: 1280, height: 800 });
}


// Scripted read responses exercise presentation only, never grant server permissions.
async function presentationBoundaries(page: Page, info: TestInfo) {
  const products = "**/api/v1/operator/catalog/products?*";
  const categoryReads = "**/api/v1/operator/catalog/categories";
  const longName = `Producto ficticio ${"presentación extensa ".repeat(7)}${"X".repeat(70)}`.slice(0, 255);
  await page.route(categoryReads, async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { categories: Array.from({ length: 50 }, (_, index) => ({
      id: `10000000-0000-4000-8000-${String(index).padStart(12, "0")}`,
      name: `Categoría ficticia ${index} ${"clasificación extensa ".repeat(8)}`,
    })), next_cursor: null } });
  });
  await page.route(products, async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    data.products[0].name = longName;
    await route.fulfill({ response, json: data });
  });
  await catalog(page);
  await expect(page.locator(".category-navigation li")).toHaveCount(50);
  await expect(page.locator(".catalog-list li").first().getByRole("link")).toHaveText(longName);
  await expect(page.getByLabel("Nombre de categoría", { exact: true })).toHaveCount(0);
  for (const width of [320, 390, 768, 1280]) {
    await page.setViewportSize({ width, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect(await page.locator(".catalog-product-name").first().evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("link", { name: "Gestionar categorías", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Categorías", exact: true })).toBeInViewport();
  await page.getByRole("link", { name: "Volver a productos", exact: true }).click();
  await expect(page.locator("#catalog-products-title")).toBeFocused();
  // Text-resize stress is separate from the unzoomed screenshot fixtures.
  await page.evaluate(() => { document.documentElement.style.fontSize = "200%"; });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.evaluate(() => { document.documentElement.style.removeProperty("font-size"); });
  await page.goto("/operations/catalog"); // Fresh entry without an explicit focus fragment.
  await page.reload();
  await expect(page.locator(".catalog-list li")).toHaveCount(50);
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Ir al contenido", exact: true })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#workspace")).toBeFocused();
  const filterControl = page.getByLabel("Filtrar productos");
  await filterControl.focus();
  expect(await filterControl.evaluate((element) => getComputedStyle(element).outlineWidth)).toBe("3px");
  await page.keyboard.press("Shift+Tab");
  expect(await page.evaluate(() => document.activeElement?.tagName)).not.toBe("BODY");
  await page.keyboard.press("Tab");
  await expect(filterControl).toBeFocused();
  await page.unroute(products);
  let release!: () => void;
  const hold = new Promise<void>((resolve) => { release = resolve; });
  await page.route(products, async (route) => { await hold; await route.fulfill({ json: { products: [], next_cursor: null } }); });
  await page.getByRole("button", { name: "Volver al inicio / actualizar", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Consultando productos" })).toBeVisible();
  release();
  await expect(page.getByRole("heading", { name: "Sin productos en este filtro" })).toBeVisible();
  await page.unroute(products);
  await page.route(products, (route) => route.fulfill({ status: 503, json: { error: { code: "unavailable" } } }));
  await page.getByRole("button", { name: "Volver al inicio / actualizar", exact: true }).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText("servicio no está disponible");
  await page.unroute(products);
  await page.unroute(categoryReads);
  await page.getByRole("button", { name: "Reintentar consulta", exact: true }).click();
  await expect(page.locator(".catalog-list li")).toHaveCount(50);
  await page.route("**/api/v1/operator/auth/session", (route) => route.fulfill({ json: { authenticated: true, capabilities: ["catalog.read_product"] } }));
  await page.reload();
  await expect(page.locator("#catalog-basic-filter")).toBeVisible();
  await expect(page.getByText("Sin permiso para consultar categorías.", { exact: true })).toBeVisible();
  await page.route("**/api/v1/operator/auth/session", (route) => route.fulfill({ json: { authenticated: true, capabilities: [] } }));
  await page.reload();
  await expect(page.getByRole("main").getByRole("alert")).toContainText("no tiene permiso para consultar productos");
  await expect(page.locator(".catalog-list")).toHaveCount(0);
  await page.unroute("**/api/v1/operator/auth/session");
  await page.reload();
  await expect(page.locator(".catalog-list li")).toHaveCount(50);
  await page.setViewportSize(info.project.use.viewport!);
  writeFileSync(path.join(artifacts, `presentation-boundaries-${info.project.name}.json`), JSON.stringify({
    source: "scripted GET responses in authorized native browser test; no simulated writes or server grants",
    longNameLength: longName.length, categories: 50, widths: [320, 390, 768, 1280],
    keyboardSkipAndFocus: "PASS", categoryAnchorRoundTrip: "PASS", textResize200Percent: "PASS", zoomReflowEquivalents: { "200%": 640, "400%": 320 }, loadingEmptyErrorRecovery: "PASS", partialPermissionRendering: "PASS",
  }, null, 2));
}

async function login(page: Page, info: TestInfo) {
  await page.goto("/login");
  await page.getByLabel("Usuario", { exact: true }).fill(account(info));
  await page.getByLabel("Contraseña", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Iniciar sesión", exact: true }).click();
  await expect(page).toHaveURL(/\/operations\/catalog$/);
  await expect(page.getByRole("navigation").getByRole("link", { name: "Catálogo", exact: true })).toHaveAttribute("aria-current", "page");
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
  await page.getByRole("navigation").getByRole("link", { name: "Nuevo producto", exact: true }).click();
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
  await expect(page).toHaveURL(/\/operations\/catalog\?category=uncategorized$/);
  await expect(page.getByRole("link", { name: `Fictional ${info.project.name} browser catalog`, exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: path.join(artifacts, `catalog-navigation-${info.project.name}.png`), fullPage: true });
  await page.getByRole("link", { name: `Fictional ${info.project.name} browser catalog`, exact: true }).click();
  await expect.poll(() => new URL(page.url()).searchParams.get("category")).toBe("uncategorized");
  await expect(page.locator("#product-title")).toBeFocused();
  await page.getByLabel("Categoría del producto", { exact: true }).selectOption({ label: name });
  await page.getByRole("button", { name: "Guardar categoría", exact: true }).click();
  await expect(page.locator(".classification-panel").getByRole("status")).toContainText("Clasificación guardada");
  await page.getByRole("button", { name: "Activar producto", exact: true }).click();
  await expect(page.getByRole("button", { name: "Desactivar producto", exact: true })).toBeEnabled();
  await page.screenshot({ path: path.join(artifacts, `ux02-classification-${info.project.name}.png`), fullPage: true });
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
  const optionValue = async (label: string) => page.getByLabel("Categoría del producto").locator("option", { hasText: label }).getAttribute("value");
  const categoryA = (await optionValue(categoryName(info, "A")))!;
  const categoryB = (await optionValue(categoryName(info, "B")))!;
  const categoryCreated = (await optionValue(name))!;
  const detail = "**/api/v1/operator/catalog/products/*";
  async function scriptedServerCategory(categoryId: string) {
    await page.unroute(detail);
    await page.route(detail, async (route) => {
      const response = await route.fetch();
      const data = await response.json();
      data.category_id = categoryId;
      await route.fulfill({ response, json: data });
    });
  }
  // Scripted GET responses model another writer without changing fixture Audit totals.
  await scriptedServerCategory(categoryB);
  await page.getByRole("button", { name: "Actualizar detalle", exact: true }).click();
  await expect(page.getByLabel("Categoría del producto")).toHaveValue(categoryB); // clean local selection follows the server
  await page.getByLabel("Categoría del producto").selectOption(categoryA);
  await expect(page.getByText("Selección local sin guardar.")).toBeVisible();
  await page.unroute(detail);
  let releaseOld!: () => void;
  const oldHeld = new Promise<void>((resolve) => { releaseOld = resolve; });
  let oldStarted!: () => void;
  const oldReady = new Promise<void>((resolve) => { oldStarted = resolve; });
  let detailReads = 0;
  await page.route(detail, async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    detailReads += 1;
    data.category_id = detailReads === 1 ? categoryB : categoryCreated;
    if (detailReads === 1) { oldStarted(); await oldHeld; }
    await route.fulfill({ response, json: data }).catch(() => { /* obsolete GET may be aborted */ });
  });
  await page.getByRole("button", { name: "Actualizar detalle", exact: true }).click();
  await oldReady;
  await page.getByRole("button", { name: "Actualizar detalle", exact: true }).click();
  await expect(page.locator(".classification-panel .saved-category strong")).toHaveText(name);
  await expect(page.getByLabel("Categoría del producto")).toHaveValue(categoryA); // dirty selection survives refresh
  await expect(page.getByText("La categoría guardada cambió durante tu edición.", { exact: false })).toBeVisible();
  releaseOld();
  await expect(page.locator(".classification-panel .saved-category strong")).toHaveText(name); // obsolete response cannot replace newer read
  await page.screenshot({ path: path.join(artifacts, `ux02-pending-selection-${info.project.name}.png`), fullPage: true });
  await expect(page.getByRole("button", { name: "Guardar categoría", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Usar valor guardado" }).click();
  await expect(page.getByLabel("Categoría del producto")).toHaveValue(categoryCreated);
  await expect(page.getByText("Selección local sin guardar.")).toHaveCount(0);
  await page.unroute(detail);
  await expect(page.locator(".stock-count")).toHaveCount(0); // no Inventory mutation
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: path.join(artifacts, `catalog-${info.project.name}.png`), fullPage: true });
});

test("catalog-readonly", async ({ page }, info) => {
  await login(page, info);
  await fixture(page, info, "catalog-readonly");
  await page.getByRole("link", { name: "Volver al catálogo", exact: true }).click();
  await expect(page.getByLabel("Filtrar productos")).toHaveValue((await page.locator("#catalog-filter option", { hasText: categoryName(info, "B") }).getAttribute("value"))!);
  await expect(page.locator("#catalog-products-title")).toBeFocused();
  await page.getByRole("link", { name: fixtureName(info, "catalog-readonly"), exact: true }).click();
  await expect(page.getByText("Sin permiso para cambiar la categoría.")).toBeVisible();
  await expect(page.getByText("Sin permiso para cambiar el estado administrativo.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Guardar categoría" })).toHaveCount(0);
  const denied = await page.evaluate(async (root) => {
    const csrf = await (await fetch(`${root}/auth/csrf`)).json();
    return (await fetch(`${root}/catalog/categories`, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf.csrf_token }, body: JSON.stringify({ name: "Should not persist" }) })).status;
  }, root);
  expect(denied).toBe(403);
  await presentationBoundaries(page, info);
  const categoryB = await page.locator("#catalog-filter option", { hasText: categoryName(info, "B") }).getAttribute("value");
  expect(categoryB).toMatch(/^[0-9a-f-]{36}$/);
  await page.getByLabel("Filtrar productos").selectOption(categoryB!);
  await expect.poll(() => new URL(page.url()).searchParams.get("category")).toBe(categoryB);
  await page.getByLabel("Filtrar productos").selectOption("uncategorized");
  await expect.poll(() => new URL(page.url()).searchParams.get("category")).toBe("uncategorized");
  await page.goBack();
  await expect(page.getByLabel("Filtrar productos")).toHaveValue(categoryB!);
  await page.goForward();
  await expect(page.getByLabel("Filtrar productos")).toHaveValue("uncategorized");
  await page.goto(`/operations/catalog?after=invalid&category=${categoryB!.toUpperCase()}&unknown=https%3A%2F%2Fexample.invalid`);
  await expect(page).toHaveURL(`/operations/catalog?category=${categoryB}`);
  await expect(page.getByLabel("Filtrar productos")).toHaveValue(categoryB!);
  await page.goto(`/operations/catalog?category=${categoryB}&category=${categoryB}&after=ffffffff-ffff-ffff-ffff-ffffffffffff`);
  await expect(page).toHaveURL("/operations/catalog");
  await page.goto(`/operations/catalog?category=${categoryB}&after=ffffffff-ffff-ffff-ffff-ffffffffffff`);
  await expect(page.getByRole("heading", { name: "Sin productos en este filtro" })).toBeVisible();
  await page.getByRole("button", { name: "Volver al inicio / actualizar", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/operations/catalog\\?category=${categoryB}(?:#.*)?$`));
  await expect(page.locator(".catalog-list li").first()).toBeVisible();
  let releaseCategories!: () => void;
  const heldCategories = new Promise<void>((resolve) => { releaseCategories = resolve; });
  await page.route("**/api/v1/operator/catalog/categories", async (route) => { await heldCategories; await route.continue(); });
  try {
    await page.goto(`/operations/catalog?category=${categoryB}`);
    await expect(page.locator(`#catalog-filter option[value="${categoryB}"]`)).toHaveText("Categoría seleccionada — cargá más categorías");
  } finally { releaseCategories(); }
  await expect(page.locator(`#catalog-filter option[value="${categoryB}"]`)).toHaveText(categoryName(info, "B"));
  await page.unroute("**/api/v1/operator/catalog/categories");
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
  let detailReads = 0;
  page.on("request", (request) => {
    if (request.method() === "GET" && /\/api\/v1\/operator\/catalog\/products\/[0-9a-f-]{36}$/.test(new URL(request.url()).pathname)) detailReads += 1;
  });
  await login(page, info);
  await catalog(page);
  await filter(page, categoryName(info, "A"));
  const semanticList = page.getByRole("list", { name: "Productos del catálogo", exact: true });
  await expect(semanticList).toBeVisible();
  await expect(semanticList.getByRole("listitem")).toHaveCount(50);
  await captureCatalogViewports(page, info);
  await page.getByRole("button", { name: "Más productos", exact: true }).click();
  await expect(page.locator(".catalog-list li")).toHaveCount(2);
  expect(detailReads).toBe(0); // List still has no per-product detail reads.
  const pageTwo = new URL(page.url());
  expect(pageTwo.searchParams.get("category")).toMatch(/^[0-9a-f-]{36}$/);
  expect(pageTwo.searchParams.get("after")).toMatch(/^[0-9a-f-]{36}$/);
  await page.locator(".catalog-list .catalog-product-name").first().click();
  await expect.poll(() => new URL(page.url()).searchParams.get("after")).toBe(pageTwo.searchParams.get("after"));
  await page.getByRole("link", { name: "Volver al catálogo", exact: true }).click();
  await expect(page.locator(".catalog-list li")).toHaveCount(2);
  await expect(page.locator("#catalog-products-title")).toBeFocused();
  expect(new URL(page.url()).search).toBe(pageTwo.search);
  await page.reload();
  await expect(page.locator(".catalog-list li")).toHaveCount(2);
  await page.goBack();
  await expect(page.locator("#product-title")).toBeVisible();
  await page.goForward();
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
  expect(detailReads).toBeGreaterThan(0);
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
