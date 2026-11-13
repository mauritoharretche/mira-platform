"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useRef, useState } from "react";
import { canonicalUuid, catalogHref, CatalogViewState, productHref } from "@/lib/catalog-view-state";
import { Category, CategoryPage, catalogQuery, messageFor, OperatorError, post, ProductPage, ReadGeneration, request } from "@/lib/operations-api";
import { useOperator } from "./operations-shell";

export function CatalogBrowser({ view }: { view: CatalogViewState }) {
  const router = useRouter();
  const { capabilities } = useOperator();
  const canRead = capabilities.includes("catalog.read_product");
  const canCategories = capabilities.includes("catalog.list_categories");
  const canCreate = capabilities.includes("catalog.create_category");
  const [page, setPage] = useState<{ key: string; value: ProductPage } | null>(null);
  const [categories, setCategories] = useState<Category[]>([]);
  const [categoryCursor, setCategoryCursor] = useState<string | null>(null);
  const [categoryAfter, setCategoryAfter] = useState<string | null>(null);
  const [categoryAttempt, setCategoryAttempt] = useState(0);
  const [categoryLoading, setCategoryLoading] = useState(true);
  const [categoryError, setCategoryError] = useState("");
  const [error, setError] = useState<{ key: string; message: string } | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [name, setName] = useState("");
  const [notice, setNotice] = useState("");
  const [createError, setCreateError] = useState("");
  const [busy, setBusy] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const reads = useRef(new ReadGeneration());
  const categoryReads = useRef(new ReadGeneration());
  const alive = useRef(false);
  const filter = view.category;
  const key = catalogQuery(filter, view.after);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    if (window.location.hash === "#catalog-products-title") document.getElementById("catalog-products-title")?.focus();
  }, [key]); // Navigation only: never move focus when a background GET resolves.

  useEffect(() => {
    if (!canRead) return;
    const controller = new AbortController();
    const stream = reads.current;
    const token = stream.begin();
    request<ProductPage>(key, { signal: controller.signal }).then((value) => {
      if (!controller.signal.aborted && stream.accepts(token)) { setPage({ key, value }); setError(null); }
    }).catch((failure) => { if (!controller.signal.aborted && stream.accepts(token)) setError({ key, message: messageFor(failure) }); });
    return () => { controller.abort(); stream.invalidate(); };
  }, [key, attempt, canRead]);

  useEffect(() => {
    if (!canCategories) return;
    const controller = new AbortController();
    const categoryStream = categoryReads.current;
    const token = categoryStream.begin();
    request<CategoryPage>(`/catalog/categories${categoryAfter ? `?after=${encodeURIComponent(categoryAfter)}` : ""}`, { signal: controller.signal }).then((value) => {
      if (controller.signal.aborted || !categoryStream.accepts(token)) return;
      setCategories((previous) => categoryAfter ? [...previous, ...value.categories.filter((item) => !previous.some((old) => old.id === item.id))] : value.categories);
      setCategoryCursor(value.next_cursor); setCategoryLoading(false); setCategoryError("");
    }).catch((failure) => { if (!controller.signal.aborted && categoryStream.accepts(token)) { setCategoryError(messageFor(failure)); setCategoryLoading(false); } });
    return () => { controller.abort(); categoryStream.invalidate(); };
  }, [categoryAfter, categoryAttempt, canCategories]);

  function selectFilter(value: string) {
    if (value === filter) return;
    reads.current.invalidate(); setPage(null); setError(null);
    router.push(catalogHref({ category: value, after: null }));
  }
  function startOrRefresh() {
    reads.current.invalidate(); setPage(null); setError(null);
    if (view.after !== null) router.push(`${catalogHref({ category: filter, after: null })}#catalog-products-title`);
    else setAttempt((value) => value + 1);
  }
  function nextPage(cursor: string) {
    const after = canonicalUuid(cursor);
    if (!after) return;
    reads.current.invalidate(); setPage(null); setError(null);
    router.push(`${catalogHref({ category: filter, after })}#catalog-products-title`);
  }
  function refreshCategories() {
    categoryReads.current.invalidate(); setCategoryLoading(true); setCategoryAfter(null); setCategoryAttempt((value) => value + 1);
  }
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canCreate || busy || uncertain) return;
    if (!name.trim()) { setCreateError("Ingresá un nombre de categoría."); return; }
    setBusy(true); setCreateError(""); setNotice("");
    try {
      const result = await post<Category>("/catalog/categories", { name });
      if (!alive.current) return;
      if (typeof result !== "object" || result === null || typeof result.id !== "string" || !result.id || typeof result.name !== "string" || !result.name) {
        throw new OperatorError("invalid_response", undefined, true);
      }
      setNotice(`Categoría creada: ${result.name}.`); setName("");
      if (canCategories) refreshCategories();
    } catch (failure) {
      if (!alive.current) return;
      setCreateError(messageFor(failure));
      if (failure instanceof OperatorError && failure.uncertain) setUncertain(true);
    } finally { if (alive.current) setBusy(false); }
  }
  const current = page?.key === key ? page.value : null;
  const currentError = error?.key === key ? error.message : "";
  return <>
    <div className="page-heading catalog-page-heading"><p className="eyebrow">Catálogo operativo</p><h1>Organizá tus productos</h1><p className="muted">Clasificación y estado administrativo. Activar no significa publicado, disponible ni vendible.</p></div>
    <div className="catalog-layout">
      <section className="panel catalog-products" aria-labelledby="catalog-products-title">
        <div className="section-heading"><h2 id="catalog-products-title" tabIndex={-1}>Productos</h2>
          {capabilities.includes("catalog.create_product_with_initial_variant") && <Link className="entry-link button-primary" href="/operations">Nuevo producto</Link>}
        </div>
        <div className="catalog-toolbar">
          <div className="catalog-filter">
            <label htmlFor={canCategories ? "catalog-filter" : "catalog-basic-filter"}>Filtrar productos</label>
            <select id={canCategories ? "catalog-filter" : "catalog-basic-filter"} value={filter} onChange={(event) => selectFilter(event.target.value)} disabled={!canRead}>
              <option value="all">Todos los productos</option><option value="uncategorized">Sin categoría</option>
              {filter !== "all" && filter !== "uncategorized" && !categories.some((item) => item.id === filter) && <option value={filter}>Categoría seleccionada{canCategories ? " — cargá más categorías" : ""}</option>}
              {canCategories && <>
                {categories.map((category) => <option key={category.id} value={category.id}>{category.name}</option>)}
              </>}
            </select>
          </div>
          <a className="category-shortcut" href="#catalog-categories">Gestionar categorías</a>
        </div>
        {!canRead ? <p role="alert" className="catalog-message notice">Tu cuenta no tiene permiso para consultar productos.</p> : <>
          {currentError && <p role="alert" className="catalog-message error-box">{currentError}</p>}
          {!current && !currentError && <p role="status" className="catalog-message">Consultando productos…</p>}
          {current && <>{!current.products.length ? <div className="catalog-message empty-state"><h3>Sin productos en este filtro</h3><p>No hay productos para este filtro. Probá otra categoría o volvé a todos los productos.</p></div> : <>
            <div className="catalog-columns" aria-hidden="true"><span>Producto</span><span>Categoría</span><span>Estado administrativo</span></div>
            <ul className="catalog-list" role="list" aria-label="Productos del catálogo">{current.products.map((product) => <li key={product.id}>
              <Link className="catalog-product-name" href={`${productHref(product.id, view)}#product-title`}>{product.name}</Link>
              <p className="catalog-product-category"><span className="visually-hidden">Categoría: </span>{product.category_id ? categories.find((item) => item.id === product.category_id)?.name ?? "Categoría asignada" : "Sin categoría"}</p>
              <span className={`badge ${product.is_active ? "badge-active" : "badge-inactive"}`}>{product.is_active ? "Activo administrativamente" : "Inactivo administrativamente"}</span>
            </li>)}</ul>
          </>}
          <p role="status" className="visually-hidden">{current.products.length} productos en esta página.</p>
          <div className="catalog-pagination"><button className="button-quiet" onClick={startOrRefresh}>Volver al inicio / actualizar</button>
            {current.next_cursor && <button onClick={() => nextPage(current.next_cursor!)}>Más productos</button>}</div>
          <p className="caption catalog-page-note">Listado por páginas. Cambiar el filtro vuelve al inicio.</p></>}
          {currentError && <button className="catalog-retry" onClick={() => { setError(null); setAttempt((value) => value + 1); }}>Reintentar consulta</button>}
        </>}
      </section>
      <aside id="catalog-categories" className="panel category-panel" aria-labelledby="categories-title" tabIndex={-1}>
        <h2 id="categories-title">Categorías</h2>
        <a className="category-return" href="#catalog-products-title">Volver a productos</a>
        {!canCategories ? <p className="muted">Sin permiso para consultar categorías.</p> : <>
          <p className="caption">Elegí una categoría para filtrar el catálogo.</p>
          <ul className="category-navigation" aria-label="Categorías cargadas">{categories.map((category) => <li key={category.id}><button className="button-quiet" disabled={!canRead} aria-pressed={filter === category.id} onClick={() => selectFilter(category.id)}>{category.name}</button></li>)}</ul>
          {categoryError && <p role="alert" className="field-error">{categoryError}</p>}
          {categoryLoading && <p role="status">Consultando categorías…</p>}
          {categoryCursor && <button className="button-quiet" disabled={categoryLoading} onClick={() => { setCategoryLoading(true); setCategoryAfter(categoryCursor); setCategoryAttempt((value) => value + 1); }}>Más categorías</button>}
          <button className="button-quiet" disabled={categoryLoading} onClick={refreshCategories}>Actualizar categorías</button>
        </>}
        {canCreate && <form className="category-create" onSubmit={(event) => void create(event)} aria-busy={busy}>
          <h3>Nueva categoría</h3><label htmlFor="category-name">Nombre de categoría</label><input id="category-name" maxLength={255} value={name} onChange={(event) => setName(event.target.value)} required disabled={busy || uncertain} />
          <button className="button-primary" disabled={busy || uncertain}>{busy ? "Creando…" : "Crear categoría"}</button>
          {createError && <p role="alert" className="field-error">{createError}</p>}{notice && <p role="status">{notice}</p>}
          {uncertain && <p className="notice">La creación pudo haberse guardado. Consultá las categorías; no reenvíes esta operación.</p>}
        </form>}
      </aside>
    </div>
  </>;
}
