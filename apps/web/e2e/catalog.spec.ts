import { expect, Page, test, TestInfo } from "@playwright/test";
import { existsSync, writeFileSync } from "node:fs";
import path from "node:path";
import { OperationPermitCoordinator } from "../src/components/product-workspace";

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

// Hold the response at the browser fetch boundary, after the real GET finishes.
// This keeps the application read pending without retaining a Playwright Route
// that the browser may already have cancelled before a late fulfill.
async function holdPriceHistoryRead(page: Page, pathname: string) {
  await page.evaluate((historyPath) => {
    const originalFetch = window.fetch;
    let intercepted = false;
    let resolveRead: ((response: Response) => void) | undefined;
    let rejectRead: ((reason: DOMException) => void) | undefined;
    let controller: AbortController | undefined;
    const gate = {
      state: "waiting",
      release() {
        if (gate.state !== "ready" || !resolveRead) throw new Error("History read is not ready");
        gate.state = "released";
        const response = new Response(JSON.stringify({ revisions: [], next_cursor: null }), {
          status: 200, headers: { "Content-Type": "application/json" },
        });
        const parse = response.json.bind(response);
        response.json = async () => {
          const body = await parse();
          gate.state = "consumed";
          return body;
        };
        resolveRead(response);
      },
      cancel() {
        if (gate.state !== "ready" || !rejectRead) throw new Error("History read is not ready");
        gate.state = "cancelled";
        controller?.abort();
        rejectRead(new DOMException("Cancelled test read", "AbortError"));
      },
      restore() {
        if (gate.state === "fetching" || gate.state === "ready") {
          gate.state = "cancelled";
          controller?.abort();
          rejectRead?.(new DOMException("Disposed test read", "AbortError"));
        }
        window.fetch = originalFetch;
      },
    };
    (window as Window & { __miraHistoryGate?: typeof gate }).__miraHistoryGate = gate;
    window.fetch = (input, init) => {
      const request = new Request(input, init);
      if (intercepted || request.method !== "GET" || new URL(request.url).pathname !== historyPath) {
        return originalFetch(input, init);
      }
      intercepted = true;
      gate.state = "fetching";
      const pending = new Promise<Response>((resolve, reject) => {
        resolveRead = resolve;
        rejectRead = reject;
      });
      controller = new AbortController();
      const signal = init?.signal ? AbortSignal.any([init.signal, controller.signal]) : controller.signal;
      void originalFetch(input, { ...init, signal }).then(
        (response) => {
          if (gate.state === "cancelled") return;
          if (!response.ok) {
            gate.state = "failed";
            rejectRead?.(new DOMException("History GET failed", "NetworkError"));
          } else gate.state = "ready";
        },
        () => {
          if (gate.state === "cancelled") return;
          gate.state = "failed";
          rejectRead?.(new DOMException("History GET failed", "NetworkError"));
        },
      );
      return pending;
    };
  }, pathname);
  const state = () => page.evaluate(() => (window as Window & { __miraHistoryGate?: { state: string } }).__miraHistoryGate?.state);
  await expect.poll(state).toBe("waiting");
  return {
    ready: () => expect.poll(state).toBe("ready"),
    release: async () => {
      await page.evaluate(() => (window as Window & { __miraHistoryGate?: { release(): void } }).__miraHistoryGate!.release());
      await expect.poll(state).toBe("consumed");
      await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    },
    cancel: () => page.evaluate(() => (window as Window & { __miraHistoryGate?: { cancel(): void } }).__miraHistoryGate!.cancel()),
    restore: () => page.evaluate(() => {
      const target = window as Window & { __miraHistoryGate?: { state: string; restore(): void } };
      if (target.__miraHistoryGate) {
        target.__miraHistoryGate.restore();
        delete target.__miraHistoryGate;
      }
    }),
  };
}

test("catalog-journey", async ({ page }, info) => {
  const permits = new OperationPermitCoordinator();
  const old = permits.acquire("classification", "first-product");
  expect(old).not.toBeNull();
  expect(permits.acquire("variant", "first-product")).toBeNull();
  expect(permits.release(old!)).toBe(true);
  const current = permits.acquire("stock", "second-product");
  expect(current).not.toBeNull();
  expect(permits.release(old!)).toBe(false); // a late completion cannot unlock another operation
  expect(permits.busy).toBe(true);
  expect(permits.release(current!)).toBe(true);
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
  const productId = new URL(page.url()).pathname.split("/").at(-1)!;
  const categoryWrite = `**/api/v1/operator/catalog/products/${productId}/category`;
  const form = page.locator(".classification-panel form");
  let noopProcessed!: () => void;
  const noopReady = new Promise<void>((resolve) => { noopProcessed = resolve; });
  let releaseNoop!: () => void;
  const noopHeld = new Promise<void>((resolve) => { releaseNoop = resolve; });
  let noopStatus: number | null = null;
  await page.route(categoryWrite, async (route) => {
    if (route.request().method() !== "POST") { await route.continue(); return; }
    const response = await route.fetch();
    noopStatus = response.status();
    noopProcessed();
    await noopHeld;
    await route.fulfill({ response });
  });
  try {
    await page.getByRole("button", { name: "Guardar categoría", exact: true }).click(); // authorized no-op
    await noopReady;
    expect(noopStatus).toBe(200);
    await expect(form).toHaveAttribute("aria-busy", "true");
    releaseNoop();
    await expect(form).toHaveAttribute("aria-busy", "false");
    await expect(page.getByLabel("Categoría del producto")).toHaveValue("");
    await expect(page.locator(".classification-panel .saved-category strong")).toHaveText("Sin categoría");
  } finally {
    releaseNoop();
    await page.unroute(categoryWrite);
  }
  await expect(page.getByText("Activo no significa publicado, disponible ni vendible.", { exact: false })).toBeVisible();
  const optionValue = async (label: string) => page.getByLabel("Categoría del producto").locator("option", { hasText: label }).getAttribute("value");
  const categoryA = (await optionValue(categoryName(info, "A")))!;
  const categoryB = (await optionValue(categoryName(info, "B")))!;
  const categoryCreated = (await optionValue(name))!;
  const detail = "**/api/v1/operator/catalog/products/*";
  async function scriptedServerCategory(categoryId: string) {
    await page.unroute(detail);
    await page.route(detail, async (route) => {
      if (route.request().method() !== "GET") { await route.continue(); return; }
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
    if (route.request().method() !== "GET") { await route.continue(); return; }
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
  const categoryWriteForProduct = `**/api/v1/operator/catalog/products/${new URL(page.url()).pathname.split("/").at(-1)!}/category`;
  async function concurrentReadAndNoop(staleCategory: string, savedLabel: string, releaseReadAfterPost: boolean) {
    let readStarted!: () => void;
    const readReady = new Promise<void>((resolve) => { readStarted = resolve; });
    let releaseRead!: () => void;
    const readHeld = new Promise<void>((resolve) => { releaseRead = resolve; });
    let readFinished!: () => void;
    const readDone = new Promise<void>((resolve) => { readFinished = resolve; });
    await page.route(detail, async (route) => {
      if (route.request().method() !== "GET") return route.continue();
      try {
        const response = await route.fetch();
        const data = await response.json();
        data.category_id = staleCategory;
        readStarted();
        await readHeld;
        await route.fulfill({ response, json: data }).catch(() => { /* invalidated GET may be aborted */ });
      } finally { readFinished(); }
    });
    let postStarted!: () => void;
    const postReady = new Promise<void>((resolve) => { postStarted = resolve; });
    let releasePost!: () => void;
    const postHeld = new Promise<void>((resolve) => { releasePost = resolve; });
    let postStatus: number | null = null;
    try {
      await page.getByRole("button", { name: "Actualizar detalle", exact: true }).click();
      await readReady;
      await page.getByLabel("Categoría del producto").selectOption("");
      await page.route(categoryWriteForProduct, async (route) => {
        if (route.request().method() !== "POST") return route.continue();
        const response = await route.fetch();
        postStatus = response.status();
        postStarted();
        await postHeld;
        await route.fulfill({ response });
      });
      await page.getByRole("button", { name: "Guardar categoría", exact: true }).click();
      await postReady;
      expect(postStatus).toBe(200);
      await expect(page.locator(".classification-panel form")).toHaveAttribute("aria-busy", "true");
      await expect(page.getByRole("button", { name: "Actualizar detalle", exact: true })).toBeDisabled();
      await expect(page.getByRole("button", { name: "Agregar variante", exact: true })).toBeDisabled();
      await expect(page.getByRole("button", { name: "Registrar recepción inicial", exact: true })).toBeDisabled();
      if (!releaseReadAfterPost) {
        releaseRead(); await readDone;
        await expect(page.locator(".saved-category strong")).toHaveText(savedLabel);
      }
      releasePost();
      await expect(page.locator(".classification-panel form")).toHaveAttribute("aria-busy", "false");
      await expect(page.locator(".saved-category strong")).toHaveText("Sin categoría");
      await expect(page.getByLabel("Categoría del producto")).toHaveValue("");
      if (releaseReadAfterPost) {
        releaseRead(); await readDone;
        await expect(page.locator(".saved-category strong")).toHaveText("Sin categoría");
      }
      await expect(page.getByRole("button", { name: "Actualizar detalle", exact: true })).toBeEnabled();
    } finally {
      releaseRead(); releasePost();
      await page.unroute(detail);
      await page.unroute(categoryWriteForProduct);
    }
  }
  // The server still has no category: both controlled writes are real no-ops and add no Audit entries.
  await concurrentReadAndNoop(categoryB, name, false); // old GET arrives during the pending POST
  await scriptedServerCategory(categoryB);
  await page.getByRole("button", { name: "Actualizar detalle", exact: true }).click();
  await expect(page.locator(".saved-category strong")).toHaveText(categoryName(info, "B"));
  await expect(page.getByLabel("Categoría del producto")).toHaveValue(categoryB); // a later explicit GET may win
  await page.unroute(detail);
  await concurrentReadAndNoop(categoryCreated, categoryName(info, "B"), true); // old GET arrives after confirmation
  await expect(page.locator(".stock-count")).toHaveCount(0); // no Inventory mutation
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: path.join(artifacts, `catalog-${info.project.name}.png`), fullPage: true });
});

test("catalog-readonly", async ({ page }, info) => {
  await login(page, info);
  await fixture(page, info, "catalog-readonly");
  await expect(page.getByRole("button", { name: "Agregar variante", exact: true })).toHaveCount(0);
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
  let assignmentProcessed!: () => void;
  const assignmentReady = new Promise<void>((resolve) => { assignmentProcessed = resolve; });
  let releaseAssignment!: () => void;
  const assignmentHeld = new Promise<void>((resolve) => { releaseAssignment = resolve; });
  await page.route("**/catalog/products/*/category", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    assignments += 1;
    try {
      expect((await route.fetch()).status()).toBe(200);
      assignmentProcessed();
      await assignmentHeld;
      await route.abort("failed");
    } finally { assignmentProcessed(); }
  });
  try {
    await page.getByLabel("Categoría del producto").selectOption({ label: categoryName(info, "B") });
    await page.getByRole("button", { name: "Guardar categoría", exact: true }).click();
    await assignmentReady;
    await expect(page.locator(".classification-panel form")).toHaveAttribute("aria-busy", "true");
    await expect(page.getByRole("button", { name: "Actualizar detalle", exact: true })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Agregar variante", exact: true })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Registrar recepción inicial", exact: true })).toBeDisabled();
    releaseAssignment();
    await expect(page.locator(".classification-panel [role=alert]")).toContainText("Resultado incierto");
    await expect(page.getByRole("button", { name: "Actualizar detalle", exact: true })).toBeEnabled();
    await expect(page.getByRole("button", { name: "Guardar categoría", exact: true })).toBeDisabled();
    await page.getByRole("button", { name: "Actualizar detalle", exact: true }).click();
    await expect(page.locator(".classification-panel strong")).toHaveText(categoryName(info, "B"));
    await expect(page.locator(".classification-panel [role=alert]")).toContainText("Resultado incierto");
    expect(assignments).toBe(1);
  } finally { releaseAssignment(); await page.unroute("**/catalog/products/*/category"); }
  await page.reload(); // explicit fresh observation, no replay of the lost command
  await expect(page.getByRole("button", { name: "Activar producto", exact: true })).toBeEnabled();
  let activations = 0;
  await page.route("**/catalog/products/*/active", async (route) => {
    activations += 1;
    expect((await route.fetch()).status()).toBe(200);
    await route.abort("failed");
  });
  await page.getByRole("button", { name: "Activar producto", exact: true }).click();
  await expect(page.locator(".classification-panel [role=alert]")).toContainText("Resultado incierto");
  await page.getByRole("button", { name: "Actualizar detalle", exact: true }).click();
  await expect(page.getByRole("button", { name: "Desactivar producto", exact: true })).toBeDisabled();
  await expect(page.locator(".success-box")).toHaveCount(0);
  expect(activations).toBe(1);
});

// BC-01A is independent of catalog-journey and its open CI-01 investigation.
test("catalog-variants", async ({ page }, info) => {
  await login(page, info);
  await fixture(page, info, "catalog-variants");
  const productId = new URL(page.url()).pathname.split("/").pop()!;
  const original = await page.getByLabel("Presentación / SKU", { exact: true }).inputValue();
  const selectedIdentity = page.locator(".variant-panel .selected-variant-identity");
  await expect(selectedIdentity.locator("strong")).toHaveText("Fictional fixture");
  await expect(selectedIdentity.locator("span")).toHaveText(`SKU: CATALOG-FIXTURE-${info.project.name}-catalog-variants`);
  const detail = `${root}/catalog/products/${productId}`;
  let releaseRead!: () => void;
  let readStarted!: () => void;
  const held = new Promise<void>((resolve) => { releaseRead = resolve; });
  const started = new Promise<void>((resolve) => { readStarted = resolve; });
  let readDone!: () => void;
  const done = new Promise<void>((resolve) => { readDone = resolve; });
  await page.route(`**${detail}`, async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    try { const response = await route.fetch(); readStarted(); await held; await route.fulfill({ response }).catch(() => {}); }
    finally { readDone(); }
  }, { times: 1 });
  let releaseCreate!: () => void;
  let createStarted!: () => void;
  const createHeld = new Promise<void>((resolve) => { releaseCreate = resolve; });
  const createReady = new Promise<void>((resolve) => { createStarted = resolve; });
  await page.route(`**${detail}/variants`, async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    try { const response = await route.fetch(); createStarted(); await createHeld; await route.fulfill({ response }); }
    finally { createStarted(); }
  }, { times: 1 });
  try {
    await page.getByRole("button", { name: "Actualizar detalle", exact: true }).click();
    await started;
    await page.getByLabel("Etiqueta de presentación", { exact: true }).fill("Fictional fixture second pack");
    await page.getByLabel("SKU de la nueva variante", { exact: true }).fill(`BC01A-${info.project.name}-confirmed`);
    const created = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname === `${detail}/variants`);
    await page.getByRole("button", { name: "Agregar variante", exact: true }).click();
    await createReady;
    await expect(page.getByLabel("Categoría del producto", { exact: true })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Guardar categoría", exact: true })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Actualizar detalle", exact: true })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Registrar recepción inicial", exact: true })).toBeDisabled();
    releaseCreate();
    expect((await created).status()).toBe(201);
    await expect(page.getByText("Variante creada y seleccionada.", { exact: true })).toBeVisible();
    const selected = await page.getByLabel("Presentación / SKU", { exact: true }).inputValue();
    expect(selected).not.toBe(original);
    await expect(selectedIdentity.locator("strong")).toHaveText("Fictional fixture second pack");
    await expect(selectedIdentity.locator("span")).toHaveText(`SKU: BC01A-${info.project.name}-confirmed`);
    await expect(page.getByLabel("Categoría del producto", { exact: true })).toBeEnabled();
    await page.getByLabel("Categoría del producto", { exact: true }).selectOption({ label: categoryName(info, "B") });
    await page.getByRole("button", { name: "Guardar categoría", exact: true }).click();
    await expect(page.locator(".classification-panel form")).toHaveAttribute("aria-busy", "false");
    await page.getByRole("button", { name: "Activar producto", exact: true }).click();
    await expect(page.getByRole("button", { name: "Desactivar producto", exact: true })).toBeEnabled();
    releaseRead(); await done;
    await expect(page.getByLabel("Presentación / SKU", { exact: true })).toHaveValue(selected);
    await expect(page.locator("#variant option")).toHaveCount(2);
    await expect(page.locator(".saved-category strong")).toHaveText(categoryName(info, "B"));
    await expect(page.getByRole("button", { name: "Desactivar producto", exact: true })).toBeEnabled();
    await expect(page.getByText("Sin recepción inicial", { exact: true })).toBeVisible();
    await page.getByLabel("Presentación / SKU", { exact: true }).selectOption(original);
    await expect(selectedIdentity.locator("strong")).toHaveText("Fictional fixture");
    await expect(selectedIdentity.locator("span")).toHaveText(`SKU: CATALOG-FIXTURE-${info.project.name}-catalog-variants`);
    await expect(page.locator(".identity-panel .identifier")).toHaveText(original);
    await page.getByLabel("Presentación / SKU", { exact: true }).selectOption(selected);
    await expect(selectedIdentity.locator("strong")).toHaveText("Fictional fixture second pack");
    await expect(selectedIdentity.locator("span")).toHaveText(`SKU: BC01A-${info.project.name}-confirmed`);
    await expect(page.locator(".identity-panel .identifier")).toHaveText(selected);
    await expect(page.getByText("Sin recepción inicial", { exact: true })).toBeVisible();
    await page.getByLabel("Etiqueta de presentación", { exact: true }).fill("Fictional duplicate");
    await page.getByLabel("SKU de la nueva variante", { exact: true }).fill(`BC01A-${info.project.name}-confirmed`);
    await page.getByRole("button", { name: "Agregar variante", exact: true }).click();
    await expect(page.locator(".variant-panel [role=alert]")).toContainText("ya está registrado");
    await expect(page.locator("#variant option")).toHaveCount(2);
    await expect(page.getByRole("button", { name: "Actualizar detalle", exact: true })).toBeEnabled();
    await expect(page.getByLabel("Categoría del producto", { exact: true })).toBeEnabled();
    let releaseReceipt!: () => void;
    let receiptStarted!: () => void;
    const receiptHeld = new Promise<void>((resolve) => { releaseReceipt = resolve; });
    const receiptReady = new Promise<void>((resolve) => { receiptStarted = resolve; });
    const receiptPath = `${root}/inventory/variants/${selected}/initial-receipt`;
    await page.route(`**${receiptPath}`, async (route) => {
      if (route.request().method() !== "POST") return route.continue();
      try { expect((await route.fetch()).status()).toBe(201); receiptStarted(); await receiptHeld; await route.abort("failed"); }
      finally { receiptStarted(); }
    });
    try {
      await page.getByLabel("Unidad de inventario", { exact: true }).selectOption("EACH");
      await page.getByLabel("Cantidad recibida", { exact: true }).fill("12");
      await page.getByRole("button", { name: "Registrar recepción inicial", exact: true }).click();
      await receiptReady;
      await expect(page.getByLabel("Presentación / SKU", { exact: true })).toBeDisabled();
      await expect(page.getByRole("button", { name: "Agregar variante", exact: true })).toBeDisabled();
      await expect(page.getByLabel("Categoría del producto", { exact: true })).toBeDisabled();
      await expect(page.getByRole("button", { name: "Actualizar detalle", exact: true })).toBeDisabled();
      releaseReceipt();
      await expect(page.getByText("El resultado de la recepción es incierto.", { exact: false })).toBeVisible();
      await expect(page.locator(".stock-count")).toHaveText("12 EACH");
      await expect(page.getByLabel("Presentación / SKU", { exact: true })).toBeEnabled();
      await expect(page.getByLabel("Categoría del producto", { exact: true })).toBeEnabled();
      await page.getByLabel("Presentación / SKU", { exact: true }).selectOption(original);
      await expect(page.locator(".stock-count")).toHaveCount(0);
      await expect(page.getByText("Sin recepción inicial", { exact: true })).toBeVisible();
      await expect(page.getByText("requiere revisión. Cambiar de selección", { exact: false })).toBeVisible();
      await page.getByLabel("Presentación / SKU", { exact: true }).selectOption(selected);
      await expect(page.getByText("El resultado de la recepción es incierto.", { exact: false })).toBeVisible();
      await expect(page.locator(".stock-count")).toHaveText("12 EACH");
      await expect(page.getByRole("button", { name: "Registrar recepción inicial", exact: true })).toHaveCount(0);
    } finally { releaseReceipt(); await page.unroute(`**${receiptPath}`); }
    expect(await page.locator(".product-workspace").evaluate((workspace) =>
      [".classification-panel", ".variant-panel", ".price-panel", ".measurement-panel", ".identity-panel", ".stock-grid"]
        .map((selector) => workspace.querySelector(selector))
        .every((element, index, elements) => element !== null && (index === 0 || Boolean(elements[index - 1]!.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING))))).toBe(true);
    await expect(page.locator(".variant-panel .workspace-block-label")).toHaveText(["Registrado · variante seleccionada", "En edición"]);
    await page.getByLabel("Presentación / SKU", { exact: true }).focus();
    await page.keyboard.press("Tab");
    await expect(page.getByLabel("Etiqueta de presentación", { exact: true })).toBeFocused();
    await page.keyboard.press("Shift+Tab");
    await expect(page.getByLabel("Presentación / SKU", { exact: true })).toBeFocused();
    for (const width of [320, 390, 768, 1024, 1280, 1440]) {
      await page.setViewportSize({ width, height: 844 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await expect(selectedIdentity).toBeVisible();
      expect(await selectedIdentity.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
      if (width === 320 || width === 390) {
        await page.getByLabel("Presentación / SKU", { exact: true }).selectOption(original);
        await expect(selectedIdentity.locator("strong")).toHaveText("Fictional fixture");
        await expect(selectedIdentity.locator("span")).toHaveText(`SKU: CATALOG-FIXTURE-${info.project.name}-catalog-variants`);
        expect(await selectedIdentity.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
        await page.getByLabel("Presentación / SKU", { exact: true }).selectOption(selected);
        await expect(selectedIdentity.locator("strong")).toHaveText("Fictional fixture second pack");
        await expect(selectedIdentity.locator("span")).toHaveText(`SKU: BC01A-${info.project.name}-confirmed`);
      }
      for (const name of ["Actualizar detalle", "Agregar variante", "Actualizar consulta"]) {
        const box = await page.getByRole("button", { name, exact: true }).boundingBox();
        expect(box).not.toBeNull();
        expect(box!.width).toBeGreaterThanOrEqual(44);
        expect(box!.height).toBeGreaterThanOrEqual(44);
      }
    }
    const longName = `Producto ficticio ${"presentación extensa ".repeat(8)}`.slice(0, 255);
    await page.route(`**${detail}`, async (route) => {
      if (route.request().method() !== "GET") return route.continue();
      const response = await route.fetch();
      const data = await response.json();
      await route.fulfill({ response, json: { ...data, name: longName, variants: [] } });
    });
    try {
      await page.getByRole("button", { name: "Actualizar detalle", exact: true }).click();
      await expect(page.getByRole("heading", { level: 1, name: longName })).toBeVisible();
      await expect(page.getByLabel("Presentación / SKU", { exact: true })).toBeDisabled();
      await expect(page.locator(".price-panel, .measurement-panel, .stock-grid")).toHaveCount(0);
      await expect(page.getByText("No hay una variante disponible para consultar precio, medición o inventario.")).toBeVisible();
      for (const width of [320, 390, 768, 1024, 1280, 1440]) {
        await page.setViewportSize({ width, height: 844 });
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      }
    } finally { await page.unroute(`**${detail}`); }
  } finally { releaseRead(); releaseCreate(); await page.unroute(`**${detail}`); await page.unroute(`**${detail}/variants`); }
});

test("catalog-variants-lost", async ({ page }, info) => {
  await login(page, info);
  await fixture(page, info, "catalog-variants-lost");
  let release!: () => void;
  let started!: () => void;
  const hold = new Promise<void>((resolve) => { release = resolve; });
  const ready = new Promise<void>((resolve) => { started = resolve; });
  let posts = 0;
  await page.route("**/api/v1/operator/catalog/products/*/variants", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    posts += 1;
    try { expect((await route.fetch()).status()).toBe(201); started(); await hold; await route.abort("failed"); }
    finally { started(); }
  });
  try {
    await page.getByLabel("Etiqueta de presentación", { exact: true }).fill("Fictional uncertain pack");
    await page.getByLabel("SKU de la nueva variante", { exact: true }).fill(`BC01A-${info.project.name}-lost`);
    await page.getByRole("button", { name: "Agregar variante", exact: true }).click();
    await ready;
    await expect(page.locator(".variant-panel form")).toHaveAttribute("aria-busy", "true");
    await expect(page.getByLabel("Presentación / SKU", { exact: true })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Registrar recepción inicial", exact: true })).toBeDisabled();
    release();
    await expect(page.locator(".variant-panel [role=alert]")).toContainText("Resultado incierto");
    await page.getByRole("button", { name: "Actualizar detalle", exact: true }).click();
    await expect(page.locator("#variant option")).toHaveCount(2);
    await expect(page.getByRole("button", { name: "Agregar variante", exact: true })).toBeDisabled();
    await expect(page.locator(".variant-panel .notice")).toContainText("La lectura no confirma");
    expect(posts).toBe(1);
  } finally { release(); await page.unroute("**/api/v1/operator/catalog/products/*/variants"); }
});

test("catalog-price", async ({ page }, info) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.name));
  await login(page, info);
  await fixture(page, info, "catalog-price");
  const selector = page.getByLabel("Presentación / SKU", { exact: true });
  await selector.selectOption({ label: `Fictional fixture · CATALOG-FIXTURE-${info.project.name}-catalog-price` });
  const original = await selector.inputValue();
  await expect(page.locator("#variant option")).toHaveCount(2);
  const values = await page.locator("#variant option").evaluateAll((options) => options.map((option) => (option as HTMLOptionElement).value));
  const other = values.find((value) => value && value !== original)!;
  const selectedIdentity = page.locator(".variant-panel .selected-variant-identity");
  await expect(selectedIdentity.locator("strong")).toHaveText("Fictional fixture");
  await expect(selectedIdentity.locator("span")).toHaveText(`SKU: CATALOG-FIXTURE-${info.project.name}-catalog-price`);
  await expect(page.getByText("Sin precio asignado", { exact: true })).toBeVisible();
  await expect(page.locator(".price-panel .workspace-block-label")).toHaveText(["Registrado", "En edición"]);
  await expect(page.locator(".measurement-panel .workspace-block-label")).toHaveText(["Registrado", "En edición"]);
  const pricePath = `${root}/catalog/variants/${original}/price`;
  let pricePosts = 0;
  page.on("request", (request) => {
    if (request.method() === "POST" && new URL(request.url()).pathname === pricePath) pricePosts += 1;
  });
  await page.getByLabel("Importe ARS por variante").fill("12,30");
  const first = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname === pricePath);
  await page.getByRole("button", { name: "Guardar precio", exact: true }).click();
  expect((await first).status()).toBe(200);
  await expect(page.locator(".price-panel .stock-count")).toHaveText("ARS 12,30");
  await expect(page.locator(".price-history tbody tr")).toHaveCount(1);
  // No-op must retain its revision; the backend and Audit assertions verify the same invariant.
  const noop = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname === pricePath);
  await page.getByRole("button", { name: "Guardar precio", exact: true }).click();
  expect((await noop).status()).toBe(200);
  await expect(page.locator(".price-panel form")).toHaveAttribute("aria-busy", "false");
  await expect(page.locator(".price-history tbody tr")).toHaveCount(1);

  const historyPath = `${pricePath}/revisions`;
  const oldHistory = await holdPriceHistoryRead(page, historyPath);
  let releaseConflict!: () => void;
  let conflictStarted!: () => void;
  let conflictPosts = 0;
  const conflictHeld = new Promise<void>((resolve) => { releaseConflict = resolve; });
  const conflictReady = new Promise<void>((resolve) => { conflictStarted = resolve; });
  try {
    await page.getByRole("button", { name: "Consultar historial" }).click();
    await oldHistory.ready();
    await expect(page.getByRole("status").filter({ hasText: "Consultando revisiones" })).toBeVisible();
    await page.route(`**${pricePath}`, async (route) => {
      if (route.request().method() !== "POST") return route.continue();
      conflictPosts += 1;
      conflictStarted();
      await conflictHeld;
      await route.fulfill({ status: 409, json: { error: { code: "price_conflict" } } });
    }, { times: 1 });
    await page.getByLabel("Importe ARS por variante").fill("14,50");
    await page.getByRole("button", { name: "Guardar precio", exact: true }).click();
    await conflictReady;
    await oldHistory.cancel();
    releaseConflict();
    await expect(page.locator(".price-panel [role=alert]")).toContainText("revisión de precio cambió");
    await expect(page.getByLabel("Importe ARS por variante")).toHaveValue("14,50");
    await expect(page.getByRole("button", { name: "Guardar precio", exact: true })).toBeDisabled();
    await expect(page.getByRole("status").filter({ hasText: "Consultando revisiones" })).toHaveCount(0);
    await expect(page.locator(".price-history [role=alert]")).toHaveCount(0);
    await expect(page.locator(".price-history tbody tr")).toHaveCount(1);
    await expect(page.getByRole("button", { name: "Consultar historial" })).toBeEnabled();
    await page.getByRole("button", { name: "Consultar historial" }).click();
    await expect(page.locator(".price-history tbody tr")).toHaveCount(1);
    expect(conflictPosts).toBe(1);
  } finally {
    releaseConflict();
    if (!page.isClosed()) { await oldHistory.restore(); await page.unroute(`**${pricePath}`); }
  }
  expect(pricePosts).toBe(3); // Initial write, no-op, and exactly one conflicted write.
  await selector.selectOption(other);
  await expect(selectedIdentity.locator("strong")).toHaveText("Fictional alternate");
  await expect(selectedIdentity.locator("span")).toHaveText(`SKU: CATALOG-FIXTURE-${info.project.name}-catalog-price-alternate`);
  await expect(page.getByText("Sin precio asignado", { exact: true })).toBeVisible();
  await expect(page.locator(".price-panel .error-box")).toHaveCount(0);
  await expect(page.getByText("tiene un conflicto pendiente. El borrador se conserva", { exact: false })).toBeVisible();
  await selector.selectOption(original);
  await expect(selectedIdentity.locator("strong")).toHaveText("Fictional fixture");
  await expect(selectedIdentity.locator("span")).toHaveText(`SKU: CATALOG-FIXTURE-${info.project.name}-catalog-price`);
  await expect(page.getByText("tiene un conflicto pendiente. El borrador se conserva", { exact: false })).toHaveCount(0);
  await expect(page.locator(".price-panel .error-box")).toContainText("revisión de precio cambió");
  await expect(page.getByLabel("Importe ARS por variante")).toHaveValue("14,50");
  await expect(page.getByRole("button", { name: "Guardar precio", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Editar sobre esta revisión" })).toHaveCount(0);
  await page.getByRole("button", { name: "Consultar precio vigente" }).click();
  await expect(page.getByLabel("Importe ARS por variante")).toHaveValue("14,50");
  await expect(page.getByRole("button", { name: "Guardar precio", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Editar sobre esta revisión" })).toBeVisible();
  expect(pricePosts).toBe(3);
  await page.getByRole("button", { name: "Editar sobre esta revisión" }).click();
  await expect(page.getByRole("button", { name: "Guardar precio", exact: true })).toBeEnabled();
  const second = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname === pricePath);
  await page.getByRole("button", { name: "Guardar precio", exact: true }).click();
  expect((await second).status()).toBe(200);
  await expect(page.locator(".price-panel .stock-count")).toHaveText("ARS 14,50");
  await expect(page.locator(".price-history tbody tr")).toHaveCount(2);
  await selector.selectOption(other);
  await expect(page.locator(".price-panel")).toHaveCount(1);
  await expect(page.getByText("Sin precio asignado", { exact: true })).toBeVisible();
  await selector.selectOption(original);
  await expect(page.locator(".price-panel")).toHaveCount(1);
  await expect(page.locator(".price-panel .stock-count")).toHaveText("ARS 14,50");

  let malformedPosts = 0;
  const malformedPrice = { revision_id: "00000000-0000-4000-8000-000000000099", variant_id: original,
    sequence: "3", amount: "16.00", currency: "ARS", price_basis: "PER_VARIANT", recorded_at: "not-a-date" };
  await page.route(`**${pricePath}`, async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    malformedPosts += 1;
    await route.fulfill({ status: 200, json: { price: malformedPrice } });
  });
  try {
    await page.getByLabel("Importe ARS por variante").fill("16,00");
    await page.getByRole("button", { name: "Guardar precio", exact: true }).click();
    await expect(page.locator(".price-panel .error-box")).toContainText("Resultado incierto");
    await expect(page.locator(".price-panel .success-box")).toHaveCount(0);
    await expect(page.locator(".price-panel .stock-count")).toHaveText("ARS 14,50");
    await expect(page.getByRole("button", { name: "Guardar precio", exact: true })).toBeDisabled();
    await page.route(`**${pricePath}`, async (route) => {
      if (route.request().method() !== "GET") return route.continue();
      await route.fulfill({ status: 200, json: { price: { ...malformedPrice, recorded_at: "2026-02-30T12:00:00+00:00" } } });
    });
    const malformedRead = page.waitForResponse((response) => response.request().method() === "GET" && new URL(response.url()).pathname === pricePath);
    await page.getByRole("button", { name: "Consultar precio vigente" }).click();
    await malformedRead;
    await expect(page.locator(".price-panel > p[role=alert].notice")).toBeVisible();
    await expect(page.locator(".price-panel .stock-count")).toHaveText("ARS 14,50");
    await page.unroute(`**${pricePath}`);
    await page.getByRole("button", { name: "Consultar precio vigente" }).click();
    await expect(page.locator(".price-panel > p[role=alert].notice")).toHaveCount(0);
    await expect(page.locator(".price-panel > p[role=status].notice")).toContainText("Resultado incierto para esta variante");
    await expect(page.getByRole("button", { name: "Guardar precio", exact: true })).toBeDisabled();
    expect(malformedPosts).toBe(1);
    expect(pageErrors).toEqual([]);
  } finally { await page.unroute(`**${pricePath}`); }

  // Measurement uses the same fictional login, with separate read/write grants.
  const measurementPath = `${root}/catalog/variants/${original}/measurement-spec`;
  let measurementPosts = 0;
  page.on("request", (request) => {
    if (request.method() === "POST" && new URL(request.url()).pathname === measurementPath) measurementPosts += 1;
  });
  await expect(page.locator(".measurement-panel")).toHaveCount(1);
  await expect(page.getByText("Sin especificación declarada", { exact: true })).toBeVisible();
  await page.getByLabel("Unidad comercial").selectOption("EACH");
  await page.getByLabel("Contenido neto").fill("250");
  await page.getByLabel("Unidad de contenido").selectOption("G");
  const created = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname === measurementPath);
  await page.getByRole("button", { name: "Guardar medición", exact: true }).click();
  expect((await created).status()).toBe(200);
  await expect(page.locator(".measurement-panel .caption").first()).toContainText("Versión 1");
  await expect(page.locator(".measurement-panel .presentation-meta")).toContainText("250.000000 G");
  const measurementNoop = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname === measurementPath);
  await page.getByRole("button", { name: "Guardar medición", exact: true }).click();
  expect((await measurementNoop).status()).toBe(200);
  await expect(page.locator(".measurement-panel .caption").first()).toContainText("Versión 1");

  await page.route(`**${measurementPath}`, async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    await route.fulfill({ status: 409, json: { error: { code: "measurement_conflict" } } });
  }, { times: 1 });
  await page.getByLabel("Contenido neto").fill("500");
  await page.getByRole("button", { name: "Guardar medición", exact: true }).click();
  await expect(page.locator(".measurement-panel .error-box")).toContainText("versión de medición cambió");
  await expect(page.getByRole("button", { name: "Guardar medición", exact: true })).toBeDisabled();
  await selector.selectOption(other);
  await expect(page.locator(".measurement-panel")).toHaveCount(1);
  await expect(page.getByText("Sin especificación declarada", { exact: true })).toBeVisible();
  await expect(page.locator(".measurement-panel .error-box")).toHaveCount(0);
  await expect(page.getByText("tiene un conflicto pendiente. El borrador se conserva", { exact: false })).toBeVisible();
  await selector.selectOption(original);
  await expect(page.getByText("tiene un conflicto pendiente. El borrador se conserva", { exact: false })).toHaveCount(0);
  await expect(page.getByLabel("Contenido neto")).toHaveValue("500");
  await expect(page.locator(".measurement-panel .error-box")).toContainText("versión de medición cambió");
  await expect(page.getByRole("button", { name: "Editar sobre esta versión" })).toHaveCount(0);
  await page.getByRole("button", { name: "Consultar medición vigente" }).click();
  await expect(page.getByRole("button", { name: "Guardar medición", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Editar sobre esta versión" })).toBeVisible();
  expect(measurementPosts).toBe(3);
  await page.getByRole("button", { name: "Editar sobre esta versión" }).click();
  const replaced = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname === measurementPath);
  await page.getByRole("button", { name: "Guardar medición", exact: true }).click();
  expect((await replaced).status()).toBe(200);
  await expect(page.locator(".measurement-panel .caption").first()).toContainText("Versión 2");
  await expect(page.locator(".measurement-panel .presentation-meta")).toContainText("500.000000 G");

  await page.route(`**${measurementPath}`, async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    await route.fulfill({ status: 200, json: { spec: { version: "bad" } } });
  }, { times: 1 });
  await page.getByLabel("Contenido neto").fill("600");
  await page.getByRole("button", { name: "Guardar medición", exact: true }).click();
  await expect(page.locator(".measurement-panel .error-box")).toContainText("Resultado incierto");
  await expect(page.getByRole("button", { name: "Guardar medición", exact: true })).toBeDisabled();
  await selector.selectOption(other);
  await selector.selectOption(original);
  await expect(page.locator(".measurement-panel [role=status].notice")).toContainText("Resultado incierto para esta variante");
  await page.getByRole("button", { name: "Consultar medición vigente" }).click();
  await expect(page.getByRole("button", { name: "Guardar medición", exact: true })).toBeDisabled();
  expect(measurementPosts).toBe(5); // Two real writes, one no-op, one conflict, one malformed response.
  expect(pageErrors).toEqual([]);
});

test("catalog-price-lost", async ({ page }, info) => {
  await login(page, info);
  await fixture(page, info, "catalog-price-lost");
  const selector = page.getByLabel("Presentación / SKU", { exact: true });
  await selector.selectOption({ label: `Fictional fixture · CATALOG-FIXTURE-${info.project.name}-catalog-price-lost` });
  await expect(page.getByText("Sin precio asignado", { exact: true })).toBeVisible();
  const original = await selector.inputValue();
  const values = await page.locator("#variant option").evaluateAll((options) => options.map((option) => (option as HTMLOptionElement).value));
  const other = values.find((value) => value !== original)!;
  const pricePath = `${root}/catalog/variants/${original}/price`;
  const historyPath = `${pricePath}/revisions`;
  const oldHistory = await holdPriceHistoryRead(page, historyPath);
  let release!: () => void;
  let started!: () => void;
  const hold = new Promise<void>((resolve) => { release = resolve; });
  const ready = new Promise<void>((resolve) => { started = resolve; });
  let posts = 0;
  await page.route(`**${pricePath}`, async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    posts += 1;
    try { expect((await route.fetch()).status()).toBe(200); started(); await hold; await route.abort("failed"); }
    finally { started(); }
  });
  try {
    await page.getByRole("button", { name: "Consultar historial" }).click();
    await oldHistory.ready();
    await expect(page.getByRole("status").filter({ hasText: "Consultando revisiones" })).toBeVisible();
    await page.getByLabel("Importe ARS por variante").fill("19,90");
    await page.getByRole("button", { name: "Guardar precio", exact: true }).click();
    await ready;
    await expect(page.locator(".price-panel form")).toHaveAttribute("aria-busy", "true");
    await expect(selector).toBeDisabled();
    release();
    await expect(page.locator(".price-panel [role=alert]")).toContainText("Resultado incierto");
    await expect(page.getByRole("status").filter({ hasText: "Consultando revisiones" })).toHaveCount(0);
    await expect(page.locator(".price-history [role=alert]")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Consultar historial" })).toBeEnabled();
    await page.getByRole("button", { name: "Consultar historial" }).click();
    await expect(page.locator(".price-history tbody tr")).toHaveCount(1);
    await oldHistory.release(); // A late, stale empty result must not replace the newer revision.
    await expect(page.locator(".price-history tbody tr")).toHaveCount(1);
    await expect(page.locator(".price-history [role=alert]")).toHaveCount(0);
    await page.getByRole("button", { name: "Consultar precio vigente" }).click();
    await expect(page.locator(".price-panel .stock-count")).toHaveText("ARS 19,90");
    await expect(page.getByRole("button", { name: "Guardar precio", exact: true })).toBeDisabled();
    await selector.selectOption(other);
    await expect(page.locator(".price-panel")).toHaveCount(1);
    await expect(page.getByText("Sin precio asignado", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Guardar precio", exact: true })).toBeEnabled();
    await expect(page.getByText("requiere revisión. Cambiar de variante no confirma", { exact: false })).toBeVisible();
    await selector.selectOption(original);
    await expect(page.locator(".price-panel")).toHaveCount(1);
    await expect(page.locator(".price-panel > p[role=status].notice")).toHaveCount(1);
    await expect(page.locator(".price-panel > p[role=status].notice")).toContainText("Resultado incierto para esta variante");
    await page.getByRole("button", { name: "Consultar precio vigente" }).click();
    await expect(page.locator(".price-panel .stock-count")).toHaveText("ARS 19,90");
    await expect(page.locator(".price-history tbody tr")).toHaveCount(1);
    await expect(page.getByRole("button", { name: "Guardar precio", exact: true })).toBeDisabled();
    expect(posts).toBe(1);
  } finally {
    release();
    if (!page.isClosed()) { await oldHistory.restore(); await page.unroute(`**${pricePath}`); }
  }
});
